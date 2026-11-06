"""Bake anarchy specs into saved conditionings the studio can render as `anarchy: @file`.

The studio imports `sampler.py` once at boot and doesn't auto-restart, so a sampler added
since its last `make start` (a new operator, a new post-op knob) is unknown to the
running process. Every studio build already renders `anarchy: @<file>.cond.npz`, so this
script computes the conditioning with the sampler code on disk and saves it:

  outputs/anarchy/_specs/<slug>.cond.npz   keys: cond (256, 768) fp16, source_spec

The key is `source_spec`, not `spec`: a studio booted before 2026-11-06 01:00 parses a
stored `spec` and fails the render on any knob it doesn't know yet (s/j/r did exactly that).

The stored `source_spec` is what `analyze_labels.py` (and, after a restart, `sampler._from_file`)
reads back, so a precomputed render analyzes like the native spec. After a restart the
native spec string gives the identical array (same code, same rngs), so the two spellings
pool into one repeat group (`analyze_labels` groups by conditioning hash).

  stable-audio-3/optimized/mlx/.venv/bin/python sa3lab/anarchy/precompute.py \
      "anarchy: grammar-music-100 cross T1 d3 k2" "anarchy: @a.cond.npz + @b.cond.npz" …
  → prints one `anarchy: @anarchy/_specs/….cond.npz` prompt per spec

`mutate` needs the T5Gemma encoder and is not supported here (render it natively).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sampler  # noqa: E402

SPECS = sampler.OUTPUTS / "anarchy" / "_specs"


def slug(spec: str) -> str:
    body = spec.split(":", 1)[1].strip()
    if body.startswith("@"):                       # breed: name by its parents' stems
        stems = [Path(f.strip()[1:]).name.split(".")[0][:24] for f in body.split(" + ")]
        tag = hashlib.md5(body.encode()).hexdigest()[:6]
        return "breed_" + "+".join(re.sub(r"[^\w.-]+", "-", s) for s in stems) + f"_{tag}"
    return re.sub(r"[^\w.:-]+", "_", body).replace(":", "-")


def bake(spec: str, dit: str = "medium") -> str:
    parsed = sampler.parse(spec)
    if parsed.get("sampler") == "mutate":
        raise sampler.SpecError("mutate needs T5Gemma: render it natively")
    cond, _ = sampler.conditioning(parsed, dit)
    SPECS.mkdir(parents=True, exist_ok=True)
    out = SPECS / f"{slug(spec)}.cond.npz"
    np.savez_compressed(out, cond=cond.astype(np.float16), source_spec=np.array(sampler.canonical(parsed)))
    return f"anarchy: @{out.relative_to(sampler.OUTPUTS)}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("specs", nargs="+")
    ap.add_argument("--dit", default="medium")
    args = ap.parse_args()
    for s in args.specs:
        print(bake(s, args.dit), flush=True)


if __name__ == "__main__":
    main()
