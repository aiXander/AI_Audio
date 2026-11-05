"""Switchable output folder: the studio views AND renders into one *active* folder under outputs/.

Before this, every render went to `outputs/sa3-gradio/` and the history + Library showed only
that folder. Now a folder chip next to the Library button (outdir.js) picks any folder
under `AI_Audio/outputs/` (or creates a new one), and that folder becomes:

- the **view**: `library.OUT_DIR` (Library rows, labels, audio/spectrogram routes, BPM) and
  the history column (the hidden apply button reloads the session from `persist.load_recent`);
- the **destination** of new renders.

Destination is bound **per job at click time**, not read when the job runs: a hidden
textbox (`#sa3-outdir-dest`) holding the active folder is appended to the generate /
promote / pregen listeners' inputs, so gradio snapshots it with the prompt when Generate
is clicked. A wrapper around those handlers (installed *outside* studio's own wrapper, so
studio and renderq still see upstream's input list with the state last) pops it and
points `sa3_gradio.OUTPUT_DIR` there for that job. Switching folders therefore never moves
renders that are already queued.

The active folder survives restarts (`outputs/.sa3_outdir.json`). Folders are sandboxed to
`outputs/`; `deleted/`, `inputs/`, `cond/` and hidden folders are not offered.

    GET  /sa3/outdir                 → {"active": rel, "folders": [{"rel", "n"}]}
    POST /sa3/outdir {"path": rel}   switch (creates the folder if it doesn't exist)
"""
from __future__ import annotations

import copy
import functools
import json
import os
from pathlib import Path

from fastapi import Request  # module level: FastAPI resolves route annotations from globals

ROOT = (Path(__file__).resolve().parents[1] / "outputs").resolve()
STATE = ROOT / ".sa3_outdir.json"
AUDIO_EXT = {".mp3", ".wav"}
SKIP = {"deleted", "inputs", "cond"}
_up = _library = None


def resolve(rel: str | None) -> Path | None:
    """A folder under outputs/ (relative or absolute), or None if outside the sandbox."""
    if not rel:
        return None
    p = Path(rel)
    p = (p if p.is_absolute() else ROOT / p).resolve()
    if p != ROOT and ROOT not in p.parents:
        return None
    if any(part.startswith(".") or part in SKIP for part in p.relative_to(ROOT).parts):
        return None
    return p


def rel(p: Path) -> str:
    """Path relative to outputs/ (absolute if outside it: only headless test runs)."""
    p = Path(p).resolve()
    if p == ROOT:
        return "."
    return str(p.relative_to(ROOT)) if ROOT in p.parents else str(p)


def active() -> Path:
    return Path(_library.OUT_DIR)


def set_active(p: Path, remember: bool = True) -> None:
    """Switch the VIEW. Never touches sa3_gradio.OUTPUT_DIR: a running render reads that
    when it writes its file, and each job sets it from its own click-time destination."""
    p.mkdir(parents=True, exist_ok=True)
    _library.OUT_DIR = p
    if remember and not os.environ.get("SA3_OUTDIR"):
        try:
            STATE.write_text(json.dumps({"active": rel(p)}))
        except Exception:
            pass


def folders() -> list[dict]:
    out = []
    for d in sorted(ROOT.rglob("*")):
        if not d.is_dir() or resolve(str(d)) is None or len(d.relative_to(ROOT).parts) > 3:
            continue
        n = sum(1 for f in d.iterdir() if f.suffix.lower() in AUDIO_EXT)
        if n or d == active():
            out.append({"rel": rel(d), "n": n})
    return out


def init(up, library) -> None:
    """After library.init: restore the saved active folder (default: studio's own).
    $SA3_OUTDIR pins it for this process without persisting (sa3lab/anarchy/headless.py)."""
    global _up, _library
    _up, _library = up, library
    if os.environ.get("SA3_OUTDIR"):
        set_active(Path(os.environ["SA3_OUTDIR"]).resolve(), remember=False)
        up.OUTPUT_DIR = active()
        return
    try:
        saved = resolve(json.loads(STATE.read_text()).get("active"))
    except Exception:
        saved = None
    set_active(saved if saved is not None else Path(up.OUTPUT_DIR).resolve())
    up.OUTPUT_DIR = active()            # default for anything rendering without a job destination


def install(demo, persist, spec_fn, render_player, history_size: int = 10) -> None:
    """Wire the hidden destination input + the view-switch button. Call after studio's
    _wire_handlers (our wrapper must sit outside it) and before launch."""
    import gradio as gr

    blocks = list(demo.blocks.values())
    st = next(b for b in blocks if isinstance(b, gr.State)
              and isinstance(b.value, dict) and "history" in b.value)
    slot = {c: next(b for b in blocks if isinstance(b, gr.HTML) and c in (b.elem_classes or []))
            for c in ("sa3-player", "sa3-timing", "sa3-history")}
    with demo:
        # rendered but CSS-hidden (outdir.css): gradio 6 drops visible=False components from
        # the page, and outdir.js must be able to click the apply button
        with gr.Row(elem_id="sa3-outdir-hidden"):
            dest = gr.Textbox(value=rel(active()), elem_id="sa3-outdir-dest", container=False)
            apply_btn = gr.Button("apply folder", elem_id="sa3-outdir-apply")

    for bf in demo.fns.values():
        if bf.name not in ("generate", "promote", "pregen"):
            continue
        bf.inputs.append(dest)
        inner = bf.fn

        @functools.wraps(inner)
        def wrapped(*a, _inner=inner, _present=bf.name != "pregen"):
            *rest, d = a
            p = (resolve(d) or (Path(d) if d and Path(d).is_absolute() and os.environ.get("SA3_OUTDIR")
                                else None) or active())
            p.mkdir(parents=True, exist_ok=True)
            _up.OUTPUT_DIR = p              # read by upstream when it writes the file
            snap = copy.deepcopy(rest[-1]) if _present and isinstance(rest[-1], dict) else None
            out = _inner(*rest)
            if snap is not None and isinstance(out, tuple) and len(out) == 6 and p != active():
                # rendered into a folder that isn't on screen: it's saved there (and shows
                # up when that folder is opened) but this view stays as it is; keep the
                # error line. _reorder's order: player, timing, error, queued, history, state
                out = (gr.update(), gr.update(), out[2], gr.update(), gr.update(), snap)
            return out
        bf.fn = wrapped

    def reload_view(state):
        entries = persist.load_recent(active(), _up, spec_fn, history_size)
        state = {"current": entries[0] if entries else None, "queued": None,
                 "history": entries[1:]}
        if not entries:
            return "", "", "", state, rel(active())
        return (render_player(entries[0]), entries[0].get("timing", ""),
                _up.render_history(state["history"]), state, rel(active()))

    def sync_dest():
        return rel(active())

    with demo:
        apply_btn.click(reload_view, inputs=[st],
                        outputs=[slot["sa3-player"], slot["sa3-timing"], slot["sa3-history"],
                                 st, dest], queue=False, show_progress="hidden")
        demo.load(sync_dest, outputs=[dest], queue=False, show_progress="hidden")


def install_routes() -> None:
    from fastapi.responses import JSONResponse
    from gradio import routes

    def get():
        return JSONResponse({"active": rel(active()), "folders": folders()})

    async def post(request: Request):
        d = await request.json()
        p = resolve(str(d.get("path") or "").strip())
        if p is None:
            return JSONResponse({"error": "folder must be inside outputs/"}, status_code=400)
        set_active(p)
        return JSONResponse({"active": rel(p), "folders": folders()})

    orig = routes.App.create_app

    def create_app(*a, **kw):
        app = orig(*a, **kw)
        app.add_api_route("/sa3/outdir", get, methods=["GET"])
        app.add_api_route("/sa3/outdir", post, methods=["POST"])
        ours = [r for r in app.router.routes if getattr(r, "path", "").startswith("/sa3/")]
        app.router.routes[:] = ours + [r for r in app.router.routes if r not in ours]
        return app
    routes.App.create_app = staticmethod(create_app)
