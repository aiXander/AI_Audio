"""A random anarchy batch: every sampling number drawn from a distribution, no grid.

Where edge_map.py sweeps fixed ladders, this draws each clip's parameters independently
to cover the space with full spread (one batch rng, `--seed`, so a batch is reproducible):

  sampler    tensor-pca / tensor-diag / bag, balanced (shuffled ~n/3 each)
  T          uniform [--t-lo, --t-hi]            (default 0.3–3.0)
  direction  uniform integer [0, 99999]          (the conditioning rng seed)
  L          uniform integer [--l-lo, L_crop]    (content tokens; default 6–80)
  c          uniform integer [0, --c-hi]         (PCA axes skipped; tensor-pca only)
  noise seed uniform integer [1, 9999]

Each clip's prompt is its canonical spec string (anarchy/sampler.py), so the render is
regenerable from its own prompt; conditioning comes from `sampler.conditioning` and is
injected through `headless.run` (studio format) at the UI defaults except Length and CFG.
The exact tensor is saved to `cond/` and the sidecar gets an `anarchy` block.

Run with the MLX venv:
  stable-audio-3/optimized/mlx/.venv/bin/python sa3lab/anarchy/random_batch.py \
      --out outputs/anarchy/batch2-random --n 20 --seconds 120
"""
from __future__ import annotations

import argparse

import numpy as np

import sampler
from headless import AI_AUDIO, Job, run


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fit", default="grammar-music-100")
    ap.add_argument("--dit", default="medium")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--cfg", type=float, default=1.0)
    ap.add_argument("--t-lo", type=float, default=0.3)
    ap.add_argument("--t-hi", type=float, default=3.0)
    ap.add_argument("--l-lo", type=int, default=6)
    ap.add_argument("--c-hi", type=int, default=4)
    ap.add_argument("--seed", type=int, default=2, help="batch rng seed")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    out = (AI_AUDIO / args.out).resolve()
    (out / "cond").mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)
    L_max = next(f["L_crop"] for f in sampler.list_fits() if f["name"] == args.fit)
    fams = list(sampler.SAMPLERS)
    plan = [fams[i % len(fams)] for i in range(args.n)]
    rng.shuffle(plan)

    jobs = []
    for i, fam in enumerate(plan):
        spec = {"fit": args.fit, "sampler": fam,
                "T": round(float(rng.uniform(args.t_lo, args.t_hi)), 2),
                "d": int(rng.integers(0, 100_000)),
                "L": int(rng.integers(args.l_lo, L_max + 1)),
                "c": int(rng.integers(0, args.c_hi + 1)) if fam == "tensor-pca" else 0}
        seed = int(rng.integers(1, 10_000))
        label = sampler.canonical(spec)
        cond, info = sampler.conditioning(spec, args.dit)
        rel = f"cond/{i:02d}_{label.replace('anarchy: ', '').replace(' ', '_')}.npz"
        np.savez_compressed(out / rel, cond=cond)
        jobs.append(Job(label, seed=seed, cond=cond, extra={"anarchy": {
            **info, "experiment": out.name, "index": i, "noise_seed": seed, "cond_npz": rel,
            "batch": {"seed": args.seed, "T": [args.t_lo, args.t_hi], "L": [args.l_lo, L_max],
                      "c": [0, args.c_hi], "sampler": "balanced uniform"}}}))
        print(f"  {i:2d} seed {seed:>4}  dist {info['distance']:.2f}  {label}", flush=True)

    run(out, jobs, overrides={"Length": args.seconds, "CFG": args.cfg}, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
