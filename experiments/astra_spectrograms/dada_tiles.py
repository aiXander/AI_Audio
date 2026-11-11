"""Render the studio's mono dadaspec (sa3lab/hispec.py) of audio files as JPEGs for the page's DJ deck.

Runs with the MLX venv (hispec imports upstream's spec helpers from there):
    ../../stable-audio-3/optimized/mlx/.venv/bin/python dada_tiles.py --cps 120 a.wav:a.jpg b.wav:b.jpg …
Each output is `cps` columns per second of audio (the DJ view's own density), mono-folded.
"""
import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np

AI_AUDIO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_AUDIO / "stable-audio-3" / "optimized" / "mlx" / "scripts"))
sys.path.insert(0, str(AI_AUDIO / "sa3lab"))
import hispec  # noqa: E402

SR = 44100
ap = argparse.ArgumentParser()
ap.add_argument("--cps", type=int, default=120)
ap.add_argument("pairs", nargs="+")
a = ap.parse_args()
for pair in a.pairs:
    src, dst = pair.split(":")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-ac", "2", "-ar", str(SR), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    pcm = np.frombuffer(raw, np.int16).reshape(-1, 2)
    width = int(round(len(pcm) / SR * a.cps))
    Path(dst).write_bytes(hispec.render_jpeg(pcm, SR, width=width, mono=True))
    print(dst, width)
