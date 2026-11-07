"""What was this render conditioned on? One answer for every caller (composer spec 4.5).

A studio render has one conditioning; a render whose segments box carried `"cond"` (a composer
take, a take of a take, a chained-window take, an Arranger send with per-section prompts) has
several anchors blended per latent frame by weight curves (condmix.py). Every place that reuses a
render asks this module, never the sidecar directly:

| caller | what it gets |
|---|---|
| a piece on the composer timeline (compose.piece_anchors → cond_curves) | `of_render`: every anchor + its weight curve on the render's own timeline; cond_curves samples them at the source times the piece covers (`weights_at` is the same rule), so a cut, shift or beat-stretch of a take carries its curves along |
| one anchor: a piece override, a keypoint, the project's global anchor, the anchor editor's "a render's conditioning", the variant `anchors: "first"` (`expand` / `dominant`) | the render's **dominant anchor**: the one with the largest mean weight over the range used (whole render unless a range is given). Never a token average: the DiT reads the 256 tokens as an unordered bag, so averaging bags token-by-token is meaningless |
| similarity E3 (embed.py) | `mean_weights`: anchors with their mean weight, for a weight-averaged pooled vector |
| the studio's restore / regenerate (segments.py restore passthrough) | `self_contained`: the recorded curves with file-backed anchors pointed at the render's own bank |

**Render kinds** (`of_render`): `bag` = an anarchy render (`<audio>.cond.npz`, one anchor
`{"id": rid}`); `multi` = `gen.segments.cond` in the sidecar; `prompt` = a text render
(`gen.prompt`; a composite's prompt is only the composer's job label and is never used); `none`
= imports and anything else.

**The bank** `<audio>.conds.npz` makes a multi render self-contained: the exact tensors the DiT
consumed for every *file-backed* anchor, so reusing the render never depends on the renders it
was made from still existing. Written by the studio when a render's sidecar is saved (`install`
wraps persist.save; condmix keeps the resolved tensors of the last few generations), by
compose.submit_windows for a stitched take, and lazily for older takes the first time one is
read while its sources still resolve. Keys:

    bags     (M, 256, 768) float16   unique tensors (deduped by content)
    index    (N,) int                anchor k → row of bags, -1 = not stored
    anchors  json  the anchors as the box named them        curves  json  weight curves, render time
    sha      json  per anchor content hash (dedupe key)      labels  json  readable name per anchor
    v        1

Plain text prompts are never stored (index -1): the text is self-describing, re-encodes
deterministically and stays correct on another DiT. Everything else is: `id` / `npz` bags,
`anarchy: …` specs (sampled from fits on disk that can change), spliced bags. Read back, a
stored anchor becomes `{"bank": <render id>, "i": k, "sha": …}` (condmix resolves it through
`tokens`); takes of takes chain naturally, each take holding its own copy. ~80 KB per stored
anchor (fp16, compressed; measured: a 2-anchor take = 158 KB; a take usually has 2-5).

Resolution falls back to the trash: a side file (`.cond.npz`, `.conds.npz`) whose render was
trashed is read from `<folder>/deleted/` (library.delete_render moves both along), so trashing a
source never breaks a take or a project that used it.

Traps: a bank tensor is fixed to the DiT it was rendered on (as every anarchy bag is); a plain
prompt is re-encoded for whichever DiT renders. Content dedupe (`key`) merges a bank anchor with
the same bag referenced by id elsewhere in a project, so a promoted base plus the same anarchy
render layered again stays one anchor.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

SA3LAB = Path(__file__).resolve().parent
AI_AUDIO = SA3LAB.parent
OUTPUTS = (AI_AUDIO / "outputs").resolve()
BANK_EXT = ".conds.npz"
BAG_EXT = ".cond.npz"
FPS = 44100 / 4096
_cache: dict = {}
_banks: dict = {}                    # condmix: id(cond) → (cond, anchors, curves, [np bags]) of recent renders


# ── files ────────────────────────────────────────────────────────────────────────────────────
def _audio(rid_or_path) -> Path | None:
    """A render id (path under outputs/) or path → its resolved audio path (may be gone)."""
    p = Path(str(rid_or_path))
    p = (p if p.is_absolute() else OUTPUTS / str(rid_or_path).lstrip("/")).resolve()
    return p if OUTPUTS in p.parents else None


def side(rid_or_path, ext: str) -> Path | None:
    """`<audio><ext>` beside the render, or its trashed twin in `<folder>/deleted/`."""
    p = _audio(rid_or_path)
    if p is None:
        return None
    for q in (p.with_name(p.name + ext), p.parent / "deleted" / (p.name + ext)):
        if q.is_file():
            return q
    return None


def rid_of(path) -> str | None:
    try:
        return str(Path(path).resolve().relative_to(OUTPUTS))
    except (ValueError, OSError):
        return None


def _memo(kind: str, path: Path, fn):
    st = path.stat()
    k = (kind, str(path), st.st_size, st.st_mtime)
    if k not in _cache:
        if len(_cache) > 256:
            _cache.clear()
        _cache[k] = fn()
    return _cache[k]


def _sidecar(p: Path) -> dict:
    q = side(p, ".json")
    try:
        return _memo("json", q, lambda: json.loads(q.read_text())) if q else {}
    except Exception:
        return {}


def _sha(a: np.ndarray) -> str:
    return hashlib.sha1(np.ascontiguousarray(a.astype(np.float16)).tobytes()).hexdigest()[:16]


def plain_prompt(a: dict) -> bool:
    """A text prompt that re-encodes deterministically (not an `anarchy:` spec, not spliced)."""
    return ("prompt" in a and not a.get("splice")
            and not str(a["prompt"]).strip().lower().startswith("anarchy:"))


# ── the bank ─────────────────────────────────────────────────────────────────────────────────
def read_bank(p) -> dict | None:
    q = side(p, BANK_EXT)
    if q is None:
        return None

    def load():
        z = np.load(q)
        return {"bags": z["bags"], "index": z["index"].astype(int), "anchors": json.loads(str(z["anchors"])),
                "curves": json.loads(str(z["curves"])), "sha": json.loads(str(z["sha"])),
                "labels": json.loads(str(z["labels"]))}
    try:
        return _memo("bank", q, load)
    except Exception as e:  # noqa: BLE001  (a broken bank reads as none: the sidecar still answers)
        print(f"[condsrc] unreadable bank {q}: {e}", flush=True)
        return None


def write_bank(audio, anchors: list, curves: list, bags: list) -> Path | None:
    """Store every non-plain-prompt anchor's tensor (bags[k]: (256, 768) or None = unknown) beside
    `audio`. No file when nothing is storable. Atomic; returns the path."""
    audio = Path(audio)
    rows, rsha, index, shas = [], {}, [], []
    for a, b in zip(anchors, bags):
        if b is None or plain_prompt(a):
            index.append(-1)
            shas.append(key(a))
            continue
        b = np.asarray(b, np.float16).reshape(256, 768)
        s = _sha(b)
        if s not in rsha:
            rsha[s] = len(rows)
            rows.append(b)
        index.append(rsha[s])
        shas.append(s)
    if not rows:
        return None
    out = audio.with_name(audio.name + BANK_EXT)
    tmp = audio.with_name(audio.name + ".conds.tmp")
    with open(tmp, "wb") as f:
        np.savez_compressed(f, v=np.array(1), bags=np.stack(rows), index=np.array(index, np.int32),
                            anchors=np.array(json.dumps(anchors)), curves=np.array(json.dumps(curves)),
                            sha=np.array(json.dumps(shas)), labels=np.array(json.dumps([label(a) for a in anchors])))
    tmp.replace(out)
    return out


def copy_bank(src_audio, dst_audio, curves: list) -> Path | None:
    """dst gets src's stored tensors with new curves (a stitched windows take: the same anchors
    as its first window, over the whole timeline)."""
    b = read_bank(src_audio)
    if b is None:
        return None
    bags = [b["bags"][i] if i >= 0 else None for i in b["index"]]
    return write_bank(dst_audio, b["anchors"], curves, bags)


def _backfill(p: Path, cond: dict) -> dict | None:
    """An older multi render without a bank: store what numpy can load now (id / npz / bank
    anchors that still resolve; `anarchy:` specs and splices would need the MLX encoder and stay
    descriptors). Best effort, once per render."""
    if any(not plain_prompt(a) for a in cond.get("anchors") or []):
        bags = []
        for a in cond["anchors"]:
            try:
                bags.append(None if "prompt" in a or a.get("splice") else tokens(a))
            except Exception:  # noqa: BLE001  (a source that is gone stays a descriptor)
                bags.append(None)
        if any(x is not None for x in bags):
            try:
                write_bank(p, cond["anchors"], cond.get("curves") or [], bags)
            except OSError as e:
                print(f"[condsrc] backfill {p.name}: {e}", flush=True)
    return read_bank(p)


# ── what a render was conditioned on ─────────────────────────────────────────────────────────
def of_render(path) -> dict:
    """{"kind": bag|multi|prompt|none, "anchors": [anchor], "curves": [curve | None]} over the
    render's own timeline (curve None = weight 1 throughout)."""
    p = _audio(path)
    if p is None:
        return {"kind": "none", "anchors": [], "curves": []}
    if side(p, BAG_EXT):
        return {"kind": "bag", "anchors": [{"id": rid_of(p)}], "curves": [None]}
    g = _sidecar(p).get("gen") or {}
    c = (g.get("segments") or {}).get("cond") or {}
    if c.get("anchors"):
        b = read_bank(p) or _backfill(p, c)
        src = b or c
        anchors, curves = list(src["anchors"]), list(src.get("curves") or [])
        curves += [[[0.0, 1.0 if not curves and k == 0 else 0.0]] for k in range(len(curves), len(anchors))]
        if b:
            rid = rid_of(p)
            anchors = [a if (i < 0 or plain_prompt(a)) else {"bank": rid, "i": k, "sha": b["sha"][k]}
                       for k, (a, i) in enumerate(zip(anchors, b["index"]))]
        return {"kind": "multi", "anchors": anchors, "curves": curves[:len(anchors)]}
    if g.get("composite"):
        return {"kind": "none", "anchors": [], "curves": []}
    pr = g.get("prompt") or _sidecar(p).get("prompt")
    if pr and not _sidecar(p).get("import"):
        return {"kind": "prompt", "anchors": [{"prompt": pr}], "curves": [None]}
    return {"kind": "none", "anchors": [], "curves": []}


def _sample(keys, t) -> np.ndarray:
    """segments' keyframe rule at times t (linear, flat outside, repeated t = a jump)."""
    kt = np.array([float(k[0]) for k in keys])
    ku = np.array([float(k[1]) for k in keys])
    i = np.searchsorted(kt, t, side="right")
    lo, hi = np.clip(i - 1, 0, len(kt) - 1), np.clip(i, 0, len(kt) - 1)
    span = kt[hi] - kt[lo]
    w = np.where(span > 0, (t - kt[lo]) / np.where(span > 0, span, 1.0), 0.0)
    u = ku[lo] + w * (ku[hi] - ku[lo])
    return np.where(i == 0, ku[0], np.where(i >= len(kt), ku[-1], u))


def weights_at(path, times) -> tuple[list, np.ndarray]:
    """The resolver: any render + times on ITS timeline → (anchors, W [N, len(times)]), each
    column summing to 1 (all-zero columns go to anchor 0, condmix's rule). How the caller maps
    its own timeline onto the render's (cut, shift, beat-stretch) is just which times it asks for."""
    s = of_render(path)
    t = np.atleast_1d(np.asarray(times, np.float64))
    if not s["anchors"]:
        return [], np.zeros((0, len(t)))
    W = np.stack([np.ones(len(t)) if c is None else np.clip(_sample(c, t), 0, None) for c in s["curves"]])
    tot = W.sum(0)
    W[0, tot <= 0] = 1.0
    return s["anchors"], W / np.maximum(W.sum(0), 1e-9)


def _span(p: Path, s0, s1) -> np.ndarray:
    if s1 is None:
        g = _sidecar(p).get("gen") or {}
        s1 = float((g.get("composite") or {}).get("length") or g.get("seconds") or 0) or None
        if s1 is None:
            c = ((g.get("segments") or {}).get("cond") or {}).get("curves") or []
            s1 = max([float(k[0]) for cv in c for k in cv] + [1.0])
    s0 = float(s0 or 0.0)
    n = max(1, int(round((float(s1) - s0) * FPS)))
    return s0 + (np.arange(n) + 0.5) * (float(s1) - s0) / n


def mean_weights(path, s0=None, s1=None) -> tuple[list, np.ndarray]:
    """(anchors, mean weight of each over [s0, s1] of the render; whole render by default)."""
    p = _audio(path)
    if p is None:
        return [], np.zeros(0)
    anchors, W = weights_at(p, _span(p, s0, s1))
    return anchors, (W.mean(1) if len(anchors) else np.zeros(0))


def dominant(path, s0=None, s1=None) -> dict | None:
    """The render's main anchor over [s0, s1]: the largest mean weight (first on ties)."""
    anchors, w = mean_weights(path, s0, s1)
    return anchors[int(np.argmax(w))] if anchors else None


def expand(a: dict | None, _depth: int = 0) -> dict | None:
    """An anchor naming a render as ONE conditioning, made concrete: `{"id": rid}` of a multi
    render → its dominant anchor, of a text render → its prompt; an anarchy render (or anything
    else) is returned as is. Extra keys (splice) ride along for a bag."""
    if not isinstance(a, dict) or "id" not in a or _depth > 4 or side(a["id"], BAG_EXT):
        return a
    s = of_render(a["id"])
    if s["kind"] not in ("multi", "prompt"):
        return a
    d = dominant(a["id"])
    return expand(d, _depth + 1) if d else a


def tokens(a: dict) -> np.ndarray:
    """A file-backed anchor's (256, 768) float16 tensor (bank row, or an anarchy `.cond.npz` by
    id or path). Prompts are encoded by condmix (MLX), not here."""
    if "bank" in a:
        b = read_bank(a["bank"])
        if b is None:
            raise ValueError(f"no conditioning bank for {a['bank']!r}")
        i = b["index"][int(a["i"])]
        if i < 0:
            raise ValueError(f"anchor {a['i']} of {a['bank']!r} is not stored (a text prompt)")
        return b["bags"][i]
    if "id" in a:
        q = side(a["id"], BAG_EXT)
    else:
        q = Path(str(a["npz"]))
        q = (q if q.is_absolute() else OUTPUTS / q).resolve()
        q = q if AI_AUDIO.resolve() in q.parents and q.is_file() else None
    if q is None:
        raise ValueError(f"no conditioning file for {a}")
    c = _memo("bag", q, lambda: np.load(q)["cond"])
    if c.shape != (256, 768):
        raise ValueError(f"{q} holds a {c.shape} array, want (256, 768)")
    return c


def key(a: dict) -> str:
    """Dedupe key: the content hash for a bank anchor or a bag that loads; the JSON otherwise."""
    if a.get("sha") and not a.get("splice"):
        return a["sha"]
    if not a.get("splice") and ("id" in a or "npz" in a):
        try:
            return _sha(tokens(a))
        except Exception:  # noqa: BLE001
            pass
    return json.dumps({k: v for k, v in a.items() if k != "label"}, sort_keys=True)


def label(a: dict) -> str:
    """A short readable name: the prompt, the render's file name, or a bank anchor's stored label."""
    if "prompt" in a:
        return str(a["prompt"])
    if "bank" in a:
        b = read_bank(a["bank"])
        try:
            return b["labels"][int(a["i"])] if b else Path(str(a["bank"])).name
        except (IndexError, ValueError):
            return Path(str(a["bank"])).name
    return Path(str(a.get("id") or a.get("npz") or "")).name


def summary(path) -> dict:
    """{"kind", "n"}: for library rows and the composer's piece view."""
    s = of_render(path)
    return {"kind": s["kind"], "n": len(s["anchors"])}


def self_contained(path, segs: dict) -> dict:
    """A render's gen.segments for the studio's restore: its cond anchors pointed at its own bank
    (regenerating then needs none of the source renders)."""
    c = (segs or {}).get("cond")
    if not isinstance(c, dict) or not c.get("anchors"):
        return segs
    s = of_render(path)
    if s["kind"] != "multi" or not any("bank" in a for a in s["anchors"]):
        return segs
    return {**segs, "cond": {**c, "anchors": s["anchors"], "curves": s["curves"]}}


# ── studio side: write the bank when a render's sidecar is saved ─────────────────────────────
def remember(cond: dict, anchors: list, curves: list, bags: list) -> None:
    """condmix, on the MLX thread: the tensors it just resolved for this generation's cond."""
    if len(_banks) >= 8:
        _banks.pop(next(iter(_banks)))
    _banks[id(cond)] = (cond, anchors, curves, bags)


def install(persist) -> None:
    """Wrap persist.save: a render whose gen.segments.cond condmix resolved gets its bank."""
    orig = persist.save

    def save(entry):
        try:
            c = (((entry or {}).get("gen") or {}).get("segments") or {}).get("cond")
            hit = _banks.get(id(c)) if isinstance(c, dict) else None
            if hit is not None and hit[0] is c:
                write_bank(Path(entry["path"]), hit[1], hit[2], hit[3])
        except Exception as e:  # noqa: BLE001  (never break a render over bookkeeping)
            print(f"[condsrc] bank save failed: {e}", flush=True)
        return orig(entry)
    persist.save = save


if __name__ == "__main__":                     # condsrc.py <render id or path> …: what each was conditioned on
    for x in sys.argv[1:]:
        s = of_render(x)
        _, w = mean_weights(x)
        print(f"{x}\n  {s['kind']}, {len(s['anchors'])} anchors")
        for a, m in zip(s["anchors"], w):
            print(f"  {m:5.2f}  {label(a)[:90]}  {'(bank)' if 'bank' in a else ''}")
