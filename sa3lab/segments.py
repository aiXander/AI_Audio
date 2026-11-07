"""Segments: a denoising-strength curve drawn on the inpaint reference's dadaspec (segments.js/.css).

The Inpaint and Audio → audio panels show their uploaded audio as a dadaspec on a timeline
with its own playback (segments.js). Audio → audio is a viewer only: upstream's single Noise
level still applies to the whole clip. **On the Inpaint input you draw one strength curve:
every latent frame of the reference gets a strength u in [0, 1]**: 0 keeps it bit-exact, 1
regenerates it from noise (the old inpaint span), in between re-synthesises it softly, staying
closer to the reference the lower u is. It is a superset of Inpaint (a 0/1 curve) and of a
cleaner Audio → audio (a flat curve); both old paths still exist.

**The curve is keyframes** `[[t, u], …]` (seconds): linear between, flat before the first and
after the last; two keys at the same t are a hard edge (list order = the value before the jump
first). A regenerate span 4-8 s is `[4,0] [4,1] [8,1] [8,0]`; a whole-clip strength is one key.
The UI's gestures (drag a range → a plateau at the brush strength, click → a key) only write keys.
No keys = the reference is unused (what "no spans" did before); all keys 0 = keep everything.

**The sampler is differential diffusion** (Levin & Fried 2023) on SA3's pingpong schedule,
`soft_sample`, a copy of upstream's `sample_flow_pingpong` with the same RNG stream:
- after each step, a held frame's denoised estimate is replaced by the reference before the
  re-noise, so its next input is `ref·(1 − t) + n·t` with the noise draw every other frame gets
  (one uniform noise level, as the model trained: training noises the whole clip to one t);
- a frame is held on every step input with t above `threshold(u)` (fractionally on the step
  where it crosses), then denoises freely; kept frames (u 0) are pasted back at the end, as
  latents, and again as audio after the decode (`paste_kept`: SAME-L's decode of exact latents is
  still a reconstruction, so without it "keep" would lose high end on every re-inpaint);
- the mask channel (`local_add_cond` = [keep, reference × keep]) is swapped per step via the
  closure cell of upstream's `model_fn`: kept frames 1; soft frames 1 while held and 0 once
  released (mask variant "B"; "A" = 0 throughout); regenerated frames 0.
With a 0/1 curve this is bit-identical to upstream's own From/To inpaint (tested), so the old
per-span mask code is gone: every inpaint render goes through `soft_sample`.

**Strength scale (rule `dd-v2`, recorded in every sidecar).** The schedule is uniform in log-SNR,
so "k of N steps free" is the warped time `logsnr_shift(k/N)` at any step count; `threshold`
maps u linearly onto 0..5 of 8 free steps (V_SPAN 0.625), u = 1 regenerates. On 8 steps u = k/5
frees exactly k steps. Why 5: the 2026-11-06 ladder (Medium + SAME-L, 30 s, contrasting prompt,
2 seeds; latent cosine to the reference .95 / .83 / .60 / .21 / .13 for 1-5 free steps, .14 for a
full regenerate) looked like this: 1-2 free steps = the same music with a shifted timbre, 3 = same
arrangement in the new prompt's sound, 4 = section structure survives, 5 = a new piece with a
trace of the old, 6-8 = a full regenerate. Mask A is B shifted by exactly one step (A at k = B at
k + 1, to three decimals): under A the prediction made on the last held step is already free and
kept, under B the keep mask makes it ≈ the reference. B matches the nominal count, so it is the
default. Upstream a2a at the same noise levels: grainy smears already at 0.27, structure gone by
0.51, a full regenerate from 0.75. More steps (Steps slider, up to 16) give finer levels, same scale.

Traps: the model has no per-frame t, so the hold is the only lever; a frame released while its
neighbours are held sees a clean-ish context, so hard edges between very different strengths can
seam (feather them with a ramp: drag one key of a hard edge sideways). Holding kept frames too
(`kept: "hold"`, what training feeds the model) is an option in the box; the default stays
upstream's free kept frames. Tried and dropped earlier (2026-11-06): per-span noise for
Audio → audio via three sampler hacks on upstream's a2a σ (not a strength scale: its schedule
re-noises upward after the first step).

**Length follows the input** (`locked_length`): with an inpaint reference it is the
reference's length, or the end of the curve's last non-zero stretch when that reaches past it
(a continuation; Extend works this way); with only an a2a guide, the guide's length. `wire()`
overwrites the Length argument with it on every job, and segments.js sets the slider to match.

**One input mode at a time.** Upstream can combine a2a + inpaint; the studio doesn't, to keep
renders readable: with both inputs loaded segments.js disables Generate (with a note in both
editors) and `wire()` refuses the job (`BOTH`), the backstop for queued / restored / API jobs.

Data path. One CSS-hidden textbox (`#sa3-seg-data`) holds `{"strength": [[t, u], …], "mask",
"kept"}` (parse_plan; the old `{"inpaint": [[s0, s1]]}` still parses, as plateaus of 1). segments.js
rewrites only `strength` on an edit and keeps the other keys a restore put there (a take's `cond`),
so tweaking a restored composer take's curve keeps its anchors; the curve's Clear drops them too.
`add_box()` creates it; `wire()` appends it to the generate / promote / pregen inputs (outermost
wrapper, the same pattern as outdir.py's folder box) and, per job, sets upstream's hidden From/To
sliders to (0, Length) so upstream encodes the reference and builds `paste_back`, and parks the
plan in a thread-local. studio.py's mlx_call hook carries it to the MLX thread (`current()` →
`active()`), where the wrapped `sample_flow_pingpong` runs `soft_sample`. An empty box: upstream's
own path, untouched. `sa3lab/bench/soft_bench.py` renders and measures curves headless.

The box may also carry `"cond"` (conditioning anchors + weight curves over time: condmix.py) and
`"composite"` (the composer's lineage: sa3lab/composer/compose.py), both passed through the plan.
A box with `"cond"` and no strength is a *conditioning-only* plan (keys []): the sampler runs
upstream's path (no curve, From/To 0) while condmix still blends the anchors, so it works on
Audio → audio and plain text jobs too (the Arranger's per-section prompts, arranger_link.py).

The sidecar's `gen` block gains `segments` = {"strength", "mask", "kept", "rule"[, "cond"]}, and
library.restore_settings hands it back so "settings" / the session restore redraw the curve; its
"cond" anchors come back pointed at the render's own bank when it has one (condsrc.self_contained),
so regenerating a composer take needs none of the renders it was made from.
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
_tl = threading.local()               # handler thread: this job's plan (parse_plan)
_active: dict | None = None           # MLX thread: the running generation's plan
MASKS = ("A", "B")                    # soft frames' mask channel: A = 0 throughout, B = 1 while held
DEFAULT_MASK = "B"
KEPTS = ("free", "hold")              # kept frames' x: free (upstream inpaint) or held to the noised ref
DEFAULT_KEPT = "free"
RULE = "dd-v2"                        # strength → hold rule (threshold / release); bump when it changes
V_SPAN = 0.625                        # strength 1⁻ = 5 of 8 steps free (threshold)
BOX = None                            # the gr.Textbox (studio.py restore/save read it)


# ── parsing ────────────────────────────────────────────────────────────────
def _load(raw):
    if not raw:
        return None
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def spans_to_keys(spans) -> list:
    """Regenerate spans [(s0, s1)] → strength keys: a 0 → 1 jump at each start, 1 → 0 at each end
    (overlapping spans merged first)."""
    merged = []
    for a, b in sorted((min(float(x), float(y)), max(float(x), float(y))) for x, y in spans):
        if b - a <= 1e-3:
            continue
        if merged and a <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    keys = []
    for a, b in merged:
        keys += [(round(a, 3), 0.0), (round(a, 3), 1.0), (round(b, 3), 1.0), (round(b, 3), 0.0)]
    return keys


def parse_plan(raw) -> dict | None:
    """Box JSON (or a sidecar's gen.segments) → {"keys": [(t, u)], "mask", "kept"}; None when
    there is nothing to draw from (empty / unreadable box: upstream's path, the reference unused).

    The contract is one strength curve: {"strength": [[t, u], …], "mask": "A"|"B", "kept":
    "free"|"hold"}, t in seconds, u in [0, 1] (0 keep bit-exact, 1 regenerate from noise, between:
    soft re-synthesis). Linear between keys, flat before the first / after the last; two keys at
    the same t are a hard edge (list order: the value before the jump first). The old
    {"inpaint": [[s0, s1], …]} spans (older sidecars, Extend, Arranger sends) become plateaus of
    1 (spans_to_keys). Optional "cond" (per-frame conditioning anchors, condmix.py) and
    "composite" (the composer's lineage block, sa3lab/composer/) ride along untouched."""
    d = _load(raw)
    if d is None:
        return None
    keys = []
    for k in d.get("strength") or []:
        try:
            keys.append((round(max(0.0, float(k[0])), 3), round(min(1.0, max(0.0, float(k[1]))), 4)))
        except Exception:
            continue
    if not keys and d.get("inpaint"):
        try:
            keys = spans_to_keys(d["inpaint"])
        except Exception:
            keys = []
    if not keys:
        if isinstance(d.get("cond"), dict):        # conditioning only (an Arranger send, a2a / text jobs)
            return {"keys": [], "mask": DEFAULT_MASK, "kept": DEFAULT_KEPT, "cond": d["cond"],
                    **({"composite": d["composite"]} if isinstance(d.get("composite"), dict) else {})}
        return None
    plan = {"keys": sorted(keys, key=lambda k: k[0]),            # stable: hard-edge order survives
            "mask": d.get("mask") if d.get("mask") in MASKS else DEFAULT_MASK,
            "kept": d.get("kept") if d.get("kept") in KEPTS else DEFAULT_KEPT}
    for k in ("cond", "composite"):     # condmix.py's anchors + curves; the composer's lineage
        if isinstance(d.get(k), dict):
            plan[k] = d[k]
    return plan


def plan_end(plan) -> float:
    """End of the last region with strength > 0 (a curve reaching past the reference's end is a
    continuation and stretches Length, as Extend asks); 0 without one."""
    end, prev = 0.0, 0.0
    for t, u in (plan or {}).get("keys", []):
        if u > 0 or prev > 0:
            end = max(end, t)
        prev = u
    return end


def dumps(plan=None) -> str:
    plan = plan or {}
    d = {"strength": [list(k) for k in plan.get("keys", [])]}
    if d["strength"]:
        d.update(mask=plan.get("mask", DEFAULT_MASK), kept=plan.get("kept", DEFAULT_KEPT))
    d.update({k: plan[k] for k in ("cond", "composite") if plan.get(k)})
    return json.dumps(d)


def plan_record(plan) -> dict:
    """The sidecar's gen.segments: the curve as drawn + the rule that turns it into per-frame
    holds (RULE), so the render regenerates exactly."""
    r = {"strength": [list(k) for k in plan["keys"]], "mask": plan["mask"],
         "kept": plan.get("kept", DEFAULT_KEPT), "rule": RULE}
    if plan["keys"]:
        r["paste"] = "audio"           # kept frames hold the reference's own audio (paste_kept)
    if plan.get("cond"):
        r["cond"] = plan["cond"]       # anchors + weight curves (condmix.py)
    return r


def restore_value(r: dict, *, own: bool = True) -> str:
    """The box value for a restored render / session `r`. `own=False` (Extend, Arranger
    sends): ignore the parent's curve, keep only its single inpaint range, plus the parent's
    conditioning anchors when it had several (Extend of a composer take: the extension stays
    conditioned on the take's anchors, the tail holding their final mix, not on its job label)."""
    plan = parse_plan(r.get("segments")) if own else None     # sidecar dict or session raw box
    if not own and isinstance(r.get("arranger_cond"), dict):   # an Arranger send's per-section prompts
        plan = parse_plan({"cond": r["arranger_cond"]})
    rng = r.get("inpaint_range_sec")
    if plan is None and rng and float(rng[1]) > float(rng[0]):
        pc = None if own else (r.get("segments") or {}).get("cond") if isinstance(r.get("segments"), dict) else None
        plan = parse_plan({"inpaint": [rng], **({"cond": pc} if isinstance(pc, dict) else {})})
    return dumps(plan)


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
            plan = parse_plan(raw)
            _tl.seg = None
            L = locked_length(rest[12], rest[11], plan_end(plan) if plan is not None and plan["keys"] else
                              (rest[14] if rest[14] > rest[13] else 0.0))
            if L:
                rest[4] = L
            if raw is not None and not str(raw).strip() == "":
                if plan is not None and not plan["keys"]:
                    rest[13], rest[14] = 0.0, 0.0        # conditioning only: no curve, the reference unused
                    _tl.seg = plan
                elif plan is None or not rest[12]:
                    rest[13], rest[14] = 0.0, 0.0        # no curve: the reference is unused (as before)
                else:
                    # upstream gates inpainting on From < To and builds one range; the sampler hook
                    # replaces it with the curve, which needs the reference over the whole clip
                    rest[13], rest[14] = 0.0, float(rest[4] or 0)
                    _tl.seg = plan
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


def locked_length(inpaint_path, a2a_path, end: float = 0.0) -> float | None:
    """Length is the input's, not the slider's: the inpaint reference (stretched to `end`, the
    curve's last strength > 0, when it reaches past it: a continuation), else the a2a guide. None
    without an input. Upstream still clamps it to the model's maximum. (Both inputs at once
    never get here: wire() refuses that job.)"""
    if inpaint_path:
        d = duration(inpaint_path)
        return round(max(d, float(end or 0.0)), 3) if d else None
    if a2a_path:
        d = duration(a2a_path)
        return round(d, 3) if d else None
    return None


def current() -> dict | None:
    """This handler thread's plan (studio.py's mlx_call hook reads it before dispatching)."""
    return getattr(_tl, "seg", None)


@contextlib.contextmanager
def active(plan: dict | None, bound=None):
    """On the MLX thread: make `plan` visible to the sampler hook for this generation."""
    global _active
    _active = plan if plan and (plan.get("keys") or plan.get("cond")) else None
    try:
        yield
    finally:
        _active = None


# ── MLX side ───────────────────────────────────────────────────────────────
def _cell(model_fn):
    free = model_fn.__code__.co_freevars
    if "local_add_cond" not in free:
        raise RuntimeError("segments: upstream's model_fn no longer closes over local_add_cond")
    return model_fn.__closure__[free.index("local_add_cond")]


def _set_cond(cell, ctx, keep):
    """local_add_cond = [keep, reference × keep] (upstream's layout), keep shaped (1, 1, T)."""
    mx = _up.mx
    cell.cell_contents = mx.concatenate([keep, ctx.astype(mx.float32) * keep], axis=1
                                        ).transpose(0, 2, 1).astype(cell.cell_contents.dtype)


def frame_strength(keys, T: int, fps: float | None = None):
    """The curve sampled at each latent frame's centre, float32 [T] in [0, 1] (parse_plan's rule:
    linear between keys, flat outside, a repeated t is a jump). Edges on the latent grid land
    exactly where the old per-span mask put them."""
    import numpy as np
    fps = fps or _up.SAMPLE_RATE / _up.SAMPLES_PER_LATENT
    if not keys:
        return np.zeros(T, dtype=np.float32)
    kt = np.array([k[0] for k in keys], dtype=np.float64)
    ku = np.array([k[1] for k in keys], dtype=np.float64)
    c = (np.arange(T) + 0.5) / fps
    i = np.searchsorted(kt, c, side="right")             # keys with t <= c
    lo, hi = np.clip(i - 1, 0, len(kt) - 1), np.clip(i, 0, len(kt) - 1)
    span = kt[hi] - kt[lo]
    w = np.where(span > 0, (c - kt[lo]) / np.where(span > 0, span, 1.0), 0.0)
    u = ku[lo] + w * (ku[hi] - ku[lo])
    u = np.where(i == 0, ku[0], np.where(i >= len(kt), ku[-1], u))
    return np.clip(u, 0.0, 1.0).astype(np.float32)


def threshold(v):
    """UI strength v → the noise level t at which a frame is released (held while t_i > it).

    SA3's schedule is uniform in log-SNR (t = sigmoid(−λ), λ linear from 2.0 to −6.2; upstream's
    logsnr_shift), so "k of N steps free" is the warped time logsnr_shift(k / N) at any step count.
    The 2026-11-06 ladder (Medium + SAME-L, 30 s, mask B) showed 1-5 free steps of 8 are the useful
    range and 6+ already equal a full regenerate, so v spans 0..5 of 8 free steps linearly
    (V_SPAN) and v = 1 is never held: each fifth of the scale is one visibly distinct level."""
    import numpy as np
    v = np.clip(np.asarray(v, dtype=np.float64), 0.0, 1.0)
    lam = 2.0 - (V_SPAN * v) * 8.2
    t = 1.0 / (1.0 + np.exp(lam))
    return np.where(v >= 1.0, 1.0, np.where(v <= 0.0, 0.0, t))


def release(v, sigmas):
    """UI strength → release step r (float, 0..N): a soft frame's x is held to the noised reference
    on every step input i < r (fractionally at i = floor(r)), then denoises freely for N − r
    steps. Held ⇔ t_i > threshold(v), linear between schedule points; with 8 steps v = k/5 frees
    exactly k steps (k = 1..4; v → 1⁻ frees 5), v = 1 frees all 8."""
    import numpy as np
    u = threshold(v)
    sig = np.asarray(sigmas, dtype=np.float64)[:-1]        # input noise level of each step
    N = len(sig)
    idx = np.arange(N, dtype=np.float64)
    # r(u) = #steps with t_i > u: r(t_i) = i, r(0) = N, linear between (sig decreases: reverse it)
    r = np.interp(u, np.r_[0.0, sig[::-1]], np.r_[N, idx[::-1]], right=0.0)
    return np.clip(r, 0.0, N)


def hold_table(u, sigmas, mask: str = DEFAULT_MASK, kept: str = DEFAULT_KEPT):
    """Per step i: the hold weight h[i, f] (1 = x is the noised reference, 0 = free) and the
    keep-mask channel m[i, f]. Strength 0 = kept (m 1 + paste-back; x free as upstream does with
    kept="free", or held every step with kept="hold", which is what training feeds the model);
    1 = regenerated (m 0, never held); in between = soft (held while i < r, m per `mask`)."""
    import numpy as np
    N = len(sigmas) - 1
    u = np.asarray(u, dtype=np.float32)
    keep, soft = u <= 0.0, (u > 0.0) & (u < 1.0)
    H = np.zeros((N, u.size), dtype=np.float32)
    if soft.any():
        r = release(u[soft], sigmas)
        H[:, soft] = np.clip(r[None, :] - np.arange(N)[:, None], 0.0, 1.0)
    if kept == "hold":
        H[:, keep] = 1.0
    M = np.repeat(keep[None, :].astype(np.float32), N, axis=0)
    if mask == "B":
        M[:, soft] = (H[:, soft] >= 0.5).astype(np.float32)
    return H, M, keep


def soft_sample(model_fn, x, sigmas, u, ctx, seed: int = 0, mask: str = DEFAULT_MASK,
                kept: str = DEFAULT_KEPT, on_step=None, before_step=None):
    """upstream's sample_flow_pingpong (same RNG stream, same arithmetic) with per-frame holds.

    Held frames have their denoised estimate replaced by the reference before re-noising, so
    their next input is ref·(1 − t) + n·t with the very noise draw the free frames get: one
    uniform noise level, in distribution. A fractional hold blends the two estimates. The mask
    channel is swapped per step (hold_table), kept frames are pasted back at the end. With u
    only 0 / 1 this is bit-identical to the old inpaint path."""
    import numpy as np
    mx = _up.mx
    sig = np.array(sigmas.astype(mx.float32)) if hasattr(sigmas, "astype") else np.asarray(sigmas)
    N = len(sig) - 1
    H, M, keep = hold_table(u, sig, mask, kept)
    ref = ctx.astype(x.dtype)
    cell = _cell(model_fn)
    m_last = None
    if H[0].any() and sig[0] < 1.0:          # sigma_max < 1: the first input is held too
        h0 = mx.array(H[0][None, None, :]).astype(x.dtype)
        x = x + h0 * (1.0 - float(sig[0])) * (ref - x)
    key = mx.random.key(seed)
    for i in range(N):
        if m_last is None or not np.array_equal(M[i], m_last):
            _set_cond(cell, ctx, mx.array(M[i][None, None, :]))
            m_last = M[i]
        t_curr, t_next = sigmas[i], sigmas[i + 1]
        t_tensor = t_curr * mx.ones((x.shape[0],), dtype=x.dtype)
        if before_step is not None:
            before_step(i)
        v = model_fn(x, t_tensor)
        denoised = x - t_curr.astype(x.dtype) * v
        if i < N - 1 and float(t_next) > 0.0:
            key, sub = mx.random.split(key)
            noise = mx.random.normal(x.shape, dtype=x.dtype, key=sub)
            if H[i + 1].any():
                h = mx.array(H[i + 1][None, None, :]).astype(x.dtype)
                denoised = denoised + h * (ref - denoised)
            x = (1.0 - t_next).astype(x.dtype) * denoised + t_next.astype(x.dtype) * noise
        else:
            x = denoised
        mx.eval(x)
        if on_step is not None:
            on_step(i + 1, N)
    k = mx.array(keep[None, None, :].astype(np.float32)).astype(x.dtype)
    x = ref * k + x * (1.0 - k)
    mx.eval(x)
    return x


def install_sampler(up) -> None:
    """The sampler hook alone (bench scripts call this; install() adds the studio bits)."""
    global _up
    _up = up
    orig_sample = up.sample_flow_pingpong

    def sample(model_fn, x, sigmas, *a, paste_back=None, **kw):
        if _active and _active.get("keys") and paste_back is not None:
            u = frame_strength(_active["keys"], x.shape[-1])
            return soft_sample(model_fn, x, sigmas, u, paste_back[0], mask=_active["mask"],
                               kept=_active.get("kept", DEFAULT_KEPT),
                               **{k: v for k, v in kw.items() if k in ("seed", "on_step", "before_step")})
        return orig_sample(model_fn, x, sigmas, *a, paste_back=paste_back, **kw)
    up.sample_flow_pingpong = sample


PASTE_FADE = 0.15                     # s: the kept-audio paste's crossfade (raised cosine, kept side)


def splice_weights(keep, n: int, spl: int, fade: int | None = None):
    """Per-sample weight (length n) of a reference pasted over decoded audio at its kept frames
    (`keep`: bool per latent frame of `spl` samples): 1 inside a kept stretch, 0 elsewhere, and a
    raised-cosine crossfade of `fade` samples (PASTE_FADE) inside each kept edge, so it ends exactly
    where regenerated audio starts and has no slope jump at either end. The clip's own start and
    end are no edge; a kept stretch shorter than two fades gets a shorter one."""
    import numpy as np
    fade = int(round(PASTE_FADE * 44100)) if fade is None else int(fade)
    k = np.repeat(np.asarray(keep, bool), spl)[:n]
    if k.size < n:
        k = np.pad(k, (0, n - k.size), mode="edge")
    w = k.astype(np.float32)
    d = np.diff(np.r_[0, k.astype(np.int8), 0])
    for s, e in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)):   # kept runs [s, e)
        L = min(fade, (e - s) // 2 if (s > 0 and e < n) else e - s)
        if L < 2:
            continue
        ramp = (0.5 - 0.5 * np.cos(np.pi * (np.arange(L) + 0.5) / L)).astype(np.float32)
        if s > 0:
            w[s:s + L] = ramp
        if e < n:
            w[e - L:e] = np.minimum(w[e - L:e], ramp[::-1])
    return w


def paste_kept(audio, plan: dict, ref_path):
    """The decoded render with its kept frames (strength 0) replaced by the reference's own audio,
    crossfaded inside each kept edge (`splice_weights`). The sampler keeps those latents exact, but
    SAME-L's decode of them is a reconstruction (waveform correlation ~.75-.85 to the input, up to
    −10 dB above 16 kHz), and that loss would compound every time a render is inpainted again. The
    reference is read exactly as the encoder read it (upstream's read_audio_any, from sample 0);
    past its end (an Extend) it is silence, where frames are regenerated anyway."""
    import numpy as np
    spl = _up.SAMPLES_PER_LATENT
    n = audio.shape[-1]
    keep = frame_strength(plan["keys"], -(-n // spl)) <= 0.0
    if not keep.any():
        return audio
    ref = _up.read_audio_any(str(ref_path))
    ref = ref[:, :n] if ref.shape[-1] >= n else np.pad(ref, ((0, 0), (0, n - ref.shape[-1])))
    w = splice_weights(keep, n, spl)
    return (audio * (1.0 - w) + ref * w).astype(np.float32)


def install(up, library) -> None:
    """Sampler hook, kept-audio paste, the gen-record stamp, restore passthrough and the /sa3/inspec route."""
    import functools
    import inspect
    install_sampler(up)

    orig_run = up.run_generation
    run_sig = inspect.signature(orig_run)

    @functools.wraps(orig_run)
    def run_generation(*a, **kw):
        res = orig_run(*a, **kw)
        plan = _active
        if plan and plan.get("keys") and isinstance(res, tuple):
            path = run_sig.bind(*a, **kw).arguments.get("inpaint_audio_path")
            if path:
                try:
                    res = (paste_kept(res[0], plan, path), *res[1:])
                except Exception as e:  # noqa: BLE001  never lose a render over the paste
                    print(f"[segments] kept-audio paste failed ({e}); saving the decode as is", flush=True)
        return res
    up.run_generation = run_generation

    orig_rec = library.gen_record

    def gen_record(*a, **kw):
        g = orig_rec(*a, **kw)
        plan = current()
        if plan:
            g["segments"] = plan_record(plan)
            if plan.get("composite"):
                g["composite"] = plan["composite"]     # a composer take's lineage (composer/compose.py)
        return g
    library.gen_record = gen_record

    orig_rs = library.restore_settings

    def restore_settings(name):
        r = orig_rs(name)
        if r and r.get("full"):
            try:
                ap = library._audio_path(name)
                d = json.loads(library._sidecar(ap).read_text())
                if (d.get("gen") or {}).get("segments"):
                    import condsrc                         # anchors → the render's own bank: regenerating
                    r["segments"] = condsrc.self_contained(ap, d["gen"]["segments"])   # needs no source files
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
