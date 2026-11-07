"""Per-range analysis for highlights and composer pieces (spec 3.1, 4.2): key, beat phase, downbeat,
loudness, 3-band balance, stereo width. numpy + ffmpeg only, like tempo.py.

    analyze(y)            → {bpm, bpm_conf, phase, downbeat, downbeat_conf, key, key_conf,
                             lufs, bands, width}           y = (2, n) float32 @ 44.1 kHz
    analyze_file(path, s0, s1)                            the same for a range of a file
    compatible(k1, k2)    → "same" | "relative" | "fifth" | None    key_distance(k1, k2) → semitones

- **Key:** chroma from a log-frequency STFT (22.05 kHz, 8192-point frames, 55-2000 Hz, each bin's
  log magnitude added to its pitch class), correlated with the Krumhansl-Kessler and Temperley
  major / minor profiles over the 24 keys (the two profiles' correlations averaged). Confidence =
  the winner's correlation minus the best key that is *not* a neighbour (its relative major /
  minor, the keys a fifth up / down and their relatives: those share 6-7 scale notes and nearly
  tie by design; a fifth-off answer is still "compatible"), so it measures how tonal the range
  is, and a drone or noise texture scores near 0: "no key" (key None
  when key_conf < KEY_CONF) is the common, valid answer for this library. Keys are named like
  "A minor" / "C# major".
- **Tempo** comes from tempo.py on the range (needs ≥ 4 s; shorter ranges are measured in a
  window around them by the caller). Beat phase / downbeat only when bpm_conf ≥ BEAT_CONF.
- **Beat phase** = compose.beat_phase (the kick band's onset envelope folded at the beat period).
- **Downbeat** (best-effort, always nudgeable): of the four beat positions in a bar, the one whose
  low band (30-150 Hz) carries the most onset energy, folded over the whole range. `downbeat` is
  the beat index 0-3 counted from the first beat; downbeat_conf = that bin's share above uniform.
- **Loudness** = integrated LUFS (ffmpeg ebur128). **Bands** = energy in dB of the dadaspec's three
  bands (bass < 200 Hz, mid 200-1500, high > 1500; upstream spec.py SPEC_BANDS), relative to the
  range's total. **Width** = side RMS / mid RMS (0 = mono, ~1 = wide / decorrelated).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [p for p in (str(HERE), str(HERE / "composer")) if p not in sys.path]

import tempo  # noqa: E402

SR = 44100
KEY_CONF = 0.15          # below this margin the key is reported as None (no key)
BEAT_CONF = 0.3          # same as compose.BEAT_CONF / spec 1.1
NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
BANDS = ((0, 200), (200, 1500), (1500, 16000))
# major / minor key profiles, tonic first
KK = (np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]),
      np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]))
TEMPERLEY = (np.array([5.0, 2.0, 3.5, 2.0, 4.5, 4.0, 2.0, 4.5, 2.0, 3.5, 1.5, 4.0]),
             np.array([5.0, 2.0, 3.5, 4.5, 2.0, 4.0, 2.0, 4.5, 3.5, 2.0, 1.5, 4.0]))


def decode(path, s0: float = 0.0, s1: float | None = None) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, s0):.4f}"]
    if s1 is not None:
        cmd += ["-t", f"{max(0.05, s1 - s0):.4f}"]
    cmd += ["-i", str(path), "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).T.copy()


def chroma(y: np.ndarray) -> np.ndarray:
    x = y.mean(0)
    x = np.interp(np.arange(0, len(x), 2), np.arange(len(x)), x).astype(np.float32)   # 22.05 kHz
    sr, n_fft, hop = SR / 2, 8192, 4096
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    f = np.fft.rfftfreq(n_fft, 1 / sr)
    sel = (f >= 55) & (f <= 2000)
    pc = (np.round(12 * np.log2(f[sel] / 440.0)) + 9).astype(int) % 12    # A = 9
    win = np.hanning(n_fft).astype(np.float32)
    out = np.zeros(12)
    n = 1 + (len(x) - n_fft) // hop
    for s in range(0, n, 256):
        idx = np.arange(n_fft)[None, :] + hop * np.arange(s, min(n, s + 256))[:, None]
        mag = np.abs(np.fft.rfft(x[idx] * win, axis=1))[:, sel]
        mag = np.log1p(1000 * mag / (mag.max() + 1e-9))
        out += np.bincount(np.tile(pc, mag.shape[0]), weights=mag.ravel(), minlength=12)
    return out / (out.sum() + 1e-9)


def _scores(ch: np.ndarray) -> np.ndarray:
    """(24,) correlation per key: 0-11 major (C..B), 12-23 minor."""
    s = np.zeros(24)
    for prof in (KK, TEMPERLEY):
        for m in (0, 1):
            p = prof[m]
            for k in range(12):
                s[m * 12 + k] += np.corrcoef(ch, np.roll(p, k))[0, 1] / 2
    return s


def key_name(i: int) -> str:
    return f"{NAMES[i % 12]} {'major' if i < 12 else 'minor'}"


def key_index(name: str | None) -> int | None:
    if not name:
        return None
    try:
        n, m = name.split()
        return NAMES.index(n) + (12 if m == "minor" else 0)
    except ValueError:
        return None


def _relative(i: int) -> int:
    return (i % 12 + 9) % 12 + 12 if i < 12 else (i % 12 + 3) % 12


def find_key(y: np.ndarray) -> dict:
    s = _scores(chroma(y))
    i = int(np.argmax(s))
    near = {i, _relative(i)} | {(i % 12 + d) % 12 + (12 if i >= 12 else 0) for d in (5, 7)}
    near |= {_relative(j) for j in list(near)}
    conf = float(s[i] - max(s[j] for j in range(24) if j not in near))
    return {"key": key_name(i) if conf >= KEY_CONF else None, "key_guess": key_name(i),
            "key_conf": round(conf, 3), "key_r": round(float(s[i]), 3)}


def key_distance(a: str | None, b: str | None) -> int | None:
    """Smallest transpose in semitones (−5..+6) that moves key a onto key b's tonic (same mode
    as a), or onto b's relative when the modes differ."""
    ia, ib = key_index(a), key_index(b)
    if ia is None or ib is None:
        return None
    if (ia < 12) != (ib < 12):
        ib = _relative(ib)
    d = (ib - ia) % 12
    return d - 12 if d > 6 else d


def compatible(a: str | None, b: str | None) -> str | None:
    ia, ib = key_index(a), key_index(b)
    if ia is None or ib is None:
        return None
    if ia == ib:
        return "same"
    if _relative(ia) == ib:
        return "relative"
    if (ia < 12) == (ib < 12) and (ib - ia) % 12 in (5, 7):
        return "fifth"
    return None


def _band_env(y, fmin, fmax):
    import compose
    return compose._band_env(y, fmin, fmax)


def downbeat(y: np.ndarray, bpm: float, phase: float) -> tuple[int, float]:
    env = _band_env(y, 30.0, 150.0)
    P = 60.0 * tempo.FPS / bpm
    e = np.zeros(4)
    for k in range(int(len(env) / P) + 1):
        c = int(round((phase - 0.072) * tempo.FPS + k * P))      # compose.PHASE_LAG: env peaks early
        if 0 <= c < len(env):
            e[k % 4] += env[max(0, c - 2):c + 3].sum()
    if e.sum() <= 0:
        return 0, 0.0
    i = int(np.argmax(e))
    return i, round(float(e[i] / e.sum() - 0.25) / 0.75, 3)


def bands(y: np.ndarray) -> list[float]:
    x = y.mean(0)
    n = min(len(x), SR * 60)
    seg = x[(len(x) - n) // 2:(len(x) + n) // 2]
    P = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2 if len(seg) else np.zeros(1)
    f = np.fft.rfftfreq(len(seg), 1 / SR) if len(seg) else np.zeros(1)
    tot = P.sum() + 1e-12
    return [round(float(10 * np.log10(P[(f >= a) & (f < b)].sum() / tot + 1e-9)), 2) for a, b in BANDS]


def width(y: np.ndarray) -> float:
    m, s = (y[0] + y[1]) / 2, (y[0] - y[1]) / 2
    return round(float(np.sqrt((s ** 2).mean()) / (np.sqrt((m ** 2).mean()) + 1e-9)), 3)


def measure_tempo(y: np.ndarray) -> dict:
    import compose
    if y.shape[1] < SR * 4:
        return {"bpm": None, "conf": 0.0}
    try:
        return compose.measure_tempo(y)
    except Exception:
        return {"bpm": None, "conf": 0.0}


def analyze(y: np.ndarray, tempo_ctx: np.ndarray | None = None) -> dict:
    """Everything for one range. `tempo_ctx`: audio around a short range to measure tempo in."""
    import compose
    import mixdsp
    t = measure_tempo(tempo_ctx if tempo_ctx is not None else y)
    out = {"bpm": t.get("bpm"), "bpm_conf": t.get("conf", 0.0)}
    if out["bpm"] and out["bpm_conf"] >= BEAT_CONF and y.shape[1] >= SR * 2:
        ph = compose.beat_phase(y, out["bpm"])
        db, dbc = downbeat(y, out["bpm"], ph)
        out.update(phase=round(ph, 4), downbeat=db, downbeat_conf=dbc)
    out.update(find_key(y))
    out.update(lufs=mixdsp.lufs(y), bands=bands(y), width=width(y))
    return out


def analyze_file(path, s0: float = 0.0, s1: float | None = None) -> dict:
    y = decode(path, s0, s1)
    ctx = None
    if y.shape[1] < SR * 8:                       # tempo needs context: 8 s centred on the range
        m = s0 + y.shape[1] / SR / 2
        ctx = decode(path, max(0.0, m - 4), max(0.0, m - 4) + 8)
    return analyze(y, ctx)


if __name__ == "__main__":
    import json
    a = sys.argv[1:]
    print(json.dumps(analyze_file(a[0], *(float(x) for x in a[1:3])), indent=1))
