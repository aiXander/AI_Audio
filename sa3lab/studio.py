"""SA3 Studio: a dark, polished skin over upstream's MLX gradio UI.

Upstream `optimized/mlx/scripts/sa3_gradio.py` stays untouched (local edits would
conflict on `git pull`). This launcher imports it, then before `build_ui` runs:

- swaps the header / section / footer markdown for our own HTML,
- relabels a few controls (labels, placeholders, info lines) by wrapping the
  component constructors, so every event wiring upstream stays intact,
- tags the output-column HTML slots with classes so CSS/JS can target them,
- renders the newest clip as the first card of the history list (_row_player:
  upstream's history-row markup, main-player audio behaviour kept), with the
  whole output column scrolling as one list (the page itself never scrolls: controls and
  output each scroll on their own, studio.css + studio.js mountRowTop; studio.css hides the "Output"
  heading and upstream's speed / seed stats line); hitting its bottom appends 10 older renders at a time (studio.js
  mountMore, rows from /sa3/library); a LoRA badge on any render that had an
  active LoRA lists them on hover (studio.js loraChip, gen.loras via
  /sa3/label); resting 0.5 s on a clip's "settings" button shows every
  setting that made it (studio.js showSettings, /sa3/settings),
  each card's caption is the prompt alone (click it to copy, studio.js), and
  the sidebar icon left of the logo (or "\\") collapses the controls column for
  a full-width list (studio.js mountFocus),
- renders the spectrogram at full HD (hispec.py) instead of upstream's ~16 col/s,
- persists every render's entry as a sidecar and restores the last renders on
  page load (persist.py), since upstream keeps history only in session state,
  and prefills the prompt box with the last render's prompt,
- defaults Duration to 120 s (the blind-bench winner, see main()),
- adds a Stop button (shown only while a render runs) that interrupts the
  MLX job between sampler steps (see the "stop" block below),
- queues every Generate click as its own job; the Queue button (top right,
  studio.js mountQueue) opens a modal listing the running + waiting jobs with
  a settings preview, to delete, drag-reorder or skip them (renderq.py edits
  gradio's queue in place),
- turns every render into a labelled dataset row (library.py): the sidecar gets
  a "gen" block with everything needed to regenerate it and a "label" block
  (0-10 score + free-text tags) that the per-clip label strip saves instantly,
- serves a live progress estimate (library.py) from past render speeds and
  the sampler's real step count,
- restores every setting of any past render into the controls with one click
  (see _add_restore_settings); the same path keeps the live control state
  (model, prompt, sampling, a2a / inpaint inputs, LoRA slots) in
  .studio_prefs.json and puts it back on every page load, so a server restart
  loses nothing,
- puts the prompt first at full width, Seed (empty = random) next to Model /
  Codec (_layout),
- lays out one sticky top line (logo left, Generate / Stop dead centre at
  full bar height, auto-play + Library right) above 40/60 columns (see _layout); resting the cursor 0.5 s in the gap
  between them shows a drag bar to resize (studio.js mountSplit, kept in
  localStorage, double-click resets),
- replaces the LoRA panel with the UI kit's LoRA stack (ui/lora.js, the same editor
  as the Composer's Render settings; mounted by lora_link.js, synced through a
  hidden JSON box, see _add_restore_settings and loras.py): a pick from
  stable-audio-3/loras/ or a dropped file, strength, the From/To step window, an
  "N active" badge on the accordion header; upstream's 3 slots stay hidden as the
  values /generate reads,
- adds the Promptless panel under the prompt (anarchy_ui.py: an `anarchy: …` prompt is a
  sampled conditioning spec, rendered through the normal Generate path),
- makes seeds length-stable (noise.py: every noise draw is cropped from one fixed
  4608-frame canvas, so a seed keeps its opening when Length changes),
- adds an Extend button to every clip (extend.py/.js: restore the clip's settings with
  Length + N s and the clip as inpaint reference, then Generate),
- adds a DJ button to every clip (djview.py/.js: full screen, the dadaspec scrolling right to
  left under a centre playhead like a Pioneer CDJ, one slider for the seconds in view),
- turns the Inpaint and Audio → audio inputs into a dadaspec segment editor (segments.py/.js:
  click-drag spans on the input's spectrogram; inpaint regenerates every span, a2a gives
  each span its own noise level),
- blends several conditioning anchors per latent frame when the segments box carries a
  "cond" key (condmix.py: the composer's gap crossfades, sa3lab/composer/), and stamps a
  composer take's lineage as `gen.composite` (and its anchors' tensors beside it as
  `<audio>.conds.npz`, condsrc.py, so reusing the take never needs its sources),
- serves the Composer editor at /sa3/page/composer and the UI kit at /sa3/ui/* (composer/server.py:
  pieces cut from starred renders, gaps, beat alignment, takes rendered into this studio's queue),
- links the SA3 Arranger (arranger_link.py/.js: an Arranger button opens the song-sketch
  mini-DAW on :7861; its "Send to SA3" loads the sketch WAV as an a2a job through the same
  restore path, and the render's sidecar gets `gen.arranger`),
- makes the output folder switchable (outdir.py: a folder chip by the Library button picks
  the folder the history + Library show and new renders go to; queued jobs keep theirs),
- injects a dark theme, CSS, fonts and a small JS layer at `launch()`
  (gradio 6 takes theme/css/js/head there), and forces localhost-only,
  no share link.

Run with the MLX venv:  sa3lab/studio  [--dit medium] [--port 7860]
"""
from __future__ import annotations

import functools
import html as html_lib
import inspect
import re
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

AI_AUDIO = Path(__file__).resolve().parents[1]
MLX = AI_AUDIO / "stable-audio-3" / "optimized" / "mlx"
sys.path.insert(0, str(MLX))
sys.path.insert(0, str(MLX / "scripts"))

import gradio as gr  # noqa: E402

import sa3_gradio as up  # noqa: E402  (upstream UI module)

sys.path.insert(0, str(Path(__file__).parent))
import hispec  # noqa: E402  (full-HD dadaspec)
import library  # noqa: E402  (dataset sidecars, label routes, progress)
import persist  # noqa: E402  (history across reloads)
import renderq  # noqa: E402  (the render queue modal's routes)
import anarchy_ui  # noqa: E402  (the Promptless panel: anarchy_ui.js/.css + spec prompts)
import addons  # noqa: E402  (add-on browser assets merged into CSS/JS)
import djview  # noqa: E402  (full-screen CDJ-style view of a clip, /sa3/djspec)
import outdir  # noqa: E402  (switchable output folder: view + render destination)
import noise  # noqa: E402  (length-stable seeds: noise cropped from one fixed canvas)
import extend  # noqa: E402  (Extend button: re-render a clip longer, the clip kept as inpaint context)
import arranger_link  # noqa: E402  (Arranger button + "Send to SA3" from sa3lab/arranger/)
import segments  # noqa: E402  (spans drawn on the inpaint / a2a input's dadaspec → mask / σ map)
import condmix  # noqa: E402  (conditioning anchors blended per latent frame: the box's "cond")
import condsrc  # noqa: E402  (what a render was conditioned on: a multi-anchor render's bank)
import loras  # noqa: E402  (the loras/ library + the kit LoRA stack ↔ upstream's 3 slots)
from composer import server as composer_server  # noqa: E402  (the Composer page + its /sa3/composer/* routes)

HISTORY_ON_LOAD = 10   # renders restored on page load (current + previous)

# ── relabels: upstream label → (new label, info line or None) ──────────────
RELABEL = {
    "DiT model": ("Model", None),
    "Decoder (codec)": ("Codec", None),
    "Prompt": ("Prompt", None),
    "Seed (optional)": ("Seed", None),
    "Seconds": ("Length", None),
    "Steps": ("Steps", None),
    "CFG": ("CFG", None),
    "APG (only applies when CFG > 1)": ("APG", "Anti-oversaturation, only when CFG ≠ 1"),
    "sigma_max": ("Start noise σ", "1 = standard · <1 or >1 = creative drift"),
    "Negative prompt": ("Negative prompt", "Only acts when CFG ≠ 1"),
    "Guide audio — generation starts from its latents": ("Guide audio", None),
    "init_noise_level (1.0 = prompt, ~0.92 = fusion, 0 = input)":
        ("Noise level", "1.0 = ignore input · ~0.92 = fusion · lower keeps more of the input"),
    "Reference audio — kept bit-exact outside the range": ("Reference audio", "Kept bit-exact outside the range"),
    "Start (s)": ("From (s)", None),
    "End (s)": ("To (s)", None),
    "Playback Options": ("Playback", None),
    "Format": ("File format", None),
}
ACCORDION = {
    "Advanced": "Advanced sampling",
    "LoRA": "LoRA adapters",
    "Audio-to-audio (guide the whole clip)": "Audio → audio",
    "Inpainting (regenerate a span of reference audio)": "Inpaint a span",
    "Output": "Playback & files",
}
PLACEHOLDER = {
    "Prompt": "Describe the sound. Tag-style works best, e.g.  "
              "TrackType: Music, Genre: Dub Techno, BPM: 120, Moods: Hypnotic",
}
# the five gr.HTML slots of the output column, in creation order
OUT_SLOTS = ["sa3-player", "sa3-timing", "sa3-error", "sa3-queued", "sa3-history"]


def _wrap_init(cls, hook):
    orig = cls.__init__
    accepted = set(inspect.signature(orig).parameters)

    @functools.wraps(orig)
    def init(self, *a, **kw):
        a, kw = hook(list(a), kw)
        kw = {k: v for k, v in kw.items() if k in accepted}  # e.g. Audio has no `info`
        orig(self, *a, **kw)
    cls.__init__ = init


def _relabel(a, kw):
    lab = kw.get("label")
    if lab in PLACEHOLDER:
        kw["placeholder"] = PLACEHOLDER[lab]
    if lab in RELABEL:
        new, info = RELABEL[lab]
        kw["label"] = new
        if info and "info" not in kw:
            kw["info"] = info
    if lab == "Prompt":
        kw["lines"] = 3
    return a, kw


def _cls(kw, c):
    kw["elem_classes"] = list(kw.get("elem_classes") or []) + [c]


def _lora_tags(a, kw):
    """Upstream's LoRA slot widgets stay as the hidden data carriers (lora_link.css hides
    the groups that hold a .sa3-lora-file, and the add button); the kit stack is the UI."""
    lab = kw.get("label") or ""
    if re.match(r"LoRA \d \(\.safetensors\)$", lab):            # gr.File
        _cls(kw, "sa3-lora-file")
    elif lab.startswith("…or pick from loras/"):              # gr.Dropdown
        kw["allow_custom_value"] = True     # any path under loras/, even one added after launch (loras.py)
    elif (a[:1] or [kw.get("value")])[0] == "+ Add LoRA":
        _cls(kw, "sa3-lora-add")
    return a, kw


def _lora_library(dit_name: str) -> list:
    """Replaces upstream's _lora_dd_choices: every .safetensors under
    stable-audio-3/loras/ (loras.library: subfolders included, natural sort, same
    list for every model)."""
    return [("from loras/ folder…", up._DD_NONE)] + loras.library()


def _accordion(a, kw):
    if a and a[0] in ACCORDION:
        a[0] = ACCORDION[a[0]]
    return a, kw


_slot = {"i": 0}


def _html(a, kw):
    # tag the output column's 5 bare gr.HTML() slots (header/footer pass elem_classes)
    if not a and "value" not in kw and "elem_classes" not in kw and _slot["i"] < len(OUT_SLOTS):
        kw["elem_classes"] = [OUT_SLOTS[_slot["i"]]]
        _slot["i"] += 1
    return a, kw


def _audio_drop(a, kw):
    # left-side audio inputs become drop targets for clips dragged from the output side
    kw["elem_classes"] = list(kw.get("elem_classes") or []) + ["sa3-drop"]
    return a, kw


def _button(a, kw):
    if kw.get("elem_id") == "sa3-generate":
        a = ["Generate"]
        kw["elem_classes"] = ["sa3-gen"]
    return a, kw


def _add_stop_button():
    """Create the Stop button right after Generate (same column), CSS-hidden until busy."""
    orig = gr.Button.__init__

    @functools.wraps(orig)
    def init(self, *a, **kw):
        orig(self, *a, **kw)
        if kw.get("elem_id") == "sa3-generate":
            gr.Button("Stop", elem_id="sa3-stop", variant="secondary", size="lg")
    gr.Button.__init__ = init


# ── stop ───────────────────────────────────────────────────────────────────
# gradio's `cancels=` clears the UI and drops queued renders, but can't interrupt
# a running sync handler, and the MLX work runs on upstream's single owner
# thread anyway. So Stop is also cooperative: a flag is checked before every
# sampler step, before the encoder / a DiT load, before every decoder chunk and
# once after the render, raising `Stopped`; nothing is saved, and the MLX thread
# lets go within one sampler step or decoder chunk (worst case: one step of a
# long render at CFG ≠ 1, ~10 s; text encoding, ≤ 6 s, has no check).
#
# Every Generate/auto-advance click starts a new epoch; the chained pregen
# (Infinite Radio) inherits the epoch of the click that spawned it. Stop marks
# every epoch up to now as stopped, so it kills the running render AND the
# follow-up pregen, while a Generate clicked right after Stop runs normally.
class Stopped(Exception):
    pass


_stop = {"epoch": 0, "stopped_upto": 0, "run": None}
_tl = threading.local()


def _check_stop():
    run = _stop["run"]
    if run is not None and _stop["stopped_upto"] >= run:
        raise Stopped("generation stopped")


# SAME-L's decode routinely peaks above 0 dBFS on dense material (+0.1 … +0.8 dB on 21 of 23
# long renders checked 2026-11-07, composer takes included, even when the a2a/inpaint input was
# limited to −1 dBFS), and upstream saves with np.clip → int16: hard digital clipping, then MP3
# encoding turns the flat tops into inter-sample overs (+1 dBTP). So every render passes the
# composer's master limiter (mixdsp.apply_master: alimiter, 5 ms attack / 50 ms release,
# lookahead) at OUT_CEILING_DB before it is saved; renders already below it are untouched.
# The raw decode peak lands in the sidecar as gen.peak_guard.
OUT_CEILING_DB = -1.0


def _peak_guard(res):
    """(audio (2, T), timings) → same with peaks limited to OUT_CEILING_DB, plus a sidecar note."""
    try:
        import numpy as np
        import mixdsp
        y = res[0] if isinstance(res, tuple) and res else None
        if y is None:
            return res, None
        y = np.asarray(y, dtype=np.float32)
        if y.ndim != 2 or y.shape[0] != 2 or not y.size or not np.isfinite(y).all():
            return res, None
        pk = float(np.abs(y).max())
        info = {"raw_peak_db": round(20 * np.log10(max(pk, 1e-9)), 2), "ceiling_db": OUT_CEILING_DB,
                "limited": pk > 10 ** (OUT_CEILING_DB / 20)}
        if info["limited"]:
            res = (mixdsp.apply_master(y, {"limit": True, "ceiling": OUT_CEILING_DB}), *res[1:])
        return res, info
    except Exception as e:          # never lose a render over the safety limiter
        print(f"[sa3lab] peak guard failed: {e}", flush=True)
        return res, None


def _install_stop_hooks():
    """Stop checks, plus (library.py) live progress and the regen-metadata capture:
    run_generation's exact arguments + measured timings land in `_tl.gen` on the
    handler thread, and _wire_handlers attaches them to the new entry."""
    orig_call = up.mlx_call
    gen_sig = inspect.signature(up.run_generation)

    def mlx_call(fn, *a, **kw):
        if fn is not up.run_generation:
            return orig_call(fn, *a, **kw)
        epoch = getattr(_tl, "epoch", None)
        bound = gen_sig.bind(*a, **kw)
        bound.apply_defaults()

        seg = segments.current()    # this job's drawn spans (handler thread → MLX thread)

        def run(*a, **kw):          # executes on the MLX owner thread
            _stop["run"] = epoch
            library.progress_start(bound)
            try:
                _check_stop()       # a stopped chain's pregen dies before T5
                with segments.active(seg, bound):
                    out = fn(*a, **kw)
                _check_stop()       # stopped during the last decode chunk / the kept-audio paste: not saved
                return out
            finally:
                _stop["run"] = None
                library.progress_end()
        res = orig_call(run, *a, **kw)
        res, guard = _peak_guard(res)
        try:
            _tl.gen = library.gen_record(bound, res[1] if isinstance(res, tuple) else None)
            if guard:
                _tl.gen["peak_guard"] = guard
        except Exception as e:      # never break a generation over bookkeeping
            print(f"[sa3lab] gen record failed: {e}", flush=True)
        return res
    up.mlx_call = mlx_call

    orig_sample = up.sample_flow_pingpong

    def sample(*a, before_step=None, **kw):
        def bs(i):
            _check_stop()
            library.progress_step(i)
            if before_step is not None:
                before_step(i)
        return orig_sample(*a, before_step=bs, **kw)
    up.sample_flow_pingpong = sample

    # The decode is 18-66 s of a long render, built by decode_chunked as one lazy MLX graph and
    # evaluated in one go, so a Skip/Stop pressed during it used to wait the whole decode out.
    # Each decoder call (one chunk, ~1-2 s) now evaluates on its own and checks the flag first:
    # same ops, same output, just a sync point per chunk.
    class _StoppableDecoder:
        def __init__(self, model):
            self._m = model

        def __call__(self, *a, **kw):
            _check_stop()
            out = self._m(*a, **kw)
            up.mx.eval(out)
            return out

        def __getattr__(self, k):
            return getattr(self._m, k)

    orig_dec = up.get_decoder

    def get_decoder(*a, **kw):
        _check_stop()
        library.progress_phase("decode")
        model, *rest = orig_dec(*a, **kw)
        return (_StoppableDecoder(model), *rest)
    up.get_decoder = get_decoder

    # the other long stretches without a sampler step: SAME-L encode of an a2a / inpaint input
    # (up to ~10 s) and a DiT load for a new length (~3 s) each check once before starting
    for name in ("get_encoder", "get_dit"):
        def checked(*a, _orig=getattr(up, name), **kw):
            _check_stop()
            return _orig(*a, **kw)
        setattr(up, name, checked)

    # every render gets a uuid: upstream names files prompt[.cfg…].{seed}, so re-rendering
    # the same prompt + seed (e.g. with a different LoRA) silently overwrote the earlier clip
    # and its label. Filename gets the first 8 hex chars, the sidecar's gen block the full id.
    orig_vb = up.verbose_basename

    def verbose_basename(*a, **kw):
        uid = uuid.uuid4().hex
        if getattr(_tl, "gen", None) is not None:   # same handler thread, set just before
            _tl.gen["uid"] = uid
        return f"{orig_vb(*a, **kw)}.{uid[:8]}"
    up.verbose_basename = verbose_basename


def _wire_handlers(demo):
    """Wrap generate/promote/pregen: tag them with stop epochs, attach the captured
    regen metadata to the entry each one produced, and persist presented entries.
    Also hooks the Stop button up."""
    gen_fns = []
    for bf in demo.fns.values():
        if bf.name not in ("generate", "promote", "pregen"):
            continue
        gen_fns.append(bf)
        orig = bf.fn

        if bf.name == "generate":   # every click queues a job (gradio's default drops clicks while one runs)
            bf.trigger_mode = "multiple"

        def wrapped(*a, _orig=orig, _new=bf.name != "pregen", _gen=bf.name == "generate"):
            if _new:
                _stop["epoch"] += 1
            _tl.epoch = _stop["epoch"]
            _tl.gen = None
            if _gen:                # the Queue modal's "now rendering" row (renderq.py)
                renderq.running = {"id": _tl.epoch, "t": time.time(), "preview": renderq.preview(list(a[:-1]))}
            slot = "current" if _new else "queued"
            before = (a[-1].get(slot) or {}).get("key") if isinstance(a[-1], dict) else None
            try:
                out = _orig(*a)
            finally:
                if _gen and renderq.running and renderq.running["id"] == _tl.epoch:
                    renderq.running = None
            state = out[-1] if isinstance(out, tuple) else None
            entry = state.get(slot) if isinstance(state, dict) else None
            if entry and entry.get("key") != before:
                if _tl.gen and "gen" not in entry:
                    entry["gen"] = _tl.gen
                if _new:            # presented now: becomes a dataset row
                    persist.save(entry)
            return out
        functools.update_wrapper(wrapped, orig)
        bf.fn = wrapped

    stop_btn = next(b for b in demo.blocks.values()
                    if isinstance(b, gr.Button) and b.elem_id == "sa3-stop")

    def stop():
        _stop["stopped_upto"] = _stop["epoch"]
        if renderq.running:
            renderq.running["stopping"] = True

    def skip(epoch):                # Queue modal: stop only the running job, later ones still run
        _stop["stopped_upto"] = max(_stop["stopped_upto"], epoch)
        if renderq.running and renderq.running["id"] == epoch:
            renderq.running["stopping"] = True   # the modal shows "stopping…" until it lets go
    renderq.skip_hook = skip
    library.waiting_fn = lambda: renderq.n_waiting(demo)   # "+N queued" counts gradio's real queue
    with demo:   # cancels also drops renders still waiting in gradio's queue (the whole batch)
        stop_btn.click(stop, queue=False, show_progress="hidden",
                       cancels=[bf.get_config() for bf in gen_fns])


HEADER = """
<div class="sa3h">
  <div class="sa3h-brand">
    <svg class="sa3h-logo" viewBox="0 0 40 40" aria-hidden="true">
      <defs><linearGradient id="sa3g" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#9b8cff"/><stop offset="1" stop-color="#ff6fb5"/></linearGradient></defs>
      <rect width="40" height="40" rx="11" fill="url(#sa3g)"/>
      <g fill="#0b0b10">
        <rect x="8" y="17" width="3" height="6" rx="1.5"/><rect x="13" y="12" width="3" height="16" rx="1.5"/>
        <rect x="18" y="8" width="3" height="24" rx="1.5"/><rect x="23" y="14" width="3" height="12" rx="1.5"/>
        <rect x="28" y="18" width="3" height="4" rx="1.5"/>
      </g>
    </svg>
    <div>
      <div class="sa3h-name">SA3 Studio</div>
      <div class="sa3h-tag">Stable Audio 3 · MLX · local</div>
    </div>
  </div>
</div>
"""

FOOTER = """
<div class="sa3f">
  <span>Renders → <code>AI_Audio/outputs/sa3-gradio/</code></span>
  <span>DiT fp16 · codecs fp32</span>
  <span><kbd>⌘</kbd><kbd>↵</kbd> generate</span>
</div>
"""


def _markdown_factory(orig_md):
    def md(value=None, *a, **kw):
        v = value or ""
        if v.startswith("# SA3 MLX"):
            return gr.HTML(HEADER, elem_classes=["sa3-header"])
        if v.strip() == "**Output**":
            return gr.HTML('<div class="sa3-sec">Output</div>', elem_classes=["sa3-sec-wrap"])
        if "WAVs saved under" in v:
            return gr.HTML(FOOTER, elem_classes=["sa3-footer"])
        return orig_md(value, *a, **kw)
    return md


# ── theme ──────────────────────────────────────────────────────────────────
ACCENT = "#9b8cff"
GRAD = "linear-gradient(135deg, #8a7bff 0%, #b67cff 50%, #ff6fb5 100%)"


def build_theme():
    t = gr.themes.Base(
        primary_hue=gr.themes.colors.violet,
        neutral_hue=gr.themes.colors.zinc,
        radius_size=gr.themes.sizes.radius_lg,
        font=[gr.themes.GoogleFont("Inter"), "-apple-system", "BlinkMacSystemFont", "sans-serif"],
        font_mono=[gr.themes.GoogleFont("JetBrains Mono"), "ui-monospace", "SFMono-Regular", "monospace"],
    )
    dark = dict(
        body_background_fill="#09090d",
        body_text_color="#e9e9f1",
        body_text_color_subdued="#8b8b9c",
        background_fill_primary="#111117",
        background_fill_secondary="#0d0d12",
        block_background_fill="#111117",
        block_border_color="rgba(255,255,255,0.06)",
        block_label_text_color="#a3a3b5",
        block_title_text_color="#c9c9d6",
        block_info_text_color="#71717f",
        block_label_background_fill="transparent",
        border_color_primary="rgba(255,255,255,0.08)",
        border_color_accent=ACCENT,
        color_accent=ACCENT,
        color_accent_soft="rgba(155,140,255,0.15)",
        input_background_fill="#0a0a0f",
        input_background_fill_focus="#0a0a0f",
        input_border_color="rgba(255,255,255,0.08)",
        input_border_color_focus="rgba(155,140,255,0.6)",
        input_placeholder_color="#5b5b6b",
        button_primary_background_fill=GRAD,
        button_primary_background_fill_hover=GRAD,
        button_primary_text_color="#ffffff",
        button_primary_border_color="transparent",
        button_secondary_background_fill="#1a1a23",
        button_secondary_background_fill_hover="#22222d",
        button_secondary_text_color="#d4d4de",
        button_secondary_border_color="rgba(255,255,255,0.08)",
        slider_color=ACCENT,
        checkbox_background_color="#0a0a0f",
        checkbox_background_color_selected=ACCENT,
        checkbox_border_color="rgba(255,255,255,0.15)",
        checkbox_border_color_selected=ACCENT,
        checkbox_label_background_fill="#15151c",
        checkbox_label_background_fill_selected="rgba(155,140,255,0.15)",
        checkbox_label_border_color="rgba(255,255,255,0.06)",
        checkbox_label_text_color="#d4d4de",
        panel_background_fill="#111117",
        panel_border_color="rgba(255,255,255,0.06)",
        shadow_drop="none",
        block_shadow="none",
        error_background_fill="rgba(255,90,110,0.08)",
        error_border_color="rgba(255,90,110,0.35)",
        error_text_color="#ff8a9a",
    )
    shared = dict(
        block_radius="14px",
        block_border_width="1px",
        block_padding="14px 16px",
        input_radius="10px",
        layout_gap="12px",
        block_label_text_size="12px",
        block_label_text_weight="500",
        block_title_text_weight="500",
        button_large_radius="14px",
        button_large_text_size="16px",
        button_large_text_weight="650",
        button_large_padding="16px",
    )
    valid = set(vars(t))
    kw = {k: v for k, v in shared.items() if k in valid}
    for k, v in dark.items():  # same look in both modes: we never want light
        for name in (k, k + "_dark"):
            if name in valid:
                kw[name] = v
    return t.set(**kw)


CSS = (Path(__file__).with_name("studio.css")).read_text()
# upstream's own rules (its Blocks(css=...) is deprecated in gradio 6, so re-supply them)
CSS += ("#sa3-promote{display:none !important}"
        "#sa3-out{gap:6px !important}"
        "#sa3-out .html-container{padding:0 !important; margin:0 !important}")
JS = (Path(__file__).with_name("studio.js")).read_text()
CSS, JS = addons.assets(CSS, JS, "anarchy_ui", "outdir", "extend", "uiprefs", "arranger_link", "segments", "composer_link", "highlights_link", "strength_tl", "lora_link", "djview")


def _add_restore_on_load(demo, render_player):
    """On every page load, fill the empty session with the newest renders on disk
    and prefill the prompt box with the last render's prompt."""
    blocks = list(demo.blocks.values())
    st = next(b for b in blocks if isinstance(b, gr.State)
              and isinstance(b.value, dict) and "history" in b.value)
    slot = {c: next(b for b in blocks if isinstance(b, gr.HTML) and c in (b.elem_classes or []))
            for c in ("sa3-player", "sa3-timing", "sa3-history")}

    prompt_box = next(b for b in blocks if isinstance(b, gr.Textbox) and b.label == "Prompt")

    def restore(state, prompt):
        if state.get("current") is not None:   # session already has renders
            return gr.update(), gr.update(), gr.update(), state, gr.update()
        entries = persist.load_recent(up.OUTPUT_DIR, up, hispec.render_spectrogram_png,
                                      HISTORY_ON_LOAD)
        if not entries:
            return gr.update(), gr.update(), gr.update(), state, gr.update()
        state = {"current": entries[0], "queued": None, "history": entries[1:]}
        # prefill the last render's prompt, unless something is already typed
        last = entries[0].get("prompt") or ""
        prefill = (gr.update(value=last) if last and not (prompt or "").strip()
                   and "session" not in _prefs() else gr.update())   # a saved session wins
        return (render_player(entries[0]), entries[0].get("timing", ""),
                up.render_history(state["history"]), state, prefill)

    with demo:
        demo.load(restore, inputs=[st, prompt_box],
                  outputs=[slot["sa3-player"], slot["sa3-timing"], slot["sa3-history"], st,
                           prompt_box])


# ── restore: put every setting of a past render back into the controls ─────
_RESTORE_PREV = "__sa3_restore__"   # scratch lora-memory key, see restore()


def _add_restore_settings(demo):
    """"Use these settings" on every clip (studio.js calls window.sa3Restore(name)).
    JS writes the clip's file name into a CSS-hidden textbox via the first step's
    js, then restore() reads the sidecar (library.restore_settings) and returns
    updates for every control.

    The model switch is the subtle part: upstream's on_dit_change resets the
    codec to the model's default, clamps seconds and swaps the LoRA panel from a
    per-model memory. restore() calls that same function itself, with the
    restored LoRAs pre-seeded into the memory under the target model, then
    overrides codec + seconds. Setting the DiT dropdown re-fires on_dit_change
    from the browser; the wrapper below makes that call a no-op for codec and
    seconds when the model didn't really change (dit == prev_dit).

    LoRAs: the kit LoRA stack (ui/lora.js, mounted by lora_link.js) replaces upstream's slot
    widgets on screen; the 15 slot values stay the data carriers (loras.py), so /generate is
    unchanged. The stack's list lives in a hidden JSON box (#sa3-lora-data): the model switch
    and restore() write the incoming slots into it (the box is appended to on_dit_change's
    outputs), and the stack's edits fill the slots (lora_apply)."""
    fns = {bf.name: bf for bf in demo.fns.values()}
    gen_bf, dit_bf, vis_bf = fns["generate"], fns["on_dit_change"], fns.get("_sync_dd_vis")
    (dit_dd, decoder_dd, prompt, neg, seconds, steps, seed, cfg, apg, sigma_g, init_noise,
     a2a, inpaint, inp_s, inp_e, _opts, _fmt) = gen_bf.inputs[:17]
    lora_dds = gen_bf.inputs[17:32][1::5]
    # upstream builds the slots' Min / Max step sliders with maximum=default_steps (8) and only
    # raises it per browser session on a Steps change; gradio validates API calls against that
    # build-time maximum, so a Composer take at 12 steps (its own gradio_client session, LoRA
    # max step 12) failed "Value 12 is greater than maximum value 8". Bound them by Steps' own max.
    for c in [x for i in range(3) for x in gen_bf.inputs[17 + 5 * i + 3: 17 + 5 * i + 5]]:
        c.maximum = steps.maximum
    orig_dit = dit_bf.fn
    mem_idx = dit_bf.outputs.index(dit_bf.inputs[4])        # lora_mem's slot in the outputs
    with demo:
        lora_box = gr.Textbox(value="[]", elem_id="sa3-lora-data", container=False, show_label=False)
        kit = gr.HTML('<div id="sa3-lora-kit"></div>', elem_classes=["sa3-lora-kitblk"])
    acc = next(b for b in demo.blocks.values() if isinstance(b, gr.Accordion) and b.label == "LoRA adapters")
    kit.parent.children.remove(kit)     # the stack's mount point (lora_link.js): first in the LoRA panel
    acc.children.insert(0, kit)
    kit.parent = acc

    def lora_json(mem, dit_name, n_steps):
        """The LoRA list of `dit_name`'s slots in the (post-switch) per-model memory."""
        snap = (mem or {}).get(dit_name)
        return json.dumps(loras.from_slots(sum(snap["slots"], []), int(n_steps)) if snap else [])

    @functools.wraps(orig_dit)
    def on_dit_change(dit_name, cur_seconds, cur_steps, prev, *rest):
        out = list(orig_dit(dit_name, cur_seconds, cur_steps, prev, *rest))
        if dit_name == prev:        # programmatic re-set (restore), not a real switch
            out[0] = gr.update()
            out[1] = gr.update(maximum=up.MAX_SECONDS.get(dit_name, 120))
        return tuple(out) + (lora_json(out[mem_idx], dit_name, cur_steps),)
    dit_bf.fn = on_dit_change
    dit_bf.outputs = list(dit_bf.outputs) + [lora_box]

    n_out = len(dit_bf.outputs) + 14

    def restore(name, cur_dit, cur_sec, cur_steps, prev, mem, vis, *slots):
        sess = name == SESSION
        ext = None if sess else extend.parse(name)     # "Extend" on a clip (extend.py / extend.js)
        arr = None if sess or ext else arranger_link.parse(name)   # "Send to SA3" from the arranger
        note = None
        if arr:
            try:
                r, note = arranger_link.settings(*arr)
            except ValueError as e:
                raise gr.Error(f"Arranger: {e}")
        else:
            r = _prefs().get("session") if sess else library.restore_settings(ext[0] if ext else name)
        if r is None:
            if sess:                # nothing saved yet: leave the defaults
                return tuple(gr.update() for _ in range(n_out))
            raise gr.Error(f"No render named {(ext[0] if ext else name)!r}")
        if ext:
            try:
                r, note = extend.apply(r, *ext, library, up.MAX_SECONDS.get(
                    r["dit"] if r.get("dit") in up.DIT_CHOICES else cur_dit, 120))
            except ValueError as e:
                raise gr.Error(f"Can't extend: {e}")
        keep = gr.update()
        tgt = r["dit"] if r.get("dit") in up.DIT_CHOICES else cur_dit
        n_steps = int(r.get("steps", cur_steps))
        mem = dict(mem or {})
        if "loras" in r:            # seed the target model's LoRA memory with this render's adapters
            valid = {v for _, v in up._lora_dd_choices(tgt)}
            nslots, nvis = [], []
            for i in range(3):
                L = r["loras"][i] if i < len(r["loras"]) else None
                if L is None:
                    nslots.append([None, up._DD_NONE, 1.0, 1, n_steps])
                    nvis.append(False)
                    continue
                path = L["path"]
                dd = path if path in valid else up._DD_NONE
                f = None if dd != up._DD_NONE else (path if os.path.isfile(path) else None)
                lo, hi = L.get("steps") or (None, None)
                nslots.append([f, dd, float(L["strength"]), int(lo or 1), int(hi or n_steps)])
                nvis.append(True)
            mem[tgt] = {"vis": nvis, "slots": nslots}
        else:                       # legacy render: no LoRA record, keep the panel as is
            mem[tgt] = {"vis": list(vis), "slots": [list(slots[5 * i: 5 * i + 5]) for i in range(3)]}
        out = list(orig_dit(tgt, cur_sec, n_steps, cur_dit if tgt != cur_dit else _RESTORE_PREV,
                            mem, vis, *slots))
        out[mem_idx] = {k: v for k, v in out[mem_idx].items() if k != _RESTORE_PREV}
        out.append(lora_json(out[mem_idx], tgt, n_steps))   # lora_box, last of dit_bf.outputs
        max_s = up.MAX_SECONDS.get(tgt, 120)
        if r.get("decoder") in up.DECODER_CHOICES:
            out[0] = gr.update(value=r["decoder"])
        out[1] = gr.update(maximum=max_s, value=min(float(r.get("seconds", cur_sec)), max_s))
        a2a_on = r.get("mode") in ("audio-to-audio", "a2a+inpaint")
        rng = r.get("inpaint_range_sec")
        full = r["full"]
        extra = [
            gr.update(value=tgt),
            r.get("prompt", keep),
            r.get("negative_prompt", keep),
            int(r["steps"]) if "steps" in r else keep,
            str(r["seed"]) if "seed" in r else keep,
            float(r["cfg"]) if "cfg" in r else keep,
            float(r["apg"]) if "apg" in r else keep,
            *((r.get("sigma_max", keep), r.get("init_noise_level", keep)) if sess else
              (keep if a2a_on or "sigma_max" not in r else float(r["sigma_max"]),
               float(r["sigma_max"]) if a2a_on and "sigma_max" in r else keep)),
            r.get("a2a_audio") if full else keep,
            r.get("inpaint_audio") if full else keep,
            *((gr.update(value=float(rng[0]), maximum=float(r["seconds"])),     # the sliders' max follows
               gr.update(value=float(rng[1]), maximum=float(r["seconds"])))    # Length only after this update
              if ext else
              (float(rng[0]) if rng else (0 if full else keep),
               float(rng[1]) if rng else (0 if full else keep))),
            segments.restore_value(r, own=not (ext or arr)) if full else keep,
        ]
        missing = [k for k, on in (("guide audio", a2a_on and full and not r.get("a2a_audio")),
                                   ("inpaint audio", bool(rng) and not r.get("inpaint_audio"))) if on]
        if note:
            gr.Info(note)
        elif not sess:
            gr.Info("Settings restored" + ("" if full else " (old render: model, prompt, seed, CFG, length only)")
                    + (f" · missing {', '.join(missing)}" if missing else ""))
        return tuple(out + extra)

    with demo:
        name_box = gr.Textbox(elem_id="sa3-restore-name", container=False, show_label=False)
        btn = gr.Button("restore", elem_id="sa3-restore-btn")
        ev = btn.click(None, js="() => window.__sa3RestoreName || ''", outputs=[name_box], queue=False
                       ).then(restore, inputs=[name_box] + list(dit_bf.inputs),
                              outputs=list(dit_bf.outputs) + [dit_dd, prompt, neg, steps, seed, cfg, apg,
                                                              sigma_g, init_noise, a2a, inpaint, inp_s, inp_e,
                                                              segments.BOX],
                              queue=False, show_progress="hidden")
        # extend.js: an Extend click renders once restored (.success: never after a failed restore)
        ev.success(None, js="() => { if (window.sa3AfterRestore) window.sa3AfterRestore(); }")
        if vis_bf is not None:
            ev = ev.then(vis_bf.fn, inputs=[dit_dd], outputs=lora_dds, queue=False, show_progress="hidden")
        ev.then(None, js=up._JS_FIX_SLIDERS)

        # the LoRA stack's edits → upstream's slots (+ visibility, so save() and the hidden
        # widgets agree). Dropdown choices are rescanned so a fresh upload is a valid pick.
        lora_slots_all = list(gen_bf.inputs[17:32])

        def lora_apply(js, dit_name, n_steps):
            items = loras.clean(js, int(n_steps))
            ch = _lora_library(dit_name)
            vals = loras.to_slots(items, int(n_steps))
            for i in range(3):
                vals[5 * i + 1] = gr.update(choices=ch, value=vals[5 * i + 1])
            return vals + [[i < len(items) for i in range(3)]]
        lora_box.change(lora_apply, inputs=[lora_box, dit_dd, steps], outputs=lora_slots_all + [dit_bf.inputs[5]],
                        queue=False, show_progress="hidden", trigger_mode="always_last")

        # Session memory: every control (model, prompt, sampling, a2a / inpaint inputs,
        # LoRA slots) is snapshot into the prefs file on change and put back on page
        # load, so a server restart (devwatch) or a reload keeps the panel as it was.
        # `armed` stays False until the load-time restore is done, so the defaults the
        # fresh page starts with can't overwrite the saved session first.
        armed = gr.State(False)
        ev = demo.load(lambda: SESSION, outputs=[name_box], queue=False
                       ).then(restore, inputs=[name_box] + list(dit_bf.inputs),
                              outputs=list(dit_bf.outputs) + [dit_dd, prompt, neg, steps, seed, cfg, apg,
                                                              sigma_g, init_noise, a2a, inpaint, inp_s, inp_e,
                                                              segments.BOX],
                              queue=False, show_progress="hidden")
        if vis_bf is not None:
            ev = ev.then(vis_bf.fn, inputs=[dit_dd], outputs=lora_dds, queue=False, show_progress="hidden")
        ev.then(None, js=up._JS_FIX_SLIDERS).then(lambda: True, outputs=[armed], queue=False)

        ctl = [dit_dd, decoder_dd, prompt, neg, seconds, steps, seed, cfg, apg, sigma_g, init_noise,
               a2a, inpaint, inp_s, inp_e, segments.BOX]
        lora_vis, lora_slots = dit_bf.inputs[5], list(gen_bf.inputs[17:32])

        def save(is_armed, dit, dec, prm, ng, sec, stp, sd, cf, ap, sg, inn, a2a_v, inp_v, s0, s1, segv, vis, *sl):
            if not is_armed:
                return
            loras = []
            for i in range(3):
                f, dd, strength, lo, hi = sl[5 * i: 5 * i + 5]
                path = f or (dd if dd and dd != up._DD_NONE else None)
                if vis and i < len(vis) and vis[i] and path:
                    loras.append({"path": path, "strength": float(strength), "steps": [int(lo), int(hi)]})
            _set_pref(session={
                "dit": dit, "decoder": dec, "prompt": prm or "", "negative_prompt": ng or "",
                "seed": sd or "", "seconds": sec, "steps": stp, "cfg": cf, "apg": ap,
                "sigma_max": sg, "init_noise_level": inn, "a2a_audio": a2a_v or None,
                "inpaint_audio": inp_v or None,
                "inpaint_range_sec": [s0, s1], "segments": segv or "", "loras": loras, "full": True})
        gr.on([c.change for c in ctl + [lora_vis] + lora_slots], save,
              inputs=[armed] + ctl + [lora_vis] + lora_slots, queue=False,
              show_progress="hidden", trigger_mode="always_last")


# ── layout: centred action bar over 40/60 columns ──────────────────────
PREFS = Path(up.OUTPUT_DIR) / ".studio_prefs.json"   # autoplay + the saved control session
SESSION = "__session__"     # restore() name that means "the saved session", not a render
_prefs_lock = threading.Lock()


def _prefs() -> dict:
    try:
        return json.loads(PREFS.read_text())
    except Exception:
        return {}


def _set_pref(**kw) -> None:
    with _prefs_lock:
        PREFS.write_text(json.dumps(_prefs() | kw))


def _row_player(orig):
    """The newest clip is the first row of the history list, not a big player above it:
    same markup as upstream's small=True history row (audio | spectrogram + caption), so
    studio.js/css treat both alike, but with the main slot's own audio behaviour (autoplay,
    hotswap, radio, loop, auto-download), which upstream only wires into the big layout."""
    def render(entry, *, small=False, **kw):
        html = orig(entry, small=small, **kw)
        j, i = html.find(">") + 1, html.find("</audio>")   # end of the opening .blk tag / of <audio>
        if small or i < 0 or not html.endswith("</div>"):
            return html
        i += len("</audio>")
        audio, spec = html[j:i], html[i:-len("</div>")]
        cap = (f'<div style="font-size:0.8em; margin:2px 0; color:#888">'
               f'{html_lib.escape(entry["prompt"]) or "<i>(no prompt)</i>"}{up._neg_disp(entry)}'
               f' · seed {entry["seed"]}{up._meta_suffix(entry)}</div>')
        return (f'{html[:j]}<div style="display:flex; gap:8px; align-items:center">'
                f'<div style="flex:1; min-width:0">{audio}</div>'
                f'<div style="flex:1; min-width:0">{spec}</div></div>{cap}</div>')
    return render


def _layout(demo):
    """40/60 columns (controls / output); Generate + Stop move out of the output column into a
    centred bar, on one sticky top line with the header (left) and the auto-play toggle (right,
    next to studio.js's Library button). Blocks are re-parented
    in the tree before launch (gradio builds its layout config at launch)."""
    blocks = list(demo.blocks.values())
    out_col = next(b for b in blocks if isinstance(b, gr.Column) and b.elem_id == "sa3-out")
    main_row = out_col.parent
    for c in main_row.children:          # 40 / 60: controls left, output right
        if isinstance(c, gr.Column):
            c.scale = 3 if c is out_col else 2
            if c is not out_col:
                c.elem_id = "sa3-ctl"    # studio.js splitter resizes #sa3-ctl / #sa3-out
    gen = next(b for b in blocks if isinstance(b, gr.Button) and b.elem_id == "sa3-generate")
    stop = next(b for b in blocks if isinstance(b, gr.Button) and b.elem_id == "sa3-stop")
    opts = next(b for b in blocks if isinstance(b, gr.CheckboxGroup) and b.label == "Playback")

    with demo:
        with gr.Row(elem_id="sa3-actions") as bar:
            pass
        with gr.Row(elem_id="sa3-right") as right:   # auto-play + (studio.js) the Library button
            autoplay = gr.Checkbox(label="Autoplay", value=True,
                                   elem_id="sa3-autoplay", container=False, scale=0)

        # the toggle mirrors "Auto-play" in Playback & files (both ways, user edits only)
        def from_toggle(on, cur):
            cur = [o for o in (cur or []) if o != "Auto-play"] + (["Auto-play"] if on else [])
            _set_pref(autoplay=bool(on))
            return gr.update(value=cur)
        autoplay.input(from_toggle, inputs=[autoplay, opts], outputs=[opts],
                       queue=False, show_progress="hidden")

        def from_opts(cur):
            on = "Auto-play" in (cur or [])
            _set_pref(autoplay=on)
            return gr.update(value=on)
        opts.input(from_opts, inputs=[opts], outputs=[autoplay], queue=False,
                   show_progress="hidden")

        def load_pref(cur):
            on = _prefs().get("autoplay", True)
            cur = [o for o in (cur or []) if o != "Auto-play"] + (["Auto-play"] if on else [])
            return gr.update(value=on), gr.update(value=cur)
        demo.load(load_pref, inputs=[opts], outputs=[autoplay, opts], queue=False,
                  show_progress="hidden")

    for b in (gen, stop):
        b.parent.children.remove(b)
        b.parent = bar
    bar.children[:0] = [gen, stop]
    # the prompt is what gets edited most: first, full column width; Seed (empty = random)
    # joins Model / Codec in the row below it
    prompt = next(b for b in blocks if isinstance(b, gr.Textbox) and b.label == "Prompt")
    seed = next(b for b in blocks if isinstance(b, gr.Textbox) and b.label == "Seed")
    model = next(b for b in blocks if isinstance(b, gr.Dropdown) and b.label == "Model")
    def row_of(b):                      # gradio wraps inputs in a Form inside the Row
        while not isinstance(b, gr.Row):
            b = b.parent
        return b
    seed.parent.children.remove(seed)
    model.parent.children.append(seed)  # into the Form that holds Model / Codec
    seed.parent, seed.scale, seed.min_width = model.parent, 0, 110
    p_row, m_row = row_of(prompt), row_of(model)
    col = m_row.parent
    i, j = col.children.index(m_row), col.children.index(p_row)
    col.children[i], col.children[j] = p_row, m_row
    # header (logo + title) and the action bar share one sticky top strip
    header = next(b for b in blocks if isinstance(b, gr.HTML) and "sa3-header" in (b.elem_classes or []))
    with demo:
        with gr.Row(elem_id="sa3-top", equal_height=False) as top:
            pass
    root = header.parent
    for b in (bar, right, top):
        b.parent.children.remove(b)
    root.children.insert(root.children.index(header), top)
    top.parent = root
    root.children.remove(header)
    top.children[:] = [header, bar, right]
    header.parent = bar.parent = right.parent = top
    # the page never scrolls (studio.css: each column scrolls on its own), so the footer can't sit
    # below the columns: it closes the controls column instead
    footer = next((b for b in blocks if isinstance(b, gr.HTML) and "sa3-footer" in (b.elem_classes or [])), None)
    ctl = next((c for c in main_row.children if getattr(c, "elem_id", None) == "sa3-ctl"), None)
    if footer is not None and ctl is not None:
        footer.parent.children.remove(footer)
        ctl.children.append(footer)
        footer.parent = ctl


def main():
    # patch component constructors before upstream's build_ui runs
    for cls in (gr.Dropdown, gr.Textbox, gr.Slider, gr.Audio, gr.CheckboxGroup, gr.Radio):
        _wrap_init(cls, _relabel)
    _wrap_init(gr.Audio, _audio_drop)
    for cls in (gr.Slider, gr.File, gr.Dropdown, gr.Button):
        _wrap_init(cls, _lora_tags)
    up._lora_dd_choices = _lora_library    # dropdown lists stable-audio-3/loras/**, not loras/<model>/
    _wrap_init(gr.Accordion, _accordion)
    _wrap_init(gr.HTML, _html)
    _wrap_init(gr.Button, _button)
    _add_stop_button()
    gr.Markdown = _markdown_factory(gr.Markdown)

    up.render_spectrogram_png = hispec.render_spectrogram_png  # sa3_gradio imported it by name
    library.init(up)
    noise.install(library)
    extend.install(library)
    arranger_link.install(library)
    segments.install(up, library)       # sampler hook, gen.segments, restore passthrough, /sa3/inspec
    condmix.install(up)                 # DiT hook: the box's "cond" anchors (composer crossfades, morphs)
    condsrc.install(persist)            # a multi-anchor render's tensors → <audio>.conds.npz on save
    outdir.init(up, library)            # restores the saved active output folder
    library.install_routes()
    djview.install_routes()
    loras.install_routes()
    renderq.install_routes()
    anarchy_ui.install(up, persist)
    anarchy_ui.install_routes()
    outdir.install_routes()
    arranger_link.install_routes()
    composer_server.install_routes()    # /sa3/page/composer, the UI kit at /sa3/ui/*
    _install_stop_hooks()
    up.render_player = _row_player(up.render_player)   # newest clip = first list row
    orig_render_player = up.render_player   # new entries are persisted in _wire_handlers
    theme = build_theme()
    out_real = str(up.OUTPUT_DIR.resolve())
    orig_launch = gr.Blocks.launch

    def launch(self, *a, **kw):
        _add_restore_on_load(self, orig_render_player)
        _wire_handlers(self)
        segments.add_box(self)                   # before the restore wiring, which reads/writes it
        _add_restore_settings(self)
        _layout(self)
        outdir.install(self, persist, hispec.render_spectrogram_png, orig_render_player,
                       HISTORY_ON_LOAD)          # after _wire_handlers: wraps outside it
        segments.wire(self)                      # outermost: strips the spans box off the inputs
        kw.update(share=False, server_name="127.0.0.1", theme=theme, css=CSS, js=JS)
        kw["allowed_paths"] = list(kw.get("allowed_paths") or []) + [out_real, str(outdir.ROOT)]
        print("SA3 Studio → http://127.0.0.1:%s" % (kw.get("server_port") or 7860), flush=True)
        return orig_launch(self, *a, **kw)
    gr.Blocks.launch = launch

    argv = sys.argv[1:]
    if "--dit" not in argv:
        argv += ["--dit", "medium"]
    # Sampling defaults from the blind A/B bench (outputs/sa3_bench_quality, logged in the
    # research doc): 120 s renders scored best; CFG 3, the base:ARC merge and the
    # squeakfix decoder LoRA did not beat CFG 1 / 8 steps / stock SAME-L, so those stay.
    if "--default-seconds" not in argv:
        argv += ["--default-seconds", "120"]
    if "--port" not in argv:
        argv += ["--port", "7860"]
    if "--no-share" not in argv:
        argv += ["--no-share"]
    sys.argv = [sys.argv[0]] + argv
    up.main()


if __name__ == "__main__":
    main()
