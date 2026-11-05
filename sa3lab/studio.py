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
- injects a dark theme, CSS, fonts and a small JS layer at `launch()`
  (gradio 6 takes theme/css/js/head there), and forces localhost-only,
  no share link.

Run with the MLX venv:  sa3lab/studio  [--dit medium] [--port 7860]
"""
from __future__ import annotations

import functools
import inspect
import sys
from pathlib import Path

AI_AUDIO = Path(__file__).resolve().parents[1]
MLX = AI_AUDIO / "stable-audio-3" / "optimized" / "mlx"
sys.path.insert(0, str(MLX))
sys.path.insert(0, str(MLX / "scripts"))

import gradio as gr  # noqa: E402

import sa3_gradio as up  # noqa: E402  (upstream UI module)

sys.path.insert(0, str(Path(__file__).parent))
import hispec  # noqa: E402  (full-HD dadaspec)
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
      <div class="sa3h-tag">Stable Audio 3 · MLX on Apple Silicon · local only</div>
    </div>
  </div>
  <div class="sa3h-chips">
    <span>Text → audio</span><span>Audio → audio</span><span>Inpainting</span><span>LoRA</span>
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
    """On every page load, fill the empty session with the newest renders on disk."""
    blocks = list(demo.blocks.values())
    st = next(b for b in blocks if isinstance(b, gr.State)
              and isinstance(b.value, dict) and "history" in b.value)
    slot = {c: next(b for b in blocks if isinstance(b, gr.HTML) and c in (b.elem_classes or []))
            for c in ("sa3-player", "sa3-timing", "sa3-history")}

    def restore(state):
        if state.get("current") is not None:   # session already has renders
            return gr.update(), gr.update(), gr.update(), state
        entries = persist.load_recent(up.OUTPUT_DIR, up, hispec.render_spectrogram_png,
                                      HISTORY_ON_LOAD)
        if not entries:
            return gr.update(), gr.update(), gr.update(), state
        state = {"current": entries[0], "queued": None, "history": entries[1:]}
        return (render_player(entries[0]), entries[0].get("timing", ""),
                up.render_history(state["history"]), state)

    with demo:
        demo.load(restore, inputs=[st],
                  outputs=[slot["sa3-player"], slot["sa3-timing"], slot["sa3-history"], st])


def main():
    # patch component constructors before upstream's build_ui runs
    for cls in (gr.Dropdown, gr.Textbox, gr.Slider, gr.Audio, gr.CheckboxGroup, gr.Radio):
        _wrap_init(cls, _relabel)
    _wrap_init(gr.Audio, _audio_drop)
    _wrap_init(gr.Accordion, _accordion)
    _wrap_init(gr.HTML, _html)
    _wrap_init(gr.Button, _button)
    gr.Markdown = _markdown_factory(gr.Markdown)

    up.render_spectrogram_png = hispec.render_spectrogram_png  # sa3_gradio imported it by name

    # persist each newly presented render (the main player is rendered once per new entry)
    orig_render_player = up.render_player

    def render_player(entry, *a, **kw):
        if not kw.get("small"):
            persist.save(entry)
        return orig_render_player(entry, *a, **kw)
    up.render_player = render_player
    theme = build_theme()
    out_real = str(up.OUTPUT_DIR.resolve())
    orig_launch = gr.Blocks.launch

    def launch(self, *a, **kw):
        _add_restore_on_load(self, orig_render_player)
        kw.update(share=False, server_name="127.0.0.1", theme=theme, css=CSS, js=JS)
        kw["allowed_paths"] = list(kw.get("allowed_paths") or []) + [out_real]
        print("SA3 Studio → http://127.0.0.1:%s" % (kw.get("server_port") or 7860), flush=True)
        return orig_launch(self, *a, **kw)
    gr.Blocks.launch = launch

    argv = sys.argv[1:]
    if "--dit" not in argv:
        argv += ["--dit", "medium"]
    if "--port" not in argv:
        argv += ["--port", "7860"]
    if "--no-share" not in argv:
        argv += ["--no-share"]
    sys.argv = [sys.argv[0]] + argv
    up.main()


if __name__ == "__main__":
    main()
