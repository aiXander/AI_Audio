"""Stack N clips' mono dadaspecs into one PNG, so an agent can *look* at audio comparisons.

    stable-audio-3/optimized/mlx/.venv/bin/python sa3lab/dadagrid.py a.mp3 b.mp3 … \
        [--labels "base" "s2" …] [--out grid.png] [--width 1600]
    → prints one line of measured numbers per row, then the PNG path (last line); open it with Read.

Same pixels as the studio's spectrogram (`hispec.render_spectrogram_png`), folded to mono
exactly as `uiprefs.js` does (L and R averaged, bass at the bottom). Mono on purpose (Xander's
call): renders are near-symmetric L/R, so one full-height panel shows more detail than the
mirrored stereo view. How to read one:
- x = time, every clip stretched to the full width; ticks every 10 s along the label strip.
- y = mel bins, 0 Hz at the bottom to 16 kHz at the top; brightness = that bin's level
  (−60…0 dB, gamma 0.6).
- colour = which band is active at that moment, each band normalised to its own peak over
  the clip: red = bass < 200 Hz, green = mids 200–1500 Hz, blue = highs > 1.5 kHz; white =
  all three hot. So colour compares a moment against the rest of *that* clip, not across clips.
- vertical striations = onsets / beats (their spacing is the tempo); horizontal lines =
  sustained pitches / harmonics; smooth smears = noise, pads, reverb; black = silence.

The numbers line (`numbers()`, measured by keyfind.py over the whole clip) carries what the
picture can't: its brightness and colour are normalised per clip and it is mono. Per row:
length, integrated LUFS, sample peak dBFS, each band's share of the total energy in dB (bass
< 200 Hz / mid / high > 1.5 kHz; 0 dB = all of it), stereo width (side / mid RMS: 0 = mono,
~0.3 normal, 1 = very wide), key and BPM with their confidence. "no clear key" and "no steady
beat" are real answers (drones, textures), not failures. BPM is measured blind, so it can
be half or double the prompted tempo (the row label shows the sidecar's prompt-aware BPM).

Each row is labelled with the given label, else the sidecar's prompt (anarchy prefix
stripped), seed, score and measured BPM. Accepts audio files or their `.json` sidecars.
Default output: `outputs/_dadagrid/<hash of inputs>.png` (gitignored like all outputs).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
AI_AUDIO = HERE.parent
sys.path.insert(0, str(AI_AUDIO / "stable-audio-3" / "optimized" / "mlx" / "scripts"))
sys.path.insert(0, str(HERE))
import hispec  # noqa: E402

SR = 44100
LABEL_H = 22


def _pcm(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "2", "-ar", str(SR),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).reshape(-1, 2)


def mono_dadaspec(path: Path) -> tuple[Image.Image, float]:
    """(mono dadaspec image, clip seconds)."""
    pcm = _pcm(path)
    img = np.asarray(Image.open(io.BytesIO(hispec.render_spectrogram_png(pcm, SR))).convert("RGB"),
                     dtype=np.uint16)
    half = img.shape[0] // 2
    mono = (img[:half] + img[::-1][:half]) // 2      # L row y + R row h-1-y, as uiprefs.js folds it
    return Image.fromarray(mono.astype(np.uint8)), len(pcm) / SR


def _label(path: Path) -> str:
    side = path.with_name(path.name + ".json")
    if not side.exists():
        return path.name
    d = json.loads(side.read_text())
    g, lab = d.get("gen") or {}, d.get("label") or {}
    a = d.get("anarchy") or {}
    p = (a.get("source_spec") or g.get("prompt") or path.name).replace("anarchy: ", "")
    p = p.replace("grammar-music-100 ", "").replace("@anarchy/_specs/", "").replace(".cond.npz", "")
    bpm = ((d.get("analysis") or {}).get("tempo") or {}).get("bpm")
    bits = [p[:110], f"seed {g.get('seed')}"]
    if lab.get("score") is not None:
        bits.append(f"score {lab['score']:g}")
    if bpm:
        bits.append(f"{bpm:.0f} BPM")
    return "  ·  ".join(bits)


def numbers(path: Path) -> str:
    """One line of measured numbers for a clip (see the module docstring for how to read it)."""
    import keyfind
    y = keyfind.decode(path)
    a = keyfind.analyze(y)
    peak = 20 * np.log10(max(float(np.abs(y).max()), 1e-5)) if y.size else -100.0
    lufs = "silent" if a["lufs"] is None else f"{a['lufs']:.1f} LUFS"
    key = f"{a['key']} (conf {a['key_conf']:.2f})" if a.get("key") else "no clear key"
    beat = (f"{a['bpm']:.1f} BPM (conf {a['bpm_conf']:.2f})"
            if a["bpm"] and a["bpm_conf"] >= keyfind.BEAT_CONF else "no steady beat")
    return (f"{y.shape[1] / SR:.1f} s | {lufs} | peak {peak:.1f} dBFS | bass/mid/high "
            + "/".join(f"{b:.1f}" for b in a["bands"]) + f" dB | width {a['width']:.2f} | {key} | {beat}")


def _font(size: int = 15):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                                  # Pillow < 10.1
        return ImageFont.load_default()


def grid(paths: list[Path], labels: list[str] | None = None, width: int = 1600) -> Image.Image:
    rows = []
    font = _font()
    for i, p in enumerate(paths):
        spec, secs = mono_dadaspec(p)
        spec = spec.resize((width, spec.height), Image.LANCZOS)
        strip = Image.new("RGB", (width, LABEL_H), (24, 24, 30))
        dr = ImageDraw.Draw(strip)
        for t in range(10, int(secs), 10):             # 10 s ticks
            x = int(width * t / secs)
            dr.line([(x, LABEL_H - 6), (x, LABEL_H)], fill=(120, 120, 130))
        dr.text((6, 3), f"{i + 1}. " + (labels[i] if labels and i < len(labels) else _label(p)),
                fill=(230, 230, 235), font=font)
        rows += [strip, spec]
    out = Image.new("RGB", (width, sum(r.height for r in rows) + 4 * len(paths)), (0, 0, 0))
    y = 0
    for r in rows:
        out.paste(r, (0, y))
        y += r.height + (4 if r.height != LABEL_H else 0)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="+")
    ap.add_argument("--labels", nargs="*")
    ap.add_argument("--out")
    ap.add_argument("--width", type=int, default=1600)
    args = ap.parse_args()
    paths = [Path(f[:-5] if f.endswith(".json") else f).resolve() for f in args.files]
    out = Path(args.out) if args.out else AI_AUDIO / "outputs" / "_dadagrid" / (
        hashlib.md5("|".join(map(str, paths)).encode()).hexdigest()[:10] + ".png")
    out.parent.mkdir(parents=True, exist_ok=True)
    grid(paths, args.labels, args.width).save(out)
    for i, p in enumerate(paths):
        print(f"{i + 1}. {numbers(p)}")
    print(out)


if __name__ == "__main__":
    main()
