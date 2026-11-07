"""Composer experiments (spec 4.6 and section 5): set up, render on the test studio, package for a blind listen.

    PY=stable-audio-3/optimized/mlx/.venv/bin/python
    $PY sa3lab/composer/teststudio.py --port 7862 &                 # never :7860 (Xander's studio)
    $PY sa3lab/composer/experiments.py run 0a 0b 0c 0d 0e 0h [--url http://127.0.0.1:7862/]
    $PY sa3lab/composer/experiments.py listen 0a 0b 0d 0e           # manifest.json + dadagrids per experiment
    uv run sa3lab/bench/make_listen.py --src outputs/composer-exp/<exp>
    cd outputs/composer-exp/<exp>/listen && uv run --no-project serve.py

Everything lands in `outputs/composer-exp/<exp>/` (projects + takes per case, `report.json` for the
metric experiments, `_grids/` dadagrids, `listen/` the blind page). Pieces are picked
deterministically from the starred set (render ids sorted), so a re-run uses the same material.

| exp | question | cases (per seed) |
|---|---|---|
| 0a | soft levels over one piece inside a composite | A → 2-beat gap → B, a soft region over B's first 8 s at u 0 / 0.2 / 0.4 / 0.6 |
| 0b | conform + fix-up, and the layered "glue" | *conform*: two mismatched beat pieces as is / Fit-all DSP fixes / fixes + soft 0.4 over B's start · *layer*: a bed with a −6 dB layer on track 2: kept as is / soft glue u 0.3 / u 0.5 over the overlap |
| 0c | does `Key:` in a text prompt set the key? (metric) | one text prompt × 6 keys + 2 keyless controls, 20 s; keyfind adherence in report.json |
| 0d | one 300 s pass vs chained windows | A (20 s) at 0, B (20 s) at 280 s, the 260 s between generated: one pass vs windows of 180 s with 60 s context (2 windows) |
| 0e | regenerate-to-fit and latent stretch vs rubberband (tempo +6 %) | a text render's 20 s: rubberband · regenerate with the new BPM in the prompt (same seed) and re-cut · latent stretch (SAME-L latents resampled in time, decoded; bench/latent_stretch.py) · latent stretch + soft 0.3 fix-up |
| 0h | BPM splice: T5 tokens of "BPM: 120" spliced into an anarchy bag (metric) | one beatless + one off-tempo bag × n = 0 / 16 / 64 / 128 vectors, 20 s; measured BPM in report.json |

0g (embedding triplets) has its own script: sa3lab/bench/embed_triplets.py.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import compose  # noqa: E402

ROOT = compose.OUTPUTS / "composer-exp"
PY = compose.AI_AUDIO / "stable-audio-3" / "optimized" / "mlx" / ".venv" / "bin" / "python"
SEEDS = [11, 22]
MODEL = {"dit": "medium", "decoder": "same-l", "steps": 8, "cfg": 1.0}


def starred() -> list[dict]:
    import server
    return sorted(server.starred_rows(), key=lambda r: r["id"])


def pick(rows, beat: bool, n: int, min_len: float = 40, skip=()) -> list[dict]:
    out = [r for r in rows if r["id"] not in skip and (r["seconds"] or 0) >= min_len
           and ((r.get("conf") or 0) >= 0.5 if beat else (r.get("conf") or 0) < 0.2) and r.get("cond")]
    if len(out) < n:
        raise SystemExit(f"need {n} starred {'beat' if beat else 'beatless'} renders ≥ {min_len} s, have {len(out)}")
    return out[:n]


def write(exp: str, name: str, proj: dict) -> Path:
    d = ROOT / exp / name
    d.mkdir(parents=True, exist_ok=True)
    proj = {"name": name, "model": MODEL, "seeds": SEEDS, **proj}
    (d / "project.json").write_text(json.dumps(proj, indent=1))
    return d / "project.json"


def built(pf: Path) -> dict:
    return compose.build(compose.load(pf))


# ── setups ──────────────────────────────────────────────────────────────────────────────────
def setup_0a(rows) -> list[Path]:
    A, B = pick(rows, True, 2)
    base = {"seam": 0, "cond": {"gap": "crossfade"},
            "sequence": [{"id": A["id"], "src": [20, 35]}, {"gap": 0.9}, {"id": B["id"], "src": [30, 50]}]}
    pf = write("0a", "soft-u0", base)
    lin = built(pf)["lineage"]
    b_at = lin["pieces"][1]["at"]
    out = [pf]
    for u in (0.2, 0.4, 0.6):
        out.append(write("0a", f"soft-u{int(u * 10)}", {**base, "regions": [{"type": "soft", "t0": b_at, "t1": b_at + 8, "u": u}]}))
    return out


def setup_0b(rows) -> list[Path]:
    import fit
    A, B = pick(rows, True, 2, skip=())[::-1]
    C = pick(rows, False, 1)[0]
    seq = {"seam": 1, "seam_unit": "beats", "cond": {"gap": "crossfade"},
           "sequence": [{"id": A["id"], "src": [15, 30]}, {"gap": 0.9}, {"id": B["id"], "src": [40, 55]}]}
    out = [write("0b", "conform-none", seq)]
    p = compose.load(out[0])
    rec = fit.recommend(p)
    fixed = json.loads(json.dumps(seq))
    for pi, fx in rec.items():
        fixed["sequence"][int(pi)]["fix"] = fx or None
    out.append(write("0b", "conform-fit", fixed))
    lin = built(out[-1])["lineage"]
    b_at = lin["pieces"][1]["at"]
    out.append(write("0b", "conform-fit-soft", {**fixed, "regions": [{"type": "soft", "t0": b_at, "t1": b_at + 6, "u": 0.4}]}))
    lay = {"seam": 0, "cond": {"gap": "crossfade"}, "tracks": 2,
           "pieces": [{"id": A["id"], "src": [10, 40], "at": 0, "track": 0},
                      {"id": C["id"], "src": [10, 24], "at": 8, "track": 1, "mix": {"gain": -6, "hp": {"f": 150, "slope": 12}}}]}
    out.append(write("0b", "layer-kept", lay))
    for u in (0.3, 0.5):
        out.append(write("0b", f"layer-glue-u{int(u * 10)}", {**lay, "regions": [{"type": "soft", "t0": 8, "t1": 22, "u": u}]}))
    return out


def setup_0d(rows) -> list[Path]:
    A, B = pick(rows, False, 2) if len(pick(rows, False, 1)) else pick(rows, True, 2)
    base = {"seam": 0, "cond": {"gap": "crossfade"}, "length": 300, "seeds": [33],
            "pieces": [{"id": A["id"], "src": [10, 30], "at": 0, "track": 0}, {"id": B["id"], "src": [10, 30], "at": 280, "track": 0}]}
    return [write("0d", "one-pass", {**base, "windows": {"max": 380}}),
            write("0d", "windows-180", {**base, "windows": {"max": 180, "context": 60}})]


# ── render ──────────────────────────────────────────────────────────────────────────────────
def render(pfs: list[Path], url: str) -> None:
    for pf in pfs:
        p = compose.load(pf)
        have = list((pf.parent / "takes").glob("*.json")) if (pf.parent / "takes").is_dir() else []
        if len(have) >= len(p.get("seeds") or SEEDS):
            print(f"skip {pf.parent.name}: {len(have)} takes already", flush=True)
            continue
        rel = str((pf.parent / "takes").relative_to(compose.OUTPUTS))
        t = time.time()
        compose.submit(p, url, outdir=rel, window=4, log=lambda m: print(f"  [{pf.parent.name}] {m}", flush=True))
        print(f"{pf.parent.parent.name}/{pf.parent.name}: {time.time() - t:.0f} s", flush=True)


def plain_jobs(jobs: list[tuple[str, dict]], url: str, outdir: str) -> None:
    """Text → audio jobs (no reference, no box): [(tag, {prompt, seconds, seed})] into outputs/<outdir>."""
    from gradio_client import Client
    c = Client(url, verbose=False)
    n_api = compose._api_n(c)
    vals = []
    for tag, j in jobs:
        v = compose.generate_args(n_api, j["prompt"], j["seconds"], j["seed"], MODEL, __file__, outdir, {})
        v[12] = None                                   # no inpaint reference
        v[-1] = ""
        vals.append((v, tag))
    compose._run_jobs(c, n_api, vals, 4, lambda m: print("  " + m, flush=True))


# ── 0c: key in the prompt ───────────────────────────────────────────────────────────────────
KEY_PROMPT = "melodic deep house, warm chords, round bassline, soft vocal chops, 122 BPM"
KEYS = ["A minor", "C major", "F# minor", "Eb major", "D minor", "G major"]


def run_0c(url: str) -> None:
    out = ROOT / "0c" / "takes"
    out.mkdir(parents=True, exist_ok=True)
    if len(list(out.glob("*.json"))) < len(KEYS) + 2:
        jobs = [(k, {"prompt": f"{KEY_PROMPT}, Key: {k}", "seconds": 20, "seed": 5}) for k in KEYS]
        jobs += [(f"nokey s{s}", {"prompt": KEY_PROMPT, "seconds": 20, "seed": s}) for s in (5, 6)]
        plain_jobs(jobs, url, str(out.relative_to(compose.OUTPUTS)))
    report_0c()


def report_0c() -> None:
    import keyfind
    out = ROOT / "0c" / "takes"
    rows = []
    for sc in sorted(out.glob("*.json")):
        d = json.loads(sc.read_text())
        pr = (d.get("gen") or {}).get("prompt") or d.get("prompt") or ""
        want = pr.split("Key: ")[1].strip() if "Key: " in pr else None
        a = keyfind.analyze_file(sc.with_suffix(""))
        rows.append({"file": sc.with_suffix("").name, "asked": want, "found": a["key"], "guess": a["key_guess"],
                     "conf": a["key_conf"], "exact": bool(want and a["key_guess"] == want),
                     "compatible": bool(want and keyfind.compatible(a["key_guess"], want)), "bpm": a["bpm"]})
    asked = [r for r in rows if r["asked"]]
    summ = {"n": len(asked), "exact": sum(r["exact"] for r in asked), "compatible": sum(r["compatible"] for r in asked)}
    (ROOT / "0c" / "report.json").write_text(json.dumps({"summary": summ, "rows": rows}, indent=1))
    print("0c", summ)
    for r in rows:
        print(f"  asked {r['asked']!s:9}  found {r['guess']:9} conf {r['conf']:.2f}  exact {r['exact']}  compat {r['compatible']}")


# ── 0h: BPM splice ──────────────────────────────────────────────────────────────────────────
def run_0h(url: str, rows) -> None:
    """Each case is a reference-less project (silence at strength 1 = plain text → audio) whose one
    anchor is the bag, optionally with "BPM: 120" spliced in (condmix.splice_bag)."""
    bags = [pick(rows, False, 1, min_len=20)[0]]
    off = [r for r in rows if r.get("cond") and (r.get("conf") or 0) >= 0.4 and r.get("bpm") and abs(compose.fold(r["bpm"], 120) - 120) > 6]
    if off:
        bags.append(off[0])
    pfs = []
    for bi, r in enumerate(bags):
        for n in (0, 16, 64, 128):
            a = {"id": r["id"]} | ({"splice": {"prompt": "BPM: 120", "n": n}} if n else {})
            pfs.append(write("0h", f"bag{bi}-n{n}", {"length": 20, "anchors": [a], "seeds": [7], "seam": 0}))
    render(pfs, url)
    report_0h(bags)


def report_0h(bags=None) -> None:
    import tempo
    rows = []
    for sc in sorted((ROOT / "0h").glob("*/takes/*.json")):
        t = tempo.estimate(str(sc.with_suffix("")), 120)
        rows.append({"case": sc.parent.parent.name, "file": sc.with_suffix("").name, "bpm": t["bpm"], "conf": t["conf"],
                     "on_120": bool(t["conf"] >= 0.3 and abs(compose.fold(t["bpm"], 120) - 120) < 1.5)})
    (ROOT / "0h" / "report.json").write_text(json.dumps({"rows": rows, "bags": [b["id"] for b in bags or []]}, indent=1))
    print("0h (bag0 = beatless, bag1 = off-tempo; target 120 BPM)")
    for r in rows:
        print(f"  {r['case']:12} bpm {r['bpm']} conf {r['conf']}  on 120: {r['on_120']}")


# ── 0e: regenerate-to-fit + latent stretch ──────────────────────────────────────────────────
def run_0e(url: str) -> None:
    import re
    import tempo
    import library
    d = ROOT / "0e"
    d.mkdir(parents=True, exist_ok=True)
    src = None
    for sc in sorted((compose.OUTPUTS / "sa3-gradio").glob("*.json")) + sorted((compose.OUTPUTS / "starred").glob("*.json")):
        try:
            x = json.loads(sc.read_text())
        except Exception:
            continue
        g = x.get("gen") or {}
        pb = tempo.prompt_bpm(g.get("prompt"))
        t = (x.get("analysis") or {}).get("tempo") or {}
        if pb and g.get("seed") is not None and not g.get("segments") and not g.get("a2a_audio") and t.get("conf", 0) >= 0.5 \
                and abs(t.get("bpm", 0) - pb) < 1 and (g.get("seconds") or 0) >= 30 and not g.get("loras"):
            src = (sc.resolve().with_suffix(""), g, pb)
            break
    if not src:
        raise SystemExit("0e needs a text render with a BPM in its prompt, measured on it (conf ≥ 0.5), no LoRA")
    path, g, pb = src
    k = 1.06
    nb = round(pb * k)
    k = nb / pb
    s0, s1 = 10.0, 30.0
    meta = {"source": library.render_id(path), "prompt": g["prompt"], "bpm": pb, "new_bpm": nb, "ratio": k, "range": [s0, s1]}
    # 1. rubberband
    y = compose.decode(path, s0, s1 - s0, tempo_k=k)
    compose.write_wav(d / "rubberband.wav", y)
    # 2. regenerate to fit: same prompt with the new BPM, same seed + length, cut the range scaled by 1/k
    regen = d / "regen"
    if not list(regen.glob("*.json")) if regen.is_dir() else True:
        regen.mkdir(exist_ok=True)
        pr = re.sub(r"(?i)(\b|_)(\d{2,3}(?:\.\d+)?)\s*bpm\b", f"{nb} BPM", g["prompt"])
        pr = re.sub(r"(?i)\bbpm\s*[:=]?\s*\d{2,3}(?:\.\d+)?", f"BPM: {nb}", pr)
        plain_jobs([("regen", {"prompt": pr, "seconds": float(g["seconds"]), "seed": int(g["seed"])})], url,
                   str(regen.relative_to(compose.OUTPUTS)))
    rp = next(iter(sorted(regen.glob("*.mp3"))), None)
    if rp:
        compose.write_wav(d / "regenerate.wav", compose.decode(rp, s0 / k, (s1 - s0) / k))
    # 3. latent stretch (SAME-L only, standalone)
    if not (d / "latent.wav").is_file():
        subprocess.run([str(PY), str(compose.SA3LAB / "bench" / "latent_stretch.py"), str(path), str(d / "latent.wav"),
                        "--s0", str(s0), "--s1", str(s1), "--ratio", str(k)], check=True)
    # 4. latent stretch + soft fix-up (u 0.3 everywhere, conditioned on the new-BPM prompt)
    fx = d / "latent-fixup"
    if not (fx / "takes").is_dir() or not list((fx / "takes").glob("*.json")):
        L = compose.decode(d / "latent.wav").shape[1] / compose.SR
        pr = g["prompt"]
        fx.mkdir(parents=True, exist_ok=True)
        imp = fx / "latent.wav"
        imp.write_bytes((d / "latent.wav").read_bytes())
        proj = {"pieces": [{"id": compose.render_id(imp), "src": [0, round(L, 3)], "at": 0, "align": False, "fade": [0, 0]}],
                "regions": [{"type": "soft", "t0": 0, "t1": round(L, 3), "u": 0.3}], "seam": 0, "seeds": [int(g["seed"])],
                "anchor": {"prompt": re.sub(r"\d{2,3}(?:\.\d+)?(?=\s*BPM)|(?<=BPM: )\d{2,3}", str(nb), pr)},
                "cond": {"gap": "global"}}
        pf = write("0e", "latent-fixup", proj)
        render([pf], url)
    (d / "meta.json").write_text(json.dumps(meta, indent=1))
    print("0e", meta)


# ── listen packaging ───────────────────────────────────────────────────────────────────────
LABEL = {"soft-u0": "no soft region (B kept)", "soft-u2": "soft u 0.2 over B's first 8 s", "soft-u4": "soft u 0.4",
         "soft-u6": "soft u 0.6", "conform-none": "no fixes", "conform-fit": "Fit-all DSP fixes",
         "conform-fit-soft": "fixes + soft 0.4 over B's start", "layer-kept": "layer kept as mixed",
         "layer-glue-u3": "soft glue u 0.3 over the overlap", "layer-glue-u5": "soft glue u 0.5 over the overlap",
         "one-pass": "one 300 s pass", "windows-180": "2 chained 180 s windows (60 s context)",
         "rubberband": "rubberband stretch", "regenerate": "regenerate-to-fit (new BPM in the prompt, same seed)",
         "latent": "latent stretch (SAME-L latents resampled)", "latent-fixup": "latent stretch + soft 0.3 fix-up"}
GROUPS = {"0a": [("soft", ["soft-u0", "soft-u2", "soft-u4", "soft-u6"])],
          "0b": [("conform", ["conform-none", "conform-fit", "conform-fit-soft"]), ("layer", ["layer-kept", "layer-glue-u3", "layer-glue-u5"])],
          "0d": [("long", ["one-pass", "windows-180"])]}
TEXT = {"soft": "A → short gap → B. Some clips re-render B's first 8 s softly. Which seam sounds most like one piece of music?",
        "conform": "Two beat pieces that don't match (A then B). Which version makes B sit best after A?",
        "layer": "A bed with a second layer entering at 8 s. Which version sounds most like one coherent performance?",
        "long": "300 s: 20 s of A, 260 s generated, 20 s of B. Does it stay musical and arrive at B?",
        "tempo": "The same 20 s, sped up ~6 %. Which sounds most natural?"}


def listen(exp: str) -> None:
    root = ROOT / exp
    files, prompts = [], []
    if exp == "0e":
        prompts.append({"id": "0e tempo", "text": TEXT["tempo"]})
        for n in ("rubberband", "regenerate", "latent"):
            if (root / f"{n}.wav").is_file():
                files.append({"file": f"{n}.wav", "prompt": "0e tempo", "config": n, "decoder": "same-l", "label": LABEL[n]})
        for t in sorted((root / "latent-fixup" / "takes").glob("*.mp3")):
            files.append({"file": str(t.relative_to(root)), "prompt": "0e tempo", "config": "latent-fixup", "decoder": "same-l", "label": LABEL["latent-fixup"]})
    for gname, cases in GROUPS.get(exp, []):
        seeds = sorted({json.loads(sc.read_text())["gen"]["seed"] for c in cases for sc in (root / c / "takes").glob("*.json")} if (root / cases[0]).is_dir() else set())
        for seed in seeds:
            gid = f"{exp} {gname} · seed {seed}"
            prompts.append({"id": gid, "text": TEXT[gname]})
            for c in cases:
                for sc in sorted((root / c / "takes").glob("*.json")):
                    d = json.loads(sc.read_text())
                    if d["gen"]["seed"] != seed or (d["gen"].get("composite") or {}).get("window"):
                        continue
                    files.append({"file": str(sc.with_suffix("").relative_to(root)), "prompt": gid, "config": c,
                                  "decoder": "same-l", "label": f"{LABEL.get(c, c)} (seed {seed})"})
    (root / "manifest.json").write_text(json.dumps({"prompts": prompts, "files": files, "labels": LABEL,
                                                    "page": {"title": f"Composer experiment {exp} · blind A/B",
                                                             "chips": ["smooth", "musical", "coherent", "abrupt", "mush", "artifacts", "off-beat"],
                                                             "neg_chips": ["abrupt", "mush", "artifacts", "off-beat"]}}, indent=1))
    grids = root / "_grids"
    grids.mkdir(exist_ok=True)
    paths = [root / f["file"] for f in files]
    if paths:
        subprocess.run([str(PY), str(compose.SA3LAB / "dadagrid.py"), *map(str, paths), "--labels",
                        *[f["label"][:60] for f in files], "--out", str(grids / f"{exp}.png")], check=False)
    print(f"{exp}: {len(files)} clips in {len(prompts)} groups → {root / 'manifest.json'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["run", "listen", "report"])
    ap.add_argument("exps", nargs="+")
    ap.add_argument("--url", default="http://127.0.0.1:7862/")
    a = ap.parse_args()
    if "7860" in a.url:
        raise SystemExit("7860 is Xander's live studio: render experiments on the test studio")
    rows = starred()
    for e in a.exps:
        if a.cmd == "listen":
            listen(e)
            continue
        if a.cmd == "report":
            {"0c": report_0c, "0h": report_0h}[e]()
            continue
        if e in ("0a", "0b", "0d"):
            render({"0a": setup_0a, "0b": setup_0b, "0d": setup_0d}[e](rows), a.url)
        elif e == "0c":
            run_0c(a.url)
        elif e == "0h":
            run_0h(a.url, rows)
        elif e == "0e":
            run_0e(a.url)


if __name__ == "__main__":
    main()
