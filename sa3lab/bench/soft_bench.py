"""Bench for the per-frame strength sampler (segments.py `soft_sample`): invariants, ladders,
spatial curves and upstream a2a, rendered straight through upstream's `run_generation` with
segments' sampler hook installed (no studio, no history entries).

    stable-audio-3/optimized/mlx/.venv/bin/python sa3lab/bench/soft_bench.py \
        --ref outputs/soft-denoise-tests/ref.wav --prompt "…" --jobs jobs.json \
        --out outputs/soft-denoise-tests/ladder [--dit medium --decoder same-l --steps 8]

`jobs.json` is a list of {"name": str, "seed": int, and one of
    "plan": <segments box dict>     strength curve (segments.parse_plan contract)
    "a2a": <init noise σ>           upstream audio → audio from the reference
    "t2a": true}                    plain text → audio
Optional per job: "prompt", "steps".

Writes `<name>.mp3` + `<name>.lat.npy` (the sampled latents) per job and appends one row per
job to `metrics.jsonl`: similarity to the reference, whole clip and per strength region
(kept u = 0 / soft / full u = 1, from the plan): latent cosine per frame (the sampler's own
space), and from audio log-band spectrogram, chroma and onset-envelope correlation (numpy
only). `--invariants` runs the three bit-exactness checks on the reference instead.
Resumable: jobs whose mp3 exists are skipped (metrics recomputed from the saved files).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
AI_AUDIO = HERE.parents[1]
MLX = AI_AUDIO / "stable-audio-3" / "optimized" / "mlx"
os.environ.setdefault("HF_HUB_CACHE", str(AI_AUDIO / "models" / "hf-cache"))
SR = 44100
SPL = 4096


def _setup():
    os.chdir(MLX)
    sys.path[:0] = [str(MLX), str(MLX / "scripts"), str(AI_AUDIO / "sa3lab")]
    import sa3_gradio as up
    import segments
    import noise
    segments.install_sampler(up)

    class _Lib:                      # noise.install wants a library to stamp; we have none
        gen_record = staticmethod(lambda *a, **k: {})
    noise.install(_Lib)              # the studio's length-stable seed → noise mapping
    cap = {}
    inner = up.sample_flow_pingpong

    def capture(model_fn, x, sigmas, *a, paste_back=None, **kw):
        cap["ref"] = None if paste_back is None else np.array(paste_back[0].astype(up.mx.float32))
        out = inner(model_fn, x, sigmas, *a, paste_back=paste_back, **kw)
        cap["lat"] = np.array(out.astype(up.mx.float32))
        return out
    up.sample_flow_pingpong = capture
    return up, segments, cap


def _write_mp3(audio: np.ndarray, path: Path):
    pcm = (np.clip(audio.T, -1, 1) * 32767).astype(np.int16).tobytes()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(SR), "-ac", "2", "-i", "-",
                    "-c:a", "libmp3lame", "-b:a", "320k", str(path)], input=pcm, check=True)


def _pcm(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


# ── audio features (numpy only: the MLX venv has no scipy) ────────────────────────────────────────────
def _feats(y: np.ndarray):
    n = 1 + max(0, (len(y) - 4096) // 1024)
    idx = np.arange(4096)[None, :] + 1024 * np.arange(n)[:, None]
    fr = np.pad(y, (0, 4096))[idx] * np.hanning(4096)[None, :]
    P = (np.abs(np.fft.rfft(fr, axis=1)) ** 2).T            # (F, frames), hop 1024 = SPL / 4
    f = np.fft.rfftfreq(4096, 1 / SR)
    edges = np.geomspace(40, 16000, 49)
    band = np.stack([P[(f >= a) & (f < b)].sum(0) for a, b in zip(edges[:-1], edges[1:])])
    logb = np.log10(band + 1e-9)
    pc = np.round(12 * np.log2(np.maximum(f, 1) / 440.0)) % 12
    sel = (f > 60) & (f < 5000)
    chroma = np.stack([P[sel & (pc == k)].sum(0) for k in range(12)])
    chroma = chroma / (chroma.sum(0, keepdims=True) + 1e-9)
    flux = np.maximum(0, np.diff(logb, axis=1)).sum(0)
    flux = np.r_[0, flux]
    return logb, chroma, flux


def _corr(a, b):
    a, b = a - a.mean(), b - b.mean()
    d = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / d) if d > 0 else float("nan")


def metrics(out_mp3: Path, ref_mp3: Path, lat: np.ndarray | None, ref_lat: np.ndarray | None,
            u: np.ndarray | None) -> dict:
    yo, yr = _pcm(out_mp3), _pcm(ref_mp3)
    n = min(len(yo), len(yr))
    fo, fr = _feats(yo[:n]), _feats(yr[:n])
    nf = min(fo[0].shape[1], fr[0].shape[1])
    regions = {"all": np.ones(nf, bool)}
    if u is not None:
        uf = np.repeat(u, 4)[:nf]                          # latent frame → 4 STFT frames
        uf = np.r_[uf, np.full(nf - len(uf), uf[-1])] if len(uf) < nf else uf
        regions.update(kept=uf <= 0, soft=(uf > 0) & (uf < 1), full=uf >= 1)
    out = {}
    for rn, m in regions.items():
        if m.sum() < 8:
            continue
        r = {"spec": _corr(fo[0][:, :nf][:, m], fr[0][:, :nf][:, m]),
             "chroma": _corr(fo[1][:, :nf][:, m], fr[1][:, :nf][:, m]),
             "onset": _corr(fo[2][:nf][m], fr[2][:nf][m])}
        if lat is not None and ref_lat is not None:
            T = min(lat.shape[-1], ref_lat.shape[-1])
            a, b = lat[0, :, :T], ref_lat[0, :, :T]
            cos = (a * b).sum(0) / (np.linalg.norm(a, axis=0) * np.linalg.norm(b, axis=0) + 1e-9)
            ul = u[:T] if u is not None else np.zeros(T)
            lm = {"all": np.ones(T, bool), "kept": ul <= 0, "soft": (ul > 0) & (ul < 1), "full": ul >= 1}[rn]
            r["lat_cos"] = float(cos[lm].mean())
        out[rn] = {k: round(v, 4) for k, v in r.items()}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--jobs")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dit", default="medium")
    ap.add_argument("--decoder", default="same-l")
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--cfg", type=float, default=1.0)
    ap.add_argument("--invariants", action="store_true")
    a = ap.parse_args()
    ref = Path(a.ref).resolve()
    out = Path(a.out).resolve()
    jobs_path = Path(a.jobs).resolve() if a.jobs else None
    out.mkdir(parents=True, exist_ok=True)
    up, segments, cap = _setup()
    seconds = round(up.read_audio_any(str(ref)).shape[-1] / SR, 3)
    T = int(np.ceil(seconds * SR / SPL))

    def render(prompt, seed, steps, plan=None, a2a=None, upstream_range=None):
        segments._active = None
        kw = dict(a2a_audio_path=None, inpaint_audio_path=None, inpaint_range_sec=None)
        sigma = 1.0
        if a2a is not None:
            kw["a2a_audio_path"], sigma = str(ref), float(a2a)
        if upstream_range is not None:                 # upstream's own single-range inpaint
            kw["inpaint_audio_path"], kw["inpaint_range_sec"] = str(ref), upstream_range
        if plan is not None:
            p = segments.parse_plan(plan)
            kw["inpaint_audio_path"] = str(ref)
            kw["inpaint_range_sec"] = (0.0, seconds)
            segments._active = p
        try:
            audio, tm = up.run_generation(a.dit, a.decoder, prompt, "", seconds, steps, seed, a.cfg,
                                          1.0, sigma, lora_specs=None, **kw)
        finally:
            segments._active = None
        return audio, cap.get("lat"), cap.get("ref"), tm

    if a.invariants:
        seed, p = 7, a.prompt
        mid = (round(seconds * 0.35, 3), round(seconds * 0.7, 3))
        _, l0, r0, _ = render(p, seed, a.steps, {"strength": [[0, 0]]})
        print(f"A strength 0 everywhere: max |out − ref| = {np.abs(l0 - r0).max():.3g} "
              f"(bit-exact: {np.array_equal(l0, r0)})")
        _, l1, _, _ = render(p, seed, a.steps, {"strength": [[0, 1]]})
        _, lt, _, _ = render(p, seed, a.steps, None)
        print(f"B strength 1 everywhere vs text→audio, same seed: max |Δ| = {np.abs(l1 - lt).max():.3g}, "
              f"cos = {_corr(l1, lt):.5f}, bit-exact: {np.array_equal(l1, lt)}")
        g = SR / SPL                                    # span edges on the latent grid, as the UI snaps
        mid = (round(round(seconds * 0.35 * g) / g, 3), round(round(seconds * 0.7 * g) / g, 3))
        _, lo, ro, _ = render(p, seed, a.steps, upstream_range=mid)
        _, ls, _, _ = render(p, seed, a.steps, {"inpaint": [list(mid)]})
        _, lk, _, _ = render(p, seed, a.steps, {"strength": [[mid[0], 0], [mid[0], 1], [mid[1], 1], [mid[1], 0]]})
        print(f"C span {mid}: upstream's own From/To inpaint vs legacy spans box: max |Δ| = "
              f"{np.abs(lo - ls).max():.3g} (bit-exact {np.array_equal(lo, ls)}); vs the same span as keys: "
              f"bit-exact {np.array_equal(lo, lk)}")
        keep = segments.frame_strength(segments.parse_plan({"inpaint": [mid]})["keys"], lo.shape[-1]) <= 0
        print(f"  kept frames equal the reference: {np.array_equal(ls[..., keep], ro[..., keep])}")
        return

    jobs = json.loads(jobs_path.read_text())
    ref_mp3 = out / "_ref.mp3"
    if not ref_mp3.exists():
        _write_mp3(up.read_audio_any(str(ref)), ref_mp3)
    ref_lat = None
    mpath = out / "metrics.jsonl"
    t0 = time.time()
    for n, j in enumerate(jobs, 1):
        mp3, latf = out / f"{j['name']}.mp3", out / f"{j['name']}.lat.npy"
        plan = j.get("plan")
        u = segments.frame_strength(segments.parse_plan(plan)["keys"], T, SR / SPL) if plan else None
        if not mp3.exists():
            t1 = time.time()
            audio, lat, rl, tm = render(j.get("prompt", a.prompt), int(j["seed"]), int(j.get("steps", a.steps)),
                                        plan=plan, a2a=j.get("a2a"))
            _write_mp3(audio, mp3)
            np.save(latf, lat.astype(np.float16))
            if rl is not None and not (out / "_ref.lat.npy").exists():
                np.save(out / "_ref.lat.npy", rl.astype(np.float16))
            print(f"[soft] {n}/{len(jobs)} {j['name']} {time.time() - t1:.0f}s", flush=True)
        if ref_lat is None and (out / "_ref.lat.npy").exists():
            ref_lat = np.load(out / "_ref.lat.npy").astype(np.float32)
        lat = np.load(latf).astype(np.float32) if latf.exists() else None
        row = {"name": j["name"], "seed": j["seed"], "plan": plan, "a2a": j.get("a2a"),
               "steps": j.get("steps", a.steps), "dit": a.dit, "decoder": a.decoder,
               "prompt": j.get("prompt", a.prompt),
               "m": metrics(mp3, ref_mp3, lat, ref_lat, u)}
        with mpath.open("a") as f:
            f.write(json.dumps(row) + "\n")
    print(f"[soft] done in {(time.time() - t0) / 60:.1f} min → {out}")


if __name__ == "__main__":
    main()
