"""Whole pipeline for every song folder: decode -> Griffin-Lim -> SA3 ladder (what the website plays).

    uv run python run_all.py [song dir ...]     (default: every s*/ in outputs/astra-spectrograms)

Song folders hold astra_spectrogram.png + astra_description.md (copied from the Codex runs);
everything this writes goes to <song>/work/. Then run make_page.py.
"""
import sys
from pathlib import Path

import cleanup
import decode
import invert

OUT = Path(__file__).resolve().parents[2] / "outputs" / "astra-spectrograms"

# (method, gate dB): gate = image level below which a pixel counts as silence.
# BigVGAN sounds best without a gate (digital silence is out of its training distribution).
# The site uses Griffin-Lim only; the other three were compared in the first round (README Findings).
INVERSIONS = [("gl", -76.0)]
EXTRA_INVERSIONS = [("additive", -76.0), ("vocos", -76.0), ("bigvgan", -200.0)]
# SA3 audio-to-audio: (source inversion, init noise levels). Measured on song 1: sigma 0.05-0.15
# keeps the layout, 0.2 starts re-writing it, 0.35+ is SA3's own piece on Astra's skeleton.
LADDER = [("raw_gl", [0.1, 0.2, 0.35, 0.6])]

songs = [Path(p) for p in sys.argv[1:]] or sorted(OUT.glob("s[0-9]-*"))
for song in songs:
    work = song / "work"
    print("==", song.name, decode.decode(song / "astra_spectrogram.png", work)["plot_box"], flush=True)
    for method, gate in INVERSIONS:
        invert.main(work, [method], gate)
    for src, sigmas in LADDER:
        cleanup.main(song, src, sigmas, 30.0, 7, 1)
