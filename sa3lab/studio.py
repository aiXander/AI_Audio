"""SA3 Studio: a dark, polished skin over upstream's MLX gradio UI.

Upstream `optimized/mlx/scripts/sa3_gradio.py` stays untouched (local edits would
conflict on `git pull`). This launcher imports it, then before `build_ui` runs:

- swaps the header / section / footer markdown for our own HTML,
- relabels a few controls (labels, placeholders, info lines) by wrapping the
  component constructors, so every event wiring upstream stays intact,
- tags the output-column HTML slots with classes so CSS/JS can target them,
- renders the spectrogram at full HD (hispec.py) instead of upstream's ~16 col/s,
- persists every render's entry as a sidecar and restores the last renders on
  page load (persist.py), since upstream keeps history only in session state,
  and prefills the prompt box with the last render's prompt,
- defaults Duration to 120 s (the blind-bench winner, see main()),
- adds a Stop button (shown only while a render runs) that interrupts the
  MLX job between sampler steps (see the "stop" block below),
- turns every render into a labelled dataset row (library.py): the sidecar gets
  a "gen" block with everything needed to regenerate it and a "label" block
  (0-10 score + free-text tags) that the per-clip label strip saves instantly,
- serves a live progress estimate (library.py) from past render speeds and
  the sampler's real step count,
- lays out one sticky top line (logo + a centred Generate / Stop / auto-play
  bar) above 40/60 columns (see _layout),
- injects a dark theme, CSS, fonts and a small JS layer at `launch()`
  (gradio 6 takes theme/css/js/head there), and forces localhost-only,
  no share link.

Run with the MLX venv:  sa3lab/studio  [--dit medium] [--port 7860]
"""
from __future__ import annotations

import functools
import inspect
import json
import sys
import threading
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

HISTORY_ON_LOAD = 10   # renders restored on page load (current + previous)

# ── relabels: upstream label → (new label, info line or None) ──────────────
RELABEL = {
    "DiT model": ("Model", None),
    "Decoder (codec)": ("Codec", None),
    "Prompt": ("Prompt", None),
    "Seed (optional)": ("Seed", None),
    "Seconds": ("Duration (s)", None),
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
    "Seed (optional)": "random",
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
# thread anyway. So Stop is also cooperative: the sampler checks a flag before
# every step (and once more before decoding) and raises `Stopped`; nothing is
# saved, and a new render waits at most one sampler step for the MLX thread.
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

        def run(*a, **kw):          # executes on the MLX owner thread
            _stop["run"] = epoch
            library.progress_start(bound)
            try:
                _check_stop()       # a stopped chain's pregen dies before T5
                return fn(*a, **kw)
            finally:
                _stop["run"] = None
                library.progress_end()
        res = orig_call(run, *a, **kw)
        try:
            _tl.gen = library.gen_record(bound, res[1] if isinstance(res, tuple) else None)
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

    orig_dec = up.get_decoder

    def get_decoder(*a, **kw):
        _check_stop()
        library.progress_phase("decode")
        return orig_dec(*a, **kw)
    up.get_decoder = get_decoder


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

        def wrapped(*a, _orig=orig, _new=bf.name != "pregen"):
            if _new:
                _stop["epoch"] += 1
            _tl.epoch = _stop["epoch"]
            _tl.gen = None
            slot = "current" if _new else "queued"
            before = (a[-1].get(slot) or {}).get("key") if isinstance(a[-1], dict) else None
            out = _orig(*a)
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
    with demo:   # cancels also drops renders still waiting in gradio's queue
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
        prefill = gr.update(value=last) if last and not (prompt or "").strip() else gr.update()
        return (render_player(entries[0]), entries[0].get("timing", ""),
                up.render_history(state["history"]), state, prefill)

    with demo:
        demo.load(restore, inputs=[st, prompt_box],
                  outputs=[slot["sa3-player"], slot["sa3-timing"], slot["sa3-history"], st,
                           prompt_box])


# ── layout: centred action bar over 40/60 columns ──────────────────────
PREFS = Path(up.OUTPUT_DIR) / ".studio_prefs.json"


def _prefs() -> dict:
    try:
        return json.loads(PREFS.read_text())
    except Exception:
        return {}


def _layout(demo):
    """40/60 columns (controls / output); Generate + Stop + the auto-play toggle move out of the
    output column into a centred bar, on one sticky top line with the header. Blocks are re-parented
    in the tree before launch (gradio builds its layout config at launch)."""
    blocks = list(demo.blocks.values())
    out_col = next(b for b in blocks if isinstance(b, gr.Column) and b.elem_id == "sa3-out")
    main_row = out_col.parent
    for c in main_row.children:          # 40 / 60: controls left, output right
        if isinstance(c, gr.Column):
            c.scale = 3 if c is out_col else 2
    gen = next(b for b in blocks if isinstance(b, gr.Button) and b.elem_id == "sa3-generate")
    stop = next(b for b in blocks if isinstance(b, gr.Button) and b.elem_id == "sa3-stop")
    opts = next(b for b in blocks if isinstance(b, gr.CheckboxGroup) and b.label == "Playback")

    with demo:
        with gr.Row(elem_id="sa3-actions") as bar:
            autoplay = gr.Checkbox(label="Auto-play when ready", value=True,
                                   elem_id="sa3-autoplay", container=False, scale=0)

        # the toggle mirrors "Auto-play" in Playback & files (both ways, user edits only)
        def from_toggle(on, cur):
            cur = [o for o in (cur or []) if o != "Auto-play"] + (["Auto-play"] if on else [])
            PREFS.write_text(json.dumps(_prefs() | {"autoplay": bool(on)}))
            return gr.update(value=cur)
        autoplay.input(from_toggle, inputs=[autoplay, opts], outputs=[opts],
                       queue=False, show_progress="hidden")

        def from_opts(cur):
            on = "Auto-play" in (cur or [])
            PREFS.write_text(json.dumps(_prefs() | {"autoplay": on}))
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
    # header (logo + title) and the action bar share one sticky top strip
    header = next(b for b in blocks if isinstance(b, gr.HTML) and "sa3-header" in (b.elem_classes or []))
    with demo:
        with gr.Row(elem_id="sa3-top", equal_height=False) as top:
            pass
    root = header.parent
    for b in (bar, top):
        b.parent.children.remove(b)
    root.children.insert(root.children.index(header), top)
    top.parent = root
    root.children.remove(header)
    top.children[:] = [header, bar]
    header.parent = bar.parent = top


def main():
    # patch component constructors before upstream's build_ui runs
    for cls in (gr.Dropdown, gr.Textbox, gr.Slider, gr.Audio, gr.CheckboxGroup, gr.Radio):
        _wrap_init(cls, _relabel)
    _wrap_init(gr.Audio, _audio_drop)
    _wrap_init(gr.Accordion, _accordion)
    _wrap_init(gr.HTML, _html)
    _wrap_init(gr.Button, _button)
    _add_stop_button()
    gr.Markdown = _markdown_factory(gr.Markdown)

    up.render_spectrogram_png = hispec.render_spectrogram_png  # sa3_gradio imported it by name
    library.init(up)
    library.install_routes()
    _install_stop_hooks()
    orig_render_player = up.render_player   # new entries are persisted in _wire_handlers
    theme = build_theme()
    out_real = str(up.OUTPUT_DIR.resolve())
    orig_launch = gr.Blocks.launch

    def launch(self, *a, **kw):
        _add_restore_on_load(self, orig_render_player)
        _wire_handlers(self)
        _layout(self)
        kw.update(share=False, server_name="127.0.0.1", theme=theme, css=CSS, js=JS)
        kw["allowed_paths"] = list(kw.get("allowed_paths") or []) + [out_real]
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
