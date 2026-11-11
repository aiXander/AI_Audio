"""The render path in one place: every patch that acts *inside* a render, installed the same way on
the Mac (studio.py) and on the RTX 3090 box (beast/worker.py), so both run the same sampler hacks.

`install(up, library, persist, hooks)` applies, in this order (the run_generation wrappers stack,
outermost last; studio.py's mlx_call hook reads `up.run_generation` only after this returns):

- noise.install: a seed's noise is one fixed canvas cropped to the clip length;
- segments.install: the strength-curve sampler (inpaint / a2a curves) + the kept-audio paste;
- condmix.install: several conditioning anchors blended per latent frame (composer takes);
- condsrc.install: a multi-anchor render's tensors → `<audio>.conds.npz` when its sidecar is saved;
- anarchy_ui.install: `anarchy: …` prompts are sampled conditionings;
- the stop / progress checks: before every sampler step, every decoder chunk, an encoder or DiT load.

**Hooks.** The checks call `hooks.check_stop()`, `hooks.progress_step(i)` and
`hooks.progress_phase(name)`. The studio passes its Stop flag and library.progress_*; the worker
passes its per-job cancel flag and its NDJSON event stream.

**Side-channel registry (`CAPTURES`).** Some modules leave state behind during a render that
`persist.save` reads later, on the Mac. On a remote render that state is born on the box, so each
such module registers a capture here: `export(job) -> (json, {name: array})` runs on the worker
right after the render, `restore(json, arrays, ctx)` on the Mac before `persist.save`. Registered:
`anarchy` (anarchy_ui._state["info"] → the sidecar's `anarchy` block + `<audio>.cond.npz`) and
`condsrc` (condsrc.remember → `<audio>.conds.npz`). **A new module that stashes state for
`persist.save` must register one too**, or its remote renders silently lose that state.

`code_hash()` (beast/codehash.py) names the render-path code: the studio only sends a job to the box
when the box's deploy copy hashes the same.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "beast"))
from codehash import code_hash  # noqa: E402,F401  (re-exported)


class Stopped(Exception):
    """A render interrupted by Stop / Skip (studio) or /cancel (worker): nothing is saved."""


class Hooks:
    """No-op defaults; the studio and the worker pass their own."""

    def check_stop(self) -> None:
        pass

    def progress_step(self, i: int) -> None:
        pass

    def progress_phase(self, name: str) -> None:
        pass


HOOKS: Hooks = Hooks()

# name → (export(job) -> (json, {name: np.ndarray}), restore(json, arrays, ctx) -> None)
CAPTURES: dict[str, tuple] = {}     # export's job dict carries "_plan": the worker's live segments plan


def register(name: str, export, restore) -> None:
    CAPTURES[name] = (export, restore)


_installed = {"v": False}


def install(up, library, persist, hooks: Hooks | None = None) -> None:
    global HOOKS
    if hooks is not None:
        HOOKS = hooks
    if _installed["v"]:
        return
    _installed["v"] = True
    import anarchy_ui
    import condmix
    import condsrc
    import noise
    import segments

    noise.install(library)
    segments.install(up, library)       # sampler hook, gen.segments, restore passthrough, /sa3/inspec
    condmix.install(up)                 # DiT hook: the box's "cond" anchors (composer crossfades, morphs)
    condsrc.install(persist)            # a multi-anchor render's tensors → <audio>.conds.npz on save
    anarchy_ui.install(up, persist)     # `anarchy: …` prompts → sampled conditioning
    _install_checks(up)
    _register_captures(anarchy_ui, condsrc, segments)


def _install_checks(up) -> None:
    """Stop checks + live progress inside a render (see studio.py's "stop" block for the why)."""
    orig_sample = up.sample_flow_pingpong

    def sample(*a, before_step=None, **kw):
        def bs(i):
            HOOKS.check_stop()
            HOOKS.progress_step(i)
            if before_step is not None:
                before_step(i)
        return orig_sample(*a, before_step=bs, **kw)
    up.sample_flow_pingpong = sample

    # The decode is 18-66 s of a long render on the Mac, built by decode_chunked as one lazy MLX graph
    # and evaluated in one go, so a Skip/Stop pressed during it used to wait the whole decode out.
    # Each decoder call (one chunk, ~1-2 s) now evaluates on its own and checks the flag first:
    # same ops, same output, just a sync point per chunk.
    class _StoppableDecoder:
        def __init__(self, model):
            self._m = model

        def __call__(self, *a, **kw):
            HOOKS.check_stop()
            out = self._m(*a, **kw)
            up.mx.eval(out)
            return out

        def __getattr__(self, k):
            return getattr(self._m, k)

    orig_dec = up.get_decoder

    def get_decoder(*a, **kw):
        HOOKS.check_stop()
        HOOKS.progress_phase("decode")
        model, *rest = orig_dec(*a, **kw)
        return (_StoppableDecoder(model), *rest)
    up.get_decoder = get_decoder

    # the other long stretches without a sampler step: SAME-L encode of an a2a / inpaint input
    # (up to ~10 s) and a DiT load for a new length (~3 s) each check once before starting
    for name in ("get_encoder", "get_dit"):
        def checked(*a, _orig=getattr(up, name), **kw):
            HOOKS.check_stop()
            return _orig(*a, **kw)
        setattr(up, name, checked)


# ── captures ───────────────────────────────────────────────────────────────
def _register_captures(anarchy_ui, condsrc, segments) -> None:
    import numpy as np

    def anarchy_export(job):
        """The main prompt's sampled conditioning + info (anchor prompts' entries are dropped:
        nothing on the Mac reads them)."""
        st = anarchy_ui._state["info"]
        got = st.pop((job["args"].get("prompt") or "").strip(), None)
        st.clear()
        if got is None:
            return None, {}
        info, cond = got
        return {"info": info}, {"cond": np.asarray(cond, np.float16)}

    def anarchy_restore(js, arrays, ctx):
        if not js or "cond" not in arrays:
            return
        info = ctx["unblob"](js["info"])
        for k in ("source_file",):
            if isinstance(info.get(k), str):
                info[k] = ctx["recorded"](info[k])
        for p in info.get("parents") or []:
            if isinstance(p, dict) and isinstance(p.get("file"), str):
                p["file"] = ctx["recorded"](p["file"])
        anarchy_ui._state["info"][(ctx["prompt"] or "").strip()] = (info, arrays["cond"])

    def condsrc_export(job):
        """condmix's resolved anchor tensors for this job's cond (one row per anchor, parse order)."""
        c = (job.get("_plan") or {}).get("cond")       # the worker's live plan object (condmix keyed on it)
        hit = condsrc._banks.get(id(c)) if isinstance(c, dict) else None
        if hit is None or hit[0] is not c:
            return None, {}
        bags = [np.zeros((256, 768), np.float16) if b is None else np.asarray(b, np.float16).reshape(256, 768)
                for b in hit[3]]
        return {"n": len(bags), "none": [i for i, b in enumerate(hit[3]) if b is None]}, \
            {"bags": np.stack(bags) if bags else np.zeros((0, 256, 768), np.float16)}

    def condsrc_restore(js, arrays, ctx):
        """Register the box's tensors under the Mac's own plan object, which is what
        condsrc.install's persist.save wrapper looks up (by identity)."""
        plan = ctx.get("seg")
        c = plan.get("cond") if isinstance(plan, dict) else None
        if not js or not isinstance(c, dict) or "bags" not in arrays:
            return
        import condmix
        p = condmix.parse(c)
        if p is None or len(p["anchors"]) != int(js["n"]):
            print(f"[engine] condsrc capture: {js['n']} tensors for {0 if p is None else len(p['anchors'])} anchors,"
                  " bank not written", flush=True)
            return
        none = set(js.get("none") or [])
        bags = [None if i in none else arrays["bags"][i] for i in range(int(js["n"]))]
        condsrc.remember(c, p["anchors"], p["curves"], bags)

    register("anarchy", anarchy_export, anarchy_restore)
    register("condsrc", condsrc_export, condsrc_restore)
