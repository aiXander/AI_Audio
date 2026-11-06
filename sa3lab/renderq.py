"""The render queue: list, delete, reorder and skip Generate jobs (the studio's Queue modal).

Every Generate click is a job in gradio's own queue (studio.py sets the generate
listener's trigger_mode="multiple"), so this module edits that queue in place
instead of keeping a second one: the waiting jobs are `Event`s in
`blocks._queue.event_queue_per_concurrency_id[<generate fn>].queue`, a plain list
gradio's worker pops from the front. Each event already carries the raw input
values of its click (`event.data.data`, in upstream's `ctrl_inputs` order), which
is where the settings preview comes from.

    GET  /sa3/queue                       → {"running": job | None, "waiting": [job, …]}
    POST /sa3/queue {op: "delete", id}    drop a waiting job
    POST /sa3/queue {op: "move", id, to}  move a waiting job to index `to` (0 = next up)
    POST /sa3/queue {op: "clear"}         drop every waiting job (the running one keeps going)
    POST /sa3/queue {op: "skip", id}      stop the running job only (id = its run token);
                                          the next waiting job starts right after

A job is {"id", "preview": {prompt, dit, decoder, seconds, steps, seed, cfg, …}}.

Deleting does what gradio's own POST /cancel does for an event: take it out of the
queue, then push a "process_completed" message so the browser tab stops waiting
for it (its pending spinner clears). Skip reuses the cooperative Stop flag
(studio.py): it marks only the running job's epoch stopped, so later jobs run.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable

from fastapi import Request
from fastapi.responses import JSONResponse

# upstream sa3_gradio ctrl_inputs order: 17 controls, then 3 LoRA slots × (file, pick, strength, from, to)
_I = {k: i for i, k in enumerate(
    ["dit", "decoder", "prompt", "neg", "seconds", "steps", "seed", "cfg", "apg", "sigma",
     "init_noise", "a2a", "inpaint", "inp_start", "inp_end", "opts", "fmt"])}
_LORA0 = 17

# set by studio.py: the running generate job (preview + run token) and the skip hook
running: dict | None = None
skip_hook: Callable[[int], None] | None = None


def _audio_name(v: Any) -> str | None:
    """A gr.Audio value, raw (FileData dict, waiting jobs) or preprocessed (path, running job)."""
    if not v:
        return None
    if isinstance(v, dict):
        return v.get("orig_name") or os.path.basename(v.get("path") or "") or "audio"
    return os.path.basename(str(v))


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def preview(vals: list) -> dict:
    """The settings worth showing for one job, from its input values (positional)."""
    g = lambda k: vals[_I[k]] if len(vals) > _I[k] else None   # noqa: E731
    seed = str(g("seed") or "").strip()
    cfg = _num(g("cfg"))
    p = {"prompt": (g("prompt") or "").strip(), "dit": g("dit"), "decoder": g("decoder"),
         "seconds": _num(g("seconds")), "steps": int(_num(g("steps")) or 0) or None,
         "seed": seed if seed and seed != "-1" else None, "cfg": cfg}
    if cfg is not None and cfg != 1:
        p["apg"] = _num(g("apg"))
        if (g("neg") or "").strip():
            p["neg"] = g("neg").strip()
    a2a, inp = _audio_name(g("a2a")), _audio_name(g("inpaint"))
    if a2a:
        p["a2a"] = {"audio": a2a, "noise": _num(g("init_noise"))}
    elif (_num(g("sigma")) or 1) != 1:
        p["sigma"] = _num(g("sigma"))
    if inp:
        p["inpaint"] = {"audio": inp, "from": _num(g("inp_start")), "to": _num(g("inp_end"))}
    seg = vals[-1] if vals and isinstance(vals[-1], str) and vals[-1].startswith('{"inpaint"') else None
    if seg:                         # segments.py's spans box rides last in a waiting job's inputs
        import segments
        if inp:
            p["inpaint"]["spans"] = len(segments.parse(seg) or [])
    loras = []
    for i in range(3):
        s = vals[_LORA0 + 5 * i: _LORA0 + 5 * i + 5]
        if len(s) < 5:
            break
        f, pick, strength = _audio_name(s[0]), s[1], _num(s[2])
        name = f or (os.path.basename(str(pick)) if pick and not str(pick).startswith("(") else None)
        if name:
            loras.append({"name": os.path.splitext(name)[0], "strength": strength})
    if loras:
        p["loras"] = loras
    return p


def _gen_queue(blocks):
    """(gradio Queue, the generate fn's waiting-event list) or (queue, None) before the first click."""
    q = blocks._queue
    fn = next((f for f in blocks.fns.values() if f.name == "generate"), None)
    eq = q.event_queue_per_concurrency_id.get(fn.concurrency_id) if fn is not None else None
    return q, (eq.queue if eq is not None else None)


def waiting(blocks) -> list[dict]:
    _, lst = _gen_queue(blocks)
    out = []
    for ev in list(lst or []):
        vals = list(getattr(ev.data, "data", None) or [])
        out.append({"id": ev._id, "t": time.time() - (time.monotonic() - ev.enqueue_time),
                    "preview": preview(vals)})
    return out


def n_waiting(blocks) -> int:
    _, lst = _gen_queue(blocks)
    return len(lst or [])


def state(blocks) -> dict:
    return {"running": running, "waiting": waiting(blocks)}


async def _delete(q, lst, ev) -> None:
    """gradio's POST /cancel for a queued event: dequeue it, then tell its tab it's done."""
    from gradio.server_messages import ProcessCompletedMessage
    async with q.delete_lock:
        if ev in lst:
            lst.remove(ev)
        q.event_ids_to_events.pop(ev._id, None)
    sess = ev.session_hash
    if sess in q.pending_messages_per_session and ev._id in q.pending_event_ids_session.get(sess, set()):
        q.pending_messages_per_session[sess].put_nowait(
            ProcessCompletedMessage(output={}, success=True, event_id=ev._id))


async def apply(blocks, d: dict) -> tuple[bool, str]:
    op = d.get("op")
    if op == "skip":
        r = running
        if r is None or skip_hook is None or r.get("id") != d.get("id"):
            return False, "that job is no longer running"
        skip_hook(r["id"])
        return True, ""
    q, lst = _gen_queue(blocks)
    if lst is None:
        return False, "queue is empty"
    if op == "clear":
        for ev in list(lst):
            await _delete(q, lst, ev)
        return True, ""
    ev = next((e for e in lst if e._id == d.get("id")), None)
    if ev is None:
        return False, "that job already started or left the queue"
    if op == "delete":
        await _delete(q, lst, ev)
        return True, ""
    if op == "move":
        async with q.delete_lock:
            if ev not in lst:
                return False, "that job just started"
            lst.remove(ev)
            lst.insert(max(0, min(len(lst), int(d.get("to", 0)))), ev)
        return True, ""
    return False, f"unknown op {op!r}"


def install_routes() -> None:
    """Add /sa3/queue to every app gradio builds, ahead of its catch-all routes."""
    from gradio import routes

    async def queue_get(request: Request):
        return JSONResponse(state(request.app.get_blocks()))

    async def queue_post(request: Request):
        blocks = request.app.get_blocks()
        ok, err = await apply(blocks, await request.json())
        return JSONResponse({"ok": ok, "error": err or None, **state(blocks)}, status_code=200 if ok else 409)

    orig = routes.App.create_app

    def create_app(*a, **kw):
        app = orig(*a, **kw)
        app.add_api_route("/sa3/queue", queue_get, methods=["GET"])
        app.add_api_route("/sa3/queue", queue_post, methods=["POST"])
        ours = [r for r in app.router.routes if getattr(r, "path", "").startswith("/sa3/")]
        app.router.routes[:] = ours + [r for r in app.router.routes if r not in ours]
        return app
    routes.App.create_app = staticmethod(create_app)
