# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "matplotlib"]
# ///
"""Which sampling settings earn good labels? Analyze a folder of labeled studio renders.

Reads every studio sidecar (`<audio>.json`, written by persist.py / library.py) under one
or more folders, recursively (so a `deleted/` subfolder's trashed clips count as score 0),
and writes `<first folder>/_analysis/`: `report.md` (also printed), `rows.csv` (one row per
render) and PNG plots. Works on anarchy renders (the `anarchy` block: sampler, T, d, L, c,
k, m, distance) and on plain text-prompt renders alike.

  uv run sa3lab/anarchy/analyze_labels.py outputs/anarchy/session-1 [more dirs] [--keep 7]

**One dataset per LoRA set.** A LoRA changes the model weights; in Xander's listening it
moves the outcome far more than the conditioning (prompt, sampler, T, L) does. Pooling LoRA
renders with base renders would make every knob a hidden proxy for "was a LoRA on". So rows
are split by the set of LoRA files active in the render (`gen.loras` names; strength
ignored for the split and analyzed as the `lora_strength` knob inside that dataset):
`_analysis/base/` (no LoRA) and `_analysis/lora-<names>/`, each with its own report.md,
noise floor, knobs and plots. `_analysis/report.md` = an overview table (the only
cross-dataset view) followed by every dataset's report; `rows.csv` stays one file, with a
`lora_set` column. Repeat groups never span LoRA sets.

The second trap: one conditioning re-rendered with other noise seeds or
lengths can swing 0 → 4 on its own (session-1, direction d0). So:
- renders sharing a conditioning (same `.cond.npz` bytes, or for text prompts the same
  prompt + settings except seed/length) form a *repeat group*; the spread inside groups
  gives the seed + label noise floor, reported as a share of total score variance;
- conditioning knobs (T, L, distance, c, k, m) are analyzed on one row per conditioning
  (group mean score), so a ten-times re-rendered tensor doesn't dominate a T bin; render
  knobs (seconds, steps, cfg, …) use every render;
- each knob gets a Spearman rank correlation with a bootstrap 90 % CI: a CI that excludes
  0 is flagged as signal, everything else is noise at this n;
- the same audio file labeled twice (a duplicate render, trashed later) measures label noise.

The third trap is our own sampling. Batches follow up on what scored well (re-seeds,
ladders, n1 / r50 variants of a liked direction), so per-render tables end up weighted by
how often we *chose* to render something, and a knob correlation can be one liked
direction in disguise (session-1: "bag T > 1.2 scores higher" was the d45391 family,
rendered 15+ times). So each render also gets an *idea* (`family_of`: fit + sampler + d,
whatever T / L / knobs / seed it was later rendered with) and a `first` flag (the idea's
earliest render, made before any of its scores existed). The report leads with a
design-independent view: per-sampler first-look and idea-mean scores, knob correlations
on first looks only, and paired knob effects (same idea, same seed, one knob token
removed; averaged per idea first). Read sampler / knob questions there. Paired effects
against a sibling that was picked for scoring high carry regression to the mean (≈ −2
after picking 5–6s), so a knob only probed on such anchors is untested, not harmful.

Plots, per dataset folder: knobs.png (score vs each numeric knob, binned mean), samplers.png (score histogram +
per-sampler strip), heat_T_L.png (mean score over T × L), repeats.png (score spread per
repeat group), timeline.png (score over render order: what was explored when, labeler drift).
Standalone: numpy + matplotlib via uv, no MLX, so it runs while the studio renders.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

# reference palette (dataviz skill), light mode; colour follows the sampler, fixed order
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
SAMPLER_ORDER = ["tensor-pca", "tensor-diag", "bag", "cocktail", "mutate", "text", "cross", "breed"]
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
OTHER = "#8a8984"
COND_KNOBS = ["T", "L", "distance", "c", "k", "m", "x"]
RENDER_KNOBS = ["seconds", "steps", "cfg", "apg", "sigma_max", "tempo_conf", "bpm", "lora_strength"]
# no "lora" here: the LoRA set splits the dataset (see dataset_name), it is never a knob
CAT_KNOBS = ["sampler", "fit", "dit", "decoder", "noise", "gen_sampler", "mode", "seconds",
             "ghosts", "normmatch"]
OUTPUTS = Path(__file__).resolve().parents[2] / "outputs"
BASE = "base"                      # dataset name for renders with no LoRA


def dataset_name(lora_set: str) -> str:
    """Folder-safe dataset name: `base`, or `lora-<names>` (strength is a knob inside it)."""
    if not lora_set:
        return BASE
    return "lora-" + "".join(c if c.isalnum() or c in "-_=+." else "_" for c in lora_set)[:80]


def color(sampler: str) -> str:
    return SERIES[SAMPLER_ORDER.index(sampler)] if sampler in SAMPLER_ORDER else OTHER


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()[:12]


# ── load ────────────────────────────────────────────────────────────────────────────────
def load(dirs: list[Path], hash_audio: bool) -> list[dict]:
    rows = []
    for root in dirs:
        for f in sorted(root.rglob("*.json")):
            if "_analysis" in f.parts:
                continue
            try:
                d = json.loads(f.read_text())
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(d, dict) or not isinstance(d.get("gen"), dict):
                continue                                  # not a studio sidecar
            g, a = d["gen"], d.get("anarchy") or {}
            if a.get("source_file") and not a.get("sampler"):
                a = {**_stored_spec(a["source_file"]), **a}   # precompute.py: the spec it baked
            lab = d.get("label") or {}
            tempo = (d.get("analysis") or {}).get("tempo") or {}
            audio = f.with_suffix("")                      # "<clip>.mp3.json" → "<clip>.mp3"
            deleted = bool(lab.get("deleted")) or "deleted" in f.relative_to(root).parts[:-1]
            score = lab.get("score")
            if score is None and deleted:
                score = 0
            tags = lab.get("tags") or []
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.replace(";", ",").split(",") if t.strip()]
            cond = Path(str(audio) + ".cond.npz")
            cond_hash = None
            if cond.exists():
                try:
                    with np.load(cond) as z:
                        cond_hash = md5(z["cond"].tobytes())
                except (OSError, KeyError, ValueError):
                    pass
            prompt = g.get("prompt") or d.get("prompt") or ""
            loras = [x for x in g.get("loras") or [] if isinstance(x, dict)]
            lora_set = "+".join(sorted(Path(x.get("name") or "?").stem for x in loras))
            if not lora_set and d.get("lora"):            # old sidecar: only the display string
                lora_set = str(d["lora"]).split("×")[0].rstrip("…") + "?"
            strengths = [float(x["strength"]) for x in loras if x.get("strength") is not None]
            r = {
                "name": audio.name, "audio": str(audio), "deleted": deleted,
                "score": None if score is None else float(score), "tags": tags,
                "played": bool(lab.get("played")), "created": g.get("created") or "",
                "prompt": prompt, "seed": g.get("seed"),
                "sampler": a.get("sampler") or ("saved-cond" if a else "text"),
                "fit": a.get("fit") or "", "d": a.get("d"),
                **{k: a.get(k) for k in COND_KNOBS},
                "seconds": g.get("seconds"), "steps": g.get("steps"), "cfg": g.get("cfg"),
                "apg": g.get("apg"), "sigma_max": g.get("sigma_max"),
                "tempo_conf": tempo.get("conf"), "bpm": tempo.get("bpm"),
                "dit": g.get("dit") or "", "decoder": g.get("decoder") or "",
                "lora": g.get("lora") or d.get("lora") or "", "lora_set": lora_set,
                "lora_strength": float(np.mean(strengths)) if strengths else None,
                "noise": g.get("noise") or "",
                "x": (a.get("x") or 1) if a else None,
                "ghosts": ("off" if a.get("g", 1) == 0 else "on") if a.get("sampler", "").startswith("tensor") else "",
                "normmatch": ("n1" if a.get("n") else "n0") if a else "",
                "gen_sampler": g.get("sampler") or "", "mode": g.get("mode") or "",
                "cond_hash": cond_hash,
                "audio_hash": md5(audio.read_bytes()) if hash_audio and audio.exists() else None,
            }
            if r["sampler"] != "text":       # k/m only mean something for their sampler
                if r["sampler"] not in ("cocktail", "cross"):
                    r["k"] = None
                if r["sampler"] != "mutate":
                    r["m"] = None
                if r["sampler"] != "tensor-pca":
                    r["c"] = None
            r["spec"] = a.get("source_spec") or prompt
            r["family"] = family_of(r, a)
            # repeat group: same conditioning + same LoRA set, any seed / length / render settings
            r["group"] = lora_set + "|" + (cond_hash or ("text:" + prompt + "|" + "|".join(
                str(g.get(k)) for k in ("dit", "decoder", "cfg", "apg", "steps", "neg"))))
            rows.append(r)
    rows.sort(key=lambda r: r["created"])
    seen = set()
    for r in rows:          # first look: the earliest render of each idea, before any of its scores existed
        key = (r["lora_set"], r["family"])
        r["first"] = key not in seen
        seen.add(key)
    return rows


def family_of(r: dict, a: dict) -> str:
    """The *idea* a render belongs to: one sampled direction (fit + sampler + d), whatever
    T / L / post-op knobs or seed it was later rendered with. Follow-ups (re-seeds, ladders,
    n1 / r50 / x4 variants) stay in their idea's family, so a liked direction re-rendered
    fifteen times still counts as one thing that was tried."""
    sp = r["sampler"]
    if sp == "text":
        return "text|" + r["prompt"]
    if sp == "breed":
        return "breed|" + md5(r["spec"].encode())[:10]
    if a.get("fit") and a.get("d") is not None:
        fam = f"{a['fit']}|{sp}|d{a['d']}"
        if sp == "mutate":
            fam += "|" + md5(str(a.get("text") or "").encode())[:8]
        return fam
    return "cond|" + str(r.get("cond_hash"))


PAIR_KNOBS = re.compile(r"\s(n1|g0|r\d+|j[\d.]+|s[\d.]+|x\d+)(?=\s|$|\|)")


def paired_effects(rows: list[dict]) -> list[dict]:
    """Knob effect at a fixed seed: each render whose spec carries a post-op knob vs its
    sibling with that one token removed (same idea, same seed). Averaged within an idea
    first, then across ideas, so one heavily probed idea is one vote."""
    by = {(r["spec"].strip(), str(r["seed"])): r["score"] for r in rows}
    eff = defaultdict(lambda: defaultdict(list))
    for r in rows:
        for m in PAIR_KNOBS.finditer(r["spec"]):
            base = (r["spec"][:m.start()] + r["spec"][m.end():]).strip()
            sib = by.get((base, str(r["seed"])))
            if sib is not None:
                eff[m.group(1)][r["family"]].append(r["score"] - sib)
    out = []
    for k, fams in eff.items():
        d = np.array([np.mean(v) for v in fams.values()])
        out.append({"knob": k, "ideas": len(d), "pairs": sum(map(len, fams.values())), "mean": float(d.mean()),
                    "up": int((d > 0).sum()), "down": int((d < 0).sum())})
    return sorted(out, key=lambda e: -e["mean"])


def per_idea(rows: list[dict]) -> list[dict]:
    """One row per idea: conditionings averaged over seeds first, then over the idea."""
    by = defaultdict(list)
    for c in per_conditioning(rows):
        by[c["family"]].append(c)
    return [{**g[0], "score": float(np.mean([c["score"] for c in g])), "n": len(g)} for g in by.values()]


def _stored_spec(source_file: str) -> dict:
    """Knobs of the spec `precompute.py` stored beside a saved conditioning ({} if none)."""
    f = Path(source_file)
    f = f if f.is_absolute() else OUTPUTS / f
    try:
        with np.load(f) as z:
            key = next((k for k in ("source_spec", "spec") if k in z.files), None)
            spec = str(z[key]) if key else ""
    except (OSError, ValueError):
        return {}
    body = spec.split(":", 1)[1].strip() if ":" in spec else ""
    if not body:
        return {}
    if body.startswith("@"):
        return {"sampler": "breed", "source_spec": spec}
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import sampler                                   # numpy-only at import
    try:
        return {**sampler.parse(spec), "source_spec": spec}
    except sampler.SpecError:
        return {}


# ── stats ───────────────────────────────────────────────────────────────────────────────
def ranks(a: np.ndarray) -> np.ndarray:
    r = np.empty(len(a))
    r[a.argsort(kind="mergesort")] = np.arange(len(a))
    for v in np.unique(a):
        m = a == v
        r[m] = r[m].mean()
    return r


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx, ry = ranks(x), ranks(y)
    if rx.std() == 0 or ry.std() == 0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def spearman_ci(x, y, n_boot=2000, seed=0):
    rho = spearman(x, y)
    rng = np.random.default_rng(seed)
    bs = []
    for _ in range(n_boot):
        i = rng.integers(0, len(x), len(x))
        v = spearman(x[i], y[i])
        if np.isfinite(v):
            bs.append(v)
    lo, hi = (np.percentile(bs, [5, 95]) if len(bs) > 50 else (np.nan, np.nan))
    return rho, float(lo), float(hi)


def per_conditioning(rows: list[dict]) -> list[dict]:
    """One row per repeat group, score = group mean (conditioning knobs are constant in it)."""
    by = defaultdict(list)
    for r in rows:
        by[r["group"]].append(r)
    out = []
    for grp in by.values():
        out.append({**grp[0], "score": float(np.mean([r["score"] for r in grp])), "n": len(grp)})
    return out


def knob_table(rows: list[dict], knobs: list[str], min_n: int = 6) -> list[dict]:
    out = []
    for k in knobs:
        pts = [(float(r[k]), r["score"]) for r in rows if r.get(k) is not None]
        if len(pts) < min_n or len({p[0] for p in pts}) < 3:
            continue
        x, y = map(np.array, zip(*pts))
        rho, lo, hi = spearman_ci(x, y)
        qs = np.unique(np.quantile(x, [0, 0.25, 0.5, 0.75, 1]))
        bins = []
        for i in range(len(qs) - 1):
            m = (x >= qs[i]) & ((x < qs[i + 1]) if i < len(qs) - 2 else (x <= qs[i + 1]))
            if m.any():
                bins.append((qs[i], qs[i + 1], int(m.sum()), float(y[m].mean())))
        out.append({"knob": k, "n": len(x), "lo": x.min(), "hi": x.max(), "rho": rho,
                    "ci": (lo, hi), "signal": np.isfinite(lo) and (lo > 0 or hi < 0),
                    "bins": bins, "x": x, "y": y})
    return out


def noise_floor(rows: list[dict]) -> dict:
    by = defaultdict(list)
    for r in rows:
        by[r["group"]].append(r["score"])
    reps = {g: s for g, s in by.items() if len(s) >= 2}
    total_sd = float(np.std([r["score"] for r in rows], ddof=1)) if len(rows) > 2 else float("nan")
    if not reps:
        return {"groups": 0, "total_sd": total_sd}
    ss = sum(((np.array(s) - np.mean(s)) ** 2).sum() for s in reps.values())
    dof = sum(len(s) - 1 for s in reps.values())
    within = float(np.sqrt(ss / dof))
    return {"groups": len(reps), "renders": sum(map(len, reps.values())), "within_sd": within,
            "total_sd": total_sd, "share": min(1.0, (within / total_sd) ** 2) if total_sd else np.nan,
            "reps": reps}


# ── plots ───────────────────────────────────────────────────────────────────────────────
def style(ax, xlabel="", ylabel=""):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(True, color=GRID, lw=0.6, zorder=0)
    ax.set_xlabel(xlabel, color=INK2, fontsize=9)
    ax.set_ylabel(ylabel, color=INK2, fontsize=9)


def jitter(y, rng, w=0.18):
    return np.asarray(y) + rng.uniform(-w, w, len(y))


def legend(fig, samplers):
    hs = [plt.Line2D([], [], ls="", marker="o", ms=7, mfc=color(s), mec="white", label=s)
          for s in samplers]
    fig.legend(handles=hs, loc="upper right", frameon=False, fontsize=8, labelcolor=INK2)


def plot_knobs(cond_rows, render_rows, out: Path, samplers):
    tables = [(t, cond_rows) for t in knob_table(cond_rows, COND_KNOBS)] + \
             [(t, render_rows) for t in knob_table(render_rows, RENDER_KNOBS)]
    if not tables:
        return None
    rng = np.random.default_rng(1)
    n = len(tables)
    cols = min(3, n)
    fig, axs = plt.subplots((n + cols - 1) // cols, cols, figsize=(4.2 * cols, 3.2 * ((n + cols - 1) // cols)),
                            squeeze=False, facecolor=SURFACE)
    for ax, (t, rows) in zip(axs.flat, tables):
        pts = [r for r in rows if r.get(t["knob"]) is not None]
        ax.scatter([float(r[t["knob"]]) for r in pts], jitter([r["score"] for r in pts], rng),
                   s=34, c=[color(r["sampler"]) for r in pts], edgecolors="white", lw=0.8, zorder=3)
        bx = [(b[0] + b[1]) / 2 for b in t["bins"]]
        ax.plot(bx, [b[3] for b in t["bins"]], color=INK, lw=2, zorder=4)
        flag = "  ◆ signal" if t["signal"] else ""
        ax.set_title(f"{t['knob']}   ρ {t['rho']:+.2f} [{t['ci'][0]:+.2f}, {t['ci'][1]:+.2f}]{flag}",
                     fontsize=9, color=INK, loc="left")
        style(ax, t["knob"], "score")
    for ax in list(axs.flat)[n:]:
        ax.axis("off")
    fig.suptitle("Score vs knob (dots jittered vertically; line = mean per quartile bin; "
                 "T/L/distance/c/k/m use one row per conditioning)", fontsize=9, color=INK2, x=0.01, ha="left")
    legend(fig, samplers)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    return tables


def plot_samplers(rows, out: Path, samplers, keep):
    rng = np.random.default_rng(2)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 3.6), facecolor=SURFACE,
                                 gridspec_kw={"width_ratios": [1, 1.4]})
    sc = Counter(int(round(r["score"])) for r in rows)
    xs = list(range(0, 11))
    a1.bar(xs, [sc.get(x, 0) for x in xs], color=SERIES[0], width=0.8, zorder=3)
    a1.axvline(keep - 0.5, color=INK2, ls="--", lw=1)
    a1.text(keep - 0.4, a1.get_ylim()[1] * 0.92, f"keeper ≥ {keep}", fontsize=8, color=INK2)
    a1.set_title("Score histogram (all labeled renders)", fontsize=9, color=INK, loc="left")
    style(a1, "score", "renders")
    for i, s in enumerate(samplers):
        ys = [r["score"] for r in rows if r["sampler"] == s]
        a2.scatter(jitter([i] * len(ys), rng, 0.2), ys, s=34, c=color(s), edgecolors="white", lw=0.8, zorder=3)
        a2.plot([i - 0.3, i + 0.3], [np.mean(ys)] * 2, color=INK, lw=2, zorder=4)
        a2.text(i, -1.1, f"n={len(ys)}", ha="center", fontsize=8, color=INK2)
    a2.set_xticks(range(len(samplers)), samplers)
    a2.set_ylim(-1.6, 10.5)
    a2.set_title("Score per sampler (bar = mean)", fontsize=9, color=INK, loc="left")
    style(a2, "", "score")
    fig.tight_layout()
    fig.savefig(out, dpi=130, facecolor=SURFACE)
    plt.close(fig)


def plot_heat(cond_rows, out: Path, samplers, min_n=12):
    groups = [("all", [r for r in cond_rows if r.get("T") is not None and r.get("L") is not None])]
    groups += [(s, [r for r in groups[0][1] if r["sampler"] == s]) for s in samplers]
    groups = [g for g in groups if len(g[1]) >= min_n]
    if len(groups) > 1 and len(groups[0][1]) == len(groups[1][1]):
        groups = groups[1:]                              # one sampler only: skip the duplicate "all"
    if not groups:
        return False
    fig, axs = plt.subplots(1, len(groups), figsize=(4.4 * len(groups), 3.6), squeeze=False, facecolor=SURFACE)
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list(
        "blue", ["#cde2fb", "#86b6ef", "#3987e5", "#256abf", "#104281", "#0d366b"])
    for ax, (name, rs) in zip(axs.flat, groups):
        T = np.array([float(r["T"]) for r in rs])
        L = np.array([float(r["L"]) for r in rs])
        y = np.array([r["score"] for r in rs])
        te = np.unique(np.quantile(T, np.linspace(0, 1, 5)))
        le = np.unique(np.quantile(L, np.linspace(0, 1, 5)))
        ti = np.clip(np.searchsorted(te, T, side="right") - 1, 0, len(te) - 2)
        li = np.clip(np.searchsorted(le, L, side="right") - 1, 0, len(le) - 2)
        M = np.full((len(le) - 1, len(te) - 1), np.nan)
        N = np.zeros_like(M, dtype=int)
        for a, b, s in zip(li, ti, y):
            N[a, b] += 1
        for a in range(M.shape[0]):
            for b in range(M.shape[1]):
                m = (li == a) & (ti == b)
                if m.any():
                    M[a, b] = y[m].mean()
        im = ax.imshow(M, origin="lower", cmap=cmap, vmin=0, vmax=max(5, np.nanmax(M)), aspect="auto")
        for a in range(M.shape[0]):
            for b in range(M.shape[1]):
                if N[a, b]:
                    ax.text(b, a, f"{M[a, b]:.1f}\nn={N[a, b]}", ha="center", va="center", fontsize=7,
                            color="white" if M[a, b] > 0.55 * max(5, np.nanmax(M)) else INK)
        ax.set_xticks(np.arange(len(te) - 1), [f"{te[i]:.2g}–{te[i + 1]:.2g}" for i in range(len(te) - 1)])
        ax.set_yticks(np.arange(len(le) - 1), [f"{le[i]:.0f}–{le[i + 1]:.0f}" for i in range(len(le) - 1)])
        ax.set_title(f"mean score, {name} (n={len(rs)})", fontsize=9, color=INK, loc="left")
        style(ax, "T (quartile bins)", "L (quartile bins)")
        ax.grid(False)
        fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    fig.savefig(out, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    return True


def plot_repeats(nf, rows, out: Path):
    if not nf.get("groups"):
        return False
    first = {}
    for r in rows:
        first.setdefault(r["group"], r)
    reps = sorted(nf["reps"].items(), key=lambda kv: np.mean(kv[1]))
    fig, ax = plt.subplots(figsize=(8, 0.45 * len(reps) + 1.4), facecolor=SURFACE)
    for i, (g, s) in enumerate(reps):
        r = first[g]
        ax.plot([min(s), max(s)], [i, i], color=GRID, lw=2, zorder=2)
        ax.scatter(s, [i] * len(s), s=40, c=color(r["sampler"]), edgecolors="white", lw=0.8, zorder=3)
        lab = r["prompt"].replace("anarchy: ", "")[:48]
        ax.text(10.4, i, f"{lab}  (n={len(s)})", va="center", fontsize=7, color=INK2)
    ax.set_yticks([])
    ax.set_xlim(-0.5, 10.3)
    ax.set_title(f"Repeat groups: same conditioning, other seed/length. Within-group SD "
                 f"{nf['within_sd']:.2f} vs total {nf['total_sd']:.2f}", fontsize=9, color=INK, loc="left")
    style(ax, "score", "")
    fig.tight_layout()
    fig.savefig(out, dpi=130, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    return True


def plot_timeline(rows, out: Path, samplers):
    rng = np.random.default_rng(3)
    y = np.array([r["score"] for r in rows])
    x = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(11, 3.2), facecolor=SURFACE)
    ax.scatter(x, jitter(y, rng, 0.12), s=30, c=[color(r["sampler"]) for r in rows],
               edgecolors="white", lw=0.8, zorder=3)
    w = max(3, len(rows) // 10)
    if len(rows) > w:
        ax.plot(x[w - 1:], np.convolve(y, np.ones(w) / w, "valid"), color=INK, lw=2, zorder=4,
                label=f"rolling mean ({w})")
        ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="upper left")
    ax.set_title("Score in render order (drift in taste, or what was being explored)",
                 fontsize=9, color=INK, loc="left")
    style(ax, "render #", "score")
    legend(fig, samplers)
    fig.tight_layout()
    fig.savefig(out, dpi=130, facecolor=SURFACE)
    plt.close(fig)


# ── report ──────────────────────────────────────────────────────────────────────────────
def link(r):
    return f"[{r['name'][:70]}](file://{urllib.parse.quote(r['audio'])})"


def fmt(v):
    return f"{v:.3g}" if isinstance(v, float) else str(v)


def report(rows_all, args, outdir: Path, dataset: str) -> str:
    lab = [r for r in rows_all if r["score"] is not None]
    L = []
    w = L.append
    w(f"# Dataset `{dataset}`: {', '.join(str(d) for d in args.dirs)}\n")
    if dataset != BASE:
        st = sorted({fmt(r["lora_strength"]) for r in rows_all})
        w(f"LoRA renders only (`{rows_all[0]['lora_set']}`, strength {', '.join(st)}). Read nothing here "
          f"against the `{BASE}` dataset: the LoRA changes the model, which moves scores more than any "
          "conditioning knob.\n")
    unl = len(rows_all) - len(lab)
    w(f"**{len(rows_all)} renders**, {len(lab)} labeled ({sum(r['deleted'] for r in lab)} trashed = 0), "
      f"{unl} unlabeled. Keeper threshold ≥ {args.keep}.\n")
    if not lab:
        return "\n".join(L)
    s = np.array([r["score"] for r in lab])
    w(f"Scores: mean {s.mean():.2f}, median {np.median(s):g}, P90 {np.percentile(s, 90):g}, max {s.max():g}, "
      f"keepers {int((s >= args.keep).sum())} ({(s >= args.keep).mean():.0%}). "
      f"Histogram: {dict(sorted(Counter(int(round(x)) for x in s).items()))}\n")

    samplers = [x for x in SAMPLER_ORDER + sorted({r['sampler'] for r in lab}) if any(r["sampler"] == x for r in lab)]
    samplers = list(dict.fromkeys(samplers))
    first, ideas = [r for r in lab if r["first"]], per_idea(lab)
    w("## Design-independent view (one vote per idea)\n")
    w(f"An *idea* = one sampled direction (fit + sampler + d), whatever T / L / knobs / seeds it was later "
      f"rendered with. {len(ideas)} ideas, {len(lab) - len(first)} of {len(lab)} renders "
      f"({(len(lab) - len(first)) / len(lab):.0%}) are follow-ups, picked because of earlier scores. "
      "**First look** = each idea's first render only: chosen before any of its scores existed, so "
      "unbiased by our choices (one seed: noisy). **Idea mean** = all its renders, averaged per idea, "
      "one vote per idea (more precise; slightly flattering, since only good ideas got follow-ups). "
      "Read sampler and knob questions here; the per-render tables below are weighted by how often "
      "we chose to render something.\n")
    w("| sampler | ideas | first look | idea mean | renders |\n|---|---|---|---|---|")
    for sp in samplers:
        f1 = [r["score"] for r in first if r["sampler"] == sp]
        im = [r["score"] for r in ideas if r["sampler"] == sp]
        w(f"| {sp} | {len(im)} | {np.mean(f1):.2f} | {np.mean(im):.2f} | {sum(r['sampler'] == sp for r in lab)} |")
    ft = knob_table(first, COND_KNOBS)
    if ft:
        w("\nKnobs on first looks only (Spearman ρ, bootstrap 90 % CI; ◆ = CI excludes 0):\n\n"
          "| knob | n | ρ | 90 % CI | quartile bins |\n|---|---|---|---|---|")
        subs = [("all", ft)] + [(f"within {sp}", knob_table([r for r in first if r["sampler"] == sp], COND_KNOBS))
                                for sp in samplers if sum(r["sampler"] == sp for r in first) >= 15]
        for kind, ts in subs:
            for t in ts:
                bins = " · ".join(f"{b[0]:.3g}–{b[1]:.3g}: {b[3]:.1f} ({b[2]})" for b in t["bins"])
                w(f"| {t['knob']}{' ◆' if t['signal'] else ''} ({kind}) | {t['n']} | {t['rho']:+.2f} | "
                  f"{t['ci'][0]:+.2f}…{t['ci'][1]:+.2f} | {bins} |")
    pe = paired_effects(lab)
    if pe:
        w("\nPaired knob effects (same idea, same seed, only that knob differs; averaged per idea first). "
          "Caveat: a sibling chosen *because* it scored high carries upward luck, which makes the knob "
          "look worse than it is.\n\n| knob | ideas | pairs | mean Δ score | ideas up / down |\n|---|---|---|---|---|")
        for e in pe:
            w(f"| `{e['knob']}` | {e['ideas']} | {e['pairs']} | {e['mean']:+.2f} | {e['up']} / {e['down']} |")

    w("\n## Per sampler (per render)\n\n| sampler | n | mean | P90 | max | keepers |\n|---|---|---|---|---|---|")
    for sp in samplers:
        y = np.array([r["score"] for r in lab if r["sampler"] == sp])
        w(f"| {sp} | {len(y)} | {y.mean():.2f} | {np.percentile(y, 90):g} | {y.max():g} | {int((y >= args.keep).sum())} |")

    nf = noise_floor(lab)
    w("\n## Noise floor (same conditioning, other seed/length)\n")
    if nf.get("groups"):
        w(f"{nf['groups']} repeat groups ({nf['renders']} renders). Within-group SD **{nf['within_sd']:.2f}** "
          f"vs total SD {nf['total_sd']:.2f}: seed + label noise ≈ **{nf['share']:.0%}** of score variance. "
          f"A mean over n seeds has SE ≈ {nf['within_sd']:.2f}/√n "
          f"(n=3 → ±{nf['within_sd'] / np.sqrt(3):.2f}).")
    else:
        w("No conditioning was rendered more than once: re-render a few directions with new seeds to measure it.")
    by_audio = defaultdict(list)
    for r in lab:
        if r["audio_hash"]:
            by_audio[r["audio_hash"]].append(r["score"])
    dup = {h: v for h, v in by_audio.items() if len(v) > 1}
    if dup:
        w(f"\nIdentical audio labeled more than once: {len(dup)} file(s), scores {list(dup.values())} "
          "(a trashed duplicate counts as 0, so this mixes label noise with dedupe).")

    cond_rows = per_conditioning(lab)
    w(f"\n## Knobs ({len(cond_rows)} unique conditionings)\n")
    w("Spearman ρ with bootstrap 90 % CI; ◆ = CI excludes 0. Bins are quartiles of the knob "
      "(mean score, n).\n\n| knob | n | range | ρ | 90 % CI | quartile bins |\n|---|---|---|---|---|---|")
    tables = {"conditioning": knob_table(cond_rows, COND_KNOBS), "render": knob_table(lab, RENDER_KNOBS)}
    per_sampler = {}
    for sp in samplers:
        sub = [r for r in cond_rows if r["sampler"] == sp]
        if len(sub) >= 15 and len(samplers) > 1:
            per_sampler[sp] = knob_table(sub, COND_KNOBS)
    for kind, ts in list(tables.items()) + [(f"within {k}", v) for k, v in per_sampler.items()]:
        for t in ts:
            bins = " · ".join(f"{b[0]:.3g}–{b[1]:.3g}: {b[3]:.1f} ({b[2]})" for b in t["bins"])
            w(f"| {t['knob']}{' ◆' if t['signal'] else ''} ({kind}) | {t['n']} | {t['lo']:.3g}–{t['hi']:.3g} | "
              f"{t['rho']:+.2f} | {t['ci'][0]:+.2f}…{t['ci'][1]:+.2f} | {bins} |")

    cats = []
    for k in CAT_KNOBS:
        lv = defaultdict(list)
        for r in lab:
            lv[fmt(r[k])].append(r["score"])
        if len(lv) >= 2:
            cats.append(f"- **{k}**: " + ", ".join(f"`{v or '∅'}` {np.mean(x):.2f} (n={len(x)})"
                                                   for v, x in sorted(lv.items(), key=lambda kv: -np.mean(kv[1]))))
    constant = [k for k in CAT_KNOBS + RENDER_KNOBS[:5] if len({fmt(r[k]) for r in lab}) == 1]
    w("\n## Categorical settings\n")
    w("\n".join(cats) if cats else "No categorical setting varied.")
    if constant:
        w("\nHeld constant (never tested): " + ", ".join(f"{k}={fmt(lab[0][k]) or '∅'}" for k in constant))

    tags = Counter(t for r in lab for t in r["tags"])
    w("\n## Tags\n")
    if tags:
        w("\n".join(f"- `{t}` n={n}, mean {np.mean([r['score'] for r in lab if t in r['tags']]):.2f}"
                    for t, n in tags.most_common()))
    else:
        w("No tags yet.")

    w(f"\n## Top {args.top} renders\n\n| score | sampler | T | L | seed | s | clip |\n|---|---|---|---|---|---|---|")
    for r in sorted(lab, key=lambda r: -r["score"])[:args.top]:
        w(f"| {r['score']:g} | {r['sampler']} | {fmt(r['T'])} | {fmt(r['L'])} | {r['seed']} | {fmt(r['seconds'])} | {link(r)} |")

    gsize = Counter(r["group"] for r in lab)
    cand = [r for r in sorted(cond_rows, key=lambda r: -r["score"]) if gsize[r["group"]] == 1][:5]
    if cand:
        w("\n## Re-seed candidates (best conditionings rendered only once)\n\n"
          "Paste the prompt, change only the seed, render 2–3 times: separates a good direction from a lucky seed.\n")
        w("\n".join(f"- {r['score']:g} · `{r['prompt']}` (seed {r['seed']})" for r in cand))

    figs = [p.name for p in sorted(outdir.glob("*.png"))]
    w("\n## Plots\n\n" + "\n".join(f"- [{p}](file://{urllib.parse.quote(str(outdir / p))})" for p in figs))
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dirs", nargs="+", type=Path)
    ap.add_argument("--keep", type=float, default=7, help="keeper threshold (score ≥)")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--out", type=Path, help="default: <first dir>/_analysis")
    ap.add_argument("--no-audio-hash", action="store_true", help="skip md5 of audio (duplicate detection)")
    args = ap.parse_args()
    args.dirs = [d.resolve() for d in args.dirs]
    outdir = (args.out or args.dirs[0] / "_analysis").resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    rows = load(args.dirs, hash_audio=not args.no_audio_hash)
    if not rows:
        raise SystemExit(f"no studio sidecars under {args.dirs}")
    with open(outdir / "rows.csv", "w", newline="") as fh:
        cols = [k for k in rows[0] if k != "tags"] + ["tags"]
        wr = csv.DictWriter(fh, fieldnames=cols)
        wr.writeheader()
        for r in rows:
            wr.writerow({**r, "tags": ";".join(r["tags"])})

    for p in outdir.rglob("*.png"):
        p.unlink()                                       # stale plots from an earlier run / dataset
    by_ds = defaultdict(list)
    for r in rows:
        by_ds[dataset_name(r["lora_set"])].append(r)
    names = sorted(by_ds, key=lambda n: (n != BASE, n))

    over = [f"# Label analysis: {', '.join(str(d) for d in args.dirs)}\n",
            "**One dataset per LoRA set.** Loading a LoRA changes the model itself and moves scores far "
            "more than any conditioning knob (prompt, T, L, sampler), so renders with a LoRA are never "
            "pooled with base renders or with another LoRA: each dataset below gets its own noise floor, "
            "knobs and plots. LoRA strength is a knob inside its LoRA's dataset. This table is the only "
            "cross-dataset view, and its rows differ in prompts and seeds too.\n",
            "| dataset | renders | labeled | mean | P90 | max | keepers | folder |",
            "|---|---|---|---|---|---|---|---|"]
    texts = []
    for n in names:
        rs, sub = by_ds[n], outdir / n
        sub.mkdir(exist_ok=True)
        lab = [r for r in rs if r["score"] is not None]
        if lab:
            samplers = [s for s in SAMPLER_ORDER if any(r["sampler"] == s for r in lab)] + \
                       sorted({r["sampler"] for r in lab} - set(SAMPLER_ORDER))
            cond_rows = per_conditioning(lab)
            plot_knobs(cond_rows, lab, sub / "knobs.png", samplers)
            plot_samplers(lab, sub / "samplers.png", samplers, args.keep)
            plot_heat(cond_rows, sub / "heat_T_L.png", samplers)
            plot_repeats(noise_floor(lab), lab, sub / "repeats.png")
            plot_timeline(lab, sub / "timeline.png", samplers)
            s = np.array([r["score"] for r in lab])
            stats = f"{s.mean():.2f} | {np.percentile(s, 90):g} | {s.max():g} | {int((s >= args.keep).sum())}"
        else:
            stats = "– | – | – | –"
        text = report(rs, args, sub, n)
        (sub / "report.md").write_text(text + "\n")
        texts.append(text)
        over.append(f"| `{n}` | {len(rs)} | {len(lab)} | {stats} | "
                    f"[{n}/](file://{urllib.parse.quote(str(sub))}/) |")
    full = "\n".join(over) + "\n\n" + "\n\n---\n\n".join(texts)
    (outdir / "report.md").write_text(full + "\n")
    print(full)
    print(f"\n→ {outdir}")


if __name__ == "__main__":
    main()
