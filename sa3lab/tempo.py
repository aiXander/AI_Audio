"""Tempo (BPM) of a render, numpy + ffmpeg only (the MLX venv has no librosa/scipy).

Used by library.py to auto-tag every render with its measured BPM and how well
it matches the BPM the prompt asked for ("..., BPM: 120" / "120 BPM").

Method, tuned for generated music (steady tempo, no drift):
1. ffmpeg decodes to mono 22.05 kHz float.
2. Onset strength = spectral flux of a log-compressed, band-pooled STFT
   (hop 256 ≈ 86 frames/s), minus its 1 s moving average, half-wave rectified.
3. Autocorrelation of that envelope, read at fractional lags. Every candidate
   tempo on a 0.1 BPM grid (55-215) is scored by a comb over its beat lag and
   multiples (1, 2, 3, 4 beats; ½-beat too): the multiples are what give
   sub-BPM precision from 11.6 ms frames. A broad log-normal prior around
   120 BPM breaks octave ties, as in librosa's tempo estimator.
4. Confidence = the winning comb's normalized autocorrelation: beat-driven
   renders measured 0.5-0.85, barking dogs / beatless clips < 0.1.

Octave errors (90 vs 180) are inherent to any tempo estimator. When the prompt
names a BPM, a strong-enough peak near it (or its ×½ / ×2) is preferred; only
if none exists does `match()` fold the octave, drop a tier and report the fold.
Measured on synthetic click tracks: within 0.5 BPM of the true tempo.
"""
from __future__ import annotations

import re
import subprocess

import numpy as np

SR = 22050
HOP = 256
NFFT = 2048
FPS = SR / HOP
BPM_LO, BPM_HI = 55.0, 215.0
MIN_CONF = 0.2           # periodicity below this: "no clear beat"

# match tiers on relative error vs the prompt's BPM (after octave folding)
GREEN, YELLOW = 0.015, 0.04          # ≤1.5 % (120 → ±1.8 BPM), ≤4 %; else orange

_BPM_RE = re.compile(r"(?i)\bbpm\s*[:=]?\s*(\d{2,3}(?:\.\d+)?)|(\d{2,3}(?:\.\d+)?)\s*bpm\b")


def prompt_bpm(prompt: str | None) -> float | None:
    m = _BPM_RE.search(prompt or "")
    if not m:
        return None
    v = float(m.group(1) or m.group(2))
    return v if 30 <= v <= 300 else None


def _decode(path: str, max_s: float = 240) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-t", str(max_s), "-ac", "1",
                          "-ar", str(SR), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def _onset_env(x: np.ndarray) -> np.ndarray:
    n = 1 + (len(x) - NFFT) // HOP
    if n < FPS * 4:
        raise ValueError("clip too short for tempo")
    win = np.hanning(NFFT).astype(np.float32)
    # pool linear bins into ~48 log-spaced bands (cheap mel stand-in)
    edges = np.unique(np.geomspace(1, NFFT // 2, 49).astype(int))
    bands = []
    for s0 in range(0, n, 2048):                                       # chunked: bounded memory
        idx = np.arange(NFFT)[None, :] + HOP * np.arange(s0, min(n, s0 + 2048))[:, None]
        mag = np.abs(np.fft.rfft(x[idx] * win, axis=1))[:, 1:]        # (frames, bins)
        bands.append(np.add.reduceat(mag, edges[:-1], axis=1))
    bands = np.concatenate(bands)
    s = np.log1p(100 * bands / (bands.max() + 1e-9))
    flux = np.maximum(0, np.diff(s, axis=0)).sum(axis=1)
    k = int(FPS)                                                       # 1 s moving average
    flux = flux - np.convolve(flux, np.ones(k) / k, mode="same")
    return np.maximum(0, flux)


def estimate(path: str, want: float | None = None) -> dict:
    """{"bpm", "conf"} for an audio file. With `want` (the prompt's BPM), a comb
    peak within ±6 % of it wins over the global best when it is at least 80 % as
    strong: octave ambiguity (90 vs 180) is then settled by the prompt, while
    the BPM itself is still measured, never copied from the prompt."""
    env = _onset_env(_decode(path))
    env = env - env.mean()
    nfft = 1 << int(np.ceil(np.log2(2 * len(env))))
    f = np.fft.rfft(env, nfft)
    ac = np.fft.irfft(f * np.conj(f))[: len(env)]
    ac = ac / (ac[0] + 1e-9)
    lags = np.arange(len(ac))

    def at(lag):                                                       # fractional-lag read
        return np.interp(lag, lags, ac)

    bpms = np.arange(BPM_LO, BPM_HI + 1e-6, 0.1)
    beat = 60.0 * FPS / bpms                                           # beat lag in frames
    W = ((1, 1.0), (2, 0.8), (3, 0.5), (4, 0.6), (0.5, 0.35))
    comb = sum(w * at(beat * m) for m, w in W) / sum(w for _, w in W)  # ~ normalized periodicity
    prior = np.exp(-0.5 * (np.log2(bpms / 120.0) / 0.9) ** 2)
    score = comb * prior
    i = int(np.argmax(score))
    if want:
        for target in (want, want / 2, want * 2):
            win = np.where(np.abs(bpms - target) <= 0.06 * target)[0]
            if len(win):
                j = int(win[np.argmax(comb[win])])
                if comb[j] >= 0.8 * comb[i]:
                    i = j
                    break
    # parabolic refinement on the 0.1 BPM grid
    if 0 < i < len(comb) - 1:
        a, b, c = comb[i - 1], comb[i], comb[i + 1]
        d = 0.5 * (a - c) / (a - 2 * b + c + 1e-12)
        bpm = float(bpms[i] + 0.1 * np.clip(d, -1, 1))
    else:
        bpm = float(bpms[i])
    # confidence = periodicity strength: beat-driven music ≈ 0.5-0.85, beatless < 0.1
    return {"bpm": round(bpm, 1), "conf": round(float(np.clip(comb[i], 0, 1)), 3)}


def match(bpm: float, want: float | None, conf: float) -> dict:
    """Tier the measured tempo against the prompt's: green / yellow / orange,
    "none" when the prompt names no BPM, "nobeat" when confidence is too low.
    Octave-folded matches (half/double time) drop one tier and report the fold."""
    if conf < MIN_CONF:
        return {"tier": "nobeat", "fold": 1, "err": None}
    if not want:
        return {"tier": "none", "fold": 1, "err": None}
    best = min((abs(bpm * fold - want) / want, fold) for fold in (1, 2, 0.5))
    err, fold = best
    tier = "green" if err <= GREEN else "yellow" if err <= YELLOW else "orange"
    if fold != 1 and tier != "orange":
        tier = "yellow" if tier == "green" else "orange"
    return {"tier": tier, "fold": fold, "err": round(err, 4)}
