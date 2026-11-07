"""Extend a clip: re-render it longer, keeping the original as inpaint context.

SA3 trains with inpainting on every step (1–10 random spans + causal "keep the start"
masks), so a continuation is an inpaint job: Length = clip + N s, reference audio = the
clip itself (upstream zero-pads a reference shorter than Length), range = [clip − redo,
new length]. `redo` regenerates the clip's last seconds too, so a clip that already fades
out gets a new ending instead of a continuation of the fade. Everything else (prompt or
anarchy spec, seed, model, steps, LoRAs; a composer take's anchors + weight curves, the tail
holding their final mix: segments.restore_value) comes from the clip's own settings, and with
noise.py's fixed canvas the same seed gives the kept span exactly the noise that made it.

Flow (no new gradio event): extend.js asks the studio's restore-settings path for
`__extend__:<add>:<redo>:<clip name>`; studio.py's restore() calls `apply()` to turn the
clip's settings into the extension's, then the restore chain's last step calls
`window.sa3AfterRestore()` and extend.js presses Generate. The new clip's `gen` block
gains `extends` (parent clip, seconds added, redo), matched by the reference audio's
sha256, which gen_record already computes when it archives the inpaint input.
"""
from __future__ import annotations

PREFIX = "__extend__:"
_pending: dict[str, dict] = {}       # parent audio sha256 → the extension it asked for


def parse(name: str):
    """`__extend__:<add>:<redo>:<clip>` → (clip, add, redo), else None."""
    if not name or not name.startswith(PREFIX):
        return None
    add, redo, clip = name[len(PREFIX):].split(":", 2)
    return clip, float(add), float(redo)


def apply(r: dict, clip: str, add: float, redo: float, library, max_s: float) -> tuple[dict, str]:
    """The clip's restored settings → the extension's settings, plus a one-line note."""
    p = library._audio_path(clip)
    if p is None:
        raise ValueError(f"no render named {clip!r}")
    cur = float(r.get("seconds") or 0)
    if cur <= 0:
        raise ValueError("clip length unknown")
    if cur >= max_s - 0.5:
        raise ValueError(f"already at this model's maximum length ({max_s:g} s)")
    new = min(cur + add, max_s)
    start = max(0.0, cur - redo)
    out = dict(r, seconds=new, inpaint_audio=str(p), inpaint_range_sec=[start, new],
               a2a_audio=None, full=True, mode="inpaint")
    if r.get("mode") in ("audio-to-audio", "a2a+inpaint"):
        out["sigma_max"] = 1.0       # its sigma_max was the guide's init level; no guide now
    sha = library._sha256(str(p))
    if sha:
        _pending[sha] = {"parent": clip, "parent_seconds": cur, "added_s": round(new - cur, 3),
                         "redo_s": round(cur - start, 3)}
    note = (f"Extending {cur:g} s → {new:g} s: keeps 0–{start:g} s, generates {start:g}–{new:g} s"
            + (" (capped at the model's max)" if new < cur + add else ""))
    return out, note


def install(library) -> None:
    orig = library.gen_record

    def gen_record(*a, **kw):
        g = orig(*a, **kw)
        sha = (g.get("inpaint_audio") or {}).get("sha256")
        if sha and sha in _pending:
            g["extends"] = _pending[sha]
        return g
    library.gen_record = gen_record
