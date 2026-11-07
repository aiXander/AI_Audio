"""Conditioning over time: several conditioning anchors, blended per latent frame (composer spec 4.5).

SA3's text conditioning is global: every frame cross-attends to the same 257 tokens (256 T5Gemma
tokens + 1 duration token, no positions) and the AdaLN input is the duration only. So "this
stretch sounds like render A, that one like B, crossfade in between" needs a hook in the DiT.
This module adds it without touching upstream files, the way segments.py patches the sampler.

**Contract (the `"cond"` key of the segments box, carried by segments.parse_plan):**

    {"mode": "m1" | "m3",
     "anchors": [{"npz": <path>} | {"id": <render id>} | {"prompt": <text or anarchy spec>}
                 | {"bank": <render id>, "i": k}, …],
                # an npz / id anchor may add "splice": {"prompt", "n", "gain"?} (splice_bag, experiment 0h)
     "curves":  [[[t, w], …], …]}          # one keyframe curve per anchor, seconds → weight

- An anchor is the 256 conditioning tokens only. `npz` = an anarchy render's `<audio>.cond.npz`
  (absolute, or relative to outputs/), `id` = a render as ONE conditioning (an anarchy render's
  `.cond.npz`; a multi-anchor render such as a composer take → its dominant anchor, a text render
  → its prompt: condsrc.expand), `bank` = anchor k stored in a multi render's own
  `<audio>.conds.npz` (condsrc.py: what every take was conditioned on, self-contained), `prompt` =
  encoded with T5Gemma (+ the DiT's padding vector) exactly as upstream does, so an `anarchy: …`
  spec works too. File-backed anchors resolve through condsrc.tokens, which falls back to the
  trash (`deleted/`). The 257th token (duration) is always the job's own length.
- Any anchor may carry `"mute": true` (the Composer's Mute conditioning): it is then SA3's
  *unconditional* input, all 257 tokens zero, duration token included, which is exactly what
  training's 10 % CFG dropout showed the DiT (dit.py `cfg_dropout_prob`; the MLX CLI's uncond
  branch is the same `zeros_like(cross)`). The rest of the dict only records what was muted.
  Every muted anchor of a job shares one zero slot (their weights add), so muting costs at most
  one extra cross-attention pass. A muted stretch under CFG ≠ 1 has cond = uncond, so CFG is a
  no-op there: the frames follow the audio context (inpaint keep / a2a init) and the noise.
- Curves use segments' keyframe rule (linear between keys, flat outside, repeated t = a jump),
  sampled at each latent frame's centre, then normalised to sum 1 per frame (a frame where every
  curve is 0 goes to anchor 0). A hard cut is two keys at the same t.
- **m1, cross-attention output blend** (the default): in every DiT block, frame f's cross-attention
  output is Σ_i w_i(f) · CA(q_f, K_i, V_i). Self-attention and the FFN run once; only the cheap
  cross-attention (257 keys) runs once per anchor. Exact at weight 1 (1·a + 0·b = a). The 64
  memory tokens get the frame-mean weights.
- **m3, velocity blend**: one full DiT pass per anchor, velocities blended per frame. N× the
  sampling cost; each pass is fully in-distribution, so it is the reference to judge m1 by.
- With CFG ≠ 1 the batch's second row is the uncond / negative branch; it keeps its own
  conditioning for every anchor (the blend is identity there).

Where it runs: `install(up)` patches `DiT.__call__` and the cross-attention class of every DiT
module upstream can load (`DIT_CHOICES[*]["loader"]`). The patched call reads the running
generation's plan from `segments._active` (set on the MLX thread by `segments.active()` in the
studio, or directly by a bench script), so it is a no-op for every render without a "cond" key.
Anchors are resolved once per generation and cached by the plan object; the resolved tensors are
handed to condsrc.remember so the render's sidecar save writes its bank (condsrc.install).

Traps: anchors are resolved on the MLX thread at the first DiT call, so a bad npz path fails the
render with a clear error, not the queue submit. An anchor whose weight is 0 on every frame is
skipped (no cost). One anchor at weight 1 everywhere is bit-identical to rendering with that
conditioning as the prompt (checked 2026-11-06: a take with `cond` = one anchor vs the same job
with prompt `anarchy: @<that .cond.npz>` and no `cond`, PCM max difference 0). Cost on a 6 s
Medium render: m1 ≈ 1.3-1.6× the sampling time with 2 anchors, m3 ≈ 2×.
"""
from __future__ import annotations

import functools
import importlib

import numpy as np

MODES = ("m1", "m3")
DEFAULT_MODE = "m1"

_up = None
_dit_name = {"v": "medium"}          # the running generation's DiT (wrapped run_generation)
_cache: dict = {}                    # id(cond dict) → {"cond": dict, "anchors": [...], "w": {T: np}}
_ca = {"w": None, "ctx": None}       # m1: set for the duration of one DiT forward


# ── contract ──────────────────────────────────────────────────────────────────────────────
def parse(cond) -> dict | None:
    """Validate a "cond" dict; None when it has no usable anchor."""
    if not isinstance(cond, dict):
        return None
    anchors = [a for a in cond.get("anchors") or [] if isinstance(a, dict)
               and any(k in a for k in ("npz", "id", "prompt", "bank", "mute"))]
    if not anchors:
        return None
    curves = list(cond.get("curves") or [])
    curves += [[[0.0, 1.0 if not curves and i == 0 else 0.0]] for i in range(len(curves), len(anchors))]
    return {"mode": cond.get("mode") if cond.get("mode") in MODES else DEFAULT_MODE,
            "anchors": anchors, "curves": curves[:len(anchors)]}


def weights(curves, T: int, fps: float) -> np.ndarray:
    """Per-anchor weight per latent frame, [N, T] float32, summing to 1 per frame."""
    import segments
    W = np.stack([segments.frame_strength([(float(t), float(w)) for t, w in c], T, fps) for c in curves])
    s = W.sum(0)
    W[0, s <= 0] = 1.0
    return (W / np.maximum(W.sum(0), 1e-9)).astype(np.float32)


def crossfade(t0: float, t1: float, n_keys: int = 24, shape: str = "cos") -> tuple[list, list]:
    """Keyframe curves for a two-anchor crossfade from A (before t0) to B (after t1): cosine by
    default, "linear", or "cut" (a jump at the midpoint)."""
    if shape == "cut" or t1 <= t0:
        m = round((t0 + t1) / 2, 4)
        return [[m, 1.0], [m, 0.0]], [[m, 0.0], [m, 1.0]]
    xs = np.linspace(0.0, 1.0, n_keys)
    wb = 0.5 - 0.5 * np.cos(np.pi * xs) if shape == "cos" else xs
    ts = t0 + xs * (t1 - t0)
    a = [[round(float(t), 4), round(float(1 - w), 5)] for t, w in zip(ts, wb)]
    b = [[round(float(t), 4), round(float(w), 5)] for t, w in zip(ts, wb)]
    return a, b


# ── anchors ───────────────────────────────────────────────────────────────────────────────
def anchor_tokens(a: dict, dit: str):
    """One anchor → its (1, 256, 768) float16 conditioning tokens (MLX). A render named as one
    anchor is made concrete first (condsrc.expand: a take → its dominant anchor, a text render
    → its prompt); file-backed anchors load through condsrc.tokens (bank rows, `.cond.npz` bags,
    with the trash as fallback)."""
    import condsrc
    mx = _up.mx
    if condsrc.muted(a):                                  # SA3's unconditional input (CFG dropout)
        return mx.zeros((1, 256, 768), dtype=mx.float16)
    a = condsrc.expand(a)
    if "prompt" in a:
        e, m = _up.get_t5().encode([str(a["prompt"])], max_len=256)
        pad, _ = _up.get_conditioner(dit)
        return _up.apply_prompt_padding(e.astype(mx.float16), m, pad.astype(mx.float16))
    try:
        c = condsrc.tokens(a)
    except ValueError as e:
        raise ValueError(f"condmix: {e}") from None
    if a.get("splice"):                                   # experiment 0h: prompt tokens spliced into the bag
        c = splice_bag(c, a["splice"], dit)
    return mx.array(c[None].astype(np.float16))


def splice_bag(bag: np.ndarray, sp: dict, dit: str) -> np.ndarray:
    """Replace `n` of an anarchy bag's 256 vectors (evenly spaced, deterministic) with the real
    (unpadded) T5Gemma tokens of `prompt`, repeated to fill n (spec 4.6 "BPM splice": the DiT
    reads the conditioning as an unordered bag, so a few `BPM: 120` tokens may pull the tempo)."""
    mx = _up.mx
    e, m = _up.get_t5().encode([str(sp["prompt"])], max_len=256)
    e = np.array(e.astype(mx.float32))[0]
    m = np.array(m).astype(bool).reshape(-1)[:e.shape[0]]
    tok = e[m] if m.any() else e[:4]
    n = max(0, min(256, int(sp.get("n", 16))))
    if not n:
        return bag
    idx = np.linspace(0, 255, n).round().astype(int)
    out = bag.astype(np.float32).copy()
    out[idx] = tok[np.arange(n) % len(tok)] * float(sp.get("gain", 1.0))
    return out


def _runtime(cond: dict, T: int):
    hit = _cache.get(id(cond))
    if hit is None or hit["cond"] is not cond:
        _cache.clear()
        p = parse(cond)
        hit = {"cond": cond, "p": p, "anchors": [anchor_tokens(a, _dit_name["v"]) for a in p["anchors"]],
               "w": {}}
        _cache[id(cond)] = hit
        import condsrc                                    # the render's bank (condsrc.install writes it)
        condsrc.remember(cond, p["anchors"], p["curves"],
                         [np.array(t[0].astype(_up.mx.float16)) for t in hit["anchors"]])
    if T not in hit["w"]:
        W = weights(hit["p"]["curves"], T, _up.SAMPLE_RATE / _up.SAMPLES_PER_LATENT)
        mut = [i for i, a in enumerate(hit["p"]["anchors"]) if condsrc_muted(a)]
        for i in mut[1:]:                                 # every muted anchor shares one zero slot
            W[mut[0]] += W[i]
            W[i] = 0.0
        hit["w"][T] = W
    return hit["p"], hit["anchors"], hit["w"][T]


def condsrc_muted(a) -> bool:
    import condsrc
    return condsrc.muted(a)


def _active_cond():
    import segments
    plan = segments._active
    return plan.get("cond") if isinstance(plan, dict) else None


# ── the DiT hook ──────────────────────────────────────────────────────────────────────────
def _raw(anchor, cross, mute: bool = False):
    """Anchor tokens + this job's duration token, batch-matched to `cross` (row 1 under CFG =
    the uncond / negative branch, kept as is). Muted: the duration token is zero too (the whole
    cross-attention input zero, as CFG dropout trained it)."""
    mx = _up.mx
    dur = cross[0:1, 256:]
    row = mx.concatenate([anchor.astype(cross.dtype), mx.zeros_like(dur) if mute else dur], axis=1)
    return row if cross.shape[0] == 1 else mx.concatenate([row, cross[1:]], axis=0)


def _patch(mod) -> None:
    DiT = mod.DiT
    CA = getattr(mod, "DifferentialCrossAttention", None) or getattr(mod, "CrossAttention")
    if getattr(DiT, "_condmix", False):
        return
    dit_call, ca_call = DiT.__call__, CA.__call__

    @functools.wraps(dit_call)
    def dit_mixed(self, x, t, cross, gcond, local_add_cond=None):
        cond = _active_cond()
        if not cond:
            return dit_call(self, x, t, cross, gcond, local_add_cond)
        mx = _up.mx
        T = x.shape[-1]
        p, anchors, W = _runtime(cond, T)
        live = [i for i in range(len(anchors)) if W[i].max() > 0]
        mute = [condsrc_muted(p["anchors"][i]) for i in range(len(anchors))]
        if len(live) == 1 and W[live[0]].min() >= 1.0:          # one anchor everywhere: exact
            return dit_call(self, x, t, _raw(anchors[live[0]], cross, mute[live[0]]), gcond, local_add_cond)
        raws = [_raw(anchors[i], cross, mute[i]) for i in live]
        if p["mode"] == "m3":
            out = None
            for i, r in zip(live, raws):
                v = dit_call(self, x, t, r, gcond, local_add_cond)
                w = mx.array(W[i][None, None, :]).astype(v.dtype)
                out = v * w if out is None else out + v * w
            return out
        ctx = []                                                  # m1: project each anchor once
        for r in raws:
            ctx.append(self.to_cond_embed[2](_silu(self.to_cond_embed[0](r))))   # = DiT's own projection
        Wl = W[live]
        _ca["w"], _ca["ctx"], _ca["T"] = Wl, ctx, T
        try:
            return dit_call(self, x, t, raws[0], gcond, local_add_cond)
        finally:
            _ca["w"] = _ca["ctx"] = None

    @functools.wraps(ca_call)
    def ca_mixed(self, x, context):
        Wl = _ca["w"]
        if Wl is None:
            return ca_call(self, x, context)
        mx = _up.mx
        pad = x.shape[1] - _ca["T"]                               # memory tokens in front
        out = None
        for wi, ctx in zip(Wl, _ca["ctx"]):
            full = np.concatenate([np.full(pad, wi.mean(), np.float32), wi]) if pad > 0 else wi
            o = ca_call(self, x, ctx)
            w = mx.array(full[None, :, None]).astype(o.dtype)
            out = o * w if out is None else out + o * w
        return out

    DiT.__call__, CA.__call__ = dit_mixed, ca_mixed
    DiT._condmix = True


def _silu(x):
    return x * _up.mx.sigmoid(x)


def install(up) -> None:
    """Patch every loadable DiT module (idempotent) and note the running DiT's name."""
    global _up
    _up = up
    import mlx.nn as nn
    globals()["_silu"] = nn.silu
    for loader in sorted({c["loader"] for c in up.DIT_CHOICES.values() if c.get("loader")}):
        try:
            _patch(importlib.import_module(loader))
        except Exception as e:                       # a DiT we can't patch just renders unmixed
            print(f"[condmix] can't patch {loader}: {e}", flush=True)
    if getattr(up.run_generation, "_condmix", False):
        return
    orig = up.run_generation

    @functools.wraps(orig)
    def run_generation(dit_name, *a, **kw):
        _dit_name["v"] = dit_name
        return orig(dit_name, *a, **kw)
    run_generation._condmix = True
    up.run_generation = run_generation
