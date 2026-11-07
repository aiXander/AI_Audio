"""Phase-0 acceptance test of the composer: package the takes for a blind listen and for looking.

    PY=stable-audio-3/optimized/mlx/.venv/bin/python
    $PY sa3lab/composer/acceptance.py [--root outputs/composer-tests]
    uv run sa3lab/bench/make_listen.py --src outputs/composer-tests
    cd outputs/composer-tests/listen && uv run --no-project serve.py

The test (spec section 5): 30 s of starred render A → 10 s gap → 30 s of starred render B, the
gap's conditioning crossfading from A's anarchy bag to B's, 3 seeds, Medium + SAME-L; once on two
beatless pieces (`accept-beatless`), once on two confident-beat pieces with tempo + beat-phase
alignment (`accept-beat`). Each take is one variant of the gap's conditioning (condmix.py):
`m1` cross-attention blend · `m3` velocity blend · `cut` hard switch at the gap's middle ·
`single` A's conditioning everywhere (no interpolation: the baseline). The 0f test bed
(`morph-*`) renders the same anchors as a plain 30 s morph, A → B over 10-20 s, no pieces.

The projects live in `<root>/<name>/project.json`, their takes in `<root>/<name>/takes/`
(rendered with `compose.py submit` against a separate studio, `teststudio.py`). This script:
- writes `<root>/manifest.json` for make_listen.py: one group per project × seed, the variants
  inside it blind; composite takes are cut to gap ± 10 s (the gap is 10-20 s of every clip),
  morphs play whole;
- draws dadagrids into `<root>/_grids/`: per project the reference + every take, plus a gap
  zoom (gap ± 5 s) per project;
- prints secondary gap metrics (loudness vs the pieces around it; for beat projects the gap's
  tempo, beat confidence and grid offset). Ears decide; these only point at what to listen for.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
AI_AUDIO = HERE.parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]
import compose  # noqa: E402

PY = AI_AUDIO / "stable-audio-3" / "optimized" / "mlx" / ".venv" / "bin" / "python"
LABELS = {"m1": "M1: cross-attention blend crossfade", "m3": "M3: velocity blend crossfade (2x cost)",
          "cut": "hard conditioning cut at the gap middle", "single": "no interpolation: A's conditioning throughout"}
ORDER = ["m1", "m3", "cut", "single"]
PAGE = {"title": "Composer phase 0 · gap conditioning · blind A/B",
        "chips": ["smooth bridge", "musical", "gap fits A", "gap fits B", "abrupt", "filler / mush",
                  "beat lost", "artifacts"],
        "neg_chips": ["abrupt", "filler / mush", "beat lost", "artifacts"]}


def takes(root: Path, name: str) -> list[tuple[str, int, Path, dict]]:
    out = []
    for sc in sorted((root / name / "takes").glob("*.mp3.json")):
        d = json.loads(sc.read_text())
        c = (d.get("gen") or {}).get("composite") or {}
        if c.get("project") == name:
            out.append((c["variant"], int(d["gen"]["seed"]), sc.with_suffix(""), c))
    return sorted(out, key=lambda t: (t[1], ORDER.index(t[0]) if t[0] in ORDER else 9))


def gap_of(c: dict):
    g = [r for r in c.get("regions") or [] if r.get("type") == "gap"]
    return (g[0]["t0"], g[0]["t1"]) if g else None


def rms_db(y):
    return float(10 * np.log10((y ** 2).mean() + 1e-12))


def metrics(path: Path, c: dict) -> dict:
    gap = gap_of(c)
    if not gap:
        return {}
    y = compose.decode(path)
    sr = compose.SR
    a, b = gap
    seg = lambda t0, t1: y[:, int(max(0, t0) * sr): int(t1 * sr)]   # noqa: E731
    m = {"gap_db": round(rms_db(seg(a, b)), 1), "before_db": round(rms_db(seg(a - 5, a)), 1),
         "after_db": round(rms_db(seg(b, b + 5)), 1)}
    if c.get("bpm"):
        t = compose.measure_tempo(seg(a, b))
        P = 60.0 / c["bpm"]
        ph = compose.beat_phase(seg(a, b), c["bpm"])
        off = ((a + ph - compose.PRE_ROLL) % P)
        m.update(gap_bpm=t["bpm"], gap_conf=t["conf"], grid_ms=round(((off + P / 2) % P - P / 2) * 1000, 1))
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(AI_AUDIO / "outputs" / "composer-tests"))
    a = ap.parse_args()
    root = Path(a.root).resolve()
    names = sorted(p.parent.name for p in root.glob("*/project.json")
                   if p.parent.name.startswith(("accept-", "morph-")))
    files, prompts = [], []
    grids = root / "_grids"
    grids.mkdir(exist_ok=True)
    for name in names:
        tk = takes(root, name)
        if not tk:
            continue
        c0 = tk[0][3]
        gap = gap_of(c0)
        kind = "beat" if "beat" in name and "beatless" not in name else "beatless"
        for seed in sorted({t[1] for t in tk}):
            gid = f"{name} · seed {seed}"
            if gap:
                text = (f"{kind} pieces: 30 s of A → {gap[1] - gap[0]:.1f} s generated gap → 30 s of B. "
                        f"Each clip is gap ± 10 s: A until 10 s, the gap 10-20 s, B after.")
            else:
                text = "no pieces: one 30 s render whose conditioning morphs from A to B over 10-20 s."
            prompts.append({"id": gid, "text": text})
            for var, s, path, c in tk:
                if s != seed:
                    continue
                r = {"file": str(path.relative_to(root)), "prompt": gid, "config": var, "decoder": "same-l",
                     "label": f"{LABELS.get(var, var)} ({name}, seed {seed})"}
                if gap:
                    r["excerpt"] = [max(0.0, gap[0] - 10), min(c["length"], gap[1] + 10)]
                files.append(r)
        # dadagrids: whole takes (+ the reference), and a gap zoom
        ref = root / name / "ref.wav"
        rows = ([ref] if gap else []) + [t[2] for t in tk]
        labs = (["reference (pieces + silent gap)"] if gap else []) + [f"{t[0]}  seed {t[1]}" for t in tk]
        subprocess.run([str(PY), str(AI_AUDIO / "sa3lab" / "dadagrid.py"), *map(str, rows), "--labels", *labs,
                        "--out", str(grids / f"{name}.png")], check=True, capture_output=True)
        if gap:
            zd = root / name / "_zoom"
            zd.mkdir(exist_ok=True)
            zrows = []
            for p in rows:
                z = zd / (p.stem + ".zoom.wav")
                compose.write_wav(z, compose.decode(p, gap[0] - 5, gap[1] - gap[0] + 10))
                zrows.append(z)
            subprocess.run([str(PY), str(AI_AUDIO / "sa3lab" / "dadagrid.py"), *map(str, zrows), "--labels",
                            *[f"{x}  (gap = 5-{5 + gap[1] - gap[0]:.1f} s)" for x in labs],
                            "--out", str(grids / f"{name}.gap-zoom.png")], check=True, capture_output=True)
            print(f"\n{name}  gap {gap[0]:.2f}-{gap[1]:.2f} s  bpm {c0.get('bpm')}")
            for var, s, path, c in tk:
                print(f"  {var:7s} seed {s}  {metrics(path, c)}")
    (root / "manifest.json").write_text(json.dumps({"prompts": prompts, "files": files, "labels": LABELS,
                                                    "page": PAGE}, indent=1))
    print(f"\nmanifest: {len(files)} clips in {len(prompts)} groups → {root / 'manifest.json'}")
    print(f"grids: {grids}")


if __name__ == "__main__":
    main()
