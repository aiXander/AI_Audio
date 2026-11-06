"""Segments: inpaint spans drawn on the reference's dadaspec (segments.js/.css).

The Inpaint and Audio → audio panels show their uploaded audio as a dadaspec on a timeline
with its own playback (segments.js). On the Inpaint input, click-dragging draws spans that
move, resize, duplicate and delete in place; **every span is regenerated (from noise, σ 1),
everything else kept bit-exact.** Audio → audio is a viewer only: upstream's single Noise
level still applies to the whole clip.

**Length follows the input** (`locked_length`): with an inpaint reference it is the
reference's length, or the last span's end when a span reaches past it (a continuation; Extend
works this way); with only an a2a guide, the guide's length. `wire()` overwrites the Length
argument with it on every job (the slider can't drift from the input and skew the dataset's
`seconds`), and segments.js sets the slider to match so the UI shows what will render.

**One input mode at a time.** Upstream can combine a2a + inpaint; the studio doesn't, to keep
renders readable: with both inputs loaded segments.js disables Generate (with a note in both
editors) and `wire()` refuses the job (`BOTH`), the backstop for queued / restored / API jobs.

Why so little backend: SA3 inpaints natively from a per-latent-frame keep mask
(`local_add_cond` = [keep, reference latents × keep], plus upstream's end-of-sampling
paste-back) and trains with 1–10 random spans per example. Upstream's gradio only exposes
one From/To range, so the only change is building that mask with one zero run per span.
Spans past the reference's end regenerate over upstream's zero padding (as Extend does,
extend.py).

Tried and dropped (2026-11-06): a per-span noise level for Audio → audio. Not native (upstream
uses one σ, and its schedule re-noises upward after the first step, so the guide acts only
through the first prediction); three sampler hacks were measured on 6 s renders and none
behaved cleanly, so a2a stays upstream's single-σ path.

Data path. One CSS-hidden textbox (`#sa3-seg-data`) holds `{"inpaint": [[s0, s1], …]}` in
seconds. `add_box()` creates it; `wire()` appends it to the generate / promote / pregen
inputs (outermost wrapper, the same pattern as outdir.py's folder box) and, per job,
overrides upstream's hidden From/To sliders with the spans' outer bounds (so upstream still
encodes the reference and enables inpainting) and parks the spans in a thread-local.
studio.py's mlx_call hook carries them to the MLX thread (`current()` → `active()`), where
the wrapped `sample_flow_pingpong` swaps the single-range mask for the per-span one (in
`model_fn`'s `local_add_cond` closure cell and in the paste-back). An empty box or no spans:
upstream's own path, untouched.

The sidecar's `gen` block gains `segments` = {"inpaint": spans}, and
library.restore_settings hands it back so "settings" / the session restore redraw them.
`/sa3/inspec?u=<file url or path>` renders an input's dadaspec (JPEG, duration in the
`X-Sa3-Duration` header) for the editor.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import urllib.parse
from pathlib import Path

ELEM_ID = "sa3-seg-data"
BOTH = "Pick one: Audio → audio or Inpaint. Clear one of the two inputs to generate."
AI_AUDIO = Path(__file__).resolve().parents[1]

_up = None
_tl = threading.local()               # handler thread: this job's spans
_active: list | None = None           # MLX thread: the running generation's spans
BOX = None                            # the gr.Textbox (studio.py restore/save read it)


# ── parsing ────────────────────────────────────────────────────────────────
def parse(raw) -> list | None:
    """Box JSON → sorted [(s0, s1)] in seconds; None when the box is empty or unreadable
    (then upstream's From/To sliders stay in charge)."""
    if not raw:
        return None
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    out = []
    for s in d.get("inpaint") or []:
        try:
            a, b = sorted((max(0.0, float(s[0])), max(0.0, float(s[1]))))
        except Exception:
            continue
        if b - a > 1e-3:
            out.append((round(a, 3), round(b, 3)))
    return sorted(out)


def dumps(spans=()) -> str:
    return json.dumps({"inpaint": [list(s) for s in spans]})


def restore_value(r: dict, *, own: bool = True) -> str:
    """The box value for a restored render / session `r`. `own=False` (Extend, Arranger
    sends): ignore the parent's spans, keep only its single inpaint range."""
    segs = (r.get("segments") or {}) if own else {}
    spans = parse(segs) if isinstance(segs, str) else segs.get("inpaint")   # session: raw box
    rng = r.get("inpaint_range_sec")
    if not spans and rng and float(rng[1]) > float(rng[0]):
        spans = [list(rng)]
    return dumps(spans or [])


# ── handler side ───────────────────────────────────────────────────────────
def add_box(demo):
    """The hidden JSON box + tags on the two audio inputs and upstream's From/To row (call
    before studio.py's restore wiring, which reads/writes the box)."""
    global BOX
    import gradio as gr

    gen = next(bf for bf in demo.fns.values() if bf.name == "generate")
    a2a, inp, s0, s1 = gen.inputs[11:15]
    for comp, cls in ((a2a, "sa3-seg-a2a"), (inp, "sa3-seg-inp")):
        comp.elem_classes = list(comp.elem_classes or []) + [cls]
    row = s0
    while row is not None and not isinstance(row, gr.Row):
        row = getattr(row, "parent", None)
    for c in [s0, s1] + ([row] if row is not None else []):
        c.elem_classes = list(c.elem_classes or []) + ["sa3-seg-old"]   # segments.css hides them
    with demo:
        BOX = gr.Textbox(value="", elem_id=ELEM_ID, container=False, show_label=False)
    return BOX


def wire(demo) -> None:
    """Append the box to every render handler's inputs (call last: outermost wrapper)."""
    import functools

    for bf in demo.fns.values():
        if bf.name not in ("generate", "promote", "pregen"):
            continue
        bf.inputs.append(BOX)
        inner = bf.fn

        @functools.wraps(inner)
        def wrapped(*a, _inner=inner):
            *rest, raw = a
            if rest[11] and rest[12]:
                import gradio as gr
                raise gr.Error(BOTH)
            spans = parse(raw)
            _tl.seg = None
            L = locked_length(rest[12], rest[11], spans if spans is not None else
                              ([(rest[13], rest[14])] if rest[14] > rest[13] else []))
            if L:
                rest[4] = L
            if spans is not None:
                seconds = float(rest[4] or 0)
                spans = [s for s in spans if s[0] < seconds] if rest[12] else []
                # upstream gates inpainting on From < To and builds one range; the real mask comes later
                rest[13], rest[14] = (spans[0][0], max(s[1] for s in spans)) if spans else (0.0, 0.0)
                _tl.seg = spans or None
            try:
                return _inner(*rest)
            finally:
                _tl.seg = None
        bf.fn = wrapped


_dur_cache: dict[tuple, float] = {}


def duration(path) -> float | None:
    """Seconds of an input as the encoder will read it (upstream's read_audio_any), cached."""
    try:
        st = os.stat(path)
        k = (str(path), st.st_size, st.st_mtime)
        if k not in _dur_cache:
            _dur_cache[k] = _up.read_audio_any(str(path)).shape[-1] / _up.SAMPLE_RATE
        return _dur_cache[k]
    except Exception as e:
        print(f"[sa3lab] segments: can't read {path}: {e}", flush=True)
        return None


def locked_length(inpaint_path, a2a_path, spans) -> float | None:
    """Length is the input's, not the slider's: the inpaint reference (stretched to the last span
    end when a span reaches past it: a continuation, as Extend asks), else the a2a guide. None
    without an input. Upstream still clamps it to the model's maximum. (Both inputs at once
    never get here: wire() refuses that job.)"""
    if inpaint_path:
        d = duration(inpaint_path)
        return round(max([d] + [float(s[1]) for s in spans]), 3) if d else None
    if a2a_path:
        d = duration(a2a_path)
        return round(d, 3) if d else None
    return None


def current() -> list | None:
    """This handler thread's spans (studio.py's mlx_call hook reads it before dispatching)."""
    return getattr(_tl, "seg", None)


@contextlib.contextmanager
def active(spans: list | None, bound=None):
    """On the MLX thread: make `spans` visible to the sampler hook for this generation."""
    global _active
    _active = spans or None
    try:
        yield
    finally:
        _active = None


# ── MLX side ───────────────────────────────────────────────────────────────
def _frames(s: float) -> int:
    return int(round(s * _up.SAMPLE_RATE / _up.SAMPLES_PER_LATENT))


def _apply(model_fn, x, paste_back):
    """Swap upstream's single-range keep mask for one with a zero run per span."""
    import numpy as np
    mx = _up.mx
    T = x.shape[-1]
    ctx, _ = paste_back
    keep_np = np.ones((1, 1, T), dtype=np.float32)
    for s0, s1 in _active:
        keep_np[:, :, max(0, _frames(s0)):min(T, max(_frames(s1), _frames(s0) + 1))] = 0.0
    keep = mx.array(keep_np)
    free = model_fn.__code__.co_freevars
    if "local_add_cond" not in free:
        raise RuntimeError("segments: upstream's model_fn no longer closes over local_add_cond")
    cell = model_fn.__closure__[free.index("local_add_cond")]
    cell.cell_contents = mx.concatenate([keep, ctx.astype(mx.float32) * keep], axis=1
                                        ).transpose(0, 2, 1).astype(cell.cell_contents.dtype)
    return ctx, keep


def install(up, library) -> None:
    """Sampler hook, the gen-record stamp, restore passthrough and the /sa3/inspec route."""
    global _up
    _up = up

    orig_sample = up.sample_flow_pingpong

    def sample(model_fn, x, sigmas, *a, paste_back=None, **kw):
        if _active and paste_back is not None:
            paste_back = _apply(model_fn, x, paste_back)
        return orig_sample(model_fn, x, sigmas, *a, paste_back=paste_back, **kw)
    up.sample_flow_pingpong = sample

    orig_rec = library.gen_record

    def gen_record(*a, **kw):
        g = orig_rec(*a, **kw)
        seg = current()
        if seg:
            g["segments"] = {"inpaint": [list(s) for s in seg]}
        return g
    library.gen_record = gen_record

    orig_rs = library.restore_settings

    def restore_settings(name):
        r = orig_rs(name)
        if r and r.get("full"):
            try:
                d = json.loads(library._sidecar(library._audio_path(name)).read_text())
                if (d.get("gen") or {}).get("segments"):
                    r["segments"] = d["gen"]["segments"]
            except Exception:
                pass
        return r
    library.restore_settings = restore_settings
    _install_route()


# ── /sa3/inspec: an input's dadaspec ───────────────────────────────────────
_spec_cache: dict[tuple, tuple[bytes, float]] = {}
_spec_lock = threading.Lock()


def _roots() -> list[str]:
    r = [AI_AUDIO, Path(tempfile.gettempdir()) / "gradio"]
    if os.environ.get("GRADIO_TEMP_DIR"):
        r.append(Path(os.environ["GRADIO_TEMP_DIR"]))
    return [os.path.realpath(p) for p in r]


def _path_of(u: str) -> str | None:
    u = urllib.parse.unquote(u or "")
    if "file=" in u:
        u = u.split("file=", 1)[1].split("?", 1)[0]
    p = os.path.realpath(u) if u.startswith("/") else None
    if not p or not os.path.isfile(p) or not any(p.startswith(r + os.sep) for r in _roots()):
        return None
    return p


def spec(u: str) -> tuple[bytes, float] | None:
    import numpy as np
    import hispec
    p = _path_of(u)
    if p is None:
        return None
    st = os.stat(p)
    k = (p, st.st_size, st.st_mtime)
    with _spec_lock:
        if k in _spec_cache:
            return _spec_cache[k]
    y = _up.read_audio_any(p)                                  # (2, T) float32 @ 44.1 kHz
    pcm = (np.clip(y, -1, 1) * 32767.0).astype(np.int16).T
    out = (hispec.render_spectrogram_png(pcm, _up.SAMPLE_RATE), y.shape[-1] / _up.SAMPLE_RATE)
    with _spec_lock:
        while len(_spec_cache) >= 8:
            _spec_cache.pop(next(iter(_spec_cache)))
        _spec_cache[k] = out
    return out


def _install_route() -> None:
    from fastapi.responses import JSONResponse, Response
    from gradio import routes

    def inspec_get(u: str):          # sync: decode + STFT run in the threadpool
        try:
            r = spec(u)
        except Exception as e:
            return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
        if r is None:
            return JSONResponse({"error": "not an allowed audio file"}, status_code=404)
        return Response(r[0], media_type="image/jpeg",
                        headers={"X-Sa3-Duration": f"{r[1]:.4f}", "Cache-Control": "no-store"})

    orig = routes.App.create_app

    def create_app(*a, **kw):
        app = orig(*a, **kw)
        app.add_api_route("/sa3/inspec", inspec_get, methods=["GET"])
        ours = [r for r in app.router.routes if getattr(r, "path", "").startswith("/sa3/")]
        app.router.routes[:] = ours + [r for r in app.router.routes if r not in ours]
        return app
    routes.App.create_app = staticmethod(create_app)
