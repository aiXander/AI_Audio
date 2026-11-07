"""Latent time-stretch (composer spec 4.6, experiment 0e): resample a clip's SAME-L latents along time
by a tempo ratio and decode, instead of stretching the audio with rubberband.

    stable-audio-3/optimized/mlx/.venv/bin/python sa3lab/bench/latent_stretch.py in.mp3 out.wav \
        [--s0 10 --s1 30] [--ratio 1.06]

ratio > 1 = faster / shorter (rubberband's tempo factor). The range is encoded with the SAME-L
encoder (the one inpaint uses), the (C, T) latents are linearly resampled to round(T / ratio)
frames, then decoded the way upstream's run_generation decodes (decoder + chunked dispatch +
patched_decode). Loads only the codec (encoder + decoder, ~1-2 GB), never a DiT, so it can run
next to a studio. Pitch is unchanged only to the extent the codec's latents separate pitch from
time (they mostly don't at 93 ms frames: expect smearing; the 0e listen decides). The soft
fix-up (u 0.3 over the result, experiments.py) is the other half of the idea.
"""
from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

AI_AUDIO = Path(__file__).resolve().parents[2]
MLX = AI_AUDIO / "stable-audio-3" / "optimized" / "mlx"
os.environ.setdefault("HF_HUB_CACHE", str(AI_AUDIO / "models" / "hf-cache"))
sys.path[:0] = [str(MLX), str(MLX / "scripts")]

SR, SPL = 44100, 4096


def decode_file(path, s0, s1):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(s0), "-t", str(s1 - s0), "-i", str(path), "-ac", "2",
                          "-ar", str(SR), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).T.copy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--s0", type=float, default=0.0)
    ap.add_argument("--s1", type=float, default=20.0)
    ap.add_argument("--ratio", type=float, default=1.06)
    ap.add_argument("--codec", default="same-l")
    a = ap.parse_args()
    import mlx.core as mx
    from sa3_mlx import load_encoder, load_decoder, patch_audio
    from models.defs.sa3_pipeline import patched_decode
    y = decode_file(a.src, a.s0, a.s1)
    T = math.ceil(y.shape[1] / SPL)
    enc, pad_mod = load_encoder(a.codec, mx.float32)
    eT = T if (T * 16) % pad_mod == 0 else math.ceil((T * 16) / pad_mod) * pad_mod // 16
    y = np.pad(y, ((0, 0), (0, eT * SPL - y.shape[1])))
    lat = enc(mx.array(patch_audio(y[None], patch_size=256)))[..., :T]
    mx.eval(lat)
    L = np.array(lat.astype(mx.float32))[0]                       # (C, T)
    T2 = max(2, int(round(T / a.ratio)))
    if T2 % 2:
        T2 += 1
    x = np.linspace(0, T - 1, T2)
    L2 = np.stack([np.interp(x, np.arange(T), c) for c in L])[None].astype(np.float32)
    del enc
    decoder, chunk_fn, (chunk, ovl) = load_decoder(a.codec, mx.float32)
    lt = mx.array(L2)
    patches = chunk_fn(decoder, lt, chunk, ovl) if T2 > chunk + 2 * ovl else decoder(lt)
    mx.eval(patches)
    audio = np.array(patched_decode(patches, patch_size=256, channels=2).astype(mx.float32))[0]
    n = int(round((a.s1 - a.s0) / a.ratio * SR))
    audio = audio[:, :n]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-", "-c:a", "pcm_f32le",
                    a.out], input=np.ascontiguousarray(audio.T).astype(np.float32).tobytes(), check=True)
    print(f"latent stretch ×{a.ratio}: {T} → {T2} frames → {a.out}")


if __name__ == "__main__":
    main()
