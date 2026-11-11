"""A/B comparison page for an experiment folder: cards of variants on the same timeline, one dadaspec each.

    PY=stable-audio-3/optimized/mlx/.venv/bin/python
    $PY sa3lab/bench/abpage.py outputs/<exp>/abpage.json [--out compare.html]
    # or from Python: import abpage; abpage.build(manifest_dict, Path("outputs/<exp>/compare.html"))

    python3 sa3lab/bench/abserve.py outputs/<exp>     # serve it: notes + 1-5 scores auto-save to ab_labels.json

Writes `compare.html` (default) next to the manifest plus `_spec/<hash>.png`, one mono dadaspec per clip
(dadagrid.mono_dadaspec, cached by path + mtime). Audio is referenced by relative path, never copied; a
clip outside the folder gets a symlink in `_audio/` so the server can reach it. Served by abserve.py,
every clip has a 1-5 score + a note box and the page has an overall-notes box, all auto-saved to
`ab_labels.json` (keyed by the clip's path; abserve.py docstring). Opened as file:// it plays but
doesn't save. Not blind (labels show): for blind scoring use make_listen.py + labeler/. Typical use: one card per seed, one variant per setting, so Tab flips
between settings at the same playhead on the same noise.

Manifest (paths relative to the manifest's folder; everything but `cards` optional):

    {"title": "Prompt steering", "note": "<html allowed: setup line>",
     "info": [{"label": "A", "color": "#ff7a45", "text": "the prompt…"}],    # boxes under the note
     "regions": [[40, 70]],          # dashed overlays in seconds (a card may override with its own)
     "jump": 36,                     # the J key / ⇤ button cue (a card may override)
     "start_card": 1,                # which card the keys act on first
     "cards": [{"title": "Seed 101", "note": "…",
                "variants": [{"label": "A · CFG 1", "src": "A/takes/x.wav", "color": "#ff7a45"}, …]}]}

A variant without `color` gets the next one from PALETTE. Each variant's playhead maps over its own
duration, so clips of different lengths still line up at their start.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import dadagrid  # noqa: E402

AI_AUDIO = HERE.parents[1]

TEMPLATE = HERE / "abpage.html"
PALETTE = ["#ff7a45", "#5ab0ff", "#8f8bff", "#d07bff", "#4fd1a5", "#ffd166", "#ef6f9a", "#9aa3b5"]


def _spec(audio: Path, out_dir: Path) -> tuple[str, float]:
    """(png path relative to out_dir, clip seconds); cached by the audio's path + mtime."""
    h = hashlib.sha1(f"{audio.resolve()}:{audio.stat().st_mtime_ns}".encode()).hexdigest()[:16]
    png, meta = out_dir / "_spec" / f"{h}.png", out_dir / "_spec" / f"{h}.json"
    if not (png.exists() and meta.exists()):
        img, dur = dadagrid.mono_dadaspec(audio)
        png.parent.mkdir(parents=True, exist_ok=True)
        img.save(png)
        meta.write_text(json.dumps({"dur": dur, "src": str(audio)}))
    return png.relative_to(out_dir).as_posix(), json.loads(meta.read_text())["dur"]


def _served(audio: Path, folder: Path) -> str:
    """The clip's URL relative to the page; a clip outside the folder is symlinked into `_audio/`."""
    if audio.is_relative_to(folder):
        return audio.relative_to(folder).as_posix()
    h = hashlib.sha1(str(audio).encode()).hexdigest()[:8]
    link = folder / "_audio" / f"{h}-{audio.name}"
    link.parent.mkdir(exist_ok=True)
    if not link.is_symlink():
        link.symlink_to(audio)
    return link.relative_to(folder).as_posix()


def build(manifest: dict, out: Path, base: Path | None = None) -> Path:
    """Write the page to `out`; variant `src` paths resolve against `base` (default: out's folder)."""
    out = out.resolve()
    base = (base or out.parent).resolve()
    data = {k: v for k, v in manifest.items() if k != "cards"}
    data["cards"] = []
    for card in manifest["cards"]:
        vs = []
        for i, v in enumerate(card["variants"]):
            audio = (base / v["src"]).resolve()
            spec, dur = _spec(audio, out.parent)
            vs.append({**v, "src": _served(audio, out.parent), "spec": spec, "dur": dur,
                       "color": v.get("color") or PALETTE[i % len(PALETTE)]})
        data["cards"].append({**card, "variants": vs})
    try:
        rel = out.parent.relative_to(AI_AUDIO)
    except ValueError:
        rel = out.parent
    data["serve"] = f"python3 sa3lab/bench/abserve.py '{rel}'  (from AI_Audio/)"
    page = TEMPLATE.read_text()
    title = str(manifest.get("title") or "A/B").replace("<", "&lt;")
    out.write_text(page.replace("__TITLE__", title).replace("__DATA__", json.dumps(data).replace("</", "<\\/")))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("manifest")
    ap.add_argument("--out", default=None, help="default: compare.html next to the manifest")
    a = ap.parse_args()
    mf = Path(a.manifest).resolve()
    print(build(json.loads(mf.read_text()), Path(a.out) if a.out else mf.parent / "compare.html", mf.parent))


if __name__ == "__main__":
    main()
