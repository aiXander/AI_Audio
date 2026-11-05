"""The render library: every studio render becomes a labelled dataset row on disk.

Each render in `outputs/sa3-gradio/` has a sidecar `<audio file>.json` (written by
persist.py). Together the sidecars ARE the raw dataset — one row per audio file:

    audio        the file next to the sidecar
    "gen"        everything needed to regenerate it bit-for-bit: models + resolved
                 checkpoint files, prompt, negative prompt, seed, seconds, steps,
                 cfg, apg, sigma_max, mode, LoRAs (path, sha256, strength, step
                 range), a2a / inpaint input audio (archived copy), code commit,
                 plus the measured timings
    "label"      {"score": 0-10 | null, "tags": [str], "updated": iso time}
    "analysis"   measured, not labelled: {"tempo": {bpm, conf, prompt_bpm, tier
                 green|yellow|orange|none|nobeat, fold, err}} (tempo.py), filled
                 in by a background thread the first time the UI asks for a label

Renders made before this module existed have no "gen" block, only upstream's
entry fields (prompt, seed, dit, cfg, sigma); their label still works.

This module also serves the browser side, as plain routes added to gradio's
FastAPI app (so a score click never waits behind a running render in the queue):

    GET  /sa3/label?name=<file>     → {"score", "tags", "bpm", "prompt"}  (bpm may be {"pending"})
    POST /sa3/label  {name, score?, tags?}   writes the sidecar's "label" at once
    GET  /sa3/progress              → live progress of the running render
    GET  /sa3/boot                  → server boot id (the page reloads when it changes)
    GET  /sa3/library               → every render as a filterable row (Library modal)
    GET  /sa3/spec?name=  /sa3/audio?name=   its spectrogram jpeg / audio file

and the progress estimate: a render's expected duration is predicted from past
renders' measured timings (sampling cost per step × latent frame, doubled with
CFG ≠ 1; decode cost per latent frame; text encoder), then corrected live by the
sampler's real step count.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import queue
import re
import shutil
import statistics
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import Request  # module level: route annotations are resolved from globals
from fastapi.responses import FileResponse, JSONResponse, Response

AUDIO_EXT = {".mp3", ".wav"}
_lock = threading.Lock()
OUT_DIR: Path | None = None        # set by init()
_up = None                         # upstream sa3_gradio module
BOOT_ID = f"{os.getpid()}-{time.time():.0f}"   # changes on every server (re)start


def init(up) -> None:
    global OUT_DIR, _up
    _up = up
    OUT_DIR = Path(up.OUTPUT_DIR).resolve()
    threading.Thread(target=_tempo_worker, name="sa3-tempo", daemon=True).start()


# ── sidecar I/O ────────────────────────────────────────────────────────────
def _audio_path(name: str) -> Path | None:
    """A render's audio path from its bare filename, refusing anything outside OUT_DIR."""
    name = os.path.basename(name or "")
    p = OUT_DIR / name
    if not name or p.suffix.lower() not in AUDIO_EXT or not p.is_file():
        return None
    return p


def _sidecar(p: Path) -> Path:
    return p.with_name(p.name + ".json")


def read_label(name: str) -> dict | None:
    p = _audio_path(name)
    if p is None:
        return None
    try:
        d = json.loads(_sidecar(p).read_text())
    except Exception:
        d = {}
    lab = d.get("label") or {}
    return {"score": lab.get("score"), "tags": lab.get("tags") or [],
            "has_gen": "gen" in d, "bpm": _tempo_of(p, d),
            "prompt": (d.get("gen") or {}).get("prompt", d.get("prompt"))}


def restore_settings(name: str) -> dict | None:
    """The UI settings that produced a render, for the studio's "use these
    settings" button. From the "gen" block when present; renders made before it
    existed only carry model, prompt, negative prompt, seed, CFG and sigma_max
    (plus the clip length, read from the audio), so steps / LoRAs / APG are
    left as they are for those (keys absent)."""
    p = _audio_path(name)
    if p is None:
        return None
    try:
        d = json.loads(_sidecar(p).read_text())
    except Exception:
        d = {}
    g = d.get("gen")
    if g:
        def inp(x):
            rel = (x or {}).get("archived")
            return str(OUT_DIR / rel) if rel and (OUT_DIR / rel).is_file() else None
        return {k: g[k] for k in ("dit", "decoder", "prompt", "negative_prompt", "seed", "seconds",
                                  "steps", "cfg", "apg", "sigma_max", "mode", "inpaint_range_sec")
                if k in g} | {"loras": g.get("loras") or [], "a2a_audio": inp(g.get("a2a_audio")),
                             "inpaint_audio": inp(g.get("inpaint_audio")), "full": True}
    out = {"full": False}
    for src, dst in (("dit", "dit"), ("prompt", "prompt"), ("neg", "negative_prompt"), ("seed", "seed"),
                     ("cfg", "cfg"), ("smx", "sigma_max"), ("mode", "mode")):
        if d.get(src) is not None:
            out[dst] = d[src]
    try:
        dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                              "csv=p=0", str(p)], capture_output=True, text=True, timeout=10).stdout
        out["seconds"] = round(float(dur))
    except Exception:
        pass
    return out


# ── auto BPM tag: measured tempo vs the prompt's (tempo.py) ────────────────
TEMPO_V = 1                        # bump to re-analyse every render
_tq: "queue.Queue[Path]" = queue.Queue()
_tq_seen: set[str] = set()


def _tempo_of(p: Path, d: dict) -> dict:
    """The sidecar's tempo analysis, or {"pending": True} after queueing it."""
    t = (d.get("analysis") or {}).get("tempo")
    if t and t.get("v") == TEMPO_V:
        return t
    if p.name not in _tq_seen:
        _tq_seen.add(p.name)
        _tq.put(p)
    return {"pending": True}


def _tempo_worker() -> None:
    import tempo
    while True:
        p = _tq.get()
        try:
            sc = _sidecar(p)
            try:
                d = json.loads(sc.read_text())
            except Exception:
                d = {}
            want = tempo.prompt_bpm((d.get("gen") or {}).get("prompt") or d.get("prompt"))
            r = tempo.estimate(str(p), want)
            t = {"v": TEMPO_V, **r, "prompt_bpm": want, **tempo.match(r["bpm"], want, r["conf"])}
        except Exception as e:
            t = {"v": TEMPO_V, "error": str(e)[:200]}
        with _lock:
            try:
                d = json.loads(sc.read_text())
            except Exception:
                d = {"name": p.name}
            d["analysis"] = {**(d.get("analysis") or {}), "tempo": t}
            tmp = sc.with_name(sc.name + ".tmp")
            tmp.write_text(json.dumps(d))
            tmp.replace(sc)


def write_label(name: str, score=..., tags=...) -> dict | None:
    """Update score and/or tags in the sidecar (read-modify-write, atomic rename)."""
    p = _audio_path(name)
    if p is None:
        return None
    sc = _sidecar(p)
    with _lock:
        try:
            d = json.loads(sc.read_text())
        except Exception:
            d = {"name": p.name}
        lab = dict(d.get("label") or {})
        if score is not ...:
            lab["score"] = None if score is None else max(0, min(10, int(score)))
        if tags is not ...:
            seen, clean = set(), []
            for t in tags or []:
                t = re.sub(r"\s+", " ", str(t)).strip()[:60]
                if t and t.lower() not in seen:
                    seen.add(t.lower())
                    clean.append(t)
            lab["tags"] = clean
        lab["updated"] = datetime.now().isoformat(timespec="seconds")
        d["label"] = lab
        tmp = sc.with_name(sc.name + ".tmp")
        tmp.write_text(json.dumps(d))
        tmp.replace(sc)
    return {"score": lab.get("score"), "tags": lab.get("tags") or []}


# ── regeneration metadata ──────────────────────────────────────────────────
_code_rev: dict | None = None


def _code() -> dict:
    """Commit of the stable-audio-3 fork + AI_Audio, and whether either tree is dirty."""
    global _code_rev
    if _code_rev is None:
        rev = {}
        mlx = Path(_up.REPO)
        for key, d in (("stable-audio-3", mlx), ("AI_Audio", mlx.parents[2])):
            try:
                sha = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"],
                                     capture_output=True, text=True, timeout=5).stdout.strip()
                dirty = bool(subprocess.run(["git", "-C", str(d), "status", "--porcelain",
                                             "--untracked-files=no"],
                                            capture_output=True, text=True, timeout=5).stdout.strip())
                rev[key] = {"commit": sha, "dirty": dirty}
            except Exception:
                pass
        _code_rev = rev
    return _code_rev


def _file_id(path: str | None) -> dict | None:
    """Resolved weight file (the HF-cache link target pins the snapshot)."""
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = Path(_up.REPO) / p
    return {"path": str(path), "resolved": os.path.realpath(p)}


_sha_cache: dict[tuple, str] = {}


def _sha256(path: str) -> str | None:
    try:
        st = os.stat(path)
        k = (path, st.st_size, st.st_mtime)
        if k not in _sha_cache:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            _sha_cache[k] = h.hexdigest()
        return _sha_cache[k]
    except Exception:
        return None


def _archive_input(path: str | None) -> dict | None:
    """Copy an a2a / inpaint input (a gradio temp file that will vanish) into
    OUT_DIR/inputs/<sha256-prefix><ext>, content-addressed so repeats dedupe."""
    if not path:
        return None
    sha = _sha256(path)
    if sha is None:
        return {"original": os.path.basename(path), "archived": None}
    dst = OUT_DIR / "inputs" / f"{sha[:16]}{Path(path).suffix.lower()}"
    try:
        if not dst.exists():
            dst.parent.mkdir(exist_ok=True)
            shutil.copy2(path, dst)
    except Exception:
        return {"original": os.path.basename(path), "sha256": sha, "archived": None}
    return {"original": os.path.basename(path), "sha256": sha,
            "archived": str(dst.relative_to(OUT_DIR))}


def gen_record(bound: inspect.BoundArguments, timings: dict | None) -> dict:
    """Build the "gen" block from run_generation's bound arguments."""
    a = bound.arguments
    dit = a["dit_name"]
    dec = a["decoder_name"]
    a2a, inp, rng = a.get("a2a_audio_path"), a.get("inpaint_audio_path"), a.get("inpaint_range_sec")
    mode = ("a2a+inpaint" if (a2a and rng) else "inpaint" if rng else
            "audio-to-audio" if a2a else "text-to-audio")
    loras = []
    for s in a.get("lora_specs") or []:
        loras.append({"path": s["path"], "sha256": _sha256(s["path"]),
                      "strength": s["strength"], "steps": list(s["steps"]) if s["steps"] else None})
    t = timings or {}
    return {
        "schema": 1,
        "created": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "dit": dit,
        "dit_ckpt": _file_id(_up.DIT_CHOICES.get(dit, {}).get("ckpt")),
        "decoder": dec,
        "decoder_ckpt": _file_id((_up.DECODER_CHOICES.get(dec) or (None,) * 4)[3]),
        "text_encoder_ckpt": _file_id(getattr(_up, "T5GEMMA_NPZ_REL", None)),
        "prompt": a["prompt"],
        "negative_prompt": a["negative_prompt"],
        "seed": int(a["seed"]),
        "seconds": float(a["seconds"]),
        "steps": int(a["steps"]),
        "cfg": float(a["cfg"]),
        "apg": float(a["apg"]),
        "sigma_max": float(a["sigma_max"]),
        "sampler": "pingpong",
        "a2a_audio": _archive_input(a2a),
        "inpaint_audio": _archive_input(inp),
        "inpaint_range_sec": list(rng) if rng else None,
        "loras": loras,
        "sample_rate": getattr(_up, "SAMPLE_RATE", 44100),
        "code": _code(),
        "timings_ms": {k: round(float(t[k]), 1) for k in
                       ("t5_ms", "sample_ms", "decode_ms", "inference_ms", "dit_load_ms", "enc_ms")
                       if k in t} | ({"T_lat": int(t["T_lat"])} if "T_lat" in t else {}),
    }


# ── speed model + live progress ────────────────────────────────────────────
_TIMING_RE = re.compile(r"Inference</b>:\s*(\d+)\s*ms.*?t5=(\d+)\s*·\s*sample=(\d+)\s*·\s*decode=(\d+)"
                        r".*?seq_len</b>:\s*(\d+)", re.S)
_speed: dict = {"mtime": None, "rows": []}


def _speed_rows() -> list[dict]:
    """Past renders' per-unit costs, newest first (re-scanned when OUT_DIR changes)."""
    try:
        m = OUT_DIR.stat().st_mtime
    except Exception:
        return []
    if _speed["mtime"] == m:
        return _speed["rows"]
    rows = []
    for sc in OUT_DIR.glob("*.json"):
        try:
            d = json.loads(sc.read_text())
        except Exception:
            continue
        g = d.get("gen") or {}
        tm = g.get("timings_ms") or {}
        if tm.get("T_lat"):
            t5, smp, dec, T = tm.get("t5_ms", 0), tm.get("sample_ms", 0), tm.get("decode_ms", 0), tm["T_lat"]
            steps, cfg = g.get("steps", 8), g.get("cfg", 1.0)
        else:
            mm = _TIMING_RE.search(d.get("timing") or "")
            if not mm:
                continue
            _, t5, smp, dec, T = map(int, mm.groups())
            steps, cfg = 8, d.get("cfg", 1.0)      # pre-library renders: steps unknown, studio default
        passes = 2 if cfg != 1.0 else 1
        rows.append({"ts": d.get("ts", 0), "dit": g.get("dit") or d.get("dit") or "",
                     "T": T, "t5": t5, "smp_k": smp / max(1, steps * T * passes),
                     "dec_k": dec / max(1, T)})
    rows.sort(key=lambda r: -r["ts"])
    _speed.update(mtime=m, rows=rows[:60])
    return _speed["rows"]


def estimate_ms(dit: str, seconds: float, steps: int, cfg: float) -> dict:
    """Predicted {t5, sample, decode, total} ms for a render; defaults if no history."""
    sr = getattr(_up, "SAMPLE_RATE", 44100)
    spl = getattr(_up, "SAMPLES_PER_LATENT", 4096)
    T = max(1, -(-int(seconds * sr) // spl))
    rows = _speed_rows()
    same = [r for r in rows if r["dit"] == dit] or rows
    near = [r for r in same if 0.6 <= r["T"] / T <= 1.6] or same     # cost per frame drifts with length
    pick = near[:12]
    if pick:
        smp_k = statistics.median(r["smp_k"] for r in pick)
        dec_k = statistics.median(r["dec_k"] for r in pick)
        t5 = statistics.median(r["t5"] for r in pick)
    else:   # M2 MacBook Pro, medium + same-l, measured 2026-11-05
        smp_k, dec_k, t5 = 1.8, 10.3, 250.0
    smp = smp_k * steps * T * (2 if cfg != 1.0 else 1)
    dec = dec_k * T
    return {"t5": t5, "sample": smp, "decode": dec, "total": t5 + smp + dec, "n": len(pick)}


_prog: dict = {"active": False}


def progress_start(bound: inspect.BoundArguments) -> None:
    a = bound.arguments
    est = estimate_ms(a["dit_name"], float(a["seconds"]), int(a["steps"]), float(a["cfg"]))
    _prog.clear()
    _prog.update(active=True, t0=time.time(), phase="text", step=0, steps=int(a["steps"]),
                 est=est, step_t=None)


def progress_step(i: int) -> None:
    if _prog.get("active"):
        if i == 0:
            _prog["sample_t0"] = time.time()
        _prog.update(phase="sampling", step=i, step_t=time.time())


def progress_phase(phase: str) -> None:
    if _prog.get("active"):
        _prog["phase"] = phase
        if phase == "decode":
            _prog["dec_t0"] = time.time()


def progress_end() -> None:
    _prog["active"] = False


def progress() -> dict:
    """Fraction done + seconds left, blending the estimate with real step progress."""
    if not _prog.get("active"):
        return {"active": False}
    now = time.time()
    est = _prog["est"]
    el = (now - _prog["t0"]) * 1000
    t5, smp, dec = est["t5"], est["sample"], est["decode"]
    ph = _prog["phase"]
    if ph == "sampling" and _prog.get("sample_t0"):
        n = max(1, _prog["steps"])
        done = _prog["step"]                       # steps fully finished before this one
        s_el = (now - _prog["sample_t0"]) * 1000
        per = s_el / done if done else smp / n     # measured step time once we have one
        smp_left = max(0.0, per * n - s_el)
        left = smp_left + dec
    elif ph == "decode" and _prog.get("dec_t0"):
        left = max(0.0, dec - (now - _prog["dec_t0"]) * 1000)
    else:
        left = max(0.0, t5 - el) + smp + dec
    total = el + left
    frac = min(0.99, el / total) if total > 0 else 0.0
    return {"active": True, "frac": round(frac, 4), "elapsed_s": round(el / 1000, 1),
            "left_s": round(left / 1000, 1), "phase": ph,
            "step": _prog.get("step", 0), "steps": _prog.get("steps"), "history_n": est["n"]}


# ── library browser: every render as one filterable row (the studio's Library modal) ──
_rows: dict[str, tuple[float, dict]] = {}     # name -> (sidecar mtime, row)


def library_rows() -> list[dict]:
    """All renders in OUT_DIR, newest first: the fields the Library modal filters
    and shows. Cached per sidecar mtime (sidecars carry a ~400 KB spectrogram)."""
    rows = []
    for p in OUT_DIR.iterdir():
        if p.suffix.lower() not in AUDIO_EXT or not p.is_file():
            continue
        sc = _sidecar(p)
        try:
            mt = sc.stat().st_mtime
        except OSError:
            mt = -1.0
        hit = _rows.get(p.name)
        if hit and hit[0] == mt:
            rows.append(hit[1])
            continue
        try:
            d = json.loads(sc.read_text())
        except Exception:
            d = {}
        g, lab = d.get("gen") or {}, d.get("label") or {}
        row = {"name": p.name, "ts": d.get("ts") or p.stat().st_mtime,
               "prompt": g.get("prompt", d.get("prompt")) or "",
               "dit": g.get("dit", d.get("dit")), "seed": g.get("seed", d.get("seed")),
               "seconds": g.get("seconds"), "steps": g.get("steps"), "cfg": g.get("cfg", d.get("cfg")),
               "mode": g.get("mode", d.get("mode")), "lora": bool(g.get("loras") or d.get("lora")),
               "score": lab.get("score"), "tags": lab.get("tags") or [],
               "has_spec": bool(d.get("spec_b64")), "bpm": _tempo_of(p, d)}
        if not row["bpm"].get("pending"):
            _rows[p.name] = (mt, row)
        rows.append(row)
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows


def spec_jpeg(name: str) -> bytes | None:
    p = _audio_path(name)
    if p is None:
        return None
    try:
        import base64
        b = json.loads(_sidecar(p).read_text()).get("spec_b64")
        return base64.b64decode(b) if b else None
    except Exception:
        return None


# ── routes ─────────────────────────────────────────────────────────────────
def install_routes() -> None:
    """Add /sa3/* routes to every gradio app gradio builds (ahead of its catch-alls)."""
    from gradio import routes

    async def label_get(name: str):
        r = read_label(name)
        return JSONResponse(r if r is not None else {"error": "unknown file"},
                            status_code=200 if r is not None else 404)

    async def label_post(request: Request):
        d = await request.json()
        kw = {}
        if "score" in d:
            kw["score"] = d["score"]
        if "tags" in d:
            kw["tags"] = d["tags"]
        r = write_label(d.get("name", ""), **kw)
        return JSONResponse(r if r is not None else {"error": "unknown file"},
                            status_code=200 if r is not None else 404)

    async def progress_get():
        return JSONResponse(progress())

    async def boot_get():            # studio.js reloads the page when this changes
        return JSONResponse({"id": BOOT_ID})

    def library_get():               # sync: runs in the threadpool (disk reads)
        return JSONResponse(library_rows())

    def spec_get(name: str):
        b = spec_jpeg(name)
        return (Response(b, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})
                if b else JSONResponse({"error": "no spectrogram"}, status_code=404))

    def audio_get(name: str):
        p = _audio_path(name)
        return FileResponse(p) if p else JSONResponse({"error": "unknown file"}, status_code=404)

    orig = routes.App.create_app

    def create_app(*a, **kw):
        app = orig(*a, **kw)
        app.add_api_route("/sa3/label", label_get, methods=["GET"])
        app.add_api_route("/sa3/label", label_post, methods=["POST"])
        app.add_api_route("/sa3/progress", progress_get, methods=["GET"])
        app.add_api_route("/sa3/library", library_get, methods=["GET"])
        app.add_api_route("/sa3/boot", boot_get, methods=["GET"])
        app.add_api_route("/sa3/spec", spec_get, methods=["GET"])
        app.add_api_route("/sa3/audio", audio_get, methods=["GET"])
        ours = [r for r in app.router.routes if getattr(r, "path", "").startswith("/sa3/")]
        app.router.routes[:] = ours + [r for r in app.router.routes if r not in ours]
        return app
    routes.App.create_app = staticmethod(create_app)
