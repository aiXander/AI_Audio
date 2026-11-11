"""Decoded image grid -> audio, four ways (phase retrieval, additive synthesis, two neural vocoders).

    uv run python invert.py <song work dir> [--methods gl,additive,bigvgan,vocos] [--gate -76]
    -> <work>/raw_<method>.wav (44.1 kHz mono, float), plus <work>/raw_<method>.spec.png

* gl       Fast Griffin-Lim (Perraudin et al. 2013, momentum 0.99), 400 iterations on a 2048-point
           STFT with hop 256 (87.5 % overlap: more redundancy = more consistent phase).
           Pure phase retrieval: no learned prior, so it is the most literal reading of the image
           but it has GL's typical phasey / metallic smear.
* bigvgan  NVIDIA BigVGAN-v2 universal vocoder, 44.1 kHz / 128 slaney-mel / hop 512
           (`nvidia/bigvgan_v2_44khz_128band_512x`, 489 MB generator). Trained on speech, music
           and environmental sound; the strongest open mel->wave model that fits the budget.
* additive Sinusoids + noise, read straight off the image (see run_additive).
* vocos    Vocos (`charactr/vocos-mel-24khz`, 54 MB): ConvNeXt backbone predicting ISTFT
           coefficients from 100 HTK-mels at 24 kHz. Trained on LibriTTS (speech), so on music
           it is a curiosity; resampled to 44.1 kHz.

The vocoders get their *own* mel of the linear magnitude from `specgrid.grid_to_linear`, so
they see exactly what they would have seen from a real recording with that spectrum.
"""
from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import torchaudio

import specgrid

HF = Path(__file__).resolve().parents[2] / "models" / "hf-cache"
SR = 44100


def _snapshot(repo: str) -> Path:
    d = HF / f"models--{repo.replace('/', '--')}" / "snapshots"
    return next(d.iterdir())


def fast_griffin_lim(mag: np.ndarray, n_fft: int, hop: int, n_iter: int = 400,
                     alpha: float = 0.99, seed: int = 0) -> np.ndarray:
    torch.manual_seed(seed)
    m = torch.from_numpy(mag).double()
    win = torch.hann_window(n_fft, dtype=torch.float64)
    phase = torch.rand_like(m) * 2 * np.pi
    c = m * torch.exp(1j * phase)
    prev = c.clone()
    length = (m.shape[1] - 1) * hop
    for _ in range(n_iter):
        y = torch.istft(c, n_fft, hop, window=win, length=length)
        proj = torch.stft(y, n_fft, hop, window=win, return_complex=True)
        t = proj + alpha * (proj - prev)
        prev = proj
        c = m * torch.exp(1j * torch.angle(t))
    return torch.istft(c, n_fft, hop, window=win, length=length).float().numpy()


def run_gl(db: np.ndarray, gate: float) -> np.ndarray:
    n_fft, hop = 2048, 256
    times = np.arange(int(specgrid.DURATION * SR / hop) + 1) * hop / SR   # center=True frames
    mag = specgrid.grid_to_linear(db, SR, n_fft, times, gate)
    return fast_griffin_lim(mag, n_fft, hop)[: int(specgrid.DURATION * SR)]


_BIGVGAN = None


def run_bigvgan(db: np.ndarray, gate: float) -> np.ndarray:
    global _BIGVGAN
    snap = _snapshot("nvidia/bigvgan_v2_44khz_128band_512x")
    if _BIGVGAN is None:
        sys.path.insert(0, str(snap))
        sys.modules.setdefault("meldataset", types.SimpleNamespace(MAX_WAV_VALUE=32767.0))  # avoid librosa
        import bigvgan  # noqa: E402  (NVIDIA's model code shipped with the checkpoint)
        h = bigvgan.load_hparams_from_json(snap / "config.json")
        model = bigvgan.BigVGAN(h, use_cuda_kernel=False)
        sd = torch.load(snap / "bigvgan_generator.pt", map_location="cpu", weights_only=True)
        model.load_state_dict(sd["generator"])
        model.remove_weight_norm()
        _BIGVGAN = model.eval()
    n_fft, hop, n_mels = 2048, 512, 128
    n_frames = int(np.ceil(specgrid.DURATION * SR / hop))
    times = (np.arange(n_frames) * hop + hop / 2) / SR            # center=False + (n_fft-hop)/2 pad
    mag = specgrid.grid_to_linear(db, SR, n_fft, times, gate)
    fb = torchaudio.functional.melscale_fbanks(n_fft // 2 + 1, 0.0, SR / 2, n_mels, SR,
                                               norm="slaney", mel_scale="slaney")  # == librosa
    mel = torch.log(torch.clamp(fb.T @ torch.from_numpy(mag), min=1e-5))
    with torch.inference_mode():
        y = _BIGVGAN(mel[None]).squeeze().numpy()
    return y[: int(specgrid.DURATION * SR)]


_VOCOS = None


def run_vocos(db: np.ndarray, gate: float) -> np.ndarray:
    global _VOCOS
    from vocos import Vocos
    if _VOCOS is None:
        snap = _snapshot("charactr/vocos-mel-24khz")
        v = Vocos.from_hparams(str(snap / "config.yaml"))
        v.load_state_dict(torch.load(snap / "pytorch_model.bin", map_location="cpu", weights_only=True))
        _VOCOS = v.eval()
    sr, n_fft, hop = 24000, 1024, 256
    times = np.arange(int(specgrid.DURATION * sr / hop) + 1) * hop / sr
    mag = specgrid.grid_to_linear(db, sr, n_fft, times, gate)
    fb = torchaudio.functional.melscale_fbanks(n_fft // 2 + 1, 0.0, sr / 2, 100, sr,
                                               norm=None, mel_scale="htk")
    mel = torch.log(torch.clamp(fb.T @ torch.from_numpy(mag), min=1e-7))
    with torch.inference_mode():
        y = _VOCOS.decode(mel[None].float()).squeeze()
    y = torchaudio.functional.resample(y, sr, SR).numpy()
    return y[: int(specgrid.DURATION * SR)]


def _peaks(col_db: np.ndarray, gate: float, prom: float = 6.0, reach: int = 4):
    """Local maxima of one image column (ascending mel rows): fractional rows + peak dB."""
    c = col_db
    n = len(c)
    m = np.zeros(n, bool)
    m[1:-1] = (c[1:-1] >= c[:-2]) & (c[1:-1] > c[2:]) & (c[1:-1] > gate)
    out = []
    for r in np.where(m)[0]:
        lo, hi = max(0, r - reach), min(n, r + reach + 1)
        if c[r] - c[lo:hi].min() < prom:
            continue
        a, b, g = c[r - 1], c[r], c[r + 1]                 # parabolic interpolation in dB
        den = a - 2 * b + g
        d = 0.5 * (a - g) / den if den != 0 else 0.0
        out.append((r + float(np.clip(d, -0.5, 0.5)), b - 0.25 * (a - g) * d))
    return out


def run_additive(db: np.ndarray, gate: float) -> np.ndarray:
    """Sinusoids + noise (SMS-style, McAulay-Quatieri tracking), read straight off the image.

    Every image column: pick spectral peaks (prominence >= 6 dB), refine them to sub-row
    precision, and chain them across columns into partial tracks (nearest frequency within
    3 %). Each track is an oscillator with continuous phase whose frequency and amplitude are
    interpolated sample by sample, so glides glide and sustained tones are perfectly clean (no
    Griffin-Lim phasiness). Whatever is not near a peak (noise clouds, the broadband part of
    clicks) is the residual, rendered as random-phase noise with the residual's magnitude.
    """
    h, w = db.shape
    asc = db[::-1]                                         # row 0 = 0 Hz
    dm = specgrid.MEL_MAX / h
    hop = specgrid.DURATION * SR / w                       # samples per column
    n = int(specgrid.DURATION * SR)
    y = np.zeros(n)
    tracks, active = [], []                                # track: list of (col, hz, amp)
    resid = asc.copy()
    for x in range(w):
        pk = _peaks(asc[:, x], gate)
        hz = [700 * (10 ** ((r + 0.5) * dm / 2595) - 1) for r, _ in pk]
        amp = [specgrid.REF_AMP * 10 ** (d / 20) for _, d in pk]
        for r, _ in pk:                                    # carve the peak lobes out of the residual
            r0 = int(round(r))
            resid[max(0, r0 - 3):r0 + 4, x] = -120
        used, nxt = set(), []
        for t in active:
            last_hz = tracks[t][-1][1]
            best, bd = None, 0.03
            for i, f in enumerate(hz):
                if i not in used and abs(f - last_hz) / last_hz < bd:
                    best, bd = i, abs(f - last_hz) / last_hz
            if best is not None:
                used.add(best)
                tracks[t].append((x, hz[best], amp[best]))
                nxt.append(t)
        for i in range(len(hz)):
            if i not in used:
                tracks.append([(x, hz[i], amp[i])])
                nxt.append(len(tracks) - 1)
        active = nxt
    for tr in tracks:
        cols = np.array([c for c, _, _ in tr], float)
        f = np.array([v for _, v, _ in tr])
        a = np.array([v for _, _, v in tr])
        # fade in/out over one column on each side
        cols = np.r_[cols[0] - 1, cols, cols[-1] + 1]
        f = np.r_[f[0], f, f[-1]]
        a = np.r_[0.0, a, 0.0]
        t = (cols + 0.5) * hop
        s0, s1 = max(0, int(t[0])), min(n, int(t[-1]) + 1)
        if s1 <= s0:
            continue
        ts = np.arange(s0, s1)
        fi = np.interp(ts, t, f)
        ai = np.interp(ts, t, a)
        ph = 2 * np.pi * np.cumsum(fi) / SR + np.random.default_rng(len(tr)).uniform(0, 2 * np.pi)
        y[s0:s1] += ai * np.sin(ph)
    # residual noise: random-phase ISTFT of the residual's magnitude
    n_fft, nhop = 2048, 512
    times = np.arange(int(specgrid.DURATION * SR / nhop) + 1) * nhop / SR
    mag = specgrid.grid_to_linear(resid[::-1], SR, n_fft, times, gate)
    rng = np.random.default_rng(0)
    spec = torch.from_numpy(mag * np.exp(1j * rng.uniform(0, 2 * np.pi, mag.shape)))
    noise = torch.istft(spec, n_fft, nhop, window=torch.hann_window(n_fft, dtype=torch.float64),
                        length=n).numpy()
    return (y + noise).astype(np.float32)


METHODS = {"gl": run_gl, "bigvgan": run_bigvgan, "vocos": run_vocos, "additive": run_additive}


def main(work: Path, methods: list[str], gate: float = -76.0, suffix: str = "") -> dict:
    db = np.load(work / "decoded.npz")["db"]
    h, w = db.shape
    report = {}
    for name in methods:
        y = METHODS[name](db, gate)
        y = y / (np.abs(y).max() + 1e-9) * 0.89
        tag = f"raw_{name}{suffix}"
        sf.write(work / f"{tag}.wav", y, SR, subtype="FLOAT")
        g = specgrid.audio_to_grid(y, SR, h, w)
        specgrid.grid_png(g, work / f"{tag}.spec.png")
        report[tag] = specgrid.fidelity(db, g)
        print(tag, report[tag], flush=True)
    specgrid.grid_png(db, work / "source.spec.png")
    prev = json.loads((work / "fidelity.json").read_text()) if (work / "fidelity.json").exists() else {}
    prev.update(report)
    (work / "fidelity.json").write_text(json.dumps(prev, indent=2))
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("work", type=Path)
    ap.add_argument("--methods", default="gl,bigvgan,vocos")
    ap.add_argument("--gate", type=float, default=-76.0)
    ap.add_argument("--suffix", default="")
    a = ap.parse_args()
    main(a.work, a.methods.split(","), a.gate, a.suffix)
