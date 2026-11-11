"""Remote rendering: the studio's queued jobs render on the RTX 3090 box (`rednax-beast`) when it can.

The studio stays exactly what it is (UI, gradio queue, Queue modal, sidecars, history, labels);
only the GPU work of a job moves. studio.py's mlx_call hook (the one cut point, see
_install_stop_hooks) asks `target()`; on "beast" it calls `render(bound, seg, stopped)` here instead of
running upstream's `run_generation` on the Mac's MLX thread, and gets the same `(audio (2, T)
float32, timings)` back, so the peak guard, gen record, file name, mp3, sidecar and history that
follow are unchanged. The box runs the same render code (engine.py) in `sa3lab/beast/worker.py`.

**The Render-on chip** (remote.js, next to Queue), a local / remote toggle: `Remote` (green) while
the box is reachable, not busy and running the same code, else `Local` (`Local (fallback)` when Remote
is chosen but the box can't take the job; reason on hover). Its menu: **Remote · GPU box** (pref
`auto`, the default: the box when available, else local) or **Local only** (pref `mac`), kept as
`render_on` in `.studio_prefs.json`.

    GET  /sa3/remote                 → {"pref", "target", "reason", "host", "health"}
    POST /sa3/remote {"pref": "auto"|"mac"}

**`target()`** = "beast" when the pref is Auto and the worker's `/health` (cached HEALTH_TTL s) is ok,
not busy (another GPU process > 8 GB, e.g. a LoRA training run) and its `code_hash` equals ours
(engine.code_hash over the render-path *.py files, computed once at studio start: the code this
process runs). `make start` deploys the Mac's code to the box first (scripts/beast.sh deploy).

**`render()`**: builds the job (`build_job`: every file a render reads is resolved *here* and uploaded
as a content-addressed blob: a2a / inpaint audio, LoRA files, condmix anchors as their (256, 768)
tensors, `anarchy: @file` references; the box never reads the Mac's outputs/), uploads the missing
blobs, streams `/render` (progress events → library.progress_*; the studio's Stop / Skip posts
`/cancel` and raises engine.Stopped like a local render), fetches the float32 WAV (never integer
PCM: SAME-L decodes peak above 0 dBFS and the peak guard must see that), then runs every
engine.CAPTURES restore (anarchy info + tensor, condsrc bank) so persist.save writes the same side
files as a local render. Returns None when the *box* failed (network, 5xx, crash, a CUDA error):
studio.py then renders the job on the Mac (one log line). A *render* error (bad anarchy spec, bad
LoRA) is re-raised with the worker's type and message, so upstream shows it exactly as a local one.

**`gen.backend`** in every sidecar: `{"host": "rednax-beast", "device", "mlx", "job", "code_hash",
"net_ms", "raw"}` for a box render, `{"host": "mac"}` for a local one; library.estimate_ms keeps the
two speed models apart by it.

**The archive** (scripts/archive.sh): a background thread mirrors outputs/ to the box's 12 TB drive
every ARCHIVE_EVERY s while the box answers (`rsync -a`, never deletes); `make archive` /
`make archive-check` run it by hand. A failure there never touches a render.

Config: SA3_BEAST_URL (default http://100.117.34.17:7870, the box's Tailscale address).
"""
from __future__ import annotations

import http.client
import io
import json
import os
import platform
import queue
import re
import subprocess
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
AI_AUDIO = HERE.parent
OUTPUTS = (AI_AUDIO / "outputs").resolve()
BEAST_URL = os.environ.get("SA3_BEAST_URL", "http://100.117.34.17:7870")
HEALTH_TTL = 10.0
HEALTH_TIMEOUT = 1.5
ARCHIVE_EVERY = 300
PREF_KEY = "render_on"

_up = _library = None
_get_pref = _set_pref = None
CODE_HASH = None
_tl = threading.local()                    # this handler thread's last render: backend record
_health = {"t": 0.0, "v": None, "err": None}
_inflight = {"job": None}
_lock = threading.Lock()


class JobError(Exception):
    """A job that can't be built for a reason a local render would fail on too (shown, not retried)."""

    def __init__(self, err: Exception):
        super().__init__(str(err))
        self.err = err


def _log(msg: str) -> None:
    print(f"[remote] {msg}", flush=True)


# ── HTTP (stdlib only) ─────────────────────────────────────────────────────
def _conn(timeout: float):
    u = urllib.parse.urlsplit(BEAST_URL)
    return http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)


def _request(method: str, path: str, body=None, headers=None, timeout: float = 30.0):
    c = _conn(timeout)
    try:
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


def health(force: bool = False) -> dict | None:
    """The worker's /health, cached HEALTH_TTL s (failures too, so an offline box costs one timeout per TTL)."""
    with _lock:
        if not force and time.time() - _health["t"] < HEALTH_TTL:
            return _health["v"]
    try:
        st, body = _request("GET", "/health", timeout=HEALTH_TIMEOUT)
        v, err = (json.loads(body), None) if st == 200 else (None, f"HTTP {st}")
    except Exception as e:  # noqa: BLE001
        v, err = None, f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
    with _lock:
        _health.update(t=time.time(), v=v, err=err)
    return v


def _invalidate() -> None:
    with _lock:
        _health["t"] = 0.0


# ── where the next job renders ─────────────────────────────────────────────
def pref() -> str:
    try:
        return "mac" if (_get_pref() or {}).get(PREF_KEY) == "mac" else "auto"
    except Exception:  # noqa: BLE001
        return "auto"


def status() -> dict:
    p = pref()
    h = health()                                       # also on Mac only: the chip still shows the box's state
    out = {"pref": p, "target": "mac", "reason": None, "host": (h or {}).get("host"), "code_hash": CODE_HASH,
           "health": h, "rendering_here": bool(_inflight["job"])}
    if p == "mac":
        out["reason"] = "Local only (set in this menu)"
    elif h is None:
        out["reason"] = f"box unreachable ({_health['err'] or 'no answer'})"
    elif not h.get("ok"):
        out["reason"] = "box worker not ok"
    elif h.get("busy"):
        out["reason"] = h.get("busy_reason") or "box GPU busy"
    elif h.get("code_hash") != CODE_HASH:
        out["reason"] = (f"box runs other code ({h.get('code_hash')} vs this studio's {CODE_HASH}): "
                         "`make start` deploys it")
    elif h.get("rendering") and not _inflight["job"]:
        out["reason"] = "box is rendering another client's job"
    else:
        out["target"] = "beast"
    return out


_last_target = {"v": None}


def target() -> str:
    """Where the next job renders; one log line whenever that changes (box went away, came back, …)."""
    try:
        s = status()
    except Exception as e:  # noqa: BLE001  (never block a render on the chip)
        _log(f"status failed: {e}")
        return "mac"
    key = (s["target"], (s["reason"] or "").split(" (")[0])
    if key != _last_target["v"]:
        _log("rendering on the box" if s["target"] == "beast" else f"rendering on the Mac: {s['reason']}")
        _last_target["v"] = key
    return s["target"]


def mark_local() -> None:
    _tl.backend = {"host": "mac"}


def backend_record() -> dict:
    return getattr(_tl, "backend", None) or {"host": "mac"}


# ── the job ────────────────────────────────────────────────────────────────
_EXT = re.compile(r"[a-z0-9]{1,16}")


class _Blobs:
    """What a job uploads: ref "blob:<sha>.<ext>" → a file path or bytes; and ref → the Mac's string."""

    def __init__(self):
        self.src: dict[str, object] = {}
        self.back: dict[str, str] = {}

    def file(self, path, typed: str | None = None) -> str:
        sha = _library._sha256(str(path))              # cached per (path, size, mtime): LoRAs hash once
        if sha is None:
            raise FileNotFoundError(f"can't read {path}")
        ext = Path(str(path)).suffix.lower().lstrip(".")
        ref = f"blob:{sha}.{ext if _EXT.fullmatch(ext) else 'bin'}"
        self.src[ref] = str(path)
        self.back[ref] = typed if typed is not None else str(path)
        return ref

    def data(self, b: bytes, ext: str, typed: str) -> str:
        import hashlib
        ref = f"blob:{hashlib.sha256(b).hexdigest()}.{ext}"
        self.src[ref] = b
        self.back.setdefault(ref, typed)              # never shadow a file's own string (same bytes, same blob)
        return ref

    def unblob(self, x):
        if isinstance(x, str):
            return re.sub(r"blob:[0-9a-f]{64}\.[a-z0-9]{1,16}", lambda m: self.back.get(m[0], m[0]), x)
        if isinstance(x, list):
            return [self.unblob(v) for v in x]
        if isinstance(x, dict):
            return {k: self.unblob(v) for k, v in x.items()}
        return x


def _saved_path(f: str) -> Path:
    """An `anarchy: @file` reference resolved as sampler._load_saved does (absolute, or under outputs/)."""
    p = Path(f).expanduser()
    return p if p.is_absolute() else OUTPUTS / p


def recorded(s: str) -> str:
    """How a local render records a saved-latent path: relative to outputs/ when it lives there."""
    try:
        p = _saved_path(s).resolve()
        return str(p.relative_to(OUTPUTS)) if OUTPUTS in p.parents else str(p)
    except (OSError, ValueError):
        return s


def rewrite_prompt(p: str, blobs: _Blobs) -> str:
    """`anarchy: @a.cond.npz [+ @b.cond.npz]` → the same spec with each file as a blob."""
    import anarchy_ui  # noqa: F401  (puts sa3lab/anarchy/ on sys.path)
    import sampler
    if not p or not sampler.is_spec(p) or "@" not in p:
        return p
    body = p.strip()[len(sampler.PREFIX):]
    if not body.strip().startswith("@"):
        return p
    out = p
    for f in [x.strip()[1:] for x in body.split(" + ")]:
        q = _saved_path(f)
        if f and q.is_file():
            out = out.replace("@" + f, "@" + blobs.file(q, typed=f), 1)
    return out


def _npz_bytes(**arrays) -> bytes:
    buf = io.BytesIO()
    np.savez(buf, **arrays)
    return buf.getvalue()


def rewrite_cond(c: dict, blobs: _Blobs) -> dict:
    """condmix anchors, resolved on the Mac (condsrc reads outputs/): file-backed ones become their
    (256, 768) tensors as blobs, prompts stay prompts (the box encodes them), muted stay muted.
    Mirrors condmix.anchor_tokens step for step, so the box conditions on exactly the same tensors."""
    import condmix
    import condsrc
    p = condmix.parse(c)
    if p is None:
        return c
    anchors = []
    for a in p["anchors"]:
        if condsrc.muted(a):
            anchors.append({"mute": True})
            continue
        e = condsrc.expand(a)
        if "prompt" in e:
            anchors.append({"prompt": rewrite_prompt(str(e["prompt"]), blobs)})
            continue
        try:
            tok = condsrc.tokens(e)
        except ValueError as err:
            raise JobError(ValueError(f"condmix: {err}")) from None
        ref = blobs.data(_npz_bytes(cond=np.asarray(tok, np.float16)), "npz", typed=json.dumps(e))
        anchors.append({"npz": ref, **({"splice": e["splice"]} if e.get("splice") else {})})
    return {"mode": p["mode"], "anchors": anchors, "curves": p["curves"]}


def build_job(bound, seg) -> tuple[dict, _Blobs]:
    import outdir
    import segments
    a = dict(bound.arguments)
    blobs = _Blobs()
    args = {k: a[k] for k in ("dit_name", "decoder_name", "prompt", "negative_prompt", "seconds", "steps",
                              "seed", "cfg", "apg", "sigma_max")}
    args["prompt"] = rewrite_prompt(args["prompt"], blobs)
    args["negative_prompt"] = rewrite_prompt(args["negative_prompt"], blobs)
    for k in ("a2a_audio_path", "inpaint_audio_path"):
        args[k] = blobs.file(a[k]) if a.get(k) else None
    rng = a.get("inpaint_range_sec")
    args["inpaint_range_sec"] = [float(rng[0]), float(rng[1])] if rng is not None else None
    specs = a.get("lora_specs")
    args["lora_specs"] = [{"path": blobs.file(s["path"]), "strength": float(s["strength"]),
                           "steps": list(s["steps"]) if s.get("steps") else None} for s in specs] if specs else None
    job_seg = None
    if seg:
        job_seg = json.loads(segments.dumps(seg))
        job_seg.pop("composite", None)                 # the composer's lineage: Mac-side bookkeeping only
        if isinstance(seg.get("cond"), dict):
            job_seg["cond"] = rewrite_cond(seg["cond"], blobs)
    job = {"job": uuid.uuid4().hex, "code_hash": CODE_HASH, "args": args, "seg": job_seg,
           "meta": {"outdir": outdir.rel(Path(_up.OUTPUT_DIR)), "mac_host": platform.node(),
                    "submitted": time.time()}}
    return job, blobs


def _upload(blobs: _Blobs) -> None:
    for ref, src in blobs.src.items():
        sha, ext = ref[5:].split(".", 1)
        st, _ = _request("HEAD", f"/blobs/{sha}?ext={ext}", timeout=10)
        if st == 200:
            continue
        if isinstance(src, bytes):
            body, n = src, len(src)
            st, msg = _request("PUT", f"/blobs/{sha}?ext={ext}", body=body,
                               headers={"Content-Length": str(n)}, timeout=120)
        else:
            n = os.path.getsize(src)
            with open(src, "rb") as f:
                st, msg = _request("PUT", f"/blobs/{sha}?ext={ext}", body=f,
                                   headers={"Content-Length": str(n)}, timeout=max(120, n / 5e6))
        if st != 200:
            raise RuntimeError(f"blob upload {sha[:12]}: HTTP {st} {msg[:200]!r}")


def _error(typ: str, msg: str) -> Exception:
    """The worker's render error as an exception of the same name, so upstream's error line reads the same."""
    if typ == "LoraError" and hasattr(_up, "LoraError"):
        return _up.LoraError(msg)
    if typ == "Stopped":
        import engine
        return engine.Stopped(msg)
    return type(typ if re.fullmatch(r"[A-Za-z_]\w*", typ or "") else "RemoteError", (Exception,), {})(msg)


# ── one remote render ──────────────────────────────────────────────────────
def render(bound, seg, stopped):
    """Render on the box: (audio (2, T) float32, timings) or None when the box failed (render locally)."""
    import engine
    _tl.backend = None
    if stopped():                        # a stopped chain's pregen dies before anything is sent (as locally)
        raise engine.Stopped("generation stopped")
    t0 = time.time()
    h = health() or {}
    try:
        job, blobs = build_job(bound, seg)
    except JobError as e:
        raise e.err
    except Exception as e:  # noqa: BLE001
        _log(f"couldn't build the job ({type(e).__name__}: {e}); rendering on the Mac")
        return None
    try:
        _upload(blobs)
    except Exception as e:  # noqa: BLE001
        _log(f"upload failed ({type(e).__name__}: {e}); rendering on the Mac")
        _invalidate()
        return None

    jid = job["job"]
    events: queue.Queue = queue.Queue()
    c = _conn(timeout=60)

    def reader():
        try:
            body = json.dumps(job).encode()
            c.request("POST", "/render", body=body, headers={"Content-Type": "application/json",
                                                              "Content-Length": str(len(body))})
            r = c.getresponse()
            if r.status != 200:
                events.put({"ev": "_http", "status": r.status, "msg": r.read()[:300].decode("utf-8", "replace")})
                return
            for line in r:
                line = line.strip()
                if line:
                    events.put(json.loads(line))
            events.put({"ev": "_eof"})
        except Exception as e:  # noqa: BLE001
            events.put({"ev": "_net", "msg": f"{type(e).__name__}: {e}"})

    host = h.get("host") or "rednax-beast"
    _library.progress_start(bound, host=host)
    _inflight["job"] = jid
    _set_running_on("Beast")
    threading.Thread(target=reader, name=f"sa3-remote-{jid[:8]}", daemon=True).start()
    cancel_at = None
    done = None
    try:
        while True:
            if cancel_at is None and stopped():
                cancel_at = time.time()
                try:
                    _request("POST", f"/cancel/{jid}", timeout=5)
                except Exception:  # noqa: BLE001
                    pass
            if cancel_at is not None and time.time() - cancel_at > 15:
                raise engine.Stopped("generation stopped")
            try:
                ev = events.get(timeout=0.25)
            except queue.Empty:
                continue
            kind = ev.get("ev")
            if kind == "step":
                _library.progress_step(int(ev["i"]))
            elif kind == "phase":
                _library.progress_phase(str(ev.get("name")))
            elif kind == "done":
                done = ev
                break
            elif kind == "error":
                if ev.get("type") == "Stopped" or cancel_at is not None:
                    raise engine.Stopped("generation stopped")
                if ev.get("fatal"):
                    _log(f"box failed ({ev.get('type')}: {ev.get('msg')}); rendering on the Mac")
                    _invalidate()
                    return None
                raise _error(str(ev.get("type")), blobs.unblob(str(ev.get("msg"))))
            elif kind in ("_http", "_net", "_eof"):
                if cancel_at is not None:
                    raise engine.Stopped("generation stopped")
                why = f"HTTP {ev['status']} {ev['msg']}" if kind == "_http" else ev.get("msg") or "stream ended early"
                _log(f"box failed ({why}); rendering on the Mac")
                _invalidate()
                return None
        if cancel_at is not None:
            raise engine.Stopped("generation stopped")
        try:
            audio, arrays = _fetch(jid, done)
        except Exception as e:  # noqa: BLE001
            _log(f"couldn't fetch the render ({type(e).__name__}: {e}); rendering on the Mac")
            _invalidate()
            return None
        if stopped():                    # Stop pressed while the audio came back: not saved, as locally
            raise engine.Stopped("generation stopped")
        ctx = {"prompt": bound.arguments.get("prompt"), "seg": seg, "unblob": blobs.unblob, "recorded": recorded}
        for name, js in (done.get("extras") or {}).items():
            fn = engine.CAPTURES.get(name)
            if fn is None:
                continue
            try:
                fn[1](js, {k.split("/", 1)[1]: v for k, v in arrays.items() if k.startswith(name + "/")}, ctx)
            except Exception as e:  # noqa: BLE001  (a side file lost, never the render)
                _log(f"capture {name} restore failed: {e}")
        t = dict(done.get("timings") or {})
        total = (time.time() - t0) * 1000
        t["net_ms"] = round(total - float(done.get("wall_ms") or 0), 1)
        _tl.backend = {"host": host, "device": h.get("device"), "mlx": h.get("mlx_version"), "job": jid,
                       "code_hash": CODE_HASH, "net_ms": t["net_ms"], "wall_ms": round(total, 1),
                       "raw": done.get("raw")}
        return audio, t
    finally:
        _inflight["job"] = None
        _set_running_on(None)
        _library.progress_end()
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass


def _fetch(jid: str, done: dict):
    import soundfile as sf
    st, body = _request("GET", done.get("audio") or f"/audio/{jid}", timeout=120)
    if st != 200:
        raise RuntimeError(f"audio: HTTP {st}")
    y, _sr = sf.read(io.BytesIO(body), dtype="float32", always_2d=True)
    audio = np.ascontiguousarray(y.T, dtype=np.float32)
    arrays = {}
    if done.get("arrays"):
        st, body = _request("GET", f"/extras/{jid}", timeout=60)
        if st == 200:
            with np.load(io.BytesIO(body)) as z:
                arrays = {k: z[k] for k in z.files}
    return audio, arrays


def _set_running_on(on) -> None:
    """The Queue modal's running row says where it renders (ui/queue.js)."""
    try:
        import renderq
        if renderq.running is not None:
            renderq.running["on"] = on
    except Exception:  # noqa: BLE001
        pass


# ── the archive mirror ─────────────────────────────────────────────────────
ARCHIVE_SH = AI_AUDIO / "scripts" / "archive.sh"


def _archive_loop() -> None:
    time.sleep(60)                               # let the studio settle after a start
    while True:
        try:
            if health() is not None:
                r = subprocess.run(["nice", "-n", "10", str(ARCHIVE_SH), "push", "--quiet"],
                                   capture_output=True, text=True, timeout=3600)
                if r.returncode != 0:
                    _log(f"archive sync failed ({r.returncode}): {(r.stderr or r.stdout).strip()[-300:]}")
        except Exception as e:  # noqa: BLE001  (never matters to a render)
            _log(f"archive sync: {e}")
        time.sleep(ARCHIVE_EVERY)


# ── install ────────────────────────────────────────────────────────────────
def install(up, library, get_pref, set_pref, archive: bool = True) -> None:
    global _up, _library, _get_pref, _set_pref, CODE_HASH
    import engine
    _up, _library, _get_pref, _set_pref = up, library, get_pref, set_pref
    CODE_HASH = engine.code_hash()
    _install_routes()
    threading.Thread(target=health, kwargs={"force": True}, name="sa3-remote-health", daemon=True).start()
    if archive and os.environ.get("SA3_ARCHIVE_SYNC", "1") != "0":
        threading.Thread(target=_archive_loop, name="sa3-archive", daemon=True).start()


def _install_routes() -> None:
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from gradio import routes

    def get():
        return JSONResponse(status())

    async def post(request: Request):
        d = await request.json()
        p = d.get("pref")
        if p not in ("auto", "mac"):
            return JSONResponse({"error": "pref must be auto or mac"}, 400)
        _set_pref(**{PREF_KEY: p})
        _invalidate()
        return JSONResponse(status())

    orig = routes.App.create_app

    def create_app(*a, **kw):
        app = orig(*a, **kw)
        app.add_api_route("/sa3/remote", get, methods=["GET"])
        app.add_api_route("/sa3/remote", post, methods=["POST"])
        ours = [r for r in app.router.routes if getattr(r, "path", "").startswith("/sa3/")]
        app.router.routes[:] = ours + [r for r in app.router.routes if r not in ours]
        return app
    routes.App.create_app = staticmethod(create_app)
