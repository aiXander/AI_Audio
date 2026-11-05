"""Mine a prompt corpus through T5Gemma and fit the anarchy distributions over it.

Two steps, one command (the encode is cached, so refits are seconds):

1. **mine** — encode every prompt exactly as the studio does (one prompt per
   `T5Gemma.encode([p], max_len=256)` call, fp16; batching would pad and could drift),
   then crop to `L_crop` = the corpus's longest prompt rounded up to 8 tokens: every
   position past it is padding in every prompt, so it is reconstructed, never stored.
   Cache: raw embeddings, masks, token ids, lengths, prompts.
2. **fit**, per DiT (the learned padding vector lives in each DiT checkpoint):
   - *tensor fit*: `apply_prompt_padding` semantics in fp16 (bit-identical to what the DiT
     sees), then semantic-anarchy's `EmbeddingDistribution.fit(padded, lengths=)` over
     `(N, L_crop, 768)`. Samples come back cropped; `expand()` restores `(256, 768)`.
   - *token fit*: every content-token vector pooled to `(ΣL, 768)` →
     `EmbeddingDistribution.fit`. The DiT reads its context as an unordered bag
     (no positional encoding, no mask on its cross-attention), so this is the unit the
     DiT actually consumes, with ~35× more samples than prompts.
   - *concept vocabulary*: spherical k-means over the token vectors; each cluster is
     named by its most frequent source tokens, so "cluster 17" reads as "sitar / tabla".
   - *autopsy-lite*: PCA spectrum vs the Marchenko–Pastur noise floor, and what the
     leading axes correlate with (prompt length, surface style, edge family, TrackType
     prefix) — a PC1 that is "style" or "length" is a wording axis, not a musical one.

Writes `models/derived/anarchy/<corpus>/` (repo rule: derived model files under models/):
  mine.npz                    the cache
  <dit>__tensor.npz/.meta.json, <dit>__token.npz/.meta.json   (EmbeddingDistribution)
  <dit>__concepts.npz         k-means centroids + assignments + token norms
  <dit>__report.json          the autopsy-lite numbers + concept table

Run with the MLX venv:
  stable-audio-3/optimized/mlx/.venv/bin/python sa3lab/anarchy/fit.py \
      sa3lab/anarchy/corpora/grammar-music-100.txt [--dit medium] [--k 48] [--remine]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
AI_AUDIO = HERE.parents[1]
MLX = AI_AUDIO / "stable-audio-3" / "optimized" / "mlx"
SA_REPO = Path(os.environ.get("SA_REPO", Path.home() / "Documents/GitHub/semantic-anarchy"))
DERIVED = AI_AUDIO / "models" / "derived" / "anarchy"
DIT_CKPT = {"medium": "models/mlx/dit_medium_f16.npz",
            "sm-music": "models/mlx/dit_sm-music_f16.npz",
            "sm-sfx": "models/mlx/dit_sm-sfx_f16.npz"}

sys.path.insert(0, str(SA_REPO))
from semantic_anarchy.distribution import EmbeddingDistribution  # noqa: E402  (numpy-only)


def _git(repo: Path) -> dict:
    def run(*a):
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True,
                              text=True).stdout.strip()
    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain"))}


# ── mine ────────────────────────────────────────────────────────────────────
def mine(prompts: list[str], cache: Path) -> dict:
    import mlx.core as mx
    sys.path.insert(0, str(MLX))
    from models.defs.t5gemma_mlx import T5Gemma

    enc = T5Gemma.from_npz(str(MLX / "models/mlx/t5gemma_f16.npz"))
    raw, masks, ids = [], [], []
    t0 = time.time()
    for p in prompts:
        e, m = enc.encode([p], max_len=256)
        mx.eval(e, m)
        raw.append(np.asarray(e[0]).astype(np.float16))
        masks.append(np.asarray(m[0]).astype(np.int8))
        tok, _ = enc.tokenize([p], max_len=256)
        ids.append(np.asarray(tok[0]).astype(np.int32))
    lengths = np.array([m.sum() for m in masks], dtype=np.int32)
    L = int(min(256, -(-int(lengths.max()) // 8) * 8))
    data = dict(raw=np.stack(raw)[:, :L], mask=np.stack(masks)[:, :L],
                ids=np.stack(ids)[:, :L], lengths=lengths, L_crop=np.int32(L),
                prompts=np.array(prompts, dtype=object))
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez(cache, **data)
    print(f"[mine] {len(prompts)} prompts encoded in {time.time() - t0:.1f}s; "
          f"lengths {lengths.min()}–{lengths.max()} (median {int(np.median(lengths))}), "
          f"L_crop {L} → {cache}", flush=True)
    return data


def _pieces(ids: np.ndarray) -> list[str]:
    import sentencepiece as spm
    sp = spm.SentencePieceProcessor()
    sp.LoadFromSerializedProto(np.load(MLX / "models/mlx/t5gemma_f16.npz")["TOKENIZER_MODEL"].tobytes())
    return [sp.IdToPiece(int(i)) for i in ids]


# ── fit ─────────────────────────────────────────────────────────────────────
def padding_vector(dit: str) -> np.ndarray:
    z = np.load(MLX / DIT_CKPT[dit])
    return z["cond.padding_embedding"].astype(np.float32)


def pad(raw: np.ndarray, mask: np.ndarray, pad_vec: np.ndarray) -> np.ndarray:
    """apply_prompt_padding in fp16, exactly as sa3_gradio/sa3_mlx compute it."""
    m = mask.astype(np.float16)[..., None]
    return (raw.astype(np.float16) * m + pad_vec.astype(np.float16) * (1 - m)).astype(np.float16)


def expand(sample: np.ndarray, pad_vec: np.ndarray) -> np.ndarray:
    """(L_crop, 768) sample → (256, 768) conditioning, padding the cropped tail."""
    out = np.tile(pad_vec.astype(np.float16), (256, 1))
    out[: sample.shape[0]] = sample
    return out


def kmeans_cos(x: np.ndarray, k: int, rng: np.random.Generator, iters: int = 50):
    xn = x / np.linalg.norm(x, axis=1, keepdims=True)
    c = xn[rng.choice(len(xn), k, replace=False)]
    for _ in range(iters):
        a = (xn @ c.T).argmax(1)
        new = np.stack([xn[a == j].mean(0) if (a == j).any() else c[j] for j in range(k)])
        new /= np.linalg.norm(new, axis=1, keepdims=True)
        if np.allclose(new, c):
            break
        c = new
    return c, (xn @ c.T).argmax(1)


def _corr(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    if a.std() == 0 or b.std() == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("corpus")
    ap.add_argument("--dit", default="medium", choices=list(DIT_CKPT))
    ap.add_argument("--k", type=int, default=48, help="concept-vocabulary clusters")
    ap.add_argument("--components", type=int, default=512, help="PCA axes cap (N-1 if smaller)")
    ap.add_argument("--remine", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    corpus = Path(args.corpus).resolve()
    prompts = [ln.strip() for ln in corpus.read_text().splitlines() if ln.strip()]
    meta_rows = [json.loads(ln) for ln in corpus.with_suffix(".jsonl").read_text().splitlines()
                 if ln.strip()] if corpus.with_suffix(".jsonl").exists() else [{}] * len(prompts)
    out = DERIVED / corpus.stem
    cache = out / "mine.npz"
    if cache.exists() and not args.remine:
        data = dict(np.load(cache, allow_pickle=True))
        if list(data["prompts"]) != prompts:
            sys.exit(f"{cache} was mined from a different prompt list: pass --remine")
        print(f"[mine] cached {cache}", flush=True)
    else:
        data = mine(prompts, cache)

    raw, mask, ids, lengths = data["raw"], data["mask"], data["ids"], data["lengths"]
    L = int(data["L_crop"])
    pv = padding_vector(args.dit)
    padded = pad(raw, mask, pv)
    provenance = {"corpus": str(corpus.relative_to(AI_AUDIO)), "n_prompts": len(prompts),
                  "dit": args.dit, "dit_ckpt": DIT_CKPT[args.dit], "L_crop": L,
                  "semantic_anarchy": _git(SA_REPO), "ai_audio": _git(AI_AUDIO),
                  "created": time.strftime("%Y-%m-%dT%H:%M:%S")}

    # tensor fit — what the DiT sees, cropped
    t0 = time.time()
    tensor = EmbeddingDistribution.fit(padded.astype(np.float32), lengths=lengths,
                                       n_components=min(args.components, len(prompts) - 1),
                                       max_corpus=len(prompts))
    tensor.save(out / f"{args.dit}__tensor")
    print(f"[fit] tensor ({len(prompts)}, {L}, 768) in {time.time() - t0:.1f}s", flush=True)

    # token fit — the bag the DiT reads
    m = mask.astype(bool)
    tokens = raw[m].astype(np.float32)                                   # (ΣL, 768)
    tok_src = np.nonzero(m)[0]                                           # prompt index per token
    tok_ids = ids[m]
    token = EmbeddingDistribution.fit(tokens, n_components=min(args.components, len(tokens) - 1),
                                      max_corpus=256)
    token.save(out / f"{args.dit}__token")
    print(f"[fit] token ({len(tokens)}, 768)", flush=True)

    # concept vocabulary
    rng = np.random.default_rng(args.seed)
    cent, assign = kmeans_cos(tokens, args.k, rng)
    pieces = _pieces(tok_ids)
    norms = np.linalg.norm(tokens, axis=1)
    concepts = []
    for j in np.argsort(-np.bincount(assign, minlength=args.k)):
        sel = np.nonzero(assign == j)[0]
        if not len(sel):
            continue
        top = Counter(pieces[i].replace("▁", "").lower() for i in sel).most_common(6)
        concepts.append({"id": int(j), "n_tokens": int(len(sel)),
                         "n_prompts": int(len(set(tok_src[sel]))),
                         "words": [w for w, _ in top if w]})
    np.savez(out / f"{args.dit}__concepts.npz", centroids=cent.astype(np.float32),
             assign=assign.astype(np.int32), tok_src=tok_src.astype(np.int32),
             tok_ids=tok_ids.astype(np.int32), norms=norms.astype(np.float32),
             pad_norm=np.float32(np.linalg.norm(pv)))

    # autopsy-lite
    X = padded.reshape(len(prompts), -1).astype(np.float64) - tensor.mean.reshape(-1)
    coef = X @ tensor.pca_components.T                                   # (N, k)
    var = tensor.pca_std ** 2
    style = [r.get("style") for r in meta_rows]
    family = [r.get("family") for r in meta_rows]
    covariates = {
        "length": lengths,
        "style=prose": [s == "prose" for s in style],
        "style=tags": [s == "tags" for s in style],
        "family=edge": [f == "edge" for f in family],
        "trackType prefix": ["tracktype" in p.lower() for p in prompts],
        "lowercase": [p == p.lower() for p in prompts],
    }
    axes = []
    for a in range(min(6, coef.shape[1])):
        axes.append({"pc": a + 1, "var_frac": float(var[a] / tensor.total_var),
                     **{k: round(_corr(coef[:, a], v), 2) for k, v in covariates.items()}})
    report = {
        **provenance,
        "noise_floor_axes": tensor.noise_floor_axes(),
        "kept_axes": int(len(var)),
        "var_frac_top10": [round(float(v / tensor.total_var), 4) for v in var[:10]],
        "corpus_distance": {"mean": float(tensor.corpus_distance.mean()),
                            "sd": float(tensor.corpus_distance.std())}
        if tensor.corpus_distance is not None else None,
        "has_length_stats": bool(tensor.has_length_stats),
        "pc_correlates": axes,
        "tokens": {"n": int(len(tokens)), "norm_mean": float(norms.mean()),
                   "norm_sd": float(norms.std()), "pad_norm": float(np.linalg.norm(pv)),
                   "noise_floor_axes": token.noise_floor_axes()},
        "concepts": concepts,
    }
    (out / f"{args.dit}__report.json").write_text(json.dumps(report, indent=1))

    print(f"[report] noise floor {report['noise_floor_axes']} of {report['kept_axes']} axes; "
          f"top-10 var frac {report['var_frac_top10']}", flush=True)
    for ax in axes:
        print("   PC{pc}: {v:.1%} var | ".format(pc=ax["pc"], v=ax["var_frac"])
              + "  ".join(f"{k} {ax[k]:+.2f}" for k in covariates), flush=True)
    print(f"   tokens: {len(tokens)}, norm {norms.mean():.1f}±{norms.std():.1f}, pad vector norm "
          f"{np.linalg.norm(pv):.1f}", flush=True)
    print("[concepts] largest clusters:", flush=True)
    for c in concepts[:20]:
        print(f"   #{c['id']:>2} {c['n_tokens']:>4} tok / {c['n_prompts']:>3} prompts: "
              + ", ".join(c["words"]), flush=True)
    print(f"→ {out}", flush=True)


if __name__ == "__main__":
    main()
