"""Render every prompt of a corpus exactly as the studio would, into a separate folder.

Uses `headless.run` (the studio itself, headless; see its docstring for why) with the UI's
default controls: Medium + SAME-L, 120 s, 8 steps, CFG 1, random seed, MP3, no LoRA.
Adds a `corpus` block to each sidecar (corpus file, line, world, family, style from the
sibling `.jsonl`), which the studio ignores. Resumable.

Run with the MLX venv (does not touch a running studio, but shares the GPU with it):
  stable-audio-3/optimized/mlx/.venv/bin/python sa3lab/anarchy/render_corpus.py \
      sa3lab/anarchy/corpora/grammar-music-100.txt --out outputs/sa3-corpus-grammar-music-100
  add --dry-run to print the control values without rendering.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from headless import AI_AUDIO, Job, run


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("corpus", help="prompt .txt, one per line (a .jsonl beside it adds the corpus block)")
    ap.add_argument("--out", required=True, help="target folder (relative = to AI_Audio)")
    ap.add_argument("--limit", type=int, default=0, help="render only the first N prompts")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    corpus = Path(args.corpus).resolve()
    out = Path(args.out)
    out = out if out.is_absolute() else AI_AUDIO / out
    prompts = [ln.strip() for ln in corpus.read_text().splitlines() if ln.strip()]
    mp = corpus.with_suffix(".jsonl")
    meta = ([json.loads(ln) for ln in mp.read_text().splitlines() if ln.strip()]
            if mp.exists() else [{} for _ in prompts])
    jobs = [Job(p, extra={"corpus": {"file": str(corpus.relative_to(AI_AUDIO)), "line": i + 1,
                                     **{k: meta[i].get(k) for k in ("world", "family", "style")}}})
            for i, p in enumerate(prompts)]
    run(out, jobs[:args.limit] if args.limit else jobs, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
