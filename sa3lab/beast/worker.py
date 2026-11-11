"""The render worker on the RTX 3090 box: the studio's GPU work, over HTTP, on the same MLX code.

The Mac's studio (remote.py) sends each queued job here when its Render-on chip says Beast; this
process runs exactly the studio's render path (`engine.install`: noise canvas, strength-curve
sampler, condmix, anarchy conditioning, stop checks) on MLX's CUDA backend and streams the raw
decoder output back. It knows nothing about output folders, sidecars or gradio. Plan and the why:
docs/TODO/sa3-beast-render.md (until it moves to docs/reference/).

Run (systemd --user unit `sa3-worker.service`, installed by `scripts/beast.sh install`):

    ~/SSD2TB/sa3-worker/.venv/bin/python ~/SSD2TB/sa3-worker/AI_Audio/sa3lab/beast/worker.py

| Route | Does |
|---|---|
| `GET /health` | `{ok, busy, busy_reason, rendering, code_hash, host, device, mlx_version, loaded, vram_free_gb, other_gpu_procs, other_gpu_gb, archive_ok, archive_free_gb}` |
| `HEAD /blobs/{sha}?ext=` | 200 if `<sha>.<ext>` is cached, else 404 |
| `PUT /blobs/{sha}?ext=` | body = the file; stored as `<blobs>/<sha>.<ext>` after the sha256 checks out (idempotent) |
| `POST /render` | body = job json (below). Response: NDJSON events `{"ev":"phase","name"}`, `{"ev":"step","i","n"}`, `{"ev":"tick"}` (keep-alive), then one `{"ev":"done","timings","extras","audio","arrays"}` or `{"ev":"error","type","msg","fatal"}`. 409 while another job runs, 503 when the GPU is busy |
| `GET /audio/{job}` | the render as float32 WAV (soundfile FLOAT): the decoder's raw output, unlimited, never integer PCM |
| `GET /extras/{job}` | the capture arrays (npz, keys `<capture>/<name>`), when `done` said `arrays: true` |
| `POST /cancel/{job}` | the next stop check raises; the stream ends `{"ev":"error","type":"Stopped"}` |

**The job** (built by remote.build_job): `{"job": <32 hex>, "code_hash", "args": run_generation's
keyword arguments, "seg": the segments box format ({"strength", "mask", "kept", "cond"}) or null,
"meta": {"outdir", "mac_host", "submitted"}}`. Any string anywhere in it may hold
`blob:<sha256>.<ext>` references (input audio, LoRAs, anchor tensors, `anarchy: @…` files); they are
replaced by the local blob path before the run, and local blob paths in what comes back are turned
back into `blob:` references (remote.py maps those to the Mac's own paths).

Behaviour:
- **One job at a time** (409 otherwise); every MLX call runs on upstream's MLX owner thread.
- **GPU sharing:** another process holding more than SA3_OTHER_GPU_GB (8) of VRAM (a LoRA training
  run, ComfyUI) makes `/health` say busy and `/render` answer 503, so the Mac renders locally; our own
  models are dropped then too, and after SA3_IDLE_RELEASE_S (900) idle seconds, so the other job gets
  the GPU. The next render pays the reload. `<sa3-worker>/fake_busy` holding a number fakes that
  many GB of other use (acceptance test 12).
- **CUDA errors** (out of memory, device errors) come back with `fatal: true`: the Mac re-renders
  those locally, while a render error (bad spec, bad LoRA) is shown like a local one.
- **Raw archive:** every finished render is written to `$SA3_ARCHIVE/raw/<outdir>/<YYYY-MM-DD>/<job>.wav`
  (float32, as decoded) + `<job>.json` (the job, timings, captures) [+ `<job>.extras.npz`] before
  `done` is sent; the blobs it used are copied to `raw/_blobs/` so it stays reproducible. With the
  12 TB drive unmounted (`nofail`), the render goes to `<sa3-worker>/scratch/` instead and
  `archive_ok` is false. Never written to an unmounted mount point.
- **Log:** one line per job in `<sa3-worker>/worker.log`.

Bound to the box's Tailscale address only (SA3_WORKER_HOST); no auth beyond that. Every path it
writes is chosen here: blob names are `<sha256>.<ext>` with a validated ext, job ids are hex, the
outdir is reduced to safe path parts.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
AI_AUDIO = HERE.parents[1]                       # the deploy copy's AI_Audio/ (or a repo clone)
MLX = AI_AUDIO / "stable-audio-3" / "optimized" / "mlx"
os.environ.setdefault("HF_HUB_CACHE", str(AI_AUDIO / "models" / "hf-cache"))
sys.path[:0] = [str(MLX), str(MLX / "scripts"), str(AI_AUDIO / "sa3lab")]

HOME = Path.home()
WORKDIR = Path(os.environ.get("SA3_WORKDIR", AI_AUDIO.parent))   # ~/SSD2TB/sa3-worker
BIND = os.environ.get("SA3_WORKER_HOST", "100.117.34.17")
PORT = int(os.environ.get("SA3_WORKER_PORT", "7870"))
NAME = os.environ.get("SA3_WORKER_NAME", platform.node())
# under AI_Audio/: condsrc.tokens only loads conditioning files that live inside the AI_Audio tree
BLOBS = Path(os.environ.get("SA3_BLOBS", AI_AUDIO / "blobs")).expanduser()
ARCHIVE = Path(os.environ.get("SA3_ARCHIVE", HOME / "12TBHDD" / "AI_Audio")).expanduser()
ARCHIVE_MOUNT = Path(os.environ.get("SA3_ARCHIVE_MOUNT", HOME / "12TBHDD")).expanduser()
SCRATCH = WORKDIR / "scratch"
LOG = WORKDIR / "worker.log"
FAKE_BUSY = WORKDIR / "fake_busy"
IDLE_S = float(os.environ.get("SA3_IDLE_RELEASE_S", "900"))
OTHER_GPU_GB = float(os.environ.get("SA3_OTHER_GPU_GB", "8"))

_HEX = re.compile(r"[0-9a-f]{32}")
_SHA = re.compile(r"[0-9a-f]{64}")
_EXT = re.compile(r"[a-z0-9]{1,16}")
BLOB_REF = re.compile(r"blob:([0-9a-f]{64})\.([a-z0-9]{1,16})")

import numpy as np  # noqa: E402
from fastapi import Request  # noqa: E402  (module level: FastAPI resolves route annotations from globals)

import sa3_gradio as up  # noqa: E402  (upstream's render function, model caches, MLX owner thread)
import engine  # noqa: E402
import library  # noqa: E402
import persist  # noqa: E402


# ── the running job (one at a time) ────────────────────────────────────────
class _Job:
    def __init__(self, jid: str, steps: int):
        self.id, self.steps = jid, steps
        self.q: queue.Queue = queue.Queue()
        self.cancel = threading.Event()


_cur: dict = {"job": None}
_slot = threading.Lock()
_state = {"last_end": time.time(), "jobs": {}}       # jobs: id → {"wav", "extras"} of recent renders


class _WorkerHooks(engine.Hooks):
    def check_stop(self):
        j = _cur["job"]
        if j is not None and j.cancel.is_set():
            raise engine.Stopped("generation stopped")

    def progress_step(self, i):
        j = _cur["job"]
        if j is not None:
            j.q.put({"ev": "step", "i": int(i), "n": j.steps})

    def progress_phase(self, name):
        j = _cur["job"]
        if j is not None:
            j.q.put({"ev": "phase", "name": str(name)})


engine.install(up, library, persist, _WorkerHooks())
import segments  # noqa: E402  (after engine.install: its sampler hook is in place)

CODE_HASH = engine.code_hash(AI_AUDIO)


# ── GPU / archive state ────────────────────────────────────────────────────
_gpu = {"t": 0.0, "v": None}


def gpu() -> dict:
    """VRAM free + every other process on the GPU (cached 3 s)."""
    if time.time() - _gpu["t"] < 3 and _gpu["v"] is not None:
        return _gpu["v"]
    v = {"device": None, "vram_free_gb": None, "other_gpu_procs": [], "other_gpu_gb": 0.0}
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.free", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=5)
        name, free = [x.strip() for x in r.stdout.splitlines()[0].split(",")]
        v["device"], v["vram_free_gb"] = name.replace("NVIDIA GeForce ", ""), round(float(free) / 1024, 2)
        r = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
        for ln in r.stdout.splitlines():
            parts = [x.strip() for x in ln.split(",")]
            if len(parts) == 3 and parts[0].isdigit() and int(parts[0]) != os.getpid():
                v["other_gpu_procs"].append({"pid": int(parts[0]), "name": Path(parts[1]).name if "/" not in parts[1]
                                             else "/".join(Path(parts[1]).parts[-4:]), "gb": round(float(parts[2]) / 1024, 2)})
    except Exception as e:  # noqa: BLE001
        v["error"] = f"nvidia-smi: {e}"
    try:
        fake = float(FAKE_BUSY.read_text().strip() or 0)
        v["other_gpu_procs"].append({"pid": 0, "name": "fake_busy", "gb": fake})
    except (OSError, ValueError):
        pass
    v["other_gpu_gb"] = round(sum(p["gb"] for p in v["other_gpu_procs"]), 2)
    _gpu.update(t=time.time(), v=v)
    return v


def archive_ok() -> bool:
    """The 12 TB drive is mounted (nofail: unmounted, the mount point is an empty dir on /home)."""
    try:
        return os.path.ismount(ARCHIVE_MOUNT) and (ARCHIVE.mkdir(parents=True, exist_ok=True) or os.access(ARCHIVE, os.W_OK))
    except OSError:
        return False


def loaded() -> list[str]:
    out = [f"dit:{k[0]}@{k[1]}" for k in list(up._dit_cache)] + [f"decoder:{k}" for k in list(up._dec_cache)]
    out += [f"encoder:{k}" for k in list(up._enc_cache)] + (["t5gemma"] if up._t5 is not None else [])
    return out


def busy_reason() -> str | None:
    g = gpu()
    if g["other_gpu_gb"] > OTHER_GPU_GB:
        who = ", ".join(f"{p['name']} {p['gb']:.1f} GB" for p in g["other_gpu_procs"])
        return f"GPU in use by another process ({who})"
    return None


def _release(why: str) -> None:
    """Drop every cached model (runs on the MLX thread). The next render reloads them."""
    if not loaded():
        return
    up._dit_cache.clear()
    up._dit_lru.clear()
    up._dec_cache.clear()
    up._enc_cache.clear()
    up._cond_cache.clear()
    up._t5 = None
    up._free_to_pool()
    _log(f"released models ({why})")


def _housekeeping() -> None:
    while True:
        time.sleep(30)
        try:
            if _slot.locked():
                continue
            idle = time.time() - _state["last_end"]
            why = busy_reason()
            if loaded() and (idle > IDLE_S or why):
                up.mlx_call(_release, f"idle {idle:.0f} s" if idle > IDLE_S else why)
            for f in SCRATCH.glob("*") if SCRATCH.exists() else []:
                if time.time() - f.stat().st_mtime > 3600:      # delivered long ago; kept nowhere else on
                    f.unlink(missing_ok=True)                   # purpose only while the archive is offline
        except Exception as e:  # noqa: BLE001
            _log(f"housekeeping: {e}")


def _log(line: str) -> None:
    msg = f"{datetime.datetime.now().isoformat(timespec='seconds')} {line}"
    print(msg, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(msg + "\n")
    except OSError:
        pass


# ── blobs ──────────────────────────────────────────────────────────────────
def blob_path(sha: str, ext: str) -> Path:
    if not _SHA.fullmatch(sha) or not _EXT.fullmatch(ext):
        raise ValueError("bad blob name")
    return BLOBS / f"{sha}.{ext}"


def resolve_blobs(x, used: set):
    """Every `blob:<sha>.<ext>` in strings anywhere in x → the local blob path (must exist)."""
    if isinstance(x, str):
        def sub(m):
            p = blob_path(m[1], m[2])
            if not p.is_file():
                raise FileNotFoundError(f"blob {m[1][:12]}….{m[2]} was never uploaded")
            used.add(p)
            return str(p)
        return BLOB_REF.sub(sub, x)
    if isinstance(x, list):
        return [resolve_blobs(v, used) for v in x]
    if isinstance(x, dict):
        return {k: resolve_blobs(v, used) for k, v in x.items()}
    return x


def unblob(x):
    """Local blob paths in what goes back to the Mac → `blob:` references."""
    root = re.escape(str(BLOBS) + os.sep)
    rx = re.compile(root + r"([0-9a-f]{64})\.([a-z0-9]{1,16})")
    if isinstance(x, str):
        return rx.sub(lambda m: f"blob:{m[1]}.{m[2]}", x)
    if isinstance(x, list):
        return [unblob(v) for v in x]
    if isinstance(x, dict):
        return {k: unblob(v) for k, v in x.items()}
    return x


def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, (str, int, float, bool)) or x is None:
        return x
    return str(x)


# ── one render ─────────────────────────────────────────────────────────────
def _args(job: dict, used: set) -> tuple[dict, dict | None]:
    args = resolve_blobs(dict(job["args"]), used)
    for s in args.get("lora_specs") or []:
        if isinstance(s.get("steps"), list):
            s["steps"] = tuple(s["steps"])                 # upstream's LoRA signature wants a tuple
    if isinstance(args.get("inpaint_range_sec"), list):
        args["inpaint_range_sec"] = tuple(args["inpaint_range_sec"])
    seg = resolve_blobs(job.get("seg"), used) if job.get("seg") else None
    return args, (segments.parse_plan(seg) if seg else None)


def _execute(j: _Job, job: dict, args: dict, plan: dict | None):
    """On the MLX owner thread: the studio's run() (studio.py _install_stop_hooks), then the captures."""
    _cur["job"] = j
    try:
        engine.HOOKS.check_stop()
        with segments.active(plan):
            audio, t = up.run_generation(**args)
        engine.HOOKS.check_stop()
        caps, arrays = {}, {}
        for name, (export, _) in engine.CAPTURES.items():
            try:
                js, arr = export({**job, "args": args, "_plan": plan})
            except Exception as e:  # noqa: BLE001  (a capture never fails a render; the sidecar just lacks it)
                _log(f"capture {name} export failed: {e}")
                continue
            if js is not None:
                caps[name] = unblob(_jsonable(js))
                arrays.update({f"{name}/{k}": v for k, v in (arr or {}).items()})
        return np.asarray(audio, np.float32), t, caps, arrays
    finally:
        _cur["job"] = None
        # MLX keeps freed buffers cached (a 120 s render peaks ~18 GB): hand them back after every job,
        # so an idle worker holds only its models (~7 GB) and a training run / ComfyUI can start
        up.mx.clear_cache()


def _safe_parts(rel) -> list[str]:
    parts = [p for p in str(rel or "").split("/") if p and p not in (".", "..")
             and re.fullmatch(r"[\w .,+()@=-]{1,80}", p)]
    return parts or ["_unknown"]


def _store(job: dict, audio, t: dict, caps: dict, arrays: dict, used: set) -> dict:
    """Raw archive copy (or the SSD scratch dir when the archive is offline). Returns the paths."""
    import soundfile as sf
    jid = job["job"]
    if archive_ok():
        d = ARCHIVE / "raw" / Path(*_safe_parts((job.get("meta") or {}).get("outdir"))) / datetime.date.today().isoformat()
    else:
        d = SCRATCH
    d.mkdir(parents=True, exist_ok=True)
    wav = d / f"{jid}.wav"
    sf.write(wav, audio.T, up.SAMPLE_RATE, subtype="FLOAT")
    out = {"wav": wav, "extras": None}
    if arrays:
        out["extras"] = d / f"{jid}.extras.npz"
        np.savez_compressed(out["extras"], **arrays)
    rec = {"job": {k: v for k, v in job.items() if not k.startswith("_")}, "timings": _jsonable(t),
           "captures": caps, "host": NAME, "code_hash": CODE_HASH, "mlx": up.mx.__version__,
           "finished": time.time(), "archived": d != SCRATCH}
    (d / f"{jid}.json").write_text(json.dumps(rec))
    if d != SCRATCH and used:
        threading.Thread(target=_keep_blobs, args=(sorted(used),), daemon=True).start()
    return out


def _keep_blobs(paths) -> None:
    """raw/_blobs/: the job's inputs survive pruning of the SSD blob cache (copy: other filesystem)."""
    dst = ARCHIVE / "raw" / "_blobs"
    try:
        dst.mkdir(parents=True, exist_ok=True)
        for p in paths:
            q = dst / p.name
            if not q.exists():
                tmp = q.with_suffix(q.suffix + ".part")
                shutil.copyfile(p, tmp)
                tmp.replace(q)
    except OSError as e:
        _log(f"blob archive copy failed: {e}")


def _fatal(e: BaseException) -> bool:
    """A failure of the box rather than of the job: the Mac re-renders those locally."""
    if isinstance(e, MemoryError):
        return True
    m = f"{type(e).__name__}: {e}".lower()
    return any(s in m for s in ("out of memory", "cuda", "cudnn", "cublas", "nccl", "no stream", "gpu"))


def _remember(jid: str, paths: dict) -> None:
    jobs = _state["jobs"]
    jobs[jid] = paths
    while len(jobs) > 64:
        jobs.pop(next(iter(jobs)))


# ── HTTP ───────────────────────────────────────────────────────────────────
def make_app():
    from fastapi import FastAPI
    from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

    app = FastAPI(title="sa3-worker")

    @app.get("/health")
    def health():
        g = gpu()
        why = busy_reason()
        free = None
        try:
            if archive_ok():
                free = round(shutil.disk_usage(ARCHIVE).free / 1e9, 1)
        except OSError:
            pass
        return {"ok": True, "busy": why is not None, "busy_reason": why, "rendering": _slot.locked(),
                "code_hash": CODE_HASH, "host": NAME, "device": g["device"], "mlx_version": up.mx.__version__,
                "loaded": loaded(), "vram_free_gb": g["vram_free_gb"], "other_gpu_procs": g["other_gpu_procs"],
                "other_gpu_gb": g["other_gpu_gb"], "archive_ok": archive_ok(), "archive_free_gb": free}

    @app.head("/blobs/{sha}")
    def blob_head(sha: str, ext: str):
        try:
            return Response(status_code=200 if blob_path(sha, ext).is_file() else 404)
        except ValueError:
            return Response(status_code=400)

    @app.put("/blobs/{sha}")
    async def blob_put(sha: str, ext: str, request: Request):
        try:
            dst = blob_path(sha, ext)
        except ValueError:
            return JSONResponse({"error": "bad blob name"}, 400)
        if dst.is_file():
            return {"ok": True, "cached": True}
        BLOBS.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{dst.name}.{os.getpid()}.{time.time_ns()}.part")
        h = hashlib.sha256()
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                h.update(chunk)
                f.write(chunk)
        if h.hexdigest() != sha:
            tmp.unlink(missing_ok=True)
            return JSONResponse({"error": "sha256 mismatch"}, 400)
        tmp.replace(dst)
        return {"ok": True, "cached": False}

    @app.post("/render")
    async def render(request: Request):
        job = await request.json()
        jid = str(job.get("job") or "")
        if not _HEX.fullmatch(jid):
            return JSONResponse({"error": "job id must be 32 hex chars"}, 400)
        if job.get("code_hash") and job["code_hash"] != CODE_HASH:
            return JSONResponse({"error": f"code hash {job['code_hash']} ≠ worker {CODE_HASH}"}, 409)
        why = busy_reason()
        if why:
            return JSONResponse({"error": why}, 503)
        if not _slot.acquire(blocking=False):
            return JSONResponse({"error": "busy: another job is rendering"}, 409)
        try:
            used: set = set()
            args, plan = _args(job, used)
        except Exception as e:  # noqa: BLE001
            _slot.release()
            return JSONResponse({"error": f"{type(e).__name__}: {e}"}, 400)
        j = _Job(jid, int(args.get("steps") or 8))
        t0 = time.time()
        fut = up._MLX_EXECUTOR.submit(_execute, j, job, args, plan)

        def finished(f):
            _state["last_end"] = time.time()
            _slot.release()
        fut.add_done_callback(finished)
        _state["jobs"].pop(jid, None)

        def stream():
            try:
                last = time.time()
                while not (fut.done() and j.q.empty()):
                    try:
                        ev = j.q.get(timeout=0.25)
                    except queue.Empty:
                        if time.time() - last > 2:
                            last = time.time()
                            yield '{"ev":"tick"}\n'
                        continue
                    last = time.time()
                    yield json.dumps(ev) + "\n"
                try:
                    audio, t, caps, arrays = fut.result()
                except engine.Stopped:
                    _log(f"job={jid[:8]} stopped after {time.time() - t0:.1f} s")
                    yield json.dumps({"ev": "error", "type": "Stopped", "msg": "generation stopped", "fatal": False}) + "\n"
                    return
                except BaseException as e:  # noqa: BLE001
                    fatal = _fatal(e)
                    _log(f"job={jid[:8]} failed {'(fatal) ' if fatal else ''}{type(e).__name__}: {e}")
                    if fatal:
                        up.mlx_call(_release, "after a CUDA error")
                    yield json.dumps({"ev": "error", "type": type(e).__name__, "msg": str(unblob(str(e))),
                                      "fatal": fatal}) + "\n"
                    return
                paths = _store(job, audio, t, caps, arrays, used)
                _remember(jid, paths)
                wall = time.time() - t0
                a = job["args"]
                _log(f"job={jid[:8]} {float(a.get('seconds', 0)):.0f}s steps={a.get('steps')} cfg={a.get('cfg')} "
                     f"wall={wall:.1f}s sample={t.get('sample_ms', 0) / 1000:.1f}s decode={t.get('decode_ms', 0) / 1000:.1f}s"
                     f" → {paths['wav']}")
                yield json.dumps({"ev": "done", "timings": _jsonable(t), "extras": caps, "arrays": bool(arrays),
                                  "audio": f"/audio/{jid}", "wall_ms": round(wall * 1000, 1),
                                  "raw": str(paths["wav"])}) + "\n"
            except GeneratorExit:                       # the Mac went away mid-job: stop the render
                j.cancel.set()
                raise
        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.get("/audio/{jid}")
    def audio(jid: str):
        p = _state["jobs"].get(jid, {}).get("wav") if _HEX.fullmatch(jid) else None
        if p is None or not Path(p).is_file():
            return JSONResponse({"error": "no such render"}, 404)
        return FileResponse(p, media_type="audio/wav")

    @app.get("/extras/{jid}")
    def extras(jid: str):
        p = _state["jobs"].get(jid, {}).get("extras") if _HEX.fullmatch(jid) else None
        if p is None or not Path(p).is_file():
            return JSONResponse({"error": "no extras"}, 404)
        return FileResponse(p, media_type="application/octet-stream")

    @app.post("/cancel/{jid}")
    def cancel(jid: str):
        j = _cur["job"]
        if j is not None and j.id == jid:
            j.cancel.set()
            return {"ok": True}
        return {"ok": False, "error": "not running"}

    return app


def main() -> None:
    import uvicorn
    for d in (WORKDIR, BLOBS, SCRATCH):
        d.mkdir(parents=True, exist_ok=True)
    if AI_AUDIO.resolve() not in BLOBS.resolve().parents:
        _log(f"warning: SA3_BLOBS={BLOBS} is outside {AI_AUDIO}: condmix anchor files there won't load")
    up._MLX_EXECUTOR.submit(lambda: up.mx.set_default_device(up.mx.gpu)).result()
    threading.Thread(target=_housekeeping, name="sa3-housekeeping", daemon=True).start()
    _log(f"sa3-worker {NAME} code {CODE_HASH} mlx {up.mx.__version__} on {BIND}:{PORT} "
         f"(archive {'ok' if archive_ok() else 'OFFLINE'}: {ARCHIVE})")
    uvicorn.run(make_app(), host=BIND, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
