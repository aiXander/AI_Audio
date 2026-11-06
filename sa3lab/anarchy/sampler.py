"""Anarchy sample specs: a prompt string that *is* a conditioning sample.

    anarchy: <fit> <sampler> T<temp> d<direction> [L<length>] [c<skip>] [k<n>] [m<pct>]
             [w:<world|family>] [g0] [n1] [r<pct>] [j<eps>] [s<scale>] [x<n>] [| <text>]
    anarchy: grammar-music-100 tensor-pca T1.5 d4821 g0
    anarchy: grammar-music-100 bag T2 d17 L24 n1 x3
    anarchy: grammar-music-100 cocktail T0.5 d9 k3
    anarchy: grammar-music-100 cross T1 d9 k2 w:club
    anarchy: grammar-music-100 mutate T1 d5 m40 | dark dub techno, 124 bpm
    anarchy: @sa3-gradio/<clip>.cond.npz          (re-render a saved latent exactly)
    anarchy: @<a>.cond.npz + @<b>.cond.npz        (breed: the union of two saved bags)

The string fully determines the `(256, 768)` conditioning: `fit` names a folder under
`models/derived/anarchy/` (made by fit.py), `sampler` is one of SAMPLERS, `d` seeds the
conditioning rng (same d + same fit = same draw direction at any T), `L` is the content
length (omitted: bootstrapped from the corpus length histogram with the same seed), `c`
skips the first c PCA axes (tensor-pca only; on grammar-music-100 PC1/PC2 are wording
axes: length and TrackType prefix).

Two bag operators lean on the DiT reading its context as an unordered bag of vectors
(no positional encoding; a vector duplicated n times gains ln n attention logit):
- `cocktail`: k concepts from the fit's concept vocabulary (k-means over corpus tokens,
  template / punctuation / number clusters excluded), drawn by `d`; the L content slots
  are shared out between them. Each slot is cluster mean + T·(a real corpus token of that
  cluster − mean), rescaled to that token's norm (a cluster mean is ~25% shorter than its
  members, and the DiT's values are unnormalized): T 0 = k pure concepts, each repeated L/k times (strong emphasis),
  T 1 = real words of those concepts, T > 1 = exaggerated. The sidecar names the concepts.
- `mutate`: encode the text after `|` with T5Gemma, then resample m% of its content
  tokens (positions picked by `d`) from the token fit at temperature T. A reach knob
  anchored on a known prompt: m0 = the prompt itself, m100 = a bag draw of its length.
- `cross`: crossover, real token vectors from k corpus prompts (drawn by `d`, optionally
  only prompts whose world or family contains `w:<text>`), dealt round-robin into the L
  slots: a genre fusion nobody wrote. T 1 = the exact real tokens; T ≠ 1 scales each
  token's deviation from the corpus token mean (norm kept, as in cocktail).
- `@a + @b` (breed): the leading content run of each saved bag (rows 0.. up to the first
  padding row, so a tensor sample's ghost band is dropped), concatenated, capped at 256.

Post-ops, any sampler, all off by default so every older spec still reproduces its clip;
applied in this order:
- `g0` (no ghosts): rows past L reset to padding. The tensor fits fall back to a pooled
  content/pad blend wherever < MIN_LENGTH_GROUP corpus prompts are that long, so a tensor
  sample carries ~25 low-norm "ghost" vectors (norm 2–45 vs ~122 for real tokens) at
  positions ~54–78 of `grammar-music-100`. Keys are qk-normed, so ghosts are attended as
  much as real tokens. Every tensor render before g0 existed has them.
- `n1` (norm match): each content row rescaled to the norm of a random real corpus token
  (rng from d), the reach correction the plan predicts pushes the mush edge out.
- `r<pct>` (retain): keep pct% of the content rows (picked by d), packed to the front; the
  rest become padding. Which tokens carry the sound.
- `j<eps>` (jitter): each content row's direction perturbed by eps × its norm of Gaussian
  noise, norm restored (j0.3 ≈ cosine 0.95 to the original). Direction change, magnitude kept.
- `s<scale>` (scale): every content row × scale, one global factor (relative weights kept,
  unlike n1). Magnitude change, direction kept. In tensor-pca, T inflates norms ~linearly
  (mean 120 at T1, 256 at T2.5), so s is how to take magnitude back out of T.
- `x<n>` (emphasis): the content rows tiled n× into the padding slots (≤ 256 rows): each
  copy adds ln n to that token's attention logit vs the ~zero-value pads, a
  conditioning-strength dial for the cfg-1 distilled model.

Because the spec is the prompt, a studio render of it is regenerable from its own prompt
text, and re-typing it reproduces the clip. A `.cond.npz` may carry a `spec` string beside
`cond` (key `source_spec`, written by `precompute.py`): `@file` renders then record that spec's knobs, so a
precomputed conditioning analyzes like the native spec it came from.

`@<file>` instead of a fit loads a saved `(256, 768)` latent (`.cond.npz`, key `cond`; path
relative to outputs/ or absolute): the studio writes one beside every latent render, so a
clip stays reproducible even after its fit is refit or deleted. fp16 is lossless here:
the DiT consumes the conditioning as fp16.

`conditioning(spec, dit)` → (cond fp16 (256, 768), info dict). Fits are cached in memory.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import numpy as np

AI_AUDIO = Path(__file__).resolve().parents[2]
MLX = AI_AUDIO / "stable-audio-3" / "optimized" / "mlx"
DERIVED = AI_AUDIO / "models" / "derived" / "anarchy"
OUTPUTS = AI_AUDIO / "outputs"
SA_REPO = Path(os.environ.get("SA_REPO", Path.home() / "Documents/GitHub/semantic-anarchy"))
DIT_CKPT = {"medium": "models/mlx/dit_medium_f16.npz",
            "sm-music": "models/mlx/dit_sm-music_f16.npz",
            "sm-sfx": "models/mlx/dit_sm-sfx_f16.npz"}
SAMPLERS = {
    "tensor-pca": "whole-prompt draw in the corpus PCA subspace",
    "tensor-diag": "independent per-coordinate Gaussians (off-manifold texture)",
    "bag": "L token vectors from the token-level fit, rest padding",
    "cocktail": "k concept clusters, each repeated, T = spread around each concept",
    "mutate": "your text prompt with m% of its tokens resampled from the token fit",
    "cross": "real tokens of k corpus prompts dealt into one bag (fusion), w: filters prompts",
}
# concept clusters whose top words are mostly template / function words are not concepts
_STOP = set("type genre genres keywords keyword mood moods description title track music instrument "
            "instruments instrumental tracktype vocaltype bpm tempo with into over from that this "
            "less".split())
PREFIX = "anarchy:"
_RE = re.compile(r"^(?P<k>[TdLckmxngsrj])(?P<v>-?\d+(?:\.\d+)?)$")
_cache: dict = {}


class SpecError(ValueError):
    pass


def is_spec(prompt: str | None) -> bool:
    return bool(prompt) and prompt.strip().lower().startswith(PREFIX)


def parse(prompt: str) -> dict:
    head, bar, text = prompt.strip()[len(PREFIX):].partition("|")
    toks = head.split()
    spec = {"fit": None, "sampler": "tensor-pca", "T": 1.0, "d": 0, "L": None, "c": 0,
            "k": 3, "m": 30, "w": None, "g": 1, "n": 0, "r": 100, "j": 0.0, "s": 1.0, "x": 1, "text": text.strip() if bar else None}
    if toks and toks[0].startswith("@"):
        files = [f.strip()[1:] for f in prompt.strip()[len(PREFIX):].split(" + ")]
        if any(not f for f in files):
            raise SpecError("breed: 'anarchy: @a.cond.npz + @b.cond.npz'")
        return {"file": files[0]} if len(files) == 1 else {"files": files}
    for t in toks:
        m = _RE.match(t)
        if t.startswith("w:") and len(t) > 2:
            spec["w"] = t[2:]
        elif t in SAMPLERS:
            spec["sampler"] = t
        elif m:
            k, v = m["k"], m["v"]
            spec[k] = float(v) if k in "Tsj" else int(float(v))
        elif spec["fit"] is None:
            spec["fit"] = t
        else:
            raise SpecError(f"anarchy spec: don't understand {t!r}")
    if not spec["fit"]:
        raise SpecError("anarchy spec needs a fit name, e.g. 'anarchy: grammar-music-100 tensor-pca T1 d0'")
    if spec["sampler"] == "mutate" and not spec["text"]:
        raise SpecError("mutate needs a text prompt after '|', e.g. 'anarchy: grammar-music-100 mutate T1 d0 m40 | dub techno'")
    return spec


def canonical(spec: dict) -> str:
    if spec.get("file"):
        return f"{PREFIX} @{spec['file']}"
    if spec.get("files"):
        return f"{PREFIX} " + " + ".join(f"@{f}" for f in spec["files"])
    s = f"{PREFIX} {spec['fit']} {spec['sampler']} T{spec['T']:g} d{spec['d']}"
    if spec.get("L"):
        s += f" L{spec['L']}"
    if spec.get("c") and spec["sampler"] == "tensor-pca":
        s += f" c{spec['c']}"
    if spec["sampler"] in ("cocktail", "cross"):
        s += f" k{spec.get('k', 3)}"
    if spec.get("w") and spec["sampler"] == "cross":
        s += f" w:{spec['w']}"
    if spec.get("g", 1) == 0:
        s += " g0"
    if spec.get("n"):
        s += " n1"
    if 0 < (spec.get("r") or 100) < 100:
        s += f" r{spec['r']}"
    if spec.get("j"):
        s += f" j{spec['j']:g}"
    if (spec.get("s") or 1.0) != 1.0:
        s += f" s{spec['s']:g}"
    if (spec.get("x") or 1) > 1:
        s += f" x{spec['x']}"
    if spec["sampler"] == "mutate":
        s += f" m{spec.get('m', 30)} | {spec.get('text') or ''}"
    return s


def padding_vector(dit: str) -> np.ndarray:
    key = ("pad", dit)
    if key not in _cache:
        _cache[key] = np.load(MLX / DIT_CKPT[dit])["cond.padding_embedding"].astype(np.float32)
    return _cache[key]


def _dists(fit: str, dit: str):
    key = ("fit", fit, dit)
    if key not in _cache:
        d = DERIVED / fit
        if not (d / f"{dit}__tensor.npz").exists():
            have = sorted(p.name.split("__")[0] for p in d.glob("*__tensor.npz")) if d.exists() else []
            raise SpecError(f"no anarchy fit '{fit}' for DiT '{dit}'"
                            + (f" (fitted for: {', '.join(have)})" if have else "")
                            + f"; run sa3lab/anarchy/fit.py --dit {dit}")
        sys.path.insert(0, str(SA_REPO))
        from semantic_anarchy.distribution import EmbeddingDistribution as E
        rep = json.loads((d / f"{dit}__report.json").read_text())
        _cache[key] = (E.load(d / f"{dit}__tensor"), E.load(d / f"{dit}__token"), rep)
    return _cache[key]


def _load_saved(name: str) -> tuple[np.ndarray, str | None, str]:
    """(cond fp16 (256, 768), the spec stored beside it or None, path as recorded)."""
    f = Path(name).expanduser()
    f = f if f.is_absolute() else OUTPUTS / f
    if not f.is_file():
        raise SpecError(f"no saved latent at {f}")
    with np.load(f) as z:
        cond = z["cond"].astype(np.float16)
        key = next((k for k in ("source_spec", "spec") if k in z.files), None)
        stored = str(z[key]) if key else None
    if cond.shape != (256, 768):
        raise SpecError(f"{f.name}: expected a (256, 768) latent, got {cond.shape}")
    return cond, stored, str(f.relative_to(OUTPUTS)) if OUTPUTS in f.parents else str(f)


def _content_run(cond: np.ndarray, pv: np.ndarray) -> np.ndarray:
    """Rows 0.. up to the first padding row: the bag's content, without a tensor sample's
    ghost band (which sits after a run of padding)."""
    is_pad = np.all(cond == pv.astype(np.float16), axis=1)
    end = int(np.argmax(is_pad)) if is_pad.any() else len(cond)
    return cond[:end]


def _from_file(spec: dict, dit: str) -> tuple[np.ndarray, dict]:
    if spec.get("files"):                                   # breed: union of saved bags
        pv = padding_vector(dit)
        parts = [_load_saved(f) for f in spec["files"]]
        runs = [_content_run(c, pv) for c, _, _ in parts]
        cond = np.tile(pv.astype(np.float16), (256, 1))
        content = np.concatenate(runs)[:256]
        cond[:len(content)] = content
        return cond, {"sampler": "breed", "L": int(len(content)), "spec": canonical(spec), "dit": dit,
                      "parents": [{"file": p, "spec": s, "rows": int(len(r))}
                                  for (_, s, p), r in zip(parts, runs)]}
    cond, stored, rel = _load_saved(spec["file"])
    info = {"source_file": rel, "spec": canonical(spec), "dit": dit}
    if stored and is_spec(stored) and not stored.split(":", 1)[1].strip().startswith("@"):
        # a precomputed native spec: record its knobs so the render analyzes like that spec
        try:
            info = {**parse(stored), **info, "source_spec": stored}
        except SpecError:      # a knob newer than this code: still render the saved array
            info = {**info, "source_spec": stored}
    return cond, info


def _corpus_meta(fit: str) -> list[dict]:
    """Per-prompt corpus rows (world, family, …) of a fit, [] if the .jsonl is gone."""
    key = ("meta", fit)
    if key not in _cache:
        rep = json.loads(next((DERIVED / fit).glob("*__report.json")).read_text())
        p = (AI_AUDIO / rep["corpus"]).with_suffix(".jsonl")
        _cache[key] = [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []
    return _cache[key]


def _cross(spec: dict, dit: str, L: int, T: float, rng) -> tuple[np.ndarray, dict]:
    _, tokens, _ = _concepts(spec["fit"], dit)
    src = np.load(DERIVED / spec["fit"] / f"{dit}__concepts.npz")["tok_src"]
    meta = _corpus_meta(spec["fit"])
    pool = np.unique(src)
    if spec.get("w"):
        w = spec["w"].lower()
        pool = np.array([i for i in pool if i < len(meta) and (w in meta[i].get("world", "").lower()
                                                               or w == meta[i].get("family", "").lower())])
        if not len(pool):
            raise SpecError(f"cross: no corpus prompt has world or family matching w:{spec['w']}")
    k = int(min(max(spec.get("k") or 3, 1), len(pool)))
    picks = rng.choice(pool, k, replace=False)
    decks = [rng.permutation(np.nonzero(src == p)[0]) for p in picks]
    idx = [decks[s % k][(s // k) % len(decks[s % k])] for s in range(L)]
    x = tokens[idx]
    if T != 1.0:
        mu = tokens.mean(0)
        v = mu + T * (x - mu)
        x = v * (np.linalg.norm(x, axis=1, keepdims=True) / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-6))
    return x.astype(np.float32), {"sources": [{"prompt": int(p), **{f: meta[p].get(f) for f in ("world", "family")}}
                                              for p in picks if p < len(meta)]}


def _post(cond: np.ndarray, L: int, spec: dict, pv: np.ndarray, fit: str, dit: str) -> tuple[np.ndarray, int, int]:
    """g0 / n1 / r / j / s / x, in that order. Returns (cond, content length, rows after emphasis)."""
    pv16 = pv.astype(np.float16)
    if spec.get("g", 1) == 0:
        cond[L:] = pv16
    if spec.get("n"):
        norms = np.load(DERIVED / fit / f"{dit}__concepts.npz")["norms"]
        r = np.random.default_rng(20_000 + spec["d"])
        c = cond[:L].astype(np.float32)
        target = r.choice(norms, L)[:, None]
        cond[:L] = (c * target / np.maximum(np.linalg.norm(c, axis=1, keepdims=True), 1e-6)).astype(np.float16)
    keep = int(spec.get("r") or 100)
    if 0 < keep < 100:
        r = np.random.default_rng(30_000 + spec["d"])
        idx = np.sort(r.choice(L, max(1, round(L * keep / 100)), replace=False))
        kept = cond[idx].copy()
        cond[:L] = pv16
        L = len(idx)
        cond[:L] = kept
    if spec.get("j"):
        r = np.random.default_rng(40_000 + spec["d"])
        c = cond[:L].astype(np.float32)
        n = np.linalg.norm(c, axis=1, keepdims=True)
        v = c + float(spec["j"]) * n * r.standard_normal(c.shape) / np.sqrt(c.shape[1])
        cond[:L] = (v * n / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-6)).astype(np.float16)
    if (spec.get("s") or 1.0) != 1.0:
        cond[:L] = (cond[:L].astype(np.float32) * float(spec["s"])).astype(np.float16)
    rows = L
    x = int(spec.get("x") or 1)
    if x > 1:
        rows = min(L * x, 256)
        cond[:rows] = np.tile(cond[:L], (x, 1))[:rows]
    return cond, L, rows


def _concepts(fit: str, dit: str):
    """(content clusters [{id, words}], raw token vectors (ΣL, 768), assignment) for a fit."""
    key = ("concepts", fit, dit)
    if key not in _cache:
        d = DERIVED / fit
        z = np.load(d / f"{dit}__concepts.npz")
        mine = np.load(d / "mine.npz", allow_pickle=True)       # our own cache (prompts are objects)
        tokens = mine["raw"][mine["mask"].astype(bool)].astype(np.float32)
        assign = z["assign"]
        if len(tokens) != len(assign):
            raise SpecError(f"{fit}: concepts and mine.npz disagree; re-run fit.py")
        rep = json.loads((d / f"{dit}__report.json").read_text())
        good = [c for c in rep.get("concepts", [])
                if c["n_tokens"] >= 8 and sum(w.isalpha() and len(w) >= 4 and w not in _STOP
                                              for w in c["words"]) >= 3]
        _cache[key] = (good, tokens, assign)
    return _cache[key]


def _cocktail(spec: dict, dit: str, L: int, T: float, rng) -> tuple[np.ndarray, list]:
    good, tokens, assign = _concepts(spec["fit"], dit)
    k = int(min(max(spec.get("k") or 3, 1), len(good), L))
    pick = [good[i] for i in rng.choice(len(good), k, replace=False)]
    out = np.empty((L, tokens.shape[1]), np.float32)
    for slot in range(L):
        c = pick[slot % k]
        members = tokens[assign == c["id"]]
        mu = members.mean(0)
        x = members[rng.integers(len(members))]
        v = mu + T * (x - mu)
        out[slot] = v * (np.linalg.norm(x) / max(np.linalg.norm(v), 1e-6))   # keep a real token's norm
    return out, [{"id": c["id"], "words": c["words"][:4]} for c in pick]


def _mutate(spec: dict, token, T: float, rng, encode_text) -> tuple[np.ndarray, dict]:
    if encode_text is None:
        raise SpecError("mutate needs the T5Gemma encoder (studio / headless only)")
    raw, L = encode_text(spec["text"])                       # (256, 768) float32, content length
    content = np.array(raw[:L], np.float32)
    m = int(min(max(spec.get("m", 30), 0), 100))
    n = int(round(L * m / 100))
    idx = np.sort(rng.choice(L, n, replace=False)) if n else np.array([], int)
    if n:
        content[idx] = token.sample(n, temperature=T, sampler="pca", rng=rng)
    return content, {"text_tokens": L, "mutated": int(n)}


def conditioning(spec: dict, dit: str, encode_text=None) -> tuple[np.ndarray, dict]:
    """`encode_text(text) -> (raw (256, 768) float32 pre-padding, content length)` is only
    needed by `mutate` (the studio passes the real T5Gemma)."""
    if spec.get("file") or spec.get("files"):
        return _from_file(spec, dit)
    tensor, token, rep = _dists(spec["fit"], dit)
    pv = padding_vector(dit)
    L_crop = tensor.feature_shape[0]
    L = spec.get("L") or int(tensor.draw_lengths(
        1, rng=np.random.default_rng(10_000 + spec["d"]), mode="corpus")[0])
    L = int(min(max(L, 1), L_crop))
    rng = np.random.default_rng(spec["d"])
    T = float(spec["T"])
    extra = {}
    if spec["sampler"] in ("cocktail", "mutate", "cross"):  # bag operators: build (256, 768) directly
        if spec["sampler"] == "cocktail":
            content, concepts = _cocktail(spec, dit, L, T, rng)
            extra = {"concepts": concepts}
        elif spec["sampler"] == "cross":
            content, extra = _cross(spec, dit, L, T, rng)
        else:
            content, extra = _mutate(spec, token, T, rng, encode_text)
            L = len(content)
        cond = np.tile(pv.astype(np.float16), (256, 1))
        cond[:L] = content.astype(np.float16)
    else:
        if spec["sampler"] == "bag":
            content = token.sample(L, temperature=T, sampler="pca", rng=rng)
            crop = np.tile(pv, (L_crop, 1)).astype(np.float32)
            crop[:L] = content
        elif spec["sampler"] == "tensor-diag":
            crop = tensor.sample(1, temperature=T, sampler="diagonal", rng=rng, lengths=np.array([L]))[0]
        else:
            crop = tensor.sample(1, temperature=T, sampler="pca", rng=rng, lengths=np.array([L]),
                                 comp_lo=int(spec.get("c") or 0))[0]
        cond = np.tile(pv.astype(np.float16), (256, 1))
        cond[:L_crop] = crop.astype(np.float16)
    cond, L_kept, rows = _post(cond, L, spec, pv, spec["fit"], dit)
    if not np.isfinite(cond).all():
        raise SpecError("sample is not finite")
    info = {**spec, "L": L, "spec": canonical({**spec, "L": spec.get("L")}), "dit": dit,
            "distance": round(float(tensor.distance(cond[:L_crop].astype(np.float32))), 3), **extra,
            "fit_corpus": rep.get("corpus"), "fit_created": rep.get("created"),
            "semantic_anarchy": rep.get("semantic_anarchy")}
    if L_kept != L:
        info["kept"] = L_kept
    if rows != L_kept:
        info["rows"] = rows
    return cond, info


def list_fits() -> list[dict]:
    out = []
    for d in sorted(DERIVED.glob("*")) if DERIVED.exists() else []:
        reps = sorted(d.glob("*__report.json"))
        if not reps:
            continue
        r = json.loads(reps[0].read_text())
        out.append({"name": d.name, "dits": [p.name.split("__")[0] for p in reps],
                    "n_prompts": r.get("n_prompts"), "L_crop": r.get("L_crop"),
                    "noise_floor": r.get("noise_floor_axes"), "corpus": r.get("corpus")})
    return out
