"""The studio's side of the SA3 Arranger (sa3lab/arranger/, its own server on :7861).

- **Arranger button** (arranger_link.js, top right): POST /sa3/arranger/start launches the
  arranger server if it isn't answering (detached, log in outputs/arranger/server.log), then
  the page opens it in a new window.
- **Send to SA3**: the arranger renders its WAV and writes `outputs/arranger/sends/<id>.json`
  (wav, seconds, prompt, σ list, seed, template hash) plus `outputs/arranger/inbox.json`
  pointing at it. The studio page polls GET /sa3/arranger/inbox, which hands the record out
  once (and drops records older than INBOX_MAX_AGE, so a studio opened later doesn't fire a
  stale send). Then, like Extend (extend.py), it runs the restore-settings path with
  `__arranger__:<k>:<send file>`; restore() calls `settings()` here, which turns the record's
  k-th σ into settings: mode audio-to-audio, a2a audio = the WAV, Length = its seconds, init
  noise = σ, the prompt, and the seed when the record has one. Model, codec default, LoRAs,
  steps and CFG stay as they are in the studio. For "Send + Generate" and the σ ladder,
  arranger_link.js presses Generate after each restore, one σ at a time.
- **Sidecar**: gradio hands the studio a temp copy of the a2a input, so the link back to the
  template is the WAV's sha256: `install()` wraps library.gen_record and, when the a2a input
  matches an entry of outputs/arranger/index.json (written by arranger/render.py), stamps
  `gen.arranger` = {name, slug, hash, wav, json (the frozen template + resolved events)}.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PREFIX = "__arranger__:"
ROOT = (Path(__file__).resolve().parents[1] / "outputs" / "arranger").resolve()
SERVER = Path(__file__).resolve().parent / "arranger" / "server.py"
URL = "http://127.0.0.1:7861"
INBOX_MAX_AGE = 120.0


def parse(name: str):
    """`__arranger__:<k>:<send file>` → (send file, k), else None."""
    if not name or not name.startswith(PREFIX):
        return None
    k, path = name[len(PREFIX):].split(":", 1)
    return path, int(k)


def settings(path: str, k: int) -> tuple[dict, str]:
    """A send record's k-th σ → restore settings (see the module docstring), plus a note."""
    p = Path(path).resolve()
    if ROOT not in p.parents or not p.is_file():
        raise ValueError(f"no arranger send at {path}")
    rec = json.loads(p.read_text())
    if not Path(rec["wav"]).is_file():
        raise ValueError(f"the arranger render is gone: {rec['wav']}")
    sig = float(rec["sigmas"][k])
    r = {"mode": "audio-to-audio", "a2a_audio": rec["wav"], "inpaint_audio": None, "seconds": float(rec["seconds"]),
         "sigma_max": sig, "prompt": rec.get("prompt") or "", "full": True}
    if rec.get("seed") is not None:
        r["seed"] = int(rec["seed"])
    n = len(rec["sigmas"])
    note = (f"Arranger: {rec.get('name', 'sketch')} · {rec['seconds']:g} s · σ {sig:.2f}"
            + (f" (ladder {k + 1}/{n}, seed {rec['seed']})" if n > 1 else "")
            + ("" if rec.get("generate") else " · press Generate"))
    return r, note


def _index() -> dict:
    try:
        return json.loads((ROOT / "index.json").read_text())
    except Exception:
        return {}


def install(library) -> None:
    orig = library.gen_record

    def gen_record(*a, **kw):
        g = orig(*a, **kw)
        sha = (g.get("a2a_audio") or {}).get("sha256")
        if sha:
            hit = _index().get(sha)
            if hit:
                g["arranger"] = {k: hit.get(k) for k in ("name", "slug", "hash", "wav", "json")}
        return g
    library.gen_record = gen_record


def _up() -> bool:
    try:
        with urllib.request.urlopen(URL + "/api/meta", timeout=1.0) as r:
            return r.status == 200
    except Exception:
        return False


def install_routes() -> None:
    from fastapi.responses import JSONResponse
    from gradio import routes

    def inbox():
        """Hand out a pending send once (fresh ones only)."""
        p = ROOT / "inbox.json"
        try:
            d = json.loads(p.read_text())
            p.unlink()
        except Exception:
            return JSONResponse({})
        if time.time() - float(d.get("created", 0)) > INBOX_MAX_AGE:
            return JSONResponse({"stale": d.get("id")})
        try:
            rec = json.loads(Path(d["file"]).read_text())
        except Exception:
            return JSONResponse({})
        return JSONResponse({"file": d["file"], "id": rec["id"], "n": len(rec["sigmas"]),
                             "generate": bool(rec.get("generate")), "name": rec.get("name")})

    def start():
        """Launch the arranger server if it isn't up (detached; it outlives the studio)."""
        if _up():
            return JSONResponse({"up": True, "started": False, "url": URL})
        ROOT.mkdir(parents=True, exist_ok=True)
        log = open(ROOT / "server.log", "a")
        subprocess.Popen([sys.executable, str(SERVER)], stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True, cwd=str(SERVER.parent))
        for _ in range(40):
            time.sleep(0.2)
            if _up():
                return JSONResponse({"up": True, "started": True, "url": URL})
        return JSONResponse({"up": False, "started": True, "url": URL, "log": str(ROOT / "server.log")})

    orig = routes.App.create_app

    def create_app(*a, **kw):
        app = orig(*a, **kw)
        app.add_api_route("/sa3/arranger/inbox", inbox, methods=["GET"])
        app.add_api_route("/sa3/arranger/start", start, methods=["POST"])
        ours = [r for r in app.router.routes if getattr(r, "path", "").startswith("/sa3/")]
        app.router.routes[:] = ours + [r for r in app.router.routes if r not in ours]
        return app
    routes.App.create_app = staticmethod(create_app)
