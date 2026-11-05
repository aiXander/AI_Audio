"""Full-HD dadaspec: the same 3-band tinted stereo mel spectrogram as upstream's
`optimized/mlx/scripts/spec.py` (a port of dada-bots/dadaspec), at real resolution.

Upstream analyses at a fixed hop of 2048 samples @ 32 kHz (~15.6 columns/s) with
30 mels, then Lanczos-upscales to 1200x240: a 10 s clip is ~156x60 real pixels
stretched ~8x, hence the blur. Here:

- the hop adapts so every clip gets ~`width` real columns (one per pixel),
- `n_mels` per channel = height/2, so every row is a real mel bin,
- analysis is uncentred (dadaspec's fix): no reflect padding, so the first and
  last columns don't light up white,
- output is JPEG (a 1920x256 noise-like PNG is several x larger, and every
  history entry embeds its image in the page).

Colouring (per-bin dB brightness x per-frame band hue, each band normalised on
its own) is unchanged, reusing upstream's helpers.
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image

from spec import (  # upstream helpers (optimized/mlx/scripts on sys.path)
    SPEC_BANDS, _BAND_COLORS, _mel_filterbank, _mel_frequencies,
    _power_to_db, _resample_to_32k,
)

WIDTH, N_MELS = 1920, 128          # → 1920 x 256 real pixels
N_FFT, FMAX = 2048, 16000
MIN_HOP = 64


def _stft_power(y, n_fft, hop):
    """Uncentred power STFT, (n_fft//2+1, frames). Every frame is real signal."""
    if y.shape[-1] < n_fft:
        y = np.pad(y, (0, n_fft - y.shape[-1]))
    win = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n_fft) / n_fft)).astype(np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(y, n_fft)[::hop] * win
    spec = np.fft.rfft(frames, n=n_fft, axis=1)
    return (spec.real ** 2 + spec.imag ** 2).T.astype(np.float32)


def _channel(y, sr, hop, n_mels):
    S = _mel_filterbank(n_mels, N_FFT, sr, FMAX) @ _stft_power(y, N_FFT, hop)
    S_db = _power_to_db(S)
    np.clip(S_db, -60, 0, out=S_db)
    S_db = ((S_db + 60.0) / 60.0) ** 0.6

    mel_f = _mel_frequencies(n_mels, fmax=FMAX)
    bands = np.zeros((3, S.shape[1]), dtype=np.float32)
    for i, (flo, fhi, _) in enumerate(SPEC_BANDS):
        mask = (mel_f >= flo) & (mel_f < fhi)
        if mask.any():
            db = np.clip(10.0 * np.log10(S[mask].sum(axis=0) + 1e-10), -20, None) + 20
            if db.max() > 0:
                db /= db.max()
            bands[i] = db
    rgb = bands.T @ _BAND_COLORS
    rgb /= np.maximum(rgb.max(axis=0, keepdims=True), 1e-12)
    return S_db.astype(np.float32), rgb.astype(np.float32)


def render_spectrogram_png(pcm_int16, sample_rate=44100, width=None, height=None,
                           n_mels=None) -> bytes:
    """Drop-in for upstream's spec.render_spectrogram_png; ignores its small
    width/height and renders at full HD. Returns JPEG bytes (browsers sniff the
    format, so upstream's `data:image/png` URI still displays it)."""
    if pcm_int16.ndim != 2 or pcm_int16.shape[1] != 2:
        raise ValueError(f"expected (T, 2) PCM, got {pcm_int16.shape}")
    y, sr = _resample_to_32k(pcm_int16, sample_rate)
    n = y.shape[-1]
    hop = max(MIN_HOP, (max(n - N_FFT, 1)) // (WIDTH - 1) or 1)

    S_L, rgb_L = _channel(y[0], sr, hop, N_MELS)
    S_R, rgb_R = _channel(y[1], sr, hop, N_MELS)
    nf = min(S_L.shape[1], S_R.shape[1])
    S_L, S_R, rgb_L, rgb_R = S_L[::-1, :nf], S_R[:, :nf], rgb_L[:nf], rgb_R[:nf]

    img = np.empty((N_MELS * 2, nf, 3), dtype=np.float32)
    img[:N_MELS] = S_L[:, :, None] * rgb_L[None]     # L on top, bass at the centre line
    img[N_MELS:] = S_R[:, :, None] * rgb_R[None]     # R below, mirrored
    np.clip(img, 0, 1, out=img)
    pil = Image.fromarray((img * 255).astype(np.uint8))
    if nf != WIDTH:   # only when the clip is too short for one column per hop
        pil = pil.resize((WIDTH, N_MELS * 2), Image.LANCZOS)
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=92, subsampling=0, optimize=True)
    return buf.getvalue()
