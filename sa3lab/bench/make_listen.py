# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "soundfile", "pyloudnorm"]
# ///
"""Build the blind listening folder for the SA3 quality bench.

Reads outputs/sa3_bench_quality/manifest.json and writes outputs/sa3_bench_quality/listen/:
  <clip>.wav   loudness-matched copy (-18 LUFS, peak capped at -1 dBFS), random clip name;
               120 s renders become their 30-60 s excerpt so length doesn't give them away
  <clip>.json  sidecar: group (prompt id), prompt text, random order within the group
  blind_key.json  clip -> config / decoder / source file (served only on Reveal)
  label.html, serve.py, label_config.json   the blind A/B page (sa3lab/bench/labeler/)
Idempotent: existing clips keep their names and order; new renders get new ones.

Run: uv run sa3lab/bench/make_listen.py
"""
import json, random, secrets, shutil
from pathlib import Path

import numpy as np, pyloudnorm as pyln, soundfile as sf

AI_AUDIO = Path(__file__).resolve().parents[2]
SRC = AI_AUDIO / "outputs" / "sa3_bench_quality"
DST = SRC / "listen"
SR, TARGET_LUFS, PEAK_CAP = 44100, -18.0, 10 ** (-1 / 20)
LABELS = {
    "A_baseline": "A baseline (ARC, CFG 1, 8 steps)",
    "B_cfg3": "B CFG 3 + APG, 16 steps",
    "C_merge33": "C base:ARC merge 1:2",
    "D_merge33_cfg3": "D merge 1:2 + CFG 3, 16 steps",
    "E_long120": "E 120 s render (30-60 s excerpt)",
    "F_tagprompt": "F tag-style prompt",
    "A_baseline_rt3": "A baseline after 3 encode/decode round trips",
}
CONFIG = {"title": "SA3 quality tricks · blind A/B",
          "chips": ["clean", "musical", "on-prompt", "airy highs", "crunchy", "squeaky highs", "muffled",
                    "artifacts", "boring"],
          "neg_chips": ["crunchy", "squeaky highs", "muffled", "artifacts", "boring"]}


def main():
    man = json.loads((SRC / "manifest.json").read_text())
    prompts = {p["id"]: p for p in man["prompts"]}
    DST.mkdir(exist_ok=True)
    for f in ("label.html", "serve.py"):
        shutil.copy(Path(__file__).parent / "labeler" / f, DST / f)
    (DST / "label_config.json").write_text(json.dumps(CONFIG, indent=1))
    key_path = DST / "blind_key.json"
    key = json.loads(key_path.read_text()) if key_path.exists() else {}
    by_src = {v["file"]: k for k, v in key.items()}
    meter = pyln.Meter(SR)
    rng = random.Random()
    group_order = {pid: i for i, pid in enumerate(prompts)}
    new = 0
    for r in man["files"]:
        src = SRC / r["file"]
        if not src.exists():
            continue
        clip = by_src.get(r["file"])
        if clip is None:
            clip, new = "clip_" + secrets.token_hex(4), new + 1
            key[clip] = dict(file=r["file"], prompt=r["prompt"], config=r["config"], decoder=r["decoder"],
                             label=LABELS.get(r["config"], r["config"]), order=rng.random())
        x, sr = sf.read(src, dtype="float64"); assert sr == SR
        if r["config"] == "E_long120":
            x = x[30 * SR: 60 * SR]
        gain = 10 ** ((TARGET_LUFS - meter.integrated_loudness(x)) / 20)
        gain = min(gain, PEAK_CAP / (np.abs(x).max() + 1e-12))
        sf.write(DST / f"{clip}.wav", x * gain, SR, subtype="PCM_24")
        key[clip]["gain_dB"] = round(20 * np.log10(gain), 2)
        (DST / f"{clip}.json").write_text(json.dumps(dict(
            audio=f"{clip}.wav", group=r["prompt"], prompt=prompts[r["prompt"]]["text"],
            group_order=group_order[r["prompt"]], order=key[clip]["order"])))
    key_path.write_text(json.dumps(key, indent=1))
    print(f"{len(key)} clips in {DST} ({new} new)")


if __name__ == "__main__":
    main()
