"""How much of T5Gemma's conditioning space does a prompt corpus span?

The anarchy sampler can only explore the hull a corpus spans, so before mining/fitting,
measure the corpus. Encodes each corpus (`.txt`, one prompt per line) through SA3's
T5Gemma once and reports, per corpus:

  nn_dist      mean nearest-neighbour cosine distance of mean-pooled prompt vectors
               (pooled over real tokens only): low = clumps / near-duplicates
  pair_dist    mean pairwise cosine distance: overall spread
  total_var    trace of the covariance of the pooled vectors (raw units, comparable
               across corpora encoded by the same model)
  erank        entropy effective rank of the pooled-vector PCA spectrum (exp of the
               spectral entropy); pr = participation ratio; d90 = PCs for 90 % variance
  tok_erank    the same effective rank over every real token vector (the per-token
               space the per-token fits will see)

plus the most-similar prompt pairs (world ids from the sibling `.jsonl` when present):
merge or replace those.

Trap: on a grammar corpus the pooled vector is dominated by the *template* (named fields,
lowercase, TrackType prefix), so the nearest pairs are template twins, not musical twins.
`--worlds` measures a catalogue in two fixed renders (description only; all fields in one
template) where the template cancels and the pairs are genuinely musical neighbours.
Run from anywhere with the MLX venv:

  stable-audio-3/optimized/mlx/.venv/bin/python sa3lab/anarchy/diversity.py \
      sa3lab/anarchy/corpora/grammar-music-100.txt --worlds sa3lab/anarchy/worlds.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MLX = HERE.parents[1] / "stable-audio-3/optimized/mlx"
NPZ = MLX / "models/mlx/t5gemma_f16.npz"


def encode(prompts: list[str], batch: int = 16) -> tuple[np.ndarray, list[np.ndarray]]:
    """→ (N, 768) mean-pooled vectors, list of (n_tok_i, 768) real-token matrices."""
    import mlx.core as mx
    sys.path.insert(0, str(MLX))
    from models.defs.t5gemma_mlx import T5Gemma

    model = encode.model = getattr(encode, "model", None) or T5Gemma.from_npz(str(NPZ))
    pooled, toks = [], []
    for i in range(0, len(prompts), batch):
        emb, mask = model.encode(prompts[i:i + batch], max_len=256)
        emb = np.asarray(emb.astype(mx.float32))
        mask = np.asarray(mask).astype(bool)
        for e, m in zip(emb, mask):
            toks.append(e[m])
            pooled.append(e[m].mean(0))
    return np.stack(pooled), toks


def _spectrum(x: np.ndarray) -> np.ndarray:
    x = x - x.mean(0)
    s = np.linalg.svd(x, compute_uv=False) ** 2
    return s / s.sum()


def stats(pooled: np.ndarray, toks: list[np.ndarray]) -> dict:
    u = pooled / np.linalg.norm(pooled, axis=1, keepdims=True)
    cos = u @ u.T
    n = len(u)
    off = cos[~np.eye(n, dtype=bool)]
    np.fill_diagonal(cos, -np.inf)
    p = _spectrum(pooled)
    tp = _spectrum(np.concatenate(toks))
    return dict(
        n=n,
        nn_dist=float((1 - cos.max(1)).mean()),
        nn_min=float((1 - cos.max(1)).min()),
        pair_dist=float((1 - off).mean()),
        total_var=float(np.var(pooled - pooled.mean(0), axis=0).sum()),
        erank=float(np.exp(-(p * np.log(p + 1e-12)).sum())),
        pr=float(1 / (p ** 2).sum()),
        d90=int(np.searchsorted(np.cumsum(p), 0.9) + 1),
        tok_erank=float(np.exp(-(tp * np.log(tp + 1e-12)).sum())),
        tokens=int(np.median([len(t) for t in toks])),
    ), cos


def _world_views(path: Path) -> dict[str, tuple[list[str], list[str]]]:
    """Two fixed renders of a world catalogue, so template noise cancels out:
    `desc` (the description alone) and `fields` (every field, one fixed template)."""
    import importlib.util
    name = f"worlds_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = sys.modules[name] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ws = mod.WORLDS
    ids = [w.id for w in ws]
    fields = [", ".join(f"{k}: {', '.join(v)}" for k, v in (
        ("Genre", w.genres), ("Instruments", w.instruments), ("Moods", w.moods)))
        + (f", BPM: {sum(w.bpm) // 2}" if w.bpm else "") for w in ws]
    return {"desc": ([w.desc for w in ws], ids), "fields": (fields, ids)}


def report(name: str, prompts: list[str], ids: list[str], pairs: int) -> None:
    s, cos = stats(*encode(prompts))
    print(f"\n== {name}\n   " + "  ".join(
        f"{k} {v:.4f}" if isinstance(v, float) else f"{k} {v}" for k, v in s.items()))
    iu = np.triu_indices(len(prompts), 1)
    for k in np.argsort(-cos[iu])[:pairs]:
        i, j = iu[0][k], iu[1][k]
        print(f"  {1 - cos[i, j]:.4f}  {ids[i]}  ~  {ids[j]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("corpora", nargs="*", help=".txt files, one prompt per line")
    ap.add_argument("--worlds", nargs="*", default=[],
                    help="worlds.py-style catalogues: also measure their fixed-template views")
    ap.add_argument("--pairs", type=int, default=10, help="most-similar pairs to list")
    args = ap.parse_args()

    for path in map(Path, args.corpora):
        prompts = [ln for ln in path.read_text().splitlines() if ln.strip()]
        meta = path.with_suffix(".jsonl")
        ids = ([json.loads(ln)["world"] for ln in meta.read_text().splitlines()]
               if meta.exists() else [str(i) for i in range(len(prompts))])
        report(str(path), prompts, ids, args.pairs)
    for path in map(Path, args.worlds):
        for view, (prompts, ids) in _world_views(path).items():
            report(f"{path} [{view}]", prompts, ids, args.pairs)


if __name__ == "__main__":
    main()
