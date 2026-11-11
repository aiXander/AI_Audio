"""The render-path code hash: one sha256 over the *.py files the box's deploy copy holds.

The Mac's studio and the box's worker each compute it over their own tree (`AI_Audio/` of the
repo, `~/SSD2TB/sa3-worker/AI_Audio/` of the deploy copy); the studio sends a job to the box only
when the two match (remote.target). The file set is exactly what `scripts/beast.sh deploy` rsyncs:
every *.py under `sa3lab/` and `stable-audio-3/optimized/mlx/`, minus the folders it excludes. Keep
EXCLUDE and beast.sh's rsync excludes in step, or the hashes never match.

Stdlib only, so `beast.sh` can run it with any python3 on either machine:

    python3 sa3lab/beast/codehash.py [<AI_Audio root>]     # prints the hash
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TREES = ("sa3lab", "stable-audio-3/optimized/mlx")
EXCLUDE = {".venv", "__pycache__", "output", "node_modules", ".git", "loras"}   # + models/mlx (weights, no .py)


def files(root: Path = ROOT) -> list[Path]:
    out = []
    for t in TREES:
        base = root / t
        for p in base.rglob("*.py"):
            rel = p.relative_to(root)
            if EXCLUDE & set(rel.parts) or not p.is_file():
                continue
            out.append(rel)
    return sorted(out, key=lambda r: r.as_posix())


def code_hash(root: Path = ROOT) -> str:
    h = hashlib.sha256()
    for rel in files(root):
        h.update(rel.as_posix().encode() + b"\0")
        h.update((root / rel).read_bytes())
        h.update(b"\0")
    return h.hexdigest()[:16]


if __name__ == "__main__":
    print(code_hash(Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else ROOT))
