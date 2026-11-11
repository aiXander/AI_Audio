"""Re-render <stem>.spec.png and recompute fidelity.json for every .wav in a song's work dir.

    uv run python rescore.py <work dir> [<work dir> ...]
"""
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

import specgrid

for work in map(Path, sys.argv[1:]):
    db = np.load(work / "decoded.npz")["db"]
    h, w = db.shape
    specgrid.grid_png(db, work / "source.spec.png")
    fid = {}
    for wav in sorted(work.glob("*.wav")):
        y, sr = sf.read(wav, dtype="float32")
        g = specgrid.audio_to_grid(y.mean(-1) if y.ndim == 2 else y, sr, h, w)
        specgrid.grid_png(g, work / f"{wav.stem}.spec.png")
        fid[wav.stem] = specgrid.fidelity(db, g)
        print(work.parent.name, wav.stem, fid[wav.stem], flush=True)
    (work / "fidelity.json").write_text(json.dumps(fid, indent=2))
