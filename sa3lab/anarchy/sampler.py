"""Anarchy sample specs: a prompt string that *is* a conditioning sample.

    anarchy: <fit> <sampler> T<temp> d<direction> [L<length>] [c<skip>] [k<concepts>] [m<pct>] [| <text>]
    anarchy: grammar-music-100 tensor-pca T1.5 d4821
    anarchy: grammar-music-100 bag T2 d17 L24
    anarchy: grammar-music-100 cocktail T0.5 d9 k3
    anarchy: grammar-music-100 mutate T1 d5 m40 | dark dub techno, 124 bpm
    anarchy: @sa3-gradio/<clip>.cond.npz          (re-render a saved latent exactly)

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
  anchored on a known prompt: m0 = the prompt itself, m100 = a bag draw of its length. Because the spec is the prompt, a studio render of it
is regenerable from its own prompt text, and re-typing it reproduces the clip.

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
}
# concept clusters whose top words are mostly template / function words are not concepts
_STOP = set("type genre genres keywords keyword mood moods description title track music instrument "
            "instruments instrumental tracktype vocaltype bpm tempo with into over from that this "
            "less".split())
PREFIX = "anarchy:"
_RE = re.compile(r"^(?P<k>[TdLckm])(?P<v>-?\d+(?:\.\d+)?)$")
_cache: dict = {}


class SpecError(ValueError):
    pass


def is_spec(prompt: str | None) -> bool:
    return bool(prompt) and prompt.strip().lower().startswith(PREFIX)


def parse(prompt: str) -> dict:
    head, bar, text = prompt.strip()[len(PREFIX):].partition("|")
    toks = head.split()
    spec = {"fit": None, "sampler": "tensor-pca", "T": 1.0, "d": 0, "L": None, "c": 0,
            "k": 3, "m": 30, "text": text.strip() if bar else None}
    if toks and toks[0].startswith("@"):
        return {"file": prompt.strip()[len(PREFIX):].strip()[1:]}
    for t in toks:
        m = _RE.match(t)
        if t in SAMPLERS:
            spec["sampler"] = t
        elif m:
            k, v = m["k"], m["v"]
            spec[k] = float(v) if k == "T" else int(float(v))
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
    s = f"{PREFIX} {spec['fit']} {spec['sampler']} T{spec['T']:g} d{spec['d']}"
    if spec.get("L"):
        s += f" L{spec['L']}"
    if spec.get("c") and spec["sampler"] == "tensor-pca":
        s += f" c{spec['c']}"
    if spec["sampler"] == "cocktail":
        s += f" k{spec.get('k', 3)}"
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


def _from_file(spec: dict, dit: str) -> tuple[np.ndarray, dict]:
    f = Path(spec["file"]).expanduser()
    f = f if f.is_absolute() else OUTPUTS / f
    if not f.is_file():
        raise SpecError(f"no saved latent at {f}")
    cond = np.load(f)["cond"].astype(np.float16)
    if cond.shape != (256, 768):
        raise SpecError(f"{f.name}: expected a (256, 768) latent, got {cond.shape}")
    return cond, {"source_file": str(f.relative_to(OUTPUTS)) if OUTPUTS in f.parents else str(f),
                  "spec": canonical(spec), "dit": dit}


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
    if spec.get("file"):
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
    if spec["sampler"] in ("cocktail", "mutate"):         # bag operators: build (256, 768) directly
        if spec["sampler"] == "cocktail":
            content, concepts = _cocktail(spec, dit, L, T, rng)
            extra = {"concepts": concepts}
        else:
            content, extra = _mutate(spec, token, T, rng, encode_text)
            L = len(content)
        cond = np.tile(pv.astype(np.float16), (256, 1))
        cond[:L] = content.astype(np.float16)
        if not np.isfinite(cond).all():
            raise SpecError("sample is not finite")
        crop = cond[:L_crop].astype(np.float32)
        info = {**spec, "L": L, "spec": canonical({**spec, "L": spec.get("L")}), "dit": dit,
                "distance": round(float(tensor.distance(crop)), 3), **extra,
                "fit_corpus": rep.get("corpus"), "fit_created": rep.get("created"),
                "semantic_anarchy": rep.get("semantic_anarchy")}
        return cond, info
    if spec["sampler"] == "bag":
        content = token.sample(L, temperature=T, sampler="pca", rng=rng)
        crop = np.tile(pv, (L_crop, 1)).astype(np.float32)
        crop[:L] = content
    elif spec["sampler"] == "tensor-diag":
        crop = tensor.sample(1, temperature=T, sampler="diagonal", rng=rng, lengths=np.array([L]))[0]
    else:
        crop = tensor.sample(1, temperature=T, sampler="pca", rng=rng, lengths=np.array([L]),
                             comp_lo=int(spec.get("c") or 0))[0]
    if not np.isfinite(crop).all():
        raise SpecError("sample is not finite")
    cond = np.tile(pv.astype(np.float16), (256, 1))
    cond[:L_crop] = crop.astype(np.float16)
    info = {**spec, "L": L, "spec": canonical({**spec, "L": spec.get("L")}), "dit": dit,
            "distance": round(float(tensor.distance(crop.astype(np.float32))), 3),
            "fit_corpus": rep.get("corpus"), "fit_created": rep.get("created"),
            "semantic_anarchy": rep.get("semantic_anarchy")}
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
