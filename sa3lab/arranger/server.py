"""SA3 Arranger: a mini-DAW for rough song-structure sketches that SA3 then texturizes.

    sa3lab/arranger/arranger            # → http://127.0.0.1:7861 (or `make arranger`)

Files: compose.py (theory + seeded generators: template → events), synth.py (numpy DSP),
render.py (events → WAV + control curves; the template schema), presets.py, server.py (this
API), static/app.js (the page: canvas timeline, inspector, helpers, send panel). The studio's
side is sa3lab/arranger_link.py/.js. v2 plans: docs/TODO/sa3-arranger.md.

Its own process and port, separate from the studio (restart it freely). FastAPI + a vanilla
JS/canvas page (static/), no build step. Files (all under outputs/arranger/, gitignored):
- `<slug>/template.json`: the working template, autosaved by the page on every edit;
- `<slug>/renders/…`: renders (render.py); only the newest KEEP_RENDERS per template are
  kept, plus every render that was sent to SA3 (index.json marks them `sent`);
- `sends/<id>.json` + `inbox.json`: "Send to SA3" records. The page writes the record and
  the inbox pointer; the studio page polls `/sa3/arranger/inbox` (sa3lab/arranger_link.py),
  takes it, and loads the WAV as an a2a job through the studio's restore path, so the
  render goes through the studio (its sidecars, labels, history).

API (JSON): GET /api/meta · GET /api/preset/{name} · GET /api/templates ·
GET /api/template/{slug} · POST /api/template (save) · POST /api/resolve (events for the
canvas) · POST /api/render · GET /api/audio?path= · GET /api/progression?seed&scale ·
POST /api/send · GET /api/studio (is the studio up?).
"""
from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import compose  # noqa: E402
import presets  # noqa: E402
import render  # noqa: E402

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

OUT = render.OUT
KEEP_RENDERS = 8
STUDIO = os.environ.get("SA3_STUDIO_URL", "http://127.0.0.1:7860")
_lock = threading.Lock()          # one render at a time (numpy is already multi-threaded)
app = FastAPI(title="SA3 Arranger")


def _index() -> dict:
    try:
        return json.loads((OUT / "index.json").read_text())
    except Exception:
        return {}


def _prune(slug: str) -> None:
    """Keep the newest KEEP_RENDERS renders of a template, and every one that was sent."""
    d = OUT / slug / "renders"
    if not d.is_dir():
        return
    sent = {Path(r["wav"]).name for r in _index().values() if r.get("sent")}
    wavs = sorted(d.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
    for w in wavs[KEEP_RENDERS:]:
        if w.name in sent:
            continue
        for p in (w, w.with_suffix(".json"), w.with_suffix(".controls.npz")):
            p.unlink(missing_ok=True)


@app.get("/api/meta")
def meta():
    return {"lanes": compose.LANES, "gens": compose.GENS, "sounds": compose.SOUNDS,
            "scales": list(compose.SCALES), "keys": compose.NOTE_NAMES,
            "presets": [{"id": k, "name": f()["name"], "doc": (f.__doc__ or "").strip()}
                        for k, f in presets.PRESETS.items()],
            "lane_defaults": render.LANE_DEFAULTS, "studio": STUDIO}


@app.get("/api/preset/{name}")
def preset(name: str):
    f = presets.PRESETS.get(name)
    return render.normalize(f()) if f else JSONResponse({"error": "unknown preset"}, 404)


@app.get("/api/templates")
def templates():
    out = []
    for p in sorted(OUT.glob("*/template.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            t = json.loads(p.read_text())
        except Exception:
            continue
        out.append({"slug": p.parent.name, "name": t.get("name", p.parent.name), "mtime": p.stat().st_mtime})
    return out


@app.get("/api/template/{slug}")
def template_get(slug: str):
    p = OUT / render.slug(slug) / "template.json"
    return render.normalize(json.loads(p.read_text())) if p.is_file() else JSONResponse({"error": "none"}, 404)


@app.post("/api/template")
async def template_post(req: Request):
    t = render.normalize(await req.json())
    sl = render.slug(t["name"])
    p = OUT / sl / "template.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(t, indent=1))
    tmp.replace(p)
    return {"slug": sl, "path": str(p)}


@app.post("/api/resolve")
async def resolve(req: Request):
    t = render.normalize(await req.json())
    r = compose.resolve(t)
    r["hash"] = render.template_hash(t)
    r["seconds_rendered"] = render.seconds_of(t)
    w = render.render_path(t)
    r["rendered"] = str(w) if w.is_file() else None
    return r


def _render(t: dict, stems: bool = False) -> dict:
    with _lock:
        r = render.render(t, stems=stems)
    _prune(render.slug(render.normalize(t)["name"]))
    return r


@app.post("/api/render")
def render_post(body: dict):
    t = body.get("template") or body
    r = _render(t, stems=bool(body.get("stems")))
    return r | {"url": "/api/audio?path=" + urllib.request.quote(r["wav"]) + "&h=" + r["hash"]}


@app.get("/api/audio")
def audio(path: str):
    p = Path(path).resolve()
    if OUT.resolve() not in p.parents or not p.is_file():
        return JSONResponse({"error": "not an arranger render"}, 404)
    return FileResponse(p, media_type="audio/wav")


@app.get("/api/progression")
def progression(seed: int, scale: str = "minor"):
    return {"degrees": compose.progression(seed, scale)}


@app.post("/api/send")
def send(body: dict):
    """Render (cached) and post the job to the studio's inbox.
    body: {template, prompt, sigmas: [σ…] (one = a single a2a load, several = σ ladder,
    rendered in a row), seed: int|null (null: the studio's seed; a ladder always gets one
    fixed seed so only σ changes)}."""
    t = render.normalize(body["template"])
    r = _render(t)
    sigmas = [float(s) for s in (body.get("sigmas") or [t["sa3"].get("init_noise", 0.8)])]
    seed = body.get("seed")
    if seed in ("", None) and len(sigmas) > 1:
        seed = random.randrange(2 ** 31)
    sid = time.strftime("%Y%m%d-%H%M%S") + f"-{r['hash'][:6]}"
    rec = {"id": sid, "created": time.time(), "wav": r["wav"], "json": r["json"], "seconds": r["seconds"],
           "sha256": r["sha256"], "hash": r["hash"], "slug": render.slug(t["name"]), "name": t["name"],
           "prompt": body.get("prompt") or t.get("prompt") or "", "sigmas": sigmas,
           "seed": None if seed in ("", None) else int(seed), "generate": len(sigmas) > 1 or bool(body.get("generate"))}
    d = OUT / "sends"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.json"
    p.write_text(json.dumps(rec, indent=1))
    idx = _index()
    if r["sha256"] in idx:
        idx[r["sha256"]]["sent"] = True
        render.index_add(r["sha256"], idx[r["sha256"]])
    inbox = OUT / "inbox.json"
    tmp = inbox.with_suffix(".tmp")
    tmp.write_text(json.dumps({"file": str(p), "id": sid, "created": rec["created"]}))
    tmp.replace(inbox)
    return rec | {"file": str(p)}


@app.get("/api/studio")
def studio():
    try:
        with urllib.request.urlopen(STUDIO + "/sa3/boot", timeout=1.5) as r:
            return {"up": r.status == 200, "inbox_pending": (OUT / "inbox.json").is_file()}
    except Exception:
        return {"up": False, "inbox_pending": (OUT / "inbox.json").is_file()}


@app.middleware("http")
async def no_cache(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.endswith((".js", ".css", ".html")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


app.mount("/", StaticFiles(directory=HERE / "static", html=True), name="static")


def main() -> None:
    import argparse
    import uvicorn
    ap = argparse.ArgumentParser(description="SA3 Arranger server")
    ap.add_argument("--port", type=int, default=7861)
    a = ap.parse_args()
    print(f"SA3 Arranger → http://127.0.0.1:{a.port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
