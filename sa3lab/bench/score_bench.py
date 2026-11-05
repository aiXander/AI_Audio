# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["numpy", "scipy", "soundfile", "pyloudnorm", "torch", "torchaudio",
#                 "transformers", "audiobox_aesthetics", "requests"]
# ///
"""SA3 quality-trick benchmark — scoring half.

Scores every wav listed in outputs/sa3_bench_quality/manifest.json:
  Audiobox Aesthetics (Meta, no-reference, 1-10): PQ production quality, PC production
      complexity, CE content enjoyment, CU content usefulness.
  CLAP (laion/larger_clap_music): cosine between the clip (mean of 10 s windows) and the
      descriptive prompt — always the descriptive one, also for tag-prompt renders.
  Signal: level of 12-16 / 16-22 kHz relative to the full band ("air"); HF tonality in
      6-18 kHz (peak-over-median per STFT frame, the squeak measure from the squeakfix
      card): p95 and % of frames above 20 dB; LUFS, sample peak, clipped-sample share,
      stereo side/mid ratio.

Run: uv run sa3lab/bench/score_bench.py   (models download into AI_Audio/models/hf-cache)
Writes results.csv + summary.md next to the renders.
"""
import json, os, sys
from pathlib import Path

AI_AUDIO = Path(__file__).resolve().parents[2]
os.environ.setdefault("HF_HUB_CACHE", str(AI_AUDIO / "models" / "hf-cache"))
os.environ.setdefault("TORCH_HOME", str(AI_AUDIO / "models" / "torch-cache"))

import numpy as np, soundfile as sf, pyloudnorm as pyln, torch
from scipy.signal import resample_poly, stft

OUT = AI_AUDIO / "outputs" / "sa3_bench_quality"
SR = 44100


def signal_metrics(x):  # x (T, 2)
    mono = x.mean(1)
    f, _, Z = stft(mono, SR, nperseg=4096, noverlap=3072)
    P = np.abs(Z) ** 2 + 1e-20
    tot = P.sum(0)
    band = lambda lo, hi: P[(f >= lo) & (f < hi)]
    rel = lambda lo, hi: 10 * np.log10(band(lo, hi).sum() / tot.sum())
    active = 10 * np.log10(tot / tot.max()) > -50
    tons = []
    for lo, hi in [(6000, 10000), (10000, 14000), (14000, 18000)]:
        b = 10 * np.log10(band(lo, hi))
        tons.append(b.max(0) - np.median(b, 0))
    ton = np.max(tons, 0)[active]
    mid, side = x.mean(1), (x[:, 0] - x[:, 1]) / 2
    return {
        "air_12_16_dB": rel(12000, 16000), "air_16_22_dB": rel(16000, 22050),
        "tonality_p95_dB": float(np.percentile(ton, 95)),
        "tonal_frames_pct": float((ton > 20).mean() * 100),
        "lufs": float(pyln.Meter(SR).integrated_loudness(x)),
        "peak_dBFS": float(20 * np.log10(np.abs(x).max() + 1e-9)),
        "clipped_pct": float((np.abs(x) > 0.999).mean() * 100),
        "side_mid_dB": float(10 * np.log10((side ** 2).mean() / ((mid ** 2).mean() + 1e-12) + 1e-12)),
    }


def main():
    man = json.loads((OUT / "manifest.json").read_text())
    prompts = {p["id"]: p for p in man["prompts"]}
    files = [r for r in man["files"] if (OUT / r["file"]).exists()]
    print(f"scoring {len(files)} files", flush=True)

    from audiobox_aesthetics.infer import initialize_predictor
    aes = initialize_predictor()
    from transformers import ClapModel, ClapProcessor
    clap = ClapModel.from_pretrained("laion/larger_clap_music").eval()
    proc = ClapProcessor.from_pretrained("laion/larger_clap_music")

    rows = []
    for i, r in enumerate(files):
        path = OUT / r["file"]
        x, sr = sf.read(path, dtype="float32"); assert sr == SR
        row = dict(r, **signal_metrics(x))
        row.update({k: float(v) for k, v in aes.forward([{"path": torch.from_numpy(x.T.copy()), "sample_rate": SR}])[0].items()})
        y = resample_poly(x.mean(1), 160, 147)  # 44.1k -> 48k
        wins = [y[s: s + 480000] for s in range(0, max(len(y) - 480000, 0) + 1, 480000)]
        with torch.no_grad():
            # full forward: returns the projected, contrastive embeddings (text_embeds / audio_embeds)
            ai = proc(text=[prompts[r["prompt"]]["text"]], audio=wins, sampling_rate=48000,
                      return_tensors="pt", padding=True)
            out = clap(**ai)
            aemb = torch.nn.functional.normalize(out.audio_embeds, dim=-1).mean(0)
            temb = torch.nn.functional.normalize(out.text_embeds[0], dim=0)
        row["clap"] = float(torch.nn.functional.normalize(aemb, dim=0) @ temb)
        rows.append(row)
        print(f"[{i+1}/{len(files)}] {r['file']}  PQ {row['PQ']:.2f}  CE {row['CE']:.2f}  "
              f"clap {row['clap']:.3f}  ton95 {row['tonality_p95_dB']:.1f}", flush=True)

    keys = list(rows[0].keys())
    with open(OUT / "results.csv", "w") as fh:
        fh.write(",".join(keys) + "\n")
        for r in rows:
            fh.write(",".join(f"{r[k]:.4f}" if isinstance(r[k], float) else str(r[k]) for k in keys) + "\n")

    # summary: mean over prompts per (config, decoder), plus delta vs baseline/stock
    metrics = ["PQ", "PC", "CE", "CU", "clap", "air_16_22_dB", "tonality_p95_dB",
               "tonal_frames_pct", "lufs", "clipped_pct", "side_mid_dB"]
    groups = {}
    for r in rows:
        groups.setdefault((r["config"], r["decoder"]), []).append(r)
    base = {m: np.mean([r[m] for r in groups[("A_baseline", "stock")]]) for m in metrics}
    lines = ["| config | decoder | " + " | ".join(metrics) + " |", "|" + "---|" * (len(metrics) + 2)]
    for (c, d), rs in sorted(groups.items()):
        cells = []
        for m in metrics:
            v = np.mean([r[m] for r in rs])
            cells.append(f"{v:.2f} ({v - base[m]:+.2f})" if (c, d) != ("A_baseline", "stock") else f"{v:.2f}")
        lines.append(f"| {c} | {d} | " + " | ".join(cells) + " |")
    lines += ["", "Per prompt (PQ / CE / clap / tonality p95):", ""]
    for pid in prompts:
        lines.append(f"**{pid}**")
        for (c, d), rs in sorted(groups.items()):
            r = next(x for x in rs if x["prompt"] == pid)
            lines.append(f"- {c} / {d}: PQ {r['PQ']:.2f}, CE {r['CE']:.2f}, clap {r['clap']:.3f}, "
                         f"ton95 {r['tonality_p95_dB']:.1f}, air16-22 {r['air_16_22_dB']:.1f}")
    (OUT / "summary.md").write_text("\n".join(lines) + "\n")
    key_path = OUT / "listen" / "blind_key.json"
    if key_path.exists():  # machine scores per blind clip, shown on the page after Reveal
        by_file = {r["file"]: r for r in rows}
        auto = {clip: {m: float(by_file[k["file"]][m]) for m in metrics} for clip, k in json.loads(key_path.read_text()).items()
                if k["file"] in by_file}
        (OUT / "listen" / "autoscores.json").write_text(json.dumps(auto, indent=1))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
