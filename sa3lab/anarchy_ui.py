"""Promptless (anarchy) generation in the studio: sample T5Gemma conditioning, not prompts.

The whole feature rides on one idea: an anarchy sample is described completely by a
prompt string (`anarchy: <fit> <sampler> T<temp> d<dir> [L<len>] [c<skip>]`, grammar
in `anarchy/sampler.py`). So the UI panel (anarchy_ui.js), while its Active switch is on,
keeps that string in the Prompt box and lets the studio's own Generate render it (one draw,
N random directions or a temperature ladder), and everything the studio already does for a
prompt — the gradio queue + Queue modal, Stop, history, sidecars, labels, restore-settings —
works unchanged. Server side, `install(up)`:

- wraps `sa3_gradio.get_t5()`: an encoder whose `encode([prompt])` returns the sampled
  conditioning (all-ones mask, so `apply_prompt_padding` passes it through) when the
  prompt is a spec, else the real T5Gemma result. The fit is per DiT (padding vector),
  so `run_generation` is wrapped to note which DiT is rendering (one MLX thread);
- wraps `persist.save`: a spec render's sidecar gains an `anarchy` block (the resolved
  spec, drawn length, `distance()` gauge, fit provenance, `cond_npz`), and the exact
  `(256, 768)` conditioning is written beside the audio as `<audio>.cond.npz` (fp16 =
  exactly what the DiT consumed); `anarchy: @<that file>` re-renders it;
- adds `GET /sa3/anarchy/fits` (the fits on disk, for the panel's picker).

Samplers `cocktail` (k concept clusters) and `mutate` (resample m% of a text prompt's tokens;
the panel's Anchor box, carried in the spec after `|`) are described in `anarchy/sampler.py`.

A bad spec raises `SpecError`, which upstream shows as the render's error line.
Renders land in the studio's normal output folder, named by their spec.
Plan and experiment context: docs/TODO/sa3-promptless-anarchy.md.
"""
from __future__ import annotations

import functools
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).with_name("anarchy")))
import sampler  # noqa: E402

_state = {"dit": "medium", "info": {}}

# The panel's Prompt-box mirror can carry a temperature RANGE, `T0.5–2(uniform)` or
# `T0.5–2(gaussian)`. The panel's own Generate swaps in concrete specs, but Infinite Radio's
# chained pregen re-reads the box server-side, so a range resolves here too: uniform, or a
# Gaussian centred on the middle with σ = range/4, redrawn outside the range (same as
# anarchy_ui.js drawT). The render's sidecar records the concrete spec it drew.
_RANGE = re.compile(r"\bT(\d+(?:\.\d+)?)[–-](\d+(?:\.\d+)?)\((uniform|gaussian)\)")
_RAND_D = re.compile(r"(?<=\s)d\?(?=\s|$)")     # blank Direction in the panel = random per render


def _is_variable(prompt: str) -> bool:
    return bool(_RANGE.search(prompt) or _RAND_D.search(prompt))


def _resolve_range(prompt: str) -> str:
    prompt = _RAND_D.sub(lambda _: f"d{np.random.randint(100000)}", prompt, count=1)
    m = _RANGE.search(prompt)
    if not m:
        return prompt
    lo, hi, dist = float(m[1]), float(m[2]), m[3]
    rng = np.random.default_rng()
    T = rng.uniform(lo, hi) if dist == "uniform" else next(
        (t for t in rng.normal((lo + hi) / 2, max(hi - lo, 1e-9) / 4, 64) if lo <= t <= hi), (lo + hi) / 2)
    return prompt[:m.start()] + f"T{round(float(T), 3):g}" + prompt[m.end():]


def install(up, persist) -> None:
    real_get_t5 = up.get_t5

    def _encode_text(text):     # `mutate`'s anchor prompt, encoded exactly as the studio does
        import mlx.core as mx
        e, m = real_get_t5().encode([text], max_len=256)
        mx.eval(e, m)
        return np.asarray(e[0]).astype(np.float32), int(np.asarray(m[0]).sum())

    class _Encoder:
        def encode(self, prompts, max_len=256):
            if len(prompts) == 1 and sampler.is_spec(prompts[0]):
                import mlx.core as mx
                cond, info = sampler.conditioning(sampler.parse(_resolve_range(prompts[0])), _state["dit"],
                                                  encode_text=_encode_text)
                _state["info"][prompts[0].strip()] = (info, cond)
                return mx.array(cond[None]), mx.ones((1, max_len), dtype=mx.int32)
            return real_get_t5().encode(prompts, max_len=max_len)

        def __getattr__(self, name):
            return getattr(real_get_t5(), name)

    enc = _Encoder()
    up.get_t5 = lambda: enc

    orig_run = up.run_generation

    @functools.wraps(orig_run)
    def run_generation(dit_name, *a, **kw):
        _state["dit"] = dit_name
        return orig_run(dit_name, *a, **kw)
    up.run_generation = run_generation

    orig_save = persist.save

    def save(entry):
        p = (entry or {}).get("prompt") or ""
        got = _state["info"].pop(p.strip(), None) if sampler.is_spec(p) else None
        if got is not None and "anarchy" not in entry:
            info, cond = got
            audio = Path(entry["path"])
            npz = audio.with_name(audio.name + ".cond.npz")
            try:
                np.savez_compressed(npz, cond=cond.astype(np.float16))
                info = {**info, "cond_npz": npz.name}
            except Exception as e:      # never break a render over bookkeeping
                print(f"[anarchy] latent save failed: {e}", flush=True)
            entry["anarchy"] = info
            if _is_variable(p) and info.get("spec"):
                entry["prompt"] = info["spec"]      # record the temperature / direction actually drawn
        return orig_save(entry)
    persist.save = save


def install_routes() -> None:
    from fastapi.responses import JSONResponse
    from gradio import routes

    def fits_get():
        return JSONResponse({"fits": sampler.list_fits(), "samplers": sampler.SAMPLERS})

    orig = routes.App.create_app

    def create_app(*a, **kw):
        app = orig(*a, **kw)
        app.add_api_route("/sa3/anarchy/fits", fits_get, methods=["GET"])
        ours = [r for r in app.router.routes if getattr(r, "path", "").startswith("/sa3/")]
        app.router.routes[:] = ours + [r for r in app.router.routes if r not in ours]
        return app
    routes.App.create_app = staticmethod(create_app)

