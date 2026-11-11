"""The shared spectrogram geometry: Astra's image grid <-> linear STFT magnitudes, and back to pictures.

Image grid (what decode.py produces): db[H, W], row 0 = 12 kHz (top), last row = 0 Hz, rows evenly
spaced in HTK mel; columns evenly spaced over 0..30 s; values in -80..0 dB.

`grid_to_linear` turns that into the |STFT| any inverter needs (any sr / n_fft / hop): columns are
interpolated to the STFT frame times, and each linear bin takes the *power average* of the image
rows its frequency span covers (many rows per bin in the bass, a fraction of a row in the treble).
Absolute level: 0 dB in the image = a sinusoid of amplitude REF_AMP, i.e. |X| = REF_AMP * sum(hann)/2,
so the log-mels we feed the vocoders sit in the range they saw in training.

`audio_to_grid` measures real audio on the same grid (for re-rendered spectrograms and the
fidelity score), `grid_png` paints a grid with magma like Astra's plot area.
"""
from __future__ import annotations

import os

import numpy as np
from matplotlib import colormaps
from PIL import Image
from scipy.signal import stft as sp_stft

DURATION = 30.0
F_MAX = 12000.0
DB_MIN = -80.0
REF_AMP = float(os.environ.get("REF_AMP", "0.5"))   # 0 dB = a sine of this amplitude


def hz_to_mel(f):
    return 2595.0 * np.log10(1.0 + np.asarray(f, dtype=np.float64) / 700.0)


MEL_MAX = float(hz_to_mel(F_MAX))


def _interp_columns(db: np.ndarray, times: np.ndarray) -> np.ndarray:
    """db[H, W] (columns = pixel centres over 0..DURATION) -> db[H, len(times)]."""
    h, w = db.shape
    xc = (np.arange(w) + 0.5) / w * DURATION
    idx = np.interp(times, xc, np.arange(w))
    i0 = np.clip(np.floor(idx).astype(int), 0, w - 1)
    i1 = np.clip(i0 + 1, 0, w - 1)
    t = (idx - i0)[None, :]
    return db[:, i0] * (1 - t) + db[:, i1] * t


def grid_to_linear(db: np.ndarray, sr: int, n_fft: int, times: np.ndarray,
                   gate_db: float = -70.0) -> np.ndarray:
    """Image dB grid -> linear STFT magnitude [n_fft//2+1, len(times)]."""
    h, _ = db.shape
    cols = _interp_columns(db, times)[::-1]                       # ascending mel now
    cols = np.where(cols < gate_db, -120.0, cols)
    p = 10.0 ** (cols / 10.0)                                     # relative power per row
    dm = MEL_MAX / h
    csum = np.vstack([np.zeros((1, p.shape[1])), np.cumsum(p, 0) * dm])   # integral at row edges

    def integral(m):
        x = np.clip(m / dm, 0, h)
        i0 = np.clip(np.floor(x).astype(int), 0, h - 1)
        frac = (x - i0)[:, None]
        return csum[i0] + (csum[i0 + 1] - csum[i0]) * frac

    freqs = np.arange(n_fft // 2 + 1) * sr / n_fft
    df = sr / n_fft
    lo = hz_to_mel(np.maximum(freqs - df / 2, 0))
    hi = hz_to_mel(freqs + df / 2)
    width = np.maximum(hi - lo, 1e-9)[:, None]
    narrow = (hi - lo) < dm                                       # sub-row bins: point sample
    avg = (integral(hi) - integral(lo)) / width
    centre = hz_to_mel(freqs)
    x = np.clip(centre / dm - 0.5, 0, h - 1)
    i0 = np.floor(x).astype(int)
    i1 = np.minimum(i0 + 1, h - 1)
    t = (x - i0)[:, None]
    point = p[i0] * (1 - t) + p[i1] * t
    power = np.where(narrow[:, None], point, avg)
    power[freqs > F_MAX] = 10 ** (-120 / 10)
    ref = REF_AMP * n_fft / 4.0                                   # |X| of a sine of amp REF_AMP
    return (np.sqrt(power) * ref).astype(np.float32)


def _rows_on_grid(y: np.ndarray, sr: int, h: int, w: int, n_fft: int) -> np.ndarray:
    hop = 256
    f, t, z = sp_stft(y, sr, window="hann", nperseg=n_fft, noverlap=n_fft - hop, boundary="even")
    pw = np.abs(z) ** 2
    # rows: mel bands of width dm, power-averaged over the linear bins inside each band
    dm = MEL_MAX / h
    edges = np.arange(h + 1) * dm
    fm = hz_to_mel(f)
    rows = np.zeros((h, pw.shape[1]))
    for r in range(h):
        sel = (fm >= edges[r]) & (fm < edges[r + 1])
        if sel.any():
            rows[r] = pw[sel].mean(0)
        else:  # band narrower than a bin: interpolate the nearest bins
            c = (edges[r] + edges[r + 1]) / 2
            k = np.interp(c, fm, np.arange(len(fm)))
            k0 = int(k)
            a = k - k0
            rows[r] = pw[k0] * (1 - a) + pw[min(k0 + 1, len(f) - 1)] * a
    xc = (np.arange(w) + 0.5) / w * DURATION
    idx = np.interp(xc, t, np.arange(len(t)))
    i0 = np.floor(idx).astype(int)
    i1 = np.minimum(i0 + 1, len(t) - 1)
    a = idx - i0
    return rows[:, i0] * (1 - a) + rows[:, i1] * a


def audio_to_grid(y: np.ndarray, sr: int, h: int, w: int) -> np.ndarray:
    """Measure mono audio on the image grid: dB[h, w] relative to its own max, floored at -80.

    Two STFT resolutions, so the re-rendered picture is as sharp as the image in both regions:
    a 8192-point window below 400 Hz (where image rows are a few Hz apart) and 2048 above.
    """
    y = y[: int(DURATION * sr)]
    if len(y) < DURATION * sr:
        y = np.pad(y, (0, int(DURATION * sr) - len(y)))
    lo = _rows_on_grid(y, sr, h, w, 8192)   # scipy's stft scales by 1/sum(window): a sine
    hi = _rows_on_grid(y, sr, h, w, 2048)   # reads A/2 at either size, so the two bands agree
    centre_hz = 700 * (10 ** (((np.arange(h) + 0.5) * MEL_MAX / h) / 2595) - 1)
    rows = np.where((centre_hz < 400)[:, None], lo, hi)[::-1]      # top = 12 kHz, like the image
    db = 10 * np.log10(rows + 1e-20)
    db -= db.max()
    return np.maximum(db, DB_MIN).astype(np.float32)


def grid_png(db: np.ndarray, path, size: tuple[int, int] | None = None) -> None:
    v = np.clip((db - DB_MIN) / -DB_MIN, 0, 1)
    rgb = (colormaps["magma"](v)[..., :3] * 255).astype(np.uint8)
    im = Image.fromarray(rgb)
    if size:
        im = im.resize(size, Image.LANCZOS)
    im.save(path)


def fidelity(src_db: np.ndarray, out_db: np.ndarray) -> dict:
    """How well a rendition matches the source grid (both dB, same shape, each max-normalised).

    corr: Pearson correlation of the dB maps over pixels where either is above -60 dB.
    mae_db: mean absolute dB error over those pixels.
    """
    s = np.maximum(src_db - src_db.max(), DB_MIN)
    o = np.maximum(out_db - out_db.max(), DB_MIN)
    m = (s > -60) | (o > -60)
    if m.sum() < 10:
        return dict(corr=0.0, mae_db=99.0)
    c = np.corrcoef(s[m], o[m])[0, 1]
    return dict(corr=round(float(c), 3), mae_db=round(float(np.abs(s[m] - o[m]).mean()), 2))
