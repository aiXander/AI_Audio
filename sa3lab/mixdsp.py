"""Mix DSP shared by the browser and the server (composer spec 4.1): one filter math, two runtimes.

The composer's per-piece **mix strip** (gain, pan, high-pass / low-pass at 12 or 24 dB/oct,
low shelf, mid peak, high shelf) and the **master bus** (gain + limiter) are monitored live in the
page with WebAudio (`sa3lab/ui/mixer.js`) and applied on the server when the reference WAV is
built (compose.build). What Xander hears while mixing must be what SA3 receives, so both sides
use the same filters:

- **Biquads**: coefficients are computed here with the *WebAudio spec's* formulas
  (BiquadFilterNode: lowpass / highpass take Q in dB, peaking takes a linear Q, shelves use S = 1)
  and run with ffmpeg's generic `biquad=b0..a2` filter; the page builds the same BiquadFilterNodes
  on an AudioContext pinned to 44.1 kHz, so the coefficients are identical. 24 dB/oct is two
  cascaded Butterworth sections (Q 0.5412 and 1.3066) on both sides.
- **Pan**: the StereoPannerNode equal-power algorithm for a stereo input, in numpy.
- **Gain**: dB → linear, in numpy.
- **Master**: gain, then ffmpeg `alimiter` (ceiling `limit` dBFS, auto-level off). The browser
  runs a lookahead brick-wall limiter worklet with the same 5 ms attack / 50 ms release
  (`ui/mixer.js` LIMITER): below the ceiling the monitor is sample-exact, on overs it is close
  (same ceiling, ~0.1 dB level difference when driven hard), not identical.

Mix strip JSON (every key optional; absent = bypass):

    {"gain": dB, "pan": -1..1, "hp": {"f": Hz, "slope": 12|24}, "lp": {"f": Hz, "slope": 12|24},
     "low": {"f": Hz, "g": dB}, "mid": {"f": Hz, "g": dB, "q": 0.1..10}, "high": {"f": Hz, "g": dB}}

`strip_chain(mix)` → the ordered biquad list (also served to the page for its response curve),
`apply_strip(y, mix)` → mixed audio, `apply_master(y, master)`, `meter(y)` → peak / LUFS.
Checked 2026-11-06 (`selftest()`): a 24 dB/oct high-pass at 150 Hz reads −3 dB at 150 Hz and
≈ −24 dB one octave below, the shelves hit their gain at DC / Nyquist.
"""
from __future__ import annotations

import json
import math
import subprocess

import numpy as np

SR = 44100
BUTTER4 = (0.5411961, 1.3065630)       # 4th-order Butterworth as two biquads (linear Q)
BUTTER2 = 0.7071068


def _db(q_lin: float) -> float:
    return 20 * math.log10(q_lin)


def biquad(kind: str, f: float, q: float = 1.0, g: float = 0.0, sr: int = SR) -> list[float]:
    """WebAudio BiquadFilterNode coefficients [b0, b1, b2, a0, a1, a2] (not normalised).
    lowpass / highpass: q in dB (the node's Q), peaking: q linear, shelves: S = 1, q unused."""
    f = max(10.0, min(float(f), sr / 2 * 0.999))
    w0 = 2 * math.pi * f / sr
    c, s = math.cos(w0), math.sin(w0)
    A = 10 ** (g / 40)
    if kind in ("lowpass", "highpass"):
        al = s / (2 * 10 ** (q / 20))
        if kind == "lowpass":
            b = [(1 - c) / 2, 1 - c, (1 - c) / 2]
        else:
            b = [(1 + c) / 2, -(1 + c), (1 + c) / 2]
        a = [1 + al, -2 * c, 1 - al]
    elif kind == "peaking":
        al = s / (2 * q)
        b = [1 + al * A, -2 * c, 1 - al * A]
        a = [1 + al / A, -2 * c, 1 - al / A]
    elif kind == "lowshelf":
        al = s / 2 * math.sqrt(2.0)
        k = 2 * math.sqrt(A) * al
        b = [A * ((A + 1) - (A - 1) * c + k), 2 * A * ((A - 1) - (A + 1) * c), A * ((A + 1) - (A - 1) * c - k)]
        a = [(A + 1) + (A - 1) * c + k, -2 * ((A - 1) + (A + 1) * c), (A + 1) + (A - 1) * c - k]
    elif kind == "highshelf":
        al = s / 2 * math.sqrt(2.0)
        k = 2 * math.sqrt(A) * al
        b = [A * ((A + 1) + (A - 1) * c + k), -2 * A * ((A - 1) + (A + 1) * c), A * ((A + 1) + (A - 1) * c - k)]
        a = [(A + 1) - (A - 1) * c + k, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - k]
    else:
        raise ValueError(kind)
    return b + a


def strip_chain(mix: dict | None) -> list[dict]:
    """The mix strip's biquads in signal order: hp, lp, low shelf, mid peak, high shelf.
    Each {"type", "f", "q", "g"} uses WebAudio's parameter meaning (mixer.js builds these)."""
    m = mix or {}
    out = []
    for key, kind in (("hp", "highpass"), ("lp", "lowpass")):
        x = m.get(key)
        if not x or not x.get("f"):
            continue
        qs = BUTTER4 if int(x.get("slope", 12)) == 24 else (BUTTER2,)
        out += [{"type": kind, "f": float(x["f"]), "q": _db(q), "g": 0.0} for q in qs]
    for key, kind in (("low", "lowshelf"), ("mid", "peaking"), ("high", "highshelf")):
        x = m.get(key)
        if not x or not x.get("f") or abs(float(x.get("g", 0))) < 1e-3:
            continue
        out.append({"type": kind, "f": float(x["f"]), "q": float(x.get("q", 1.0)) if kind == "peaking" else 1.0,
                    "g": float(x["g"])})
    return out


def _ffmpeg(y: np.ndarray, af: str) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                          "-af", af, "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                         input=np.ascontiguousarray(y.T).astype(np.float32).tobytes(),
                         capture_output=True, check=True).stdout
    out = np.frombuffer(raw, np.float32).reshape(-1, 2).T.copy()
    n = y.shape[1]
    return out[:, :n] if out.shape[1] >= n else np.pad(out, ((0, 0), (0, n - out.shape[1])))


def filters(y: np.ndarray, chain: list[dict]) -> np.ndarray:
    if not chain or y.shape[1] == 0:
        return y
    parts = []
    for bq in chain:
        b0, b1, b2, a0, a1, a2 = biquad(bq["type"], bq["f"], bq["q"], bq["g"])
        parts.append(f"biquad=b0={b0:.12g}:b1={b1:.12g}:b2={b2:.12g}:a0={a0:.12g}:a1={a1:.12g}:a2={a2:.12g}")
    return _ffmpeg(y, ",".join(parts))


def pan(y: np.ndarray, p: float) -> np.ndarray:
    """StereoPannerNode, stereo input (WebAudio spec, equal-power)."""
    p = max(-1.0, min(1.0, float(p)))
    if abs(p) < 1e-6:
        return y
    L, R = y[0], y[1]
    if p <= 0:
        x = (p + 1) * math.pi / 2
        gl, gr = math.cos(x), math.sin(x)
        return np.stack([L + R * gl, R * gr]).astype(np.float32)
    x = p * math.pi / 2
    gl, gr = math.cos(x), math.sin(x)
    return np.stack([L * gl, R + L * gr]).astype(np.float32)


def apply_strip(y: np.ndarray, mix: dict | None) -> np.ndarray:
    m = mix or {}
    y = filters(y, strip_chain(m))
    if m.get("gain"):
        y = y * np.float32(10 ** (float(m["gain"]) / 20))
    if m.get("pan"):
        y = pan(y, m["pan"])
    return y


def apply_master(y: np.ndarray, master: dict | None) -> np.ndarray:
    """Master gain, then a brick-wall limiter at `ceiling` dBFS (default −1; `limit: false` = off)."""
    m = master or {}
    if m.get("gain"):
        y = y * np.float32(10 ** (float(m["gain"]) / 20))
    if m.get("limit", True) and y.shape[1] and float(np.abs(y).max()) > 10 ** (float(m.get("ceiling", -1.0)) / 20):
        lim = 10 ** (float(m.get("ceiling", -1.0)) / 20)
        y = _ffmpeg(y, f"alimiter=limit={lim:.6f}:attack=5:release=50:level=0:latency=1")
    return y


def meter(y: np.ndarray) -> dict:
    """Sample peak (dBFS) and integrated loudness (LUFS, ffmpeg ebur128) of a mix."""
    if y.shape[1] < SR // 2:
        return {"peak": None, "lufs": None}
    pk = float(np.abs(y).max())
    return {"peak": round(20 * math.log10(max(pk, 1e-9)), 2), "lufs": lufs(y)}


def lufs(y: np.ndarray) -> float | None:
    if y.shape[1] < SR // 2:
        return None
    r = subprocess.run(["ffmpeg", "-v", "info", "-nostats", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                        "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                       input=np.ascontiguousarray(y.T).astype(np.float32).tobytes(), capture_output=True)
    txt = r.stderr.decode(errors="ignore")
    i = txt.rfind("I:")
    try:
        v = float(txt[i + 2:].split("LUFS")[0].strip())
        return None if v < -70 else round(v, 2)
    except ValueError:
        return None


def response(chain: list[dict], freqs) -> np.ndarray:
    """Magnitude response in dB of a chain (the page draws the same curve from getFrequencyResponse)."""
    w = 2 * np.pi * np.asarray(freqs, float) / SR
    z = np.exp(-1j * w)
    h = np.ones_like(z)
    for bq in chain:
        b0, b1, b2, a0, a1, a2 = biquad(bq["type"], bq["f"], bq["q"], bq["g"])
        h *= (b0 + b1 * z + b2 * z * z) / (a0 + a1 * z + a2 * z * z)
    return 20 * np.log10(np.abs(h) + 1e-12)


def selftest() -> None:
    hp = strip_chain({"hp": {"f": 150, "slope": 24}})
    r = response(hp, [75, 150, 1000])
    assert abs(r[1] + 3.01) < 0.2 and -26 < r[0] < -22 and abs(r[2]) < 0.1, r
    sh = strip_chain({"low": {"f": 200, "g": 6}, "high": {"f": 6000, "g": -6}})
    r = response(sh, [10, 21000])
    assert abs(r[0] - 6) < 0.2 and abs(r[1] + 6) < 0.6, r
    y = (np.random.default_rng(0).standard_normal((2, SR)) * 0.1).astype(np.float32)
    z = apply_strip(y, {"hp": {"f": 150, "slope": 12}, "gain": -6, "pan": 0.5})
    assert z.shape == y.shape
    print("mixdsp ok", json.dumps(hp[:1]))


if __name__ == "__main__":
    selftest()
