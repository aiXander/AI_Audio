"""Highlights: saved time ranges on renders, with tags, a note and a score (composer spec 3.1).

A highlight is a moment worth keeping: `label.highlights = [{id, s0, s1, tags, note, score, ts}]`
in the render's original sidecar (a starred/ link writes through: library._sidecar resolves it).
No audio is copied; the composer cuts on demand. Saving a highlight stars the render (the library
is the starred set), so every highlight shows up in the composer's side panel and the Library page.
Unstarring keeps the highlights in the sidecar.

Per-highlight analysis (keyfind.analyze_file: BPM, beat phase, downbeat, key, LUFS, 3 bands,
width) goes in `analysis.highlights[id]`, filled by a background worker the first time a highlight
is read without it (the library.py tempo-worker pattern). Writes use library._lock with the same
read-modify-write + atomic rename as library.write_label, because the tempo worker and the label
routes write the same file.

Routes (added to the studio's FastAPI app; renders by `?id=` render id, or `name` in the active folder):

    GET  /sa3/highlights?id=            → {"highlights": [...], "analysis": {hid: {...} | {"pending": true}},
                                           "duration", "bpm", "conf", "phase" (first beat, confident beats only)}
    POST /sa3/highlights {id, op, h}    op "add" (h = {s0, s1, tags?, note?, score?}; stars the render),
                                        "update" (h.id + fields), "remove" (h.id) → the GET answer
    GET  /sa3/highlights/all            → every starred render's highlights: [{id, prompt, hl: [...]}]
"""
from __future__ import annotations

import json
import queue
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import Request  # module level: route annotations are resolved from globals

import library

ANALYSIS_V = 1
_q: "queue.Queue[tuple[Path, str]]" = queue.Queue()
_seen: set[tuple[str, str]] = set()
_started = False


def _read(p: Path) -> dict:
    try:
        return json.loads(library._sidecar(p).read_text())
    except Exception:
        return {"name": p.name}


def _write(p: Path, fn) -> dict:
    """Read-modify-write the sidecar under library._lock (atomic rename)."""
    sc = library._sidecar(p)
    with library._lock:
        try:
            d = json.loads(sc.read_text())
        except Exception:
            d = {"name": p.resolve().name}
        fn(d)
        tmp = sc.with_name(sc.name + ".tmp")
        tmp.write_text(json.dumps(d))
        tmp.replace(sc)
    library._rows.pop(p.name, None)
    return d


def _clean(h: dict, dur: float | None) -> dict:
    s0, s1 = float(h.get("s0", 0)), float(h.get("s1", 0))
    s0, s1 = max(0.0, min(s0, s1)), max(s0, s1)
    if dur:
        s1 = min(s1, dur)
    out = {"s0": round(s0, 3), "s1": round(s1, 3)}
    if "tags" in h:
        seen, tags = set(), []
        for t in h.get("tags") or []:
            t = re.sub(r"\s+", " ", str(t)).strip()[:60]
            if t and t.lower() not in seen:
                seen.add(t.lower())
                tags.append(t)
        out["tags"] = tags
    if "note" in h:
        out["note"] = str(h.get("note") or "")[:2000]
    if "score" in h:
        out["score"] = None if h["score"] in (None, "") else max(0, min(10, int(h["score"])))
    return out


def _duration(p: Path) -> float | None:
    import segments
    try:
        return segments.duration(str(p))
    except Exception:
        return None


def view(p: Path) -> dict:
    d = _read(p)
    hl = (d.get("label") or {}).get("highlights") or []
    an = (d.get("analysis") or {}).get("highlights") or {}
    t = (d.get("analysis") or {}).get("tempo") or {}
    out = {}
    for h in hl:
        a = an.get(h["id"])
        if a and a.get("v") == ANALYSIS_V and a.get("s") == [h["s0"], h["s1"]]:
            out[h["id"]] = a
        else:
            out[h["id"]] = {"pending": True}
            _enqueue(p, h["id"])
    return {"id": library.render_id(p), "highlights": hl, "analysis": out, "duration": _duration(p),
            "bpm": t.get("bpm"), "conf": t.get("conf"), "phase": _phase(p, d, t)}


def _phase(p: Path, d: dict, t: dict) -> float | None:
    """The render's first beat (s), for the highlight modal's beat snap; only with a confident beat.
    Measured once on the first 30 s (compose.beat_phase) and kept in analysis.beatgrid."""
    if not t.get("bpm") or (t.get("conf") or 0) < 0.3:
        return None
    bg = (d.get("analysis") or {}).get("beatgrid") or {}
    if bg.get("bpm") == t["bpm"]:
        return bg.get("phase")
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent / "composer"))
        import compose
        ph = round(compose.beat_phase(compose.decode(p.resolve(), 0.0, 30.0), float(t["bpm"])), 4)
    except Exception:
        return None
    _write(p, lambda x: x.setdefault("analysis", {}).__setitem__("beatgrid", {"bpm": t["bpm"], "phase": ph}))
    return ph


def add(p: Path, h: dict) -> dict:
    dur = _duration(p)
    new = {"id": uuid.uuid4().hex[:8], **{"tags": [], "note": "", "score": None}, **_clean(h, dur),
           "ts": datetime.now().isoformat(timespec="seconds")}
    if new["s1"] - new["s0"] < 0.25:
        raise ValueError("a highlight needs at least 0.25 s")
    _write(p, lambda d: d.setdefault("label", {}).setdefault("highlights", []).append(new))
    if not (_read(p).get("label") or {}).get("starred"):
        library.star_render(p, True)
    return view(p) | {"added": new["id"]}


def update(p: Path, h: dict) -> dict:
    dur = _duration(p)

    def fn(d):
        for x in (d.get("label") or {}).get("highlights") or []:
            if x["id"] == h.get("id"):
                x.update(_clean({**x, **h}, dur))
    _write(p, fn)
    return view(p)


def remove(p: Path, hid: str) -> dict:
    def fn(d):
        lab = d.get("label") or {}
        lab["highlights"] = [x for x in lab.get("highlights") or [] if x["id"] != hid]
        an = (d.get("analysis") or {}).get("highlights")
        if an:
            an.pop(hid, None)
    _write(p, fn)
    return view(p)


def all_starred() -> list[dict]:
    out = []
    star = library.STAR_DIR
    for link in star.iterdir() if star.is_dir() else []:
        if link.suffix.lower() not in library.AUDIO_EXT:
            continue
        p = link.resolve()
        d = _read(p)
        hl = (d.get("label") or {}).get("highlights") or []
        if hl:
            g = d.get("gen") or {}
            out.append({"id": library.render_id(p), "prompt": g.get("prompt", d.get("prompt")) or "",
                        "hl": hl, "analysis": ((d.get("analysis") or {}).get("highlights") or {})})
    return out


# ── analysis worker ─────────────────────────────────────────────────────────────────────────
def _enqueue(p: Path, hid: str) -> None:
    global _started
    k = (str(p.resolve()), hid)
    if k in _seen:
        return
    _seen.add(k)
    if not _started:
        _started = True
        threading.Thread(target=_worker, name="sa3-highlights", daemon=True).start()
    _q.put((p, hid))


def _worker() -> None:
    import keyfind
    while True:
        p, hid = _q.get()
        try:
            h = next((x for x in (_read(p).get("label") or {}).get("highlights") or [] if x["id"] == hid), None)
            if h is None or not p.exists():
                continue
            a = {"v": ANALYSIS_V, "s": [h["s0"], h["s1"]], **keyfind.analyze_file(p.resolve(), h["s0"], h["s1"])}
        except Exception as e:  # noqa: BLE001
            a = {"v": ANALYSIS_V, "s": [h["s0"], h["s1"]] if h else None, "error": str(e)[:200]}
        finally:
            _seen.discard((str(p.resolve()), hid))
        if not p.exists():
            continue
        _write(p, lambda d: d.setdefault("analysis", {}).setdefault("highlights", {}).__setitem__(hid, a))
        time.sleep(0.05)


# ── routes ──────────────────────────────────────────────────────────────────────────────────
def install_routes() -> None:
    from fastapi.responses import JSONResponse
    from gradio import routes

    def path_of(name: str, rid: str) -> Path | None:
        return library.resolve_id(rid) if rid else library._audio_path(name)

    def hl_get(name: str = "", id: str = ""):
        p = path_of(name, id)
        if p is None:
            return JSONResponse({"error": "unknown file"}, status_code=404)
        return JSONResponse(view(p))

    async def hl_post(request: Request):
        import anyio
        b = await request.json()
        p = path_of(b.get("name", ""), b.get("id", ""))
        if p is None:
            return JSONResponse({"error": "unknown file"}, status_code=404)
        op, h = b.get("op"), b.get("h") or {}
        try:
            fn = {"add": lambda: add(p, h), "update": lambda: update(p, h),
                  "remove": lambda: remove(p, h.get("id", ""))}[op]
            r = await anyio.to_thread.run_sync(fn)
        except KeyError:
            return JSONResponse({"error": f"unknown op {op!r}"}, status_code=400)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(r)

    def all_get():
        return JSONResponse(all_starred())

    orig = routes.App.create_app

    def create_app(*a, **kw):
        app = orig(*a, **kw)
        app.add_api_route("/sa3/highlights/all", all_get, methods=["GET"])
        app.add_api_route("/sa3/highlights", hl_get, methods=["GET"])
        app.add_api_route("/sa3/highlights", hl_post, methods=["POST"])
        ours = [r for r in app.router.routes if getattr(r, "path", "").startswith("/sa3/")]
        app.router.routes[:] = ours + [r for r in app.router.routes if r not in ours]
        return app
    routes.App.create_app = staticmethod(create_app)
