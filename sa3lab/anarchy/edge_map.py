"""E00-edge: the first anarchy listening experiment — where does music turn to mush?

Per sampler family, 4 fixed draw *directions*, each rendered as a temperature ladder with
common random numbers: the conditioning rng is re-seeded per (family, direction), so every
rung of a ladder is the same draw scaled further from the corpus centre, and the noise seed
is shared per direction across all families. Within a ladder only the reach changes;
across families at one direction, only the sampler.

  real         8 encoded prompts (6 corpus worlds + 2 of Xander's studio prompts), control
  tensor-pca   whole-tensor fit, sampler "pca",      T 0.5 1 1.5 2 3
  tensor-diag  whole-tensor fit, sampler "diagonal", T 0.5 1 1.5 2
  bag          L token vectors drawn from the token fit ("pca"), rest padding: the
               unordered bag the DiT reads, T 0.5 1 1.5 2 3
  (lengths for tensor/bag draws: one per direction, bootstrapped from the corpus.)

Renders via `headless.run` (studio format) at the UI defaults except Length (default 10 s:
the ladder needs one fixed length and 64 clips must stay listenable). Each clip's exact
`(256, 768)` conditioning is saved to `cond/<label>.npz` before it renders, and its
sidecar gets an `anarchy` block (family, direction, T, sampler, length, distance gauges,
seeds, fit, semantic-anarchy commit).

Run with the MLX venv (fit first: sa3lab/anarchy/fit.py):
  stable-audio-3/optimized/mlx/.venv/bin/python sa3lab/anarchy/edge_map.py \
      [--fit models/derived/anarchy/grammar-music-100] [--seconds 10] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from headless import AI_AUDIO, Job, run

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fit as fitmod  # noqa: E402  (also puts semantic-anarchy on sys.path)
from semantic_anarchy.distribution import EmbeddingDistribution  # noqa: E402

EXP = "E00-edge"
FAMILIES = {"tensor-pca": [0.5, 1, 1.5, 2, 3], "tensor-diag": [0.5, 1, 1.5, 2],
            "bag": [0.5, 1, 1.5, 2, 3]}
N_DIR = 4
NOISE_SEEDS = [1000 + d for d in range(N_DIR)]
REAL_CORPUS_LINES = [4, 34, 73, 97, 1, 61]     # jungle, symphonic adagio, mbira, reversed storm (edge), warehouse techno, irish session
REAL_XANDER = ["ambient downtempo sitar, drum beats, psychedelic house",
               "sitar melody, deep jungle roar sounds, rhytmic djembe tribal percussion, "
               "spiritual, acid house, breakbeat"]


def build_jobs(fit_dir: Path, out: Path, dit: str) -> list[Job]:
    tensor = EmbeddingDistribution.load(fit_dir / f"{dit}__tensor")
    token = EmbeddingDistribution.load(fit_dir / f"{dit}__token")
    report = json.loads((fit_dir / f"{dit}__report.json").read_text())
    corpus = AI_AUDIO / report["corpus"]
    prompts = [ln.strip() for ln in corpus.read_text().splitlines() if ln.strip()]
    pv = fitmod.padding_vector(dit)
    L_crop = tensor.feature_shape[0]
    common = {"experiment": EXP, "fit": str(fit_dir.relative_to(AI_AUDIO)), "dit": dit,
              "semantic_anarchy": report["semantic_anarchy"]}
    (out / "cond").mkdir(parents=True, exist_ok=True)

    jobs: list[Job] = []
    reals = [(prompts[i - 1], f"corpus line {i}") for i in REAL_CORPUS_LINES] + \
            [(p, "xander studio prompt") for p in REAL_XANDER]
    for k, (p, src) in enumerate(reals):
        jobs.append(Job(p, seed=NOISE_SEEDS[k % N_DIR],
                        extra={"anarchy": {**common, "family": "real", "direction": k % N_DIR,
                                           "source": src}}))

    for fi, (fam, temps) in enumerate(FAMILIES.items()):
        for d in range(N_DIR):
            cseed = 7000 + 100 * fi + d
            L = int(tensor.draw_lengths(1, rng=np.random.default_rng(5000 + 100 * fi + d),
                                        mode="corpus")[0])
            for T in temps:
                rng = np.random.default_rng(cseed)       # same draw every rung: CRN
                if fam == "bag":
                    content = token.sample(L, temperature=T, sampler="pca", rng=rng)
                    crop = np.tile(pv, (L_crop, 1)).astype(np.float32)
                    crop[:L] = content
                    tok_dist = float(np.mean([token.distance(v) for v in content]))
                    sampler = "token-pca"
                else:
                    sampler = fam.split("-")[1].replace("diag", "diagonal")
                    crop = tensor.sample(1, temperature=T, sampler=sampler, rng=rng,
                                         lengths=np.array([L]))[0]
                    tok_dist = None
                cond = fitmod.expand(crop.astype(np.float16), pv)
                label = f"anarchy {EXP} {fam} d{d} T{T:g} L{L}"
                rel = f"cond/{label.replace(' ', '_')}.npz"
                np.savez_compressed(out / rel, cond=cond)
                jobs.append(Job(label, seed=NOISE_SEEDS[d], cond=cond, extra={"anarchy": {
                    **common, "family": fam, "direction": d, "temperature": T,
                    "sampler": sampler, "length": L, "cond_seed": cseed, "cond_npz": rel,
                    "distance": round(float(tensor.distance(crop.astype(np.float32))), 3),
                    **({"token_distance": round(tok_dist, 3)} if tok_dist is not None else {}),
                }}))
    return jobs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fit", default="models/derived/anarchy/grammar-music-100")
    ap.add_argument("--dit", default="medium")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--out", default=f"outputs/anarchy/{EXP}")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    fit_dir = (AI_AUDIO / args.fit).resolve()
    out = (AI_AUDIO / args.out).resolve()
    jobs = build_jobs(fit_dir, out, args.dit)
    fams = {}
    for j in jobs:
        a = j.extra["anarchy"]
        fams.setdefault(a["family"], []).append(a.get("distance"))
    for f, ds in fams.items():
        print(f"[{EXP}] {f:>12}: {len(ds)} clips"
              + (f", distance {min(ds):.2f}–{max(ds):.2f}" if ds[0] is not None else ""), flush=True)
    run(out, jobs, overrides={"Length": args.seconds}, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
