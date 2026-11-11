"""Phase-0 parity + speed probe: the same SA3 job on any machine, so the Mac and the box can be compared.

    # on each machine (Mac: the MLX venv; box: ~/SSD2TB/sa3-worker/.venv)
    <python> sa3lab/beast/parity.py dump  --out outputs/beast-parity/<host>   [--seconds 120] [--render 6]
    # then, with both folders on one machine
    <python> sa3lab/beast/parity.py compare outputs/beast-parity/mac outputs/beast-parity/beast

`dump` writes `noise_s4825_T<T>.npy` (the start noise of seed 4825 as the studio draws it: noise.py's
fixed 4608-frame canvas, cropped to the clip length) and, unless `--render 0`, one render of a fixed
job (`render.wav`, float32, the decoder's raw output) plus `timings.json` (per-phase ms, one
warm-up run first so model loads aren't timed). `--bench` adds the Phase-0 timing set (120 s at
cfg 1 and cfg 3). `compare` reports bit-equality of the noise and the max / RMS difference of the renders.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
AI_AUDIO = HERE.parents[1]
MLX = AI_AUDIO / "stable-audio-3" / "optimized" / "mlx"
os.environ.setdefault("HF_HUB_CACHE", str(AI_AUDIO / "models" / "hf-cache"))
SEED = 4825
PROMPT = "TrackType: Music, Genre: Dub Techno, BPM: 120, Moods: Hypnotic, deep chords, tape hiss"


def _up():
    sys.path[:0] = [str(MLX), str(MLX / "scripts"), str(AI_AUDIO / "sa3lab")]
    import sa3_gradio as up
    import noise

    class _Lib:                       # noise.install wraps library.gen_record; nothing reads it here
        gen_record = staticmethod(lambda *a, **k: {})
    noise.install(_Lib)
    return up


def _job(up, seconds, cfg=1.0):
    return dict(dit_name="medium", decoder_name="same-l", prompt=PROMPT, negative_prompt="",
                seconds=float(seconds), steps=8, seed=SEED, cfg=float(cfg), apg=1.0, sigma_max=1.0)


def dump(a) -> None:
    import numpy as np
    up = _up()
    mx = up.mx
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    T = -(-int(a.seconds * up.SAMPLE_RATE) // up.SAMPLES_PER_LATENT)
    n = mx.random.normal((1, 256, T), dtype=mx.float16, key=mx.random.key(SEED))
    np.save(out / f"noise_s{SEED}_T{T}.npy", np.array(n))
    meta = {"host": platform.node(), "mlx": mx.__version__, "device": str(mx.default_device()), "T": T}
    print(f"noise T={T} written", flush=True)

    def timed(seconds, cfg=1.0):
        t0 = time.time()
        y, t = up.run_generation(**_job(up, seconds, cfg))
        wall = (time.time() - t0) * 1000
        keep = {k: round(float(v), 1) for k, v in t.items() if k.endswith("_ms")}
        return y, {**keep, "wall_ms": round(wall, 1)}

    if a.render:
        timed(a.render)                                    # warm-up: loads T5, DiT, decoder
        y, t = timed(a.render)
        import soundfile as sf
        sf.write(out / "render.wav", y.T, up.SAMPLE_RATE, subtype="FLOAT")
        meta["render"] = {"seconds": a.render, **t}
        print(f"render {a.render} s: {t}", flush=True)
    if a.bench:
        meta["bench"] = {}
        for cfg in (1.0, 3.0):
            timed(120, cfg) if cfg == 1.0 else None        # warm the 120 s DiT once
            _, t = timed(120, cfg)
            meta["bench"][f"120s_cfg{cfg:g}"] = t
            print(f"120 s cfg {cfg:g}: {t}", flush=True)
    (out / "timings.json").write_text(json.dumps(meta, indent=1))


def compare(a) -> None:
    import numpy as np
    A, B = Path(a.a), Path(a.b)
    for f in sorted(A.glob("noise_*.npy")):
        g = B / f.name
        if not g.exists():
            print(f"{f.name}: missing in {B}")
            continue
        x, y = np.load(f), np.load(g)
        same = x.shape == y.shape and np.array_equal(x.view(np.uint16), y.view(np.uint16))
        print(f"{f.name}: {'bit-identical' if same else 'DIFFERENT'}"
              + ("" if same else f" (max |Δ| {np.abs(x.astype(np.float32) - y.astype(np.float32)).max():.4g},"
                 f" corr {np.corrcoef(x.ravel().astype(np.float32), y.ravel().astype(np.float32))[0, 1]:.4f})"))
    if (A / "render.wav").exists() and (B / "render.wav").exists():
        import soundfile as sf
        x, _ = sf.read(A / "render.wav", dtype="float32")
        y, _ = sf.read(B / "render.wav", dtype="float32")
        n = min(len(x), len(y))
        d = x[:n] - y[:n]
        print(f"render.wav: max |Δ| {np.abs(d).max():.4g}, rms Δ {np.sqrt((d ** 2).mean()):.4g}, "
              f"rms signal {np.sqrt((x[:n] ** 2).mean()):.4g}, corr {np.corrcoef(x[:n].ravel(), y[:n].ravel())[0, 1]:.4f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--out", required=True)
    d.add_argument("--seconds", type=float, default=120.0)
    d.add_argument("--render", type=float, default=6.0, help="seconds of the parity render, 0 = none")
    d.add_argument("--bench", action="store_true", help="also time 120 s renders at cfg 1 and 3")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    a = ap.parse_args()
    dump(a) if a.cmd == "dump" else compare(a)
