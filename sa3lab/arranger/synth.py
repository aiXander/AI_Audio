"""Crude, deterministic numpy synthesis for the arranger's sketch tracks (no samples, no scipy).

The sound only has to carry structure for SA3's audio-to-audio pass (which re-synthesises
the timbre), so everything is simple and cheap:
- drums: one-shots built once per render (kick = pitched sine sweep + click; snare = tone +
  band-passed noise; clap = 3 noise bursts + tail; hats/shaker = high-passed noise), a few
  seeded noise variants each so repeats aren't sample-identical;
- tonal: polyBLEP saw / square / sine oscillators with ADSR; pads = 3 detuned voices spread
  L/C/R;
- filters: one time-varying STFT filter (`stft_filter`: 2048-pt Hann, hop 512, gains per
  frame), so a clip's cutoff automation is a real sweep, and risers are band-passed noise
  whose centre follows a curve;
- reverb: convolution with a seeded stereo exponential-noise impulse response.

Cutoff automation values are 0..1, mapped exponentially to 80 Hz…18 kHz (`cutoff_hz`).
"""
from __future__ import annotations

import numpy as np

SR = 44100
N_FFT, HOP = 2048, 512
_WIN = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)).astype(np.float32)
_FREQS = np.fft.rfftfreq(N_FFT, 1 / SR).astype(np.float32)


def cutoff_hz(c):
    return 80.0 * (18000.0 / 80.0) ** np.clip(c, 0.0, 1.0)


def midi_hz(m):
    return 440.0 * 2.0 ** ((np.asarray(m, dtype=np.float64) - 69) / 12)


# ── STFT filtering ─────────────────────────────────────────────────────────
def stft_filter(x: np.ndarray, gain_fn) -> np.ndarray:
    """Filter mono or (2, N) `x` with per-frame gains: gain_fn(frame_times_s) → (F, 1025)."""
    if x.ndim == 2:
        return np.stack([stft_filter(c, gain_fn) for c in x])
    n = len(x)
    xp = np.pad(x.astype(np.float32), (N_FFT, N_FFT + HOP))
    frames = np.lib.stride_tricks.sliding_window_view(xp, N_FFT)[::HOP] * _WIN
    S = np.fft.rfft(frames, axis=1)
    t = (np.arange(len(S)) * HOP + N_FFT / 2 - N_FFT) / SR
    S *= gain_fn(t).astype(np.float32)
    fr = np.fft.irfft(S, N_FFT, axis=1).astype(np.float32) * _WIN
    F = len(fr)
    out = np.zeros((F + 3) * HOP, np.float32)
    for k in range(4):
        out[k * HOP: k * HOP + F * HOP] += fr[:, k * HOP:(k + 1) * HOP].reshape(-1)
    return out[N_FFT: N_FFT + n] / 1.5


def lowpass_gains(fc_fn, res: float = 0.0, order: int = 2):
    def g(t):
        fc = np.maximum(np.asarray(fc_fn(t), dtype=np.float32)[:, None], 20.0)
        r = (_FREQS[None, :] + 1.0) / fc
        out = 1.0 / np.sqrt(1.0 + r ** (2 * order))
        if res > 0:
            out = out + res * np.exp(-np.log(r) ** 2 / (2 * 0.18 ** 2))
        return out
    return g


def bandpass_gains(fc_fn, q: float = 1.2):
    def g(t):
        fc = np.maximum(np.asarray(fc_fn(t), dtype=np.float32)[:, None], 20.0)
        lr = np.log2((_FREQS[None, :] + 1.0) / fc)
        return np.exp(-(lr * q) ** 2 * 2.0)
    return g


def _shape(x: np.ndarray, lo: float | None = None, hi: float | None = None, order: int = 2) -> np.ndarray:
    """Static high/low-pass of a short buffer through one FFT (drum one-shots)."""
    n = len(x)
    m = 1 << int(np.ceil(np.log2(n + 1)))
    X = np.fft.rfft(x, m)
    f = np.fft.rfftfreq(m, 1 / SR) + 1.0
    if lo:
        X *= 1.0 / np.sqrt(1.0 + (lo / f) ** (2 * order))
    if hi:
        X *= 1.0 / np.sqrt(1.0 + (f / hi) ** (2 * order))
    return np.fft.irfft(X, m)[:n].astype(np.float32)


# ── oscillators ────────────────────────────────────────────────────────────
def _blep(p, dt):
    out = np.zeros_like(p)
    a = p < dt
    t = p[a] / dt[a]
    out[a] = t + t - t * t - 1.0
    b = p > 1.0 - dt
    t = (p[b] - 1.0) / dt[b]
    out[b] = t * t + t + t + 1.0
    return out


def osc(wave: str, f, n: int, phase0: float = 0.0) -> np.ndarray:
    """polyBLEP saw / square, or sine; f = Hz (scalar or per-sample array)."""
    f = np.broadcast_to(np.asarray(f, dtype=np.float64), (n,))
    dt = f / SR
    p = (phase0 + np.cumsum(dt) - dt[0]) % 1.0
    if wave == "sine":
        return np.sin(2 * np.pi * p).astype(np.float32)
    saw = 2 * p - 1 - _blep(p, dt)
    if wave == "square":
        p2 = (p + 0.5) % 1.0
        saw2 = 2 * p2 - 1 - _blep(p2, dt)
        return (0.5 * (saw - saw2)).astype(np.float32)
    return saw.astype(np.float32)


def adsr(n_on: int, a: float, d: float, s: float, r: float) -> np.ndarray:
    """Envelope for a note held n_on samples, plus its release."""
    A, D, R = max(1, int(a * SR)), max(1, int(d * SR)), max(1, int(r * SR))
    env = np.full(n_on, s, np.float32)
    k = min(A, n_on)
    env[:k] = np.linspace(0, 1, A, endpoint=False, dtype=np.float32)[:k]
    if n_on > A:
        k2 = min(D, n_on - A)
        env[A:A + k2] = (1 - (1 - s) * np.linspace(0, 1, D, dtype=np.float32))[:k2]
    last = env[-1] if n_on else 0.0
    return np.concatenate([env, last * np.linspace(1, 0, R, dtype=np.float32) ** 2])


# ── drums ──────────────────────────────────────────────────────────────────
def drum_kit(seed: int, variants: int = 4) -> dict[str, list[np.ndarray]]:
    """kind → list of mono one-shots (seeded noise variants)."""
    rng = np.random.default_rng(seed)
    kit: dict[str, list[np.ndarray]] = {}

    def kick(f0, f1, tp, ta, n_s, click):
        n = int(n_s * SR)
        t = np.arange(n) / SR
        f = f1 + (f0 - f1) * np.exp(-t / tp)
        body = osc("sine", f, n) * np.exp(-t / ta)
        cl = _shape(rng.standard_normal(n).astype(np.float32), lo=1500) * np.exp(-t / 0.004) * click
        return np.tanh(1.6 * (body + cl)).astype(np.float32)

    kit["punch"] = [kick(160, 48, 0.03, 0.28, 0.6, 0.5) for _ in range(variants)]
    kit["boom"] = [kick(120, 38, 0.05, 0.6, 1.2, 0.25) for _ in range(variants)]
    kit["soft"] = [kick(110, 50, 0.025, 0.18, 0.4, 0.15) * 0.8 for _ in range(variants)]

    def noise(n):
        return rng.standard_normal(n).astype(np.float32)

    snares, claps, closed, opened, shakers = [], [], [], [], []
    for _ in range(variants):
        n = int(0.32 * SR)
        t = np.arange(n) / SR
        tone = osc("sine", 185 * (1 + 0.3 * np.exp(-t / 0.01)), n) * np.exp(-t / 0.06)
        nz = _shape(noise(n), lo=1200, hi=9000) * np.exp(-t / 0.11)
        snares.append((0.6 * tone + 1.1 * nz).astype(np.float32))
        n = int(0.4 * SR)
        t = np.arange(n) / SR
        env = np.zeros(n, np.float32)
        for k, off in enumerate((0.0, 0.011, 0.022)):
            i = int(off * SR)
            env[i:] += np.exp(-(t[: n - i]) / 0.005) * (0.8 if k < 2 else 1.0)
        env += np.exp(-np.maximum(t - 0.022, 0) / 0.13) * (t > 0.022) * 0.7
        claps.append((_shape(noise(n), lo=900, hi=4500) * env * 1.6).astype(np.float32))
        n = int(0.12 * SR)
        t = np.arange(n) / SR
        closed.append((_shape(noise(n), lo=7000, order=3) * np.exp(-t / 0.035)).astype(np.float32))
        n = int(0.5 * SR)
        t = np.arange(n) / SR
        opened.append((_shape(noise(n), lo=6500, order=3) * np.exp(-t / 0.16) * 0.8).astype(np.float32))
        n = int(0.14 * SR)
        t = np.arange(n) / SR
        env = np.minimum(t / 0.012, 1) * np.exp(-t / 0.05)
        shakers.append((_shape(noise(n), lo=4500, hi=12000) * env * 0.9).astype(np.float32))
    kit["snare"], kit["clap"] = snares, claps
    kit["both"] = [0.55 * np.pad(s, (0, max(0, len(c) - len(s)))) + 0.65 * np.pad(c, (0, max(0, len(s) - len(c))))
                   for s, c in zip(snares, claps)]
    kit["closed"], kit["open"], kit["shaker"] = closed, opened, shakers
    return kit


# ── tonal voices ───────────────────────────────────────────────────────────
VOICES = {   # sound → (waves [(wave, detune cents, pan, gain)], adsr, brightness)
    "bass:saw": ([("saw", 0, 0, 0.7), ("sine", -1200, 0, 0.8)], (0.004, 0.15, 0.8, 0.06)),
    "bass:square": ([("square", 0, 0, 0.7), ("sine", -1200, 0, 0.8)], (0.004, 0.15, 0.8, 0.06)),
    "bass:sine": ([("sine", 0, 0, 1.0), ("saw", 0, 0, 0.12)], (0.004, 0.1, 0.9, 0.06)),
    "chords:pad": ([("saw", -9, -0.5, 0.35), ("saw", 0, 0, 0.35), ("saw", 9, 0.5, 0.35)], (0.35, 0.6, 0.85, 0.7)),
    "chords:saw": ([("saw", -6, -0.4, 0.4), ("saw", 6, 0.4, 0.4)], (0.01, 0.25, 0.6, 0.15)),
    "chords:pluck": ([("saw", -5, -0.3, 0.4), ("square", 5, 0.3, 0.3)], (0.002, 0.18, 0.0, 0.1)),
    "lead:saw": ([("saw", -5, -0.15, 0.45), ("saw", 5, 0.15, 0.45)], (0.008, 0.2, 0.75, 0.15)),
    "lead:square": ([("square", 0, 0, 0.55), ("saw", 7, 0.1, 0.25)], (0.008, 0.2, 0.75, 0.15)),
    "lead:pluck": ([("saw", 0, -0.1, 0.5), ("square", 4, 0.1, 0.3)], (0.002, 0.25, 0.1, 0.2)),
}


def tonal_note(sound: str, midi: float, dur_s: float, vel: float, vibrato: float = 0.0) -> np.ndarray:
    """(2, n) stereo note incl. release tail."""
    waves, (a, d, s, r) = VOICES.get(sound, VOICES["lead:saw"])
    n_on = max(1, int(dur_s * SR))
    env = adsr(n_on, a, d, s, r)
    n = len(env)
    f0 = float(midi_hz(midi))
    t = np.arange(n) / SR
    fm = 1.0
    if vibrato > 0:
        fm = 1 + vibrato * 0.006 * np.sin(2 * np.pi * 5.2 * t) * np.clip((t - 0.18) / 0.25, 0, 1)
    out = np.zeros((2, n), np.float32)
    for k, (w, cents, pan, g) in enumerate(waves):
        f = f0 * 2 ** (cents / 1200) * fm
        if np.max(f) > SR / 2 - 500:
            continue
        x = osc(w, f, n, phase0=(k * 0.37 + midi * 0.11) % 1.0) * g
        th = (pan + 1) * np.pi / 4
        out[0] += x * np.cos(th)
        out[1] += x * np.sin(th)
    return out * env * (vel ** 1.3)


# ── FX ─────────────────────────────────────────────────────────────────────
def fx(kind: str, dur_s: float, rng: np.random.Generator, shape=None, root_midi: int = 57) -> np.ndarray:
    """(2, n) FX: riser / downlifter / sweep (band-passed noise following a curve),
    impact (boom + crash), reverse (reverse cymbal into the clip's end)."""
    n = max(1, int(dur_s * SR))
    t = np.arange(n) / SR
    u = t / max(dur_s, 1e-6)
    nz = rng.standard_normal((2, n)).astype(np.float32)
    shape = shape if shape is not None else (lambda x: x)
    if kind in ("riser", "downlifter", "sweep"):
        if kind == "riser":
            pos = lambda tt: shape(np.clip(tt / dur_s, 0, 1))
            amp = (0.15 + 0.85 * u ** 2) * np.minimum((1 - u) / 0.01, 1)
        elif kind == "downlifter":
            pos = lambda tt: 1 - np.clip(tt / dur_s, 0, 1)
            amp = (1 - u) ** 1.5 * np.minimum(u / 0.005, 1)
        else:
            pos = lambda tt: np.sin(np.pi * np.clip(tt / dur_s, 0, 1))
            amp = np.sin(np.pi * u) ** 1.5
        fc = lambda tt: 250.0 * (11000.0 / 250.0) ** pos(tt)
        y = stft_filter(nz, bandpass_gains(fc, q=1.1)) * amp * 1.4
        if kind == "riser":            # plus a rising tone, two octaves over the clip
            f = midi_hz(root_midi) * 2 ** (2 * shape(u))
            y += osc("saw", f, n)[None] * 0.07 * amp
        return y.astype(np.float32)
    if kind == "impact":
        n = int(max(dur_s, 2.5) * SR)
        t = np.arange(n) / SR
        boom = osc("sine", 38 + 60 * np.exp(-t / 0.08), n) * np.exp(-t / 0.7)
        crash = stft_filter(rng.standard_normal((2, n)).astype(np.float32),
                            lowpass_gains(lambda tt: 9000 * np.exp(-np.maximum(tt, 0) / 1.5) + 500))
        crash = crash * np.exp(-t / 0.9)
        return (np.tanh(1.5 * boom)[None] * 0.9 + crash * 0.5).astype(np.float32)
    # reverse cymbal: rises exponentially into the end of the clip (max 2 s)
    L = min(dur_s, 2.5)
    env = np.where(t >= dur_s - L, np.exp((t - dur_s) / (L / 4)), 0.0)
    hp = np.stack([_shape(c, lo=3500) for c in nz])
    return (hp * env * 0.8).astype(np.float32)


# ── reverb + mix helpers ───────────────────────────────────────────────────
def reverb_ir(seed: int, rt60: float = 2.2) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(rt60 * SR)
    t = np.arange(n) / SR
    ir = rng.standard_normal((2, n)).astype(np.float32) * np.exp(-6.9 * t / rt60)
    ir = np.stack([_shape(c, lo=200, hi=6000) for c in ir])
    ir = np.pad(ir, ((0, 0), (int(0.02 * SR), 0)))
    return ir / np.sqrt(np.sum(ir ** 2, axis=1, keepdims=True)) * 0.5


def convolve(x: np.ndarray, ir: np.ndarray) -> np.ndarray:
    """(2, N) * (2, M) → (2, N), FFT convolution, channel-wise."""
    n = x.shape[1] + ir.shape[1]
    m = 1 << int(np.ceil(np.log2(n)))
    y = np.fft.irfft(np.fft.rfft(x, m) * np.fft.rfft(ir, m), m)[:, : x.shape[1]]
    return y.astype(np.float32)


def pan2(x: np.ndarray, pan: float) -> np.ndarray:
    th = (np.clip(pan, -1, 1) + 1) * np.pi / 4
    return np.stack([x * np.cos(th), x * np.sin(th)]) * np.sqrt(2)
