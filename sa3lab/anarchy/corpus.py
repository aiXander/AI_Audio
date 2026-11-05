"""Generate the promptless-anarchy base corpus in SA3's own training-prompt grammar.

Why synthetic: the corpus defines the space the fitted distribution samples from, and
SA3's training prompts follow a known recipe (paper; see
stable-audio-3/docs/reference/sa3-model-map.md, "Prompt distribution it saw"): a random
subset of AudioSparx metadata fields (description, title, keywords, BPM, genre, moods,
instruments) joined by commas, with — each independently half the time — the field name
prepended, the fields shuffled/subsampled, and the whole prompt lowercased. Prompts written
that way sit on the manifold the DiT was trained on, so the fit measures *its* space, not
our prose habits. Breadth comes from `worlds.py` (one entry per musical region).

Each prompt gets one of three surface styles, by quota, so the corpus spans the forms the
model saw: `tags` (pure metadata fields), `prose` (the description sentence, Freesound
LLM-caption style) and `mixed` (description + fields). The style is recorded per prompt
because it is a candidate for PC1 — check that in the autopsy before reading PC1 as music.
The quota is global, with one exception: `edge` worlds (deliberately off-manifold, see
`worlds.py`) only ever get `prose` or `mixed`, since a tags-only render of one reads as an
ordinary prompt. Measure a corpus with `diversity.py`.

Outputs (deterministic for a given --seed):
  <out>.txt    one prompt per line (what `mine` encodes)
  <out>.jsonl  one record per prompt: world, family, style, the grammar coin flips,
               T5Gemma token count (if the tokenizer is on disk)

Run (any python ≥3.10; the MLX venv has sentencepiece for token counts):
  stable-audio-3/optimized/mlx/.venv/bin/python sa3lab/anarchy/corpus.py \
      --per-world 1 --out sa3lab/anarchy/corpora/grammar-music-100
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from worlds import TITLES, WORLDS, World  # noqa: E402

AI_AUDIO = HERE.parents[1]
T5GEMMA_NPZ = AI_AUDIO / "stable-audio-3/optimized/mlx/models/mlx/t5gemma_f16.npz"

STYLE_QUOTA = {"tags": 0.55, "prose": 0.25, "mixed": 0.20}


def _pick(rng: random.Random, pool, lo: int, hi: int) -> list[str]:
    lo = min(lo, len(pool))            # solo worlds have a one-item pool
    k = rng.randint(lo, max(lo, hi))
    return rng.sample(list(pool), k)


def _prefix(w: World, rng: random.Random, force: bool = False) -> str | None:
    if w.track_type == "Instrument":   # stems always say so: that is what isolates them
        duo = "Format: Duo, " if len(w.instruments) == 2 else ""
        return f"TrackType: Instrument, {duo}".rstrip(", ")
    if force or rng.random() < 0.5:
        return "TrackType: Music, VocalType: Instrumental"
    return None


def _fields(w: World, rng: random.Random, flips: dict) -> list[str]:
    """The metadata-field half of a prompt, with the paper's coin flips applied."""
    named, subsample = flips["named"], flips["subsample"]
    lo = 1 if subsample else 2
    f: dict[str, str] = {}
    genres = _pick(rng, w.genres, 1, len(w.genres)) if subsample else list(w.genres)
    f["Genre"] = ", ".join(genres)
    f["Instruments"] = ", ".join(_pick(rng, w.instruments, lo, len(w.instruments)))
    f["Moods"] = ", ".join(_pick(rng, w.moods, lo, len(w.moods)))
    if w.keywords:
        f["Keywords"] = ", ".join(_pick(rng, w.keywords, 1, len(w.keywords)))
    if w.bpm:
        f["BPM"] = str(rng.randint(*w.bpm))
    if rng.random() < 0.2:
        f["Title"] = rng.choice(TITLES)
    keys = list(f)
    if subsample:
        # Drop fields, but always keep Genre and at least 3 fields: the paper's
        # subsampling can strip a prompt to "bpm: 171", which in a small corpus wastes
        # a whole world. (The description carries the genre in `mixed`, but keeping
        # it there too is harmless.)
        keep = {"Genre"} | {k for k in keys if rng.random() < 0.65}
        rest = [k for k in keys if k not in keep]
        rng.shuffle(rest)
        keep |= set(rest[:max(0, 3 - len(keep))])
        keys = [k for k in keys if k in keep]
    if flips["shuffle"]:
        rng.shuffle(keys)
    out = []
    for k in keys:
        if named:
            out.append(f"{k}: {f[k]}")
        elif k == "BPM":
            out.append(f"{f[k]} BPM")
        else:
            out.append(f[k])
    return out


def render(w: World, style: str, rng: random.Random) -> tuple[str, dict]:
    flips = {k: rng.random() < 0.5 for k in ("named", "shuffle", "subsample", "lower")}
    parts: list[str] = []
    if style == "tags":
        pre = _prefix(w, rng)
        parts = ([pre] if pre else []) + _fields(w, rng, flips)
        text = ", ".join(parts)
    elif style == "prose":
        pre = _prefix(w, rng)
        desc = w.desc
        if w.bpm and rng.random() < 0.5:
            desc = desc.rstrip(".") + f", {rng.randint(*w.bpm)} BPM."
        text = f"{pre}, {desc}" if pre else desc
    else:  # mixed: description + metadata fields
        pre = _prefix(w, rng)
        text = ", ".join(([pre] if pre else []) + [w.desc.rstrip(".")] + _fields(w, rng, flips))
    if flips["lower"]:
        text = text.lower()
    return text, flips


def _styles(jobs: list[World], rng: random.Random) -> list[str]:
    n = len(jobs)
    counts = {s: round(q * n) for s, q in STYLE_QUOTA.items()}
    counts["tags"] += n - sum(counts.values())
    styles = [s for s, c in counts.items() for _ in range(c)]
    rng.shuffle(styles)
    # Edge worlds never get pure tags: their genre/instrument fields read as a plain
    # prompt, the edge lives in the description. Swap with a non-edge prose/mixed slot.
    free = [i for i, w in enumerate(jobs) if w.family != "edge" and styles[i] != "tags"]
    for i, w in enumerate(jobs):
        if w.family == "edge" and styles[i] == "tags" and free:
            j = free.pop()
            styles[i], styles[j] = styles[j], styles[i]
    return styles


def _tokenizer():
    try:
        import numpy as np
        import sentencepiece as spm
        arr = np.load(T5GEMMA_NPZ)["TOKENIZER_MODEL"]
        sp = spm.SentencePieceProcessor()
        sp.LoadFromSerializedProto(arr.tobytes())
        return sp
    except Exception as e:  # token counts are a nicety, never a blocker
        print(f"(no token counts: {e})")
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--per-world", type=int, default=1, help="prompts drawn per world")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=str(HERE / "corpora" / "grammar-music-100"),
                    help="output path without extension")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    jobs = [w for w in WORLDS for _ in range(args.per_world)]
    styles = _styles(jobs, rng)
    sp = _tokenizer()

    rows, seen = [], set()
    for w, style in zip(jobs, styles):
        for _ in range(20):            # redraw on an exact duplicate
            text, flips = render(w, style, rng)
            if text not in seen:
                break
        seen.add(text)
        rows.append(dict(prompt=text, world=w.id, family=w.family, style=style,
                         track_type=w.track_type, **flips,
                         tokens=len(sp.Encode(text)) if sp else None))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".txt").write_text("\n".join(r["prompt"] for r in rows) + "\n")
    with out.with_suffix(".jsonl").open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    print(f"{len(rows)} prompts from {len(WORLDS)} worlds → {out}.txt / .jsonl")
    print("styles  ", dict(Counter(r["style"] for r in rows)))
    print("families", dict(Counter(r["family"] for r in rows)))
    if sp:
        toks = sorted(r["tokens"] for r in rows)

        def q(p: float) -> int:
            return toks[min(len(toks) - 1, int(p * len(toks)))]
        print(f"tokens   min {toks[0]}  p25 {q(.25)}  median {q(.5)}  p75 {q(.75)}  max {toks[-1]}")


if __name__ == "__main__":
    main()
