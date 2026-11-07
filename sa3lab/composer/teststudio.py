"""A second studio for agents: another port, its own output folder, never Xander's live :7860.

    stable-audio-3/optimized/mlx/.venv/bin/python sa3lab/composer/teststudio.py \
        [--port 7862] [--outdir composer-tests/_studio]

Same code as `sa3lab/studio` (every patch, the real `/generate` queue, sidecars), but upstream's
OUTPUT_DIR and the outdir view are pinned to `outputs/<outdir>` before studio.py is imported, so
its `.studio_prefs.json`, archived inputs and default renders stay there and the saved active
folder (`outputs/.sa3_outdir.json`) is never written (outdir.py's $SA3_OUTDIR). Jobs still pick
their own destination folder (compose.py submit --outdir). It loads its own MLX model: a second
copy in RAM next to the live studio, so stop it (Ctrl-C / kill) when the test is done.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AI_AUDIO = HERE.parents[1]
MLX = AI_AUDIO / "stable-audio-3" / "optimized" / "mlx"
os.environ.setdefault("HF_HUB_CACHE", str(AI_AUDIO / "models" / "hf-cache"))

ap = argparse.ArgumentParser()
ap.add_argument("--port", default="7862")
ap.add_argument("--outdir", default="composer-tests/_studio")
a, rest = ap.parse_known_args()
if a.port == "7860":
    raise SystemExit("7860 is Xander's live studio: pick another port")
out = (AI_AUDIO / "outputs" / a.outdir).resolve()
out.mkdir(parents=True, exist_ok=True)
sys.path[:0] = [str(MLX), str(MLX / "scripts"), str(AI_AUDIO / "sa3lab")]
import sa3_gradio as up  # noqa: E402

up.OUTPUT_DIR = out
os.environ["SA3_OUTDIR"] = str(out)
import studio  # noqa: E402

sys.argv = [sys.argv[0], "--port", str(a.port)] + rest
studio.main()
