"""Stable Audio 3 cleanup: low-strength audio-to-audio passes over a rough inversion.

    uv run python cleanup.py <song dir> --src raw_gl --sigmas 0.2,0.35,0.5,0.7 [--seconds 30]
    -> <song dir>/work/sa3_<src>_s<sigma>.wav (+ .spec.png, fidelity.json entries)

SDEdit-style: the inversion is encoded by SAME-L, noised to sigma (`--init-noise-level`, SA3's
sigma_max) and denoised by SA3-medium for 8 pingpong steps. Low sigma keeps Astra's structure and
only lets SA3's prior repair texture (phasiness, smeared transients, missing air above 12 kHz);
high sigma hands the piece over to SA3. `--repeat N` chains N passes at the same sigma.
Prompt = Astra's own title + "What it sounds like" line (astra_description.md); seed fixed.
Runs the MLX CLI (`sa3lab/sa3`) as a subprocess, one render at a time.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

import numpy as np
import soundfile as sf

import specgrid

AI_AUDIO = Path(__file__).resolve().parents[2]
# the MLX CLI (Stability's stable-audio-3 repo: optimized/mlx/sa3); override with SA3=/path/to/sa3
SA3 = Path(os.environ.get("SA3", AI_AUDIO / "sa3lab" / "sa3"))
SR = 44100


def astra_prompt(song: Path) -> str:
    md = (song / "astra_description.md").read_text()
    title = re.search(r"\*\*Title:\*\*\s*\*?(.+?)\*?\s*$", md, re.M).group(1).strip("* ")
    sounds = re.search(r"\*\*What it sounds like:\*\*\s*(.+)$", md, re.M).group(1).strip()
    return f"{title}. {sounds}"


def sa3_pass(src: Path, out: Path, sigma: float, prompt: str, seconds: float, seed: int) -> float:
    y, sr = sf.read(src, dtype="float32")
    if y.ndim == 1:
        y = np.stack([y, y], -1)
    tmp = out.with_suffix(".in.wav")
    sf.write(tmp, np.clip(y, -1, 1), sr, subtype="PCM_16")
    t0 = time.time()
    r = subprocess.run([str(SA3), "--dit", "medium", "--decoder", "same-l", "--init-audio", str(tmp),
                    "--init-noise-level", str(sigma), "--seconds", str(seconds), "--steps", "8",
                    "--seed", str(seed), "--prompt", prompt, "--out", str(out)],
                   capture_output=True, text=True)
    tmp.unlink()
    if r.returncode:
        raise RuntimeError(f"sa3 failed: {r.stderr[-2000:]}")
    return time.time() - t0


def main(song: Path, src: str, sigmas: list[float], seconds: float, seed: int, repeat: int) -> None:
    song = song.resolve()                                         # the CLI resolves relative paths elsewhere
    work = song / "work"
    prompt = astra_prompt(song)
    db = np.load(work / "decoded.npz")["db"]
    h, w = db.shape
    fid_path = work / "fidelity.json"
    fid = json.loads(fid_path.read_text()) if fid_path.exists() else {}
    for s in sigmas:
        tag = f"sa3_{src}_s{s:g}" + (f"_x{repeat}" if repeat > 1 else "")
        cur = work / f"{src}.wav"
        for i in range(repeat):
            out = work / f"{tag}.wav" if i == repeat - 1 else work / f"{tag}.pass{i}.wav"
            dt = sa3_pass(cur, out, s, prompt, seconds, seed + i)
            if cur.name.startswith(tag):
                cur.unlink()
            cur = out
        y, _ = sf.read(cur, dtype="float32")
        g = specgrid.audio_to_grid(y.mean(-1) if y.ndim == 2 else y, SR, h, w)
        specgrid.grid_png(g, work / f"{tag}.spec.png")
        fid[tag] = specgrid.fidelity(db, g)
        print(tag, fid[tag], f"{dt:.0f}s/pass", flush=True)
        fid_path.write_text(json.dumps(fid, indent=2))
    (work / "sa3_prompt.txt").write_text(prompt)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("song", type=Path)
    ap.add_argument("--src", default="raw_gl")
    ap.add_argument("--sigmas", default="0.2,0.35,0.5,0.7")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--repeat", type=int, default=1)
    a = ap.parse_args()
    main(a.song, a.src, [float(x) for x in a.sigmas.split(",")], a.seconds, a.seed, a.repeat)
