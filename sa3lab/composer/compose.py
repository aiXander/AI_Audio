"""Composer harness + the editor's backend (design: docs/reference/sa3-composer.md): a project.json of
pieces cut from renders → one reference WAV + a segments box → takes rendered by the studio.

    PY=stable-audio-3/optimized/mlx/.venv/bin/python
    $PY sa3lab/composer/compose.py build  outputs/composer/<name>/project.json
    $PY sa3lab/composer/compose.py submit outputs/composer/<name>/project.json \
        [--url http://127.0.0.1:7860/] [--outdir composer/<name>] [--window 4] [--variants m1,cut]

**project.json** (everything optional except pieces / sequence):

    {"name": "demo", "seeds": [1, 2, 3], "length": null, "bpm": null, "key": null, "lufs": null,
     "model": {"dit": "medium", "decoder": "same-l", "steps": 8, "cfg": 1.0},
     "pieces": [{"id": <render id>, "src": [s0, s1], "at": t, "track": 0,
                 "align": true, "speed": 1.0, "mute": false, "solo": false, "fade": [0.01, 0.01],
                 "mix": {mixdsp strip}, "fix": {"transpose": st, "formant": false, "gain": dB,
                         "eq": [low, mid, high dB], "width": k}, "pull": 1.0}],
     "sequence": [{"id": …, "src": [s0, s1]}, {"gap": 10}, …],   # the phase-0 / CLI shape (end to end)
     "regions": [{"type": "soft", "t0", "t1", "u": 0.4}, {"type": "gen", "t0", "t1"}],   # legacy
     "rewrite": [[t, u], …],                              # the Rewrite lane's strength curve
     "seam": 0.0, "seam_unit": "beats",                   # regenerated each side of a piece edge
     "master": {"gain": dB, "limit": true, "ceiling": -1.0},
     "cond": {"mode": "m1", "fade": "cos", "gap": "crossfade" | "global" | "keys",
              "keys": [{"t": s, "anchor": {"id"|"npz"|"prompt": …}, "cut": false}]},   # the Conditioning lane
     "anchor": {"id": …} | {"prompt": …},                 # the global anchor (default: the longest piece's)
     "windows": {"max": 380, "context": 60},              # takes longer than `max` render as chained windows
     "variants": [{"name": "m1"}, {"name": "m3", "mode": "m3"}, {"name": "cut", "fade": "cut"},
                  {"name": "single", "anchors": "first"}]}

**Pieces** sit on tracks and may overlap (layering, spec 4.1); `sequence` lays pieces out end to
end with `{"gap": s}` items (phase 0; the editor converts it to explicit pieces on open). A piece is
a render id (path under outputs/, symlinks resolved) and a source range; id + the audio's sha256 go
into every take's lineage. **Mute / solo** leave a piece out of what SA3 sees (its time becomes
uncovered unless another piece covers it).

**Per-piece chain, in render order** (spec 4.1 / 4.3): decode → *alignment* (tempo stretch +
beat cut) → *fix chain* (the Fit panel's conform: transpose with rubberband pitch, gain, 3-band EQ,
M/S width) → edge fades → **cached** as `cache/<key>.wav` in the project folder (key = the
piece's audio identity: id, sha256, range, alignment, fix, fades; unreferenced files are pruned
each build; the editor's live monitor plays these) → *mix strip* (mixdsp.py: the same biquads the
page's WebAudio graph runs) → summed → *master* (gain + limiter) → `ref.wav`, exactly the
project length (the studio's Length follows the reference: segments.locked_length).

- **Alignment (spec 4.1, tempo + beat phase).** Each piece's range is measured with tempo.py.
  The project BPM is `tempo_consensus` over every piece reading with beat confidence ≥ BEAT_CONF
  (0.3), weighted by confidence × length: a live best-effort estimate that follows whatever is on
  the timeline (`bpm` in project.json overrides it, CLI only; the editor never sets it, and no BPM
  is ever written into the conditioning). Confident pieces with `align` on are octave-folded
  (×2 / ×½) to it and time-stretched with rubberband. Every confident piece is cut to start on its own beat (beat
  phase = the kick band's onset envelope folded at the beat period) and to a whole number of
  beats. In a sequence, gaps next to a confident piece snap to whole beats; with explicit pieces a
  confident piece's `at` snaps to the project's beat grid. So the grid runs unbroken from t = 0.
  Beatless pieces and `align: false` pieces (a promoted take) are never stretched or snapped.
  A piece's own `speed` (the editor's ⌥-drag on a clip edge: 0.5 = half speed, twice as long)
  multiplies into that rubberband factor, for every piece; a beat piece is still cut to whole beats
  and snapped, so stretching it to twice its beats gives half-time on the grid.
- **Strength** (`strength_keys`): kept = every active piece's span, minus `seam` (beats on a grid,
  else seconds) at each piece edge that is not covered by another layer running through it (a
  layer entering over a held bed keeps the bed); uncovered time = 1; `soft` regions = their u;
  `gen` regions = 1 (legacy; the editor converts them). `rewrite` = the editor's Rewrite lane, a
  strength curve [[t, u], …] (ui/curve.js rules) drawn over all that: where it is above 0 it wins,
  per latent frame.
- **Conditioning** (`cond_curves`, condmix.py contract; "what was this render conditioned on" is
  condsrc.py, for every caller). Every piece contributes its source conditioning: a render's
  `.cond.npz` (anarchy), its prompt (text), or, for a multi-anchor render (a composer take, a take
  of a take, an Arranger send), the anchors + weight curves it was itself rendered on, sampled at
  the source times the piece covers (cut, shifted, and beat-stretched with it: `src_range` +
  `stretch`; so a promoted base keeps its own conditioning; this replaced phase 1's `cond_fixed`).
  A take's file-backed anchors come from its own bank (`<take>.conds.npz`), so the renders it was
  made from may be gone. Imported audio has none and follows the global anchor. Wherever a
  render stands for ONE anchor (a piece override, a keypoint, the global anchor, variant
  `anchors: "first"`), a multi-anchor render gives its dominant anchor (condsrc.expand /
  dominant). Per latent frame: over a piece its anchors; over an overlap the layers share the
  frame in proportion to their RMS after the mix strip × `pull` (a −12 dB pad barely pulls);
  across a gap the last-ending piece(s) crossfade (`fade`: cos | linear | cut) into the
  next-starting one(s). Identical anchors merge (by content, condsrc.key: a take's bank row and
  the same bag named by id are one anchor); weights are normalised per frame and stored as
  keyframes at frame centres (flat runs collapsed). `cond.gap = "global"` = one anchor everywhere.
  Variants for the phase-0 tests: `mode` (m1 cross-attention blend | m3 velocity blend), `fade`,
  `anchors` "first" (the first piece's conditioning everywhere) | "global".
- **Overrides** (the editor's Conditioning lane): a piece's `anchor` replaces its source
  conditioning (e.g. a text prompt instead of its anarchy bag) and the automatic curves above still
  apply; `cond.gap = "keys"` replaces the automatic curves with explicit keypoints `cond.keys`
  (`key_curves`: each keypoint = one anchor at weight 1, cosine crossfades between consecutive
  keypoints, a keypoint with `cut: true` is a hard switch). The keys land in the take's
  `gen.composite.cond_keys`, the resolved curves in its `gen.segments.cond`.
- **Morph** (the 0f test bed: conditioning A → B over one render, no pieces): no pieces, a
  `length`, `"anchors": [{"id": …}, {"id": …}]` and `"morph": [t0, t1]`. The reference is
  silence at strength 1 everywhere, which samples exactly like plain text → audio.
- **Box** (the studio contract): `{"strength": [[t, u], …], "mask": "B", "cond": {…},
  "composite": {…}}`; segments' gen record stamps `gen.composite` into the take's sidecar
  (project, variant, length, bpm, pieces with id / sha256 / src / at / track / stretch / fix / mix,
  regions, master, the reference's sha256 and meter).

`build` writes, next to project.json: `ref.wav` (float WAV), `box.<variant>.json`,
`project.resolved.json` (the laid-out pieces + measurements), `cache/`. `submit` sends every
variant × seed to the studio's `/generate` with gradio_client and keeps at most `--window` jobs
waiting, so Xander's own Generate clicks interleave; keep the process alive until it exits
(gradio drops a client's waiting jobs when its connection closes). In the editor the same
`submit()` runs on a thread inside the studio process (server.py). Projects longer than
`windows.max` (380 s on Medium) render as chained windows (`submit_windows`). `build` computes the
conditioning before it writes anything, so a failed build never leaves a ref.wav that doesn't
match project.resolved.json (the editor addresses ref images by its sha256).

**Splice export** (`splice`, spec 4.1): a take with its kept frames replaced by the reference in
the sample domain (the fixed piece audio instead of the codec's reconstruction), crossfaded over
3 latent frames inside each kept edge.

Traps: a take's kept frames are the codec's reconstruction of the pieces (exact latents, decoded
next to new material), not the source audio. SA3 sees only the summed mix: a generated frame over
an overlap regenerates every layer in it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SA3LAB = HERE.parent
AI_AUDIO = SA3LAB.parent
OUTPUTS = (AI_AUDIO / "outputs").resolve()
sys.path.insert(0, str(SA3LAB))

import condsrc  # noqa: E402
import mixdsp  # noqa: E402
import tempo  # noqa: E402

SR = 44100
SPL = 4096
FPS = SR / SPL                       # latent frames per second (one frame = 92.9 ms)
BEAT_CONF = 0.3                      # spec 1.1: tempo readings are trustworthy above this
FADE = 0.01                          # default piece edge fades (s)
PRE_ROLL = 0.02                      # a beat piece starts this long before its first beat (keeps the kick's attack)
MARGIN = 2.0                         # extra source audio around a beat piece (stretch + snap room)
MAX_LEN = 380.0                      # one Medium pass (spec 1)
CACHE_V = 1                          # bump when the per-piece chain changes
DEFAULT_MODEL = {"dit": "medium", "decoder": "same-l", "steps": 8, "cfg": 1.0}
DEFAULT_VARIANTS = [{"name": "m1"}]


# ── audio helpers (numpy + ffmpeg, like tempo.py) ──────────────────────────────────────────
def decode(path: Path, t0: float = 0.0, dur: float | None = None, tempo_k: float = 1.0) -> np.ndarray:
    """(2, n) float32 at 44.1 kHz from t0 for dur seconds of source, time-stretched by tempo_k
    (rubberband: > 1 = faster / shorter)."""
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t0):.6f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.6f}"]
    cmd += ["-i", str(path)]
    if abs(tempo_k - 1.0) > 1e-6:
        cmd += ["-af", f"rubberband=tempo={tempo_k:.8f}:transients=crisp"]
    cmd += ["-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).T.copy()


def write_wav(path: Path, y: np.ndarray) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                    "-c:a", "pcm_f32le", str(path)], input=np.ascontiguousarray(y.T).astype(np.float32).tobytes(),
                   check=True)


def read_wav(path: Path) -> np.ndarray:
    return decode(path)


_cache: dict = {}


def _cached(kind: str, path: Path, *key, fn):
    """Memoise per (file, size, mtime, key): the editor re-lays the project on every edit."""
    st = Path(path).stat()
    k = (kind, str(path), st.st_size, st.st_mtime, *key)
    if k not in _cache:
        if len(_cache) > 512:
            _cache.clear()
        _cache[k] = fn()
    return _cache[k]


def sha256(path: Path) -> str:
    return _cached("sha", path, fn=lambda: _sha256(path))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _mono22(y: np.ndarray) -> np.ndarray:
    m = y.mean(0)
    n = int(len(m) * tempo.SR / SR)
    return np.interp(np.linspace(0, len(m) - 1, n), np.arange(len(m)), m).astype(np.float32)


def measure_tempo(y: np.ndarray) -> dict:
    """tempo.py's estimate on in-memory audio: {"bpm", "conf"}."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        write_wav(Path(f.name), y)
        return tempo.estimate(f.name)


def _band_env(y: np.ndarray, fmin: float, fmax: float) -> np.ndarray:
    """Spectral-flux onset envelope of one band (tempo.py's frames: 22.05 kHz, hop 256)."""
    x = _mono22(y)
    n = 1 + (len(x) - tempo.NFFT) // tempo.HOP
    if n < 2:
        return np.zeros(2)
    f = np.fft.rfftfreq(tempo.NFFT, 1 / tempo.SR)
    sel = (f >= fmin) & (f < fmax)
    win = np.hanning(tempo.NFFT).astype(np.float32)
    rows = []
    for s0 in range(0, n, 2048):                                   # chunked: bounded memory
        idx = np.arange(tempo.NFFT)[None, :] + tempo.HOP * np.arange(s0, min(n, s0 + 2048))[:, None]
        rows.append(np.abs(np.fft.rfft(x[idx] * win, axis=1))[:, sel])
    mag = np.concatenate(rows)
    s = np.log1p(100 * mag / (mag.max() + 1e-9))
    return np.r_[0.0, np.maximum(0, np.diff(s, axis=0)).sum(1)]


PHASE_LAG = 0.072        # the envelope peaks this long before the onset (synthetic kicks, 100-174 BPM)


def beat_phase(y: np.ndarray, bpm: float) -> float:
    """Seconds from the start of `y` to its first beat. The kick band's (30-150 Hz; full band when
    there is no bass) onset envelope is folded at the beat period into a 64-bin histogram and the
    peak bin wins. Checked 2026-11-06: windows of one render agree within ~5 ms this way, while
    the full-band circular mean wandered by 1/4 beat (off-beat hats pull it)."""
    env = _band_env(y, 30.0, 150.0)
    if env.sum() < 1e-3 * len(env):
        env = _band_env(y, 30.0, 11025.0)
    P = 60.0 * tempo.FPS / bpm                                     # beat period in envelope frames
    n = np.arange(len(env))
    B = 64
    h = np.bincount(((n % P) / P * B).astype(int) % B, weights=env, minlength=B)
    h = np.convolve(np.r_[h[-2:], h, h[:2]], [1, 2, 3, 2, 1], "valid")
    sec = (np.argmax(h) + 0.5) / B * P / tempo.FPS + PHASE_LAG
    return float(sec % (60.0 / bpm))


def tempo_consensus(readings: list) -> float | None:
    """The tempo most of the timeline agrees on: readings [(bpm, weight)] (weight = beat confidence
    × seconds), octave-folded; the candidate with the most weighted support within ~3 % wins (an
    unfolded match counts extra, so 140 beats 70 when the clips read 140), then the weighted median
    of the readings near it (stable: one more clip near the tempo doesn't nudge every stretch).
    A diagnostic and the grid the beat pieces snap to; never written into the conditioning."""
    rs = [(float(b), float(w)) for b, w in readings if b and w > 0]
    if not rs:
        return None

    def score(c):
        return sum(w * (math.exp(-0.5 * (math.log2(fold(b, c) / c) / 0.03) ** 2)
                        + 0.5 * math.exp(-0.5 * (math.log2(b / c) / 0.03) ** 2)) for b, w in rs)
    c = max((b for b, _ in rs), key=score)
    near = sorted((fold(b, c), w) for b, w in rs if abs(math.log2(fold(b, c) / c)) < 0.04)
    half, acc = sum(w for _, w in near) / 2, 0.0
    for b, w in near:
        acc += w
        if acc >= half:
            return round(b, 3)
    return round(c, 3)


def fold(bpm: float, ref: float) -> float:
    """bpm × 2^k closest to ref (octave misreads: 70 vs 140)."""
    return min((bpm * f for f in (0.25, 0.5, 1.0, 2.0, 4.0)), key=lambda b: abs(np.log2(b / ref)))


# ── the fix chain (spec 4.3: non-destructive conform settings on a piece) ───────────────────
EQ_BANDS = ((200.0, "lowshelf"), (550.0, "peaking"), (1500.0, "highshelf"))   # the dadaspec's 3 bands


def fix_chain(fix: dict | None) -> list[dict]:
    """The fix's EQ as biquads (mixdsp's format): low shelf 200 Hz, mid peak 550 Hz (Q 0.7),
    high shelf 1500 Hz: one control per dadaspec band."""
    eq = (fix or {}).get("eq") or []
    out = []
    for (f, kind), g in zip(EQ_BANDS, eq):
        if g and abs(float(g)) >= 0.05:
            out.append({"type": kind, "f": f, "q": 0.7 if kind == "peaking" else 1.0, "g": float(g)})
    return out


def apply_fix(y: np.ndarray, fix: dict | None) -> np.ndarray:
    f = fix or {}
    st = float(f.get("transpose") or 0)
    if abs(st) >= 0.01 and y.shape[1]:
        fm = ":formant=preserved" if f.get("formant") else ""
        y = mixdsp._ffmpeg(y, f"rubberband=pitch={2 ** (st / 12):.8f}:transients=crisp{fm}")
    y = mixdsp.filters(y, fix_chain(f))
    if f.get("gain"):
        y = y * np.float32(10 ** (float(f["gain"]) / 20))
    k = f.get("width")
    if k is not None and abs(float(k) - 1) > 1e-3:
        m, s = (y[0] + y[1]) / 2, (y[0] - y[1]) / 2 * np.float32(k)
        y = np.stack([m + s, m - s]).astype(np.float32)
    return y


def _fades(y: np.ndarray, fade) -> np.ndarray:
    a, b = (fade if isinstance(fade, (list, tuple)) else (fade, fade)) if fade is not None else (FADE, FADE)
    fa, fb = int(max(0.0, float(a)) * SR), int(max(0.0, float(b)) * SR)
    n = y.shape[1]
    if fa + fb >= n:
        fa = fb = n // 4
    y = y.copy()
    if fa > 1:
        y[:, :fa] *= np.linspace(0, 1, fa, dtype=np.float32)
    if fb > 1:
        y[:, n - fb:] *= np.linspace(1, 0, fb, dtype=np.float32)
    return y


# ── project → layout ──────────────────────────────────────────────────────────────────────
def load(path) -> dict:
    p = Path(path).resolve()
    d = json.loads(p.read_text())
    d["_dir"] = str(p.parent)
    d.setdefault("name", p.parent.name)
    return d


def piece_audio_path(rid: str) -> Path:
    p = (OUTPUTS / rid).resolve()
    if OUTPUTS not in p.parents or not p.is_file():
        raise SystemExit(f"no render with id {rid!r} under outputs/")
    return p


def render_id(path) -> str:
    return str(Path(path).resolve().relative_to(OUTPUTS))


def _sidecar(p: Path) -> dict:
    try:
        return json.loads(p.with_name(p.name + ".json").read_text())
    except Exception:
        return {}


def active_items(proj: dict) -> tuple[list, list]:
    """(items laid out, muted piece items). Solo wins: with any solo, only soloed pieces play."""
    items = proj.get("sequence")
    if not items:
        items = sorted(proj.get("pieces") or [], key=lambda x: float(x.get("at", 0)))
    solo = any(it.get("solo") for it in items if "id" in it)
    keep, muted = [], []
    for it in items:
        if "id" in it and (it.get("mute") or (solo and not it.get("solo"))):
            muted.append(it)
            if proj.get("sequence"):                   # a muted piece in a sequence leaves its time as a gap
                keep.append({"gap": float(it["src"][1]) - float(it["src"][0]), "_muted": True})
            continue
        keep.append(it)
    return keep, muted


def _speed(it: dict) -> float:
    """A piece's own time-stretch (the editor's ⌥-drag on an edge): a rubberband tempo factor on top of
    the beat alignment, 0.5 = half speed (twice as long). Clamped to [0.25, 4]; 1 = unset."""
    v = float(it.get("speed") or 1.0)
    return 1.0 if abs(v - 1.0) < 1e-4 else round(min(4.0, max(0.25, v)), 6)


def _audio_key(pc: dict, it: dict, bpm, P) -> str:
    k = {"v": CACHE_V, "sha": pc["sha256"], "src": pc["src"], "beat": pc["beat"],
         "bpm": round(bpm, 4) if (pc["beat"] and P) else None, "fix": it.get("fix") or None,
         "fade": it.get("fade")}
    if _speed(it) != 1.0:                              # only when set: unstretched pieces keep their cache files
        k["speed"] = _speed(it)
    return hashlib.sha1(json.dumps(k, sort_keys=True).encode()).hexdigest()[:16]


def layout(proj: dict, cache_dir: Path | None = None) -> dict:
    """Measure + place every active piece. Returns {"pieces", "gaps", "length", "bpm", "muted"};
    each piece gets `audio` (2, n): aligned, fixed and faded, ready for its mix strip at `at`."""
    items, muted = active_items(proj)
    proj_align = proj.get("align", True)
    pieces, bpm = [], proj.get("bpm")
    for it in items:                                   # measure every piece's range first
        if "gap" in it:
            continue
        rid = render_id(piece_audio_path(it["id"]))   # normalise (symlink → original)
        src = piece_audio_path(rid)
        s0, s1 = float(it["src"][0]), float(it["src"][1])
        align = it.get("align", proj_align)
        m = (s0 + s1) / 2                              # tempo needs ≥ 4 s: measure short ranges in context
        w0, w1 = (s0, s1) if s1 - s0 >= 8 else (max(0.0, m - 4), m + 4)
        t = _cached("tempo", src, round(w0, 4), round(w1, 4), fn=lambda: measure_tempo(decode(src, w0, w1 - w0)))
        pc = {"id": rid, "sha256": sha256(src), "src": [s0, s1], "bpm": t["bpm"], "conf": t["conf"],
              "pi": next((k for k, x in enumerate(proj.get("sequence") or proj.get("pieces") or []) if x is it), None),
              "beat": bool(align and t["bpm"] and t["conf"] >= BEAT_CONF), "align": bool(align),
              "_path": src, "_item": it}
        pieces.append(pc)
    if bpm is None:                                    # the editor never sets one: a live best-effort estimate
        bpm = tempo_consensus([(pc["bpm"], pc["conf"] * (pc["src"][1] - pc["src"][0]))
                               for pc in pieces if pc["bpm"] and pc["conf"] >= BEAT_CONF])
    P = 60.0 / bpm if bpm else None
    for pc in pieces:                                  # align → fix → fades (cached per audio key)
        it = pc["_item"]
        s0, s1 = pc["src"]
        key = _audio_key(pc, it, bpm, P)
        hit = (cache_dir / f"{key}.wav") if cache_dir else None
        meta = (cache_dir / f"{key}.json") if cache_dir else None
        if hit and hit.is_file() and meta.is_file():
            pc["audio"] = read_wav(hit)
            pc.update(json.loads(meta.read_text()))
        else:
            sp = _speed(it)
            info = {"stretch": sp}
            if pc["beat"] and P:
                b = fold(pc["bpm"], bpm)
                k = bpm / b * sp                       # rubberband tempo factor (alignment × the piece's own speed)
                m0 = min(MARGIN, s0)
                y = decode(pc["_path"], s0 - m0, (s1 - s0) + m0 + MARGIN, tempo_k=k)
                ph = beat_phase(y, bpm)
                want = m0 / k                          # where s0 landed after the stretch
                start = max(0.0, ph + max(0, round((want - ph) / P)) * P - PRE_ROLL)   # beats land PRE_ROLL in
                n = max(1, round((s1 - s0) / k / P))
                i0 = int(round(start * SR))
                y = y[:, i0:i0 + int(round(n * P * SR))]
                info = {"stretch": round(k, 6), "phase": round(ph, 4), "beats": n,
                        "src_cut": [round(s0 - m0 + start * k, 4), round(s0 - m0 + (start + n * P) * k, 4)]}
            else:
                y = decode(pc["_path"], s0, s1 - s0, tempo_k=sp)
            y = _fades(apply_fix(y, it.get("fix")), it.get("fade"))
            pc["audio"] = y
            pc.update(info)
            if hit:
                cache_dir.mkdir(parents=True, exist_ok=True)
                tmp = hit.with_name(hit.stem + ".tmp.wav")
                write_wav(tmp, y)
                tmp.replace(hit)
                meta.write_text(json.dumps(info))
        pc["audio_key"] = key
    # place
    out, gaps, cur = [], [], 0.0
    by_item = {id(pc["_item"]): pc for pc in pieces}
    seq = bool(proj.get("sequence"))
    for j, it in enumerate(items):
        if "gap" in it:
            g = float(it["gap"])
            near = [by_item.get(id(items[j + d])) for d in (-1, 1) if 0 <= j + d < len(items)]
            if P and any(n is not None and n["beat"] for n in near):
                g = max(1, round(g / P)) * P           # gaps next to a beat piece: whole beats
            gaps.append([round(cur, 4), round(cur + g, 4)])
            cur += g
            continue
        pc = by_item[id(it)]
        if seq:
            at = cur
        else:
            at = max(0.0, float(it.get("at", 0.0)))
            if pc["beat"] and P:
                at = round(at / P) * P                 # a beat piece sits on the project grid
        pc["at"] = round(at, 4)
        pc["at_req"] = round(float(it.get("at", 0.0)), 4) if not seq else pc["at"]   # what the page asked for
        pc["dur"] = round(pc["audio"].shape[1] / SR, 4)
        pc["track"] = int(it.get("track", 0) or 0)
        cur = at + pc["dur"] if seq else max(cur, at + pc["dur"])
        out.append(pc)
    length = float(proj.get("length") or cur)
    if not seq:
        gaps = uncovered(out, length)
    return {"pieces": out, "gaps": gaps, "length": round(length, 4), "bpm": bpm,
            "muted": [{"id": it["id"], "src": it["src"], "at": it.get("at"), "track": it.get("track", 0)}
                      for it in muted]}


def first_beat(pc: dict, P: float | None) -> float | None:
    """Seconds from the start of a laid-out beat piece's audio to its first beat, from the snap's
    own measurement (`phase` on the padded decode, `src_cut` for where the cut landed): PRE_ROLL by
    construction, less when the cut hit the start of the source. fit.py checks the grid and overlap
    phase with this rather than re-measuring the cut piece, whose phase flips by half a beat on
    busy breakbeats. None for pieces that were not snapped."""
    if not P or pc.get("phase") is None or not pc.get("src_cut"):
        return None
    s0 = float(pc["src"][0])
    start = (float(pc["src_cut"][0]) - (s0 - min(MARGIN, s0))) / float(pc.get("stretch") or 1.0)
    return float((pc["phase"] - start) % P)


def aligned_bpm(pc: dict) -> float | None:
    """A beat piece's tempo after alignment, from layout's in-context reading of its source (short
    ranges are measured over 8 s around them) times the rubberband factor."""
    return float(pc["bpm"]) * float(pc.get("stretch") or 1.0) if pc.get("bpm") else None


def uncovered(pieces: list, L: float) -> list:
    spans = sorted((pc["at"], pc["at"] + pc["dur"]) for pc in pieces)
    out, cur = [], 0.0
    for a, b in spans:
        if a > cur + 1e-3:
            out.append([round(cur, 4), round(a, 4)])
        cur = max(cur, b)
    if L > cur + 1e-3:
        out.append([round(cur, 4), round(L, 4)])
    return out


# ── reference WAV: mix strip per piece, summed, master bus ─────────────────────────────────
def strip(pc: dict) -> np.ndarray:
    m = pc["_item"].get("mix") or {}
    k = ("strip", pc["audio_key"], json.dumps(m, sort_keys=True))
    if k not in _cache:
        if len(_cache) > 512:
            _cache.clear()
        _cache[k] = mixdsp.apply_strip(pc["audio"], m)
    return _cache[k]


def mix(lay: dict, proj: dict | None = None) -> np.ndarray:
    n = int(round(lay["length"] * SR))
    ref = np.zeros((2, n), np.float32)
    for pc in lay["pieces"]:
        y = strip(pc) if "_item" in pc else pc["audio"]
        pc["rms"] = float(np.sqrt((y.astype(np.float64) ** 2).mean())) if y.size else 0.0
        i0 = int(round(pc["at"] * SR))
        i1 = min(n, i0 + y.shape[1])
        if i1 > i0:
            ref[:, i0:i1] += y[:, :i1 - i0]
    return mixdsp.apply_master(ref, (proj or {}).get("master"))


def _seam(pc: dict, proj: dict, P) -> float:
    seam = float(proj.get("seam") or 0.0)
    return seam * P if (P and pc["beat"] and proj.get("seam_unit", "beats") == "beats") else seam


def strength_keys(lay: dict, proj: dict) -> list:
    """Piecewise-constant strength: pieces 0 (minus seams at uncovered-by-another-layer edges),
    uncovered 1, soft / gen regions on top."""
    L = lay["length"]
    P = 60.0 / lay["bpm"] if lay["bpm"] else None
    pcs = lay["pieces"]
    kept = []

    def spanned(t, me):                                # another layer runs through t
        return any(o is not me and o["at"] < t - 1e-3 and o["at"] + o["dur"] > t + 1e-3 for o in pcs)
    for pc in pcs:
        s = _seam(pc, proj, P)
        a, b = pc["at"], pc["at"] + pc["dur"]
        a2 = a + (s if a > 1e-6 and not spanned(a, pc) else 0.0)      # no seam at the project's edges
        b2 = b - (s if b < L - 1e-6 and not spanned(b, pc) else 0.0)
        if b2 > a2:
            kept.append((a2, b2))
    regs = [r for r in proj.get("regions") or [] if r.get("type") in ("soft", "gen")]
    pts = sorted({0.0, L} | {x for a, b in kept for x in (a, b)} |
                 {float(r[k]) for r in regs for k in ("t0", "t1")})
    pts = [p for p in pts if 0.0 <= p <= L]
    keys = []
    for a, b in zip(pts[:-1], pts[1:]):
        m = (a + b) / 2
        u = 0.0 if any(x <= m < y for x, y in kept) else 1.0
        for r in regs:
            if float(r["t0"]) <= m < float(r["t1"]):
                u = 1.0 if r["type"] == "gen" else float(r.get("u", 0.4))
        if keys and keys[-1][1] == u:
            keys[-1] = (round(b, 4), u)
            continue
        keys += [(round(a, 4), u), (round(b, 4), u)]
    rw = [(float(t), float(u)) for t, u in proj.get("rewrite") or []]
    if not rw:
        return [list(k) for k in keys]
    # the Rewrite lane: a drawn strength curve (curve.js rules) over the automatic one; where it is
    # above 0 it wins (it can soften a gap or regenerate a clip), elsewhere the automatic value holds
    n, out = int(math.ceil(L * FPS - 1e-6)), []
    for f in range(n):
        tc = (f + 0.5) / FPS
        ur = curve_at(rw, tc)
        u = round(ur if ur > 0 else curve_at(keys, tc), 4)
        a, b = round(f / FPS, 4), round(min(L, (f + 1) / FPS), 4)
        if out and out[-1][1] == u:
            out[-1] = [b, u]
        else:
            out += [[a, u], [b, u]]
    return out


def silent_spans(ref: np.ndarray, floor_db: float = -60.0, min_s: float = 0.5) -> list:
    """Stretches of the reference with no audio at all (every latent frame's RMS below floor_db),
    at least min_s long: the editor warns when one is only partly rewritten (0 < u < 1 denoises
    silence, which is rarely what anyone wants)."""
    n = ref.shape[1] // SPL
    if not n:
        return []
    fr = ref[:, :n * SPL].reshape(ref.shape[0], n, SPL)
    quiet = 20 * np.log10(np.sqrt((fr ** 2).mean(axis=2)).max(axis=0) + 1e-9) < floor_db
    out, a = [], None
    for f, q in enumerate(list(quiet) + [False]):
        if q and a is None:
            a = f
        elif not q and a is not None:
            if (f - a) / FPS >= min_s:
                out.append([round(a / FPS, 3), round(f / FPS, 3)])
            a = None
    return out


def curve_at(K: list, t: float) -> float:
    """A strength curve's value at t (ui/curve.js `at`): linear between keys, flat outside, from the
    right at a hard edge."""
    if not K:
        return 0.0
    j = next((i for i, k in enumerate(K) if k[0] > t), None)
    if j is None:
        return float(K[-1][1])
    if j == 0:
        return float(K[0][1])
    (t0, u0), (t1, u1) = K[j - 1], K[j]
    return float(u0 + (t - t0) / (t1 - t0) * (u1 - u0))


# ── conditioning anchors per piece (spec 4.5; what a render was conditioned on: condsrc.py) ────
def src_range(pc: dict) -> tuple[float, float]:
    """The source span a laid-out piece's audio really covers (a beat piece is re-cut: src_cut)."""
    s = pc.get("src_cut") or pc["src"]
    return float(s[0]), float(s[1])


def anchor_of(pc: dict) -> dict:
    """A piece as ONE anchor: its override, else its source's dominant anchor over the range it
    uses (an anarchy bag, its prompt, or a take's most-weighted anchor: condsrc.dominant)."""
    ov = (pc.get("_item") or {}).get("anchor")
    a = condsrc.expand(ov) if ov else condsrc.dominant(pc["_path"], *src_range(pc))
    if not a:
        raise SystemExit(f"piece {pc['id']} has no conditioning (an import?)")
    return a


def piece_anchors(pc: dict) -> list:
    """[(anchor, keys | None)]: the piece's source conditioning (condsrc.of_render). keys = the
    weight curve over the *source* timeline (a multi-anchor render's recorded curves: composer
    takes, takes of takes, Arranger sends), None = weight 1 throughout. [] for audio without
    conditioning (imports). An override (`anchor` on the piece) is one anchor (condsrc.expand)."""
    ov = (pc.get("_item") or {}).get("anchor")
    if ov:                                             # Xander's override (a prompt, another render, an npz)
        return [(condsrc.expand(ov), None)]
    s = condsrc.of_render(pc["_path"])
    return list(zip(s["anchors"], s["curves"]))


def global_anchor(lay: dict, proj: dict | None) -> dict:
    """The one anchor of a "global" project and the fallback for imports: `anchor` when set,
    else the longest piece's (anchor_of: a promoted base take gives its dominant anchor)."""
    if (proj or {}).get("anchor"):
        return condsrc.expand(proj["anchor"])
    for pc in sorted(lay["pieces"], key=lambda pc: -pc["dur"]):
        try:
            return anchor_of(pc)
        except SystemExit:
            continue
    raise SystemExit("no piece has conditioning (all imports?): set a global anchor (a prompt) in the project")


def _sample(keys, t) -> np.ndarray:
    """segments.frame_strength's rule at arbitrary times t (linear, flat outside, repeated t = jump)."""
    kt = np.array([k[0] for k in keys], np.float64)
    ku = np.array([k[1] for k in keys], np.float64)
    i = np.searchsorted(kt, t, side="right")
    lo, hi = np.clip(i - 1, 0, len(kt) - 1), np.clip(i, 0, len(kt) - 1)
    span = kt[hi] - kt[lo]
    w = np.where(span > 0, (t - kt[lo]) / np.where(span > 0, span, 1.0), 0.0)
    u = ku[lo] + w * (ku[hi] - ku[lo])
    return np.where(i == 0, ku[0], np.where(i >= len(kt), ku[-1], u))


def _xfade(x: np.ndarray, shape: str) -> np.ndarray:
    if shape == "cut":
        return (x >= 0.5).astype(np.float64)
    return 0.5 - 0.5 * np.cos(np.pi * x) if shape == "cos" else x


def _simplify(tc: np.ndarray, w: np.ndarray, eps: float = 0.003) -> list:
    """Keyframes at frame centres reproducing w within eps (Ramer-Douglas-Peucker)."""
    keep = {0, len(w) - 1}
    stack = [(0, len(w) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        lin = w[a] + (w[b] - w[a]) * (tc[a + 1:b] - tc[a]) / (tc[b] - tc[a])
        err = np.abs(w[a + 1:b] - lin)
        i = int(np.argmax(err))
        if err[i] > eps:
            m = a + 1 + i
            keep.add(m)
            stack += [(a, m), (m, b)]
    return [[round(float(tc[i]), 4), round(float(w[i]), 5)] for i in sorted(keep)]


def cond_curves(lay: dict, proj: dict, shape: str = "cos") -> tuple[list, list]:
    """(anchors, curves) over the project timeline, per latent frame (see the module docstring)."""
    L = lay["length"]
    T = max(1, int(math.ceil(L * FPS)))
    tc = (np.arange(T) + 0.5) / FPS
    pcs = lay["pieces"]
    anchors, idx = [], {}
    W = []

    def row(a):
        k = condsrc.key(a)                             # content dedupe: a take's bank row = the same bag by id
        if k not in idx:
            idx[k] = len(anchors)
            anchors.append(a)
            W.append(np.zeros(T))
        return W[idx[k]]
    sets = [piece_anchors(pc) for pc in pcs]
    try:
        g = global_anchor(lay, proj)
    except SystemExit:
        g = None
    pulls = [max(0.0, float(pc["_item"].get("pull", 1.0))) * max(pc.get("rms", 1.0), 1e-6) for pc in pcs]

    def add(i, frames, scale, s_time):
        s = sets[i] or ([(g, None)] if g else [])
        if not s:
            return
        ws = [np.ones(len(frames)) if k is None else np.clip(_sample(k, s_time), 0, None) for _, k in s]
        tot = np.maximum(sum(ws), 1e-9)
        for (a, _), w in zip(s, ws):
            row(a)[frames] += scale * w / tot

    def src_time(pc, t):                               # timeline → the piece's source timeline
        return src_range(pc)[0] + (np.asarray(t) - pc["at"]) * pc.get("stretch", 1.0)
    covered = np.zeros(T, bool)
    for i, pc in enumerate(pcs):
        f = np.where((tc >= pc["at"]) & (tc < pc["at"] + pc["dur"]))[0]
        if len(f):
            covered[f] = True
            add(i, f, pulls[i], src_time(pc, tc[f]))
    f = 0
    while f < T:                                       # gaps: crossfade last-ending → next-starting
        if covered[f]:
            f += 1
            continue
        e = f
        while e < T and not covered[e]:
            e += 1
        g0, g1 = f / FPS, e / FPS
        fr = np.arange(f, e)
        left = [i for i, pc in enumerate(pcs) if pc["at"] + pc["dur"] <= tc[f] + 1e-3]
        right = [i for i, pc in enumerate(pcs) if pc["at"] >= tc[e - 1] - 1e-3]
        if left:
            g0 = max(pcs[i]["at"] + pcs[i]["dur"] for i in left)
            left = [i for i in left if pcs[i]["at"] + pcs[i]["dur"] >= g0 - 1 / FPS]
        if right:
            g1 = min(pcs[i]["at"] for i in right)
            right = [i for i in right if pcs[i]["at"] <= g1 + 1 / FPS]
        x = _xfade((tc[fr] - g0) / max(g1 - g0, 1e-6), shape)
        wl = (1 - x) if right else np.ones(len(fr))
        wr = x if left else np.ones(len(fr))
        for grp, w in ((left, wl), (right, wr)):
            tot = sum(pulls[i] for i in grp) or 1.0
            for i in grp:
                pc = pcs[i]
                edge = pc["at"] + pc["dur"] - 1e-3 if i in left else pc["at"]
                add(i, fr, w * pulls[i] / tot, np.full(len(fr), float(src_time(pc, edge))))
        f = e
    if not anchors:
        raise SystemExit("no conditioning for this project: add a piece with conditioning or set an anchor")
    Wm = np.stack(W)
    s = Wm.sum(0)
    Wm[0, s <= 0] = 1.0
    Wm = Wm / np.maximum(Wm.sum(0), 1e-9)
    keep = [i for i in range(len(anchors)) if Wm[i].max() > 1e-4]
    anchors = [anchors[i] for i in keep]
    Wm = Wm[keep]
    if len(anchors) == 1:
        return anchors, [[[0.0, 1.0]]]
    return anchors, [_simplify(tc, w) for w in Wm]


def key_curves(keys: list, L: float) -> tuple[list, list]:
    """Explicit conditioning keypoints `cond.keys = [{t, anchor, cut?}]` → (anchors, curves): at a
    keypoint its anchor has weight 1; between two keypoints the conditioning crossfades (cosine)
    from one to the next, or jumps at the later one when it has `cut: true`. Flat before the first
    and after the last."""
    ks = sorted([{**k, "anchor": condsrc.expand(k["anchor"])} for k in keys if k.get("anchor")],
                key=lambda k: float(k["t"]))           # a render as a keypoint = its dominant anchor
    if not ks:
        raise SystemExit("conditioning keypoints have no anchors")
    T = max(1, int(math.ceil(L * FPS)))
    tc = (np.arange(T) + 0.5) / FPS
    anchors, idx = [], {}
    for k in ks:
        j = condsrc.key(k["anchor"])
        if j not in idx:
            idx[j] = len(anchors)
            anchors.append(k["anchor"])
    W = np.zeros((len(anchors), T))
    ai = [idx[condsrc.key(k["anchor"])] for k in ks]
    W[ai[0], tc < float(ks[0]["t"])] = 1.0
    W[ai[-1], tc >= float(ks[-1]["t"])] = 1.0
    for n in range(len(ks) - 1):
        a, b = float(ks[n]["t"]), float(ks[n + 1]["t"])
        f = (tc >= a) & (tc < b)
        x = _xfade((tc[f] - a) / max(b - a, 1e-6), "cut" if ks[n + 1].get("cut") else "cos")
        if ks[n + 1].get("cut"):
            x = (tc[f] >= b).astype(float)
        W[ai[n], f] += 1 - x
        W[ai[n + 1], f] += x
    W = W / np.maximum(W.sum(0), 1e-9)
    if len(anchors) == 1:
        return anchors, [[[0.0, 1.0]]]
    return anchors, [_simplify(tc, w) for w in W]


def cond_block(lay: dict, var: dict, proj: dict | None = None) -> dict:
    import condmix
    proj = proj or {}
    ck = (proj.get("cond") or {})
    if ck.get("gap") == "keys" and ck.get("keys") and not var.get("anchors"):
        anchors, curves = key_curves(ck["keys"], lay["length"])
        return {"mode": var.get("mode", "m1"), "anchors": anchors, "curves": curves}
    pcs = lay["pieces"]
    mode, shape = var.get("mode", "m1"), var.get("fade", "cos")
    if not pcs:                                        # a morph: explicit anchors, no reference audio
        anchors = [condsrc.expand(a) for a in proj.get("anchors") or []]
        if not anchors:
            if proj.get("anchor"):
                return {"mode": mode, "anchors": [condsrc.expand(proj["anchor"])], "curves": [[[0.0, 1.0]]]}
            raise SystemExit("a project without pieces needs \"anchors\" (and \"morph\": [t0, t1])")
        if var.get("anchors") == "first" or len(anchors) == 1:
            return {"mode": mode, "anchors": anchors[:1], "curves": [[[0.0, 1.0]]]}
        t0, t1 = proj.get("morph") or [0.0, lay["length"]]
        return {"mode": mode, "anchors": anchors[:2], "curves": list(condmix.crossfade(t0, t1, shape=shape))}
    if var.get("anchors") == "first":
        return {"mode": mode, "anchors": [anchor_of(pcs[0])], "curves": [[[0.0, 1.0]]]}
    if var.get("anchors") == "global":
        return {"mode": mode, "anchors": [global_anchor(lay, proj)], "curves": [[[0.0, 1.0]]]}
    anchors, curves = cond_curves(lay, proj, shape)
    return {"mode": mode, "anchors": anchors, "curves": curves}


def variants(proj: dict) -> list[dict]:
    """`variants`, else one from the editor's `cond.gap`: "crossfade" (m1) | "global"."""
    base = proj.get("cond") or {}
    dflt = ([{"name": "global", "anchors": "global"}] if base.get("gap") == "global"
            else [{"name": "keys"}] if base.get("gap") == "keys" and base.get("keys") else DEFAULT_VARIANTS)
    return [{"name": v.get("name", "m1"), **{k: base[k] for k in ("mode", "fade") if k in base}, **v}
            for v in proj.get("variants") or dflt]


def _prune(cache_dir: Path, keep: set) -> None:
    if not cache_dir.is_dir():
        return
    for f in cache_dir.iterdir():
        if f.is_file() and f.name.split(".")[0] not in keep and time.time() - f.stat().st_mtime > 5:
            f.unlink(missing_ok=True)


def build(proj: dict, prune: bool = True) -> dict:
    """project → ref.wav + box.<variant>.json + project.resolved.json (+ cache/) in the project folder."""
    d = Path(proj["_dir"])
    cache = Path(proj["_cache"]) if proj.get("_cache") else d / "cache"   # a job snapshot reuses its project's cache
    prune = prune and not proj.get("_cache")
    lay = layout(proj, cache)
    ref = mix(lay, proj)                               # also sets each piece's post-strip RMS (anchor pull)
    conds = {var["name"]: cond_block(lay, var, proj) for var in variants(proj)}   # may raise: before any write,
    ref_path = d / "ref.wav"                           # so a failed build never leaves a stale ref.wav behind
    tmp = d / "ref.tmp.wav"
    write_wav(tmp, ref)
    tmp.replace(ref_path)
    keys = strength_keys(lay, proj)
    pieces = [{k: pc[k] for k in ("id", "pi", "sha256", "src", "at", "dur", "track", "stretch", "bpm", "conf", "beat",
                                  "at_req", "align", "phase", "beats", "src_cut", "audio_key", "rms") if k in pc}
              | {k: pc["_item"][k] for k in ("mix", "fix", "fade", "pull", "anchor", "speed") if pc["_item"].get(k) is not None}
              for pc in lay["pieces"]]
    regions = ([{"type": "gap", "t0": a, "t1": b} for a, b in lay["gaps"]]
               + [r for r in proj.get("regions") or []])
    lineage = {"project": proj["name"], "project_file": _rel(d / "project.json"), "length": lay["length"],
               "bpm": lay["bpm"], "pieces": pieces, "regions": regions, "rewrite": proj.get("rewrite") or None,
               "muted": lay["muted"],
               "seam": proj.get("seam", 0.0), "master": proj.get("master") or {},
               "cond_mode": (proj.get("cond") or {}).get("gap", "crossfade"),
               "cond_keys": (proj.get("cond") or {}).get("keys") if (proj.get("cond") or {}).get("gap") == "keys" else None,
               "meter": mixdsp.meter(ref), "silent": silent_spans(ref), "ref_sha256": sha256(ref_path)}
    boxes = {}
    for var in variants(proj):
        box = {"strength": keys, "mask": "B", "cond": conds[var["name"]],
               "composite": {**lineage, "variant": var["name"]}}
        (d / f"box.{var['name']}.json").write_text(json.dumps(box))
        boxes[var["name"]] = box
    resolved = {k: v for k, v in proj.items() if not k.startswith("_")} | {"resolved": lineage}
    (d / "project.resolved.json").write_text(json.dumps(resolved, indent=1))
    if prune:
        _prune(cache, {pc["audio_key"] for pc in lay["pieces"]})
    return {"ref": ref_path, "length": lay["length"], "boxes": boxes, "lineage": lineage, "keys": keys,
            "layout": lay}


def _rel(p: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(OUTPUTS))
    except ValueError:
        return str(p)


# ── splice export (spec 4.1) ────────────────────────────────────────────────────────────────
def splice(take: Path, ref: Path, keys: list, out: Path, xf_frames: int = 3) -> Path:
    """The take with its kept frames (strength 0) replaced by the reference audio, crossfaded over
    `xf_frames` latent frames inside each kept edge. Both must be the same length (± a frame)."""
    y, r = decode(take), decode(ref)
    n = min(y.shape[1], r.shape[1])
    y, r = y[:, :n], r[:, :n]
    T = int(math.ceil(n / SPL))
    u = _sample([(float(a), float(b)) for a, b in keys], (np.arange(T) + 0.5) / FPS) if keys else np.ones(T)
    keep = (u <= 1e-6).astype(np.float64)
    k = np.ones(xf_frames * 2 + 1) / (xf_frames * 2 + 1)        # soften inside kept stretches only
    soft = np.minimum(keep, np.convolve(keep, k, "same"))
    soft = np.where(keep > 0, np.clip((np.convolve(keep, np.ones(2 * xf_frames + 1), "same") - xf_frames)
                                      / (xf_frames + 1), 0, 1), 0.0)
    w = np.interp(np.arange(n) / SPL, np.arange(T) + 0.5, soft).astype(np.float32)
    z = y * (1 - w) + r * w
    write_wav(out, z)
    return out


# ── server-side submit (gradio_client → the studio's own queue) ─────────────────────────────
def generate_args(api_n: int, prompt: str, seconds: float, seed: int, model: dict, ref: str,
                  outdir: str, box: dict) -> list:
    """/generate's positional inputs (enqueue.py's order) + outdir's destination box + the
    segments box; padded LoRA slots fill whatever the studio has in between."""
    from gradio_client import handle_file
    steps = int(model.get("steps", 8))
    vals = [model.get("dit", "medium"), model.get("decoder", "same-l"), prompt, "", float(seconds), steps,
            str(seed), float(model.get("cfg", 1.0)), float(model.get("apg", 1.0)), 1.0, 0.92,
            None, handle_file(ref), 0, 0, [], "Save to MP3 (V0)"]
    slots = [[None, "", 1.0, 1, 8]] * 3
    vals += sum(slots, [])
    vals += [outdir, json.dumps(box)]
    if len(vals) != api_n:
        raise SystemExit(f"/generate takes {api_n} inputs here, the harness builds {len(vals)}: "
                         "the studio's input list changed (check enqueue.py's order)")
    return vals


def _api_n(client) -> int:
    info = client.view_api(return_format="dict", print_info=False)
    return len(info["named_endpoints"]["/generate"]["parameters"])


def _landed(out: Path, project: str, since: float) -> list[Path]:
    takes = []
    for sc in out.glob("*.json"):
        try:
            d = json.loads(sc.read_text())
        except Exception:
            continue
        c = (d.get("gen") or {}).get("composite") if isinstance(d, dict) else None
        if c and c.get("project") == project and sc.stat().st_mtime >= since:
            takes.append(sc)
    return sorted(takes)


def _run_jobs(c, n_api, jobs, window, log):
    """jobs: [(vals, tag)] → results in order (each a fut.result() or None on failure)."""
    live: deque = deque()
    res = {}

    def finish(i, fut, tag):
        try:
            r = fut.result() or ()
            err = [x for x in r if isinstance(x, str) and "error:" in x]
            log(("FAIL " + tag + " " + err[0][:200]) if err else f"done {tag}")
            res[i] = r if not err else None
        except Exception as e:  # noqa: BLE001  (one failed job must not drop the rest)
            log(f"FAIL {tag} {e}")
            res[i] = None
    for i, (vals, tag) in enumerate(jobs):
        while len(live) >= window:
            finish(*live.popleft())
        live.append((i, c.submit(*vals, api_name="/generate"), tag))
        time.sleep(0.2)                               # submission order = queue order
    while live:
        finish(*live.popleft())
    return [res.get(i) for i in range(len(jobs))]


def submit(proj: dict, url: str, outdir: str | None = None, window: int = 4,
           only: set | None = None, log=print, seeds: list | None = None,
           section: list | None = None) -> list[Path]:
    """Build, then queue every variant × seed into the studio at `url`; blocks until all are
    done and returns the takes' sidecars. Safe to run on a thread (the composer backend, which
    points `_dir` at a frozen snapshot folder so edits during the render can't swap ref.wav).
    `section` = [t0, t1]: render only that stretch of the timeline (submit_section)."""
    from gradio_client import Client
    b = build(proj)
    if section:
        return submit_section(proj, b, url, outdir, window, log, seeds, section)
    if b["length"] > float((proj.get("windows") or {}).get("max", MAX_LEN)) + 1e-3:
        return submit_windows(proj, b, url, outdir, window, log, seeds)
    outdir = outdir or f"composer/{proj['name']}"
    model = DEFAULT_MODEL | (proj.get("model") or {})
    seeds = seeds or proj.get("seeds") or [0]
    c = Client(url, verbose=False)
    n_api = _api_n(c)
    t0 = time.time() - 1
    jobs = []
    for name, box in b["boxes"].items():
        if only and name not in only:
            continue
        for seed in seeds:
            jobs.append((generate_args(n_api, f"composer {proj['name']} {name}", b["length"], seed, model,
                                       str(b["ref"]), outdir, box), f"{name} seed {seed}"))
    _run_jobs(c, n_api, jobs, window, log)
    takes = _landed(OUTPUTS / outdir, proj["name"], t0)
    log(f"{len(takes)} takes with gen.composite in outputs/{outdir}")
    return takes


def submit_section(proj: dict, b: dict, url: str, outdir: str | None, window: int, log, seeds,
                   section: list) -> list[Path]:
    """Only [t0, t1] of the timeline (the editor's ruler selection: try one transition without
    re-rendering the whole piece): the reference, strength keys and conditioning curves cut to it
    and shifted to start at 0, like one window of submit_windows without the chaining. SA3 sees only
    the section, so kept audio at its edges is its only context. The take is t1 - t0 long, on its
    own timeline; `composite.section` = [t0, t1] places it back on the project's."""
    from gradio_client import Client
    t0, t1 = max(0.0, float(section[0])), min(float(b["length"]), float(section[1]))
    if t1 - t0 < 1.0:
        raise SystemExit("the section is shorter than 1 s")
    if t1 - t0 > MAX_LEN + 1e-3:
        raise SystemExit(f"a section renders in one pass: at most {MAX_LEN:.0f} s")
    outdir = outdir or f"composer/{proj['name']}"
    model = DEFAULT_MODEL | (proj.get("model") or {})
    seeds = seeds or proj.get("seeds") or [0]
    rp = Path(proj["_dir"]) / "ref.section.wav"
    write_wav(rp, decode(b["ref"])[:, int(round(t0 * SR)):int(round(t1 * SR))])
    c = Client(url, verbose=False)
    n_api = _api_n(c)
    since = time.time() - 1
    jobs = []
    for name, box in b["boxes"].items():
        sbox = {**box, "strength": _shift_keys(box["strength"], t0, t1) if box.get("strength") else box.get("strength"),
                "cond": _shift_cond(box["cond"], t0),
                "composite": {**box["composite"], "section": [round(t0, 4), round(t1, 4)], "length": round(t1 - t0, 4)}}
        for seed in seeds:
            jobs.append((generate_args(n_api, f"composer {proj['name']} {name} {t0:.0f}-{t1:.0f}s", t1 - t0, seed, model,
                                       str(rp), outdir, sbox), f"{name} {t0:.0f}-{t1:.0f}s seed {seed}"))
    try:
        _run_jobs(c, n_api, jobs, window, log)
    finally:
        rp.unlink(missing_ok=True)
    takes = _landed(OUTPUTS / outdir, proj["name"], since)
    log(f"{len(takes)} section takes ({t0:.1f}-{t1:.1f} s) in outputs/{outdir}")
    return takes


# ── windows past one pass (spec 4.1 "beyond 380 s") ─────────────────────────────────────────
def window_plan(L: float, wmax: float, ctx: float) -> list[tuple[float, float]]:
    """[(t0, t1)] windows of at most wmax s; each later window starts `ctx` s before the previous
    one's end (that stretch is kept from the previous take: the context)."""
    out, t0 = [], 0.0
    while True:
        t1 = min(L, t0 + wmax)
        out.append((round(t0, 4), round(t1, 4)))
        if t1 >= L - 1e-3:
            return out
        t0 = t1 - ctx


def _shift_keys(keys, t0, t1):
    """Strength keys cut to [t0, t1] and shifted to start at 0."""
    ts = sorted({float(k[0]) for k in keys if t0 < float(k[0]) < t1} | {t0, t1})
    out = []
    for t in ts:
        for k in keys:
            if abs(float(k[0]) - t) < 1e-9:
                out.append([round(t - t0, 4), float(k[1])])
        if not any(abs(float(k[0]) - t) < 1e-9 for k in keys):
            out.append([round(t - t0, 4), float(_sample(keys, np.array([t]))[0])])
    return out


def _shift_cond(cond: dict, t0: float) -> dict:
    return {**cond, "curves": [[[round(float(t) - t0, 4), w] for t, w in c] for c in cond.get("curves") or []]}


def _stitch_xf(a: np.ndarray, b: np.ndarray, at: int, xf: int) -> np.ndarray:
    """a up to `at`, then b (b's sample 0 = a's sample at - xf), equal-power crossfade of xf samples."""
    xf = max(1, min(xf, at, b.shape[1]))
    t = np.linspace(0, np.pi / 2, xf, dtype=np.float32)
    head = a[:, :at - xf]
    mid = a[:, at - xf:at] * np.cos(t) + b[:, :xf] * np.sin(t)
    return np.concatenate([head, mid, b[:, xf:]], axis=1)


def submit_windows(proj: dict, b: dict, url: str, outdir: str | None, window: int, log, seeds) -> list[Path]:
    """Chained windows: window 1 is the project's [0, max]; each next window's reference is the
    previous take's last `context` s (kept, strength 0) + the project's reference after it (its own
    strength), conditioned on the project's curves shifted in time. Per seed the takes are
    stitched in the middle of each context (equal-power, 0.5 s) into one take with its own sidecar
    (`gen.composite.windows`: the window takes and their spans) and a bank (condsrc.copy_bank:
    window 1's tensors, the project's curves over the whole timeline). No global coherence across
    windows: each sees only its context."""
    from gradio_client import Client
    mlx = AI_AUDIO / "stable-audio-3" / "optimized" / "mlx"
    for q in (str(mlx), str(mlx / "scripts")):             # hispec needs upstream's spec.py (CLI runs too)
        if q not in sys.path:
            sys.path.append(q)
    import hispec
    wcfg = proj.get("windows") or {}
    wmax, ctx = float(wcfg.get("max", MAX_LEN)), float(wcfg.get("context", 60.0))
    outdir = outdir or f"composer/{proj['name']}"
    out_dir = OUTPUTS / outdir
    wdir = out_dir / "windows"
    wdir.mkdir(parents=True, exist_ok=True)
    plan = window_plan(b["length"], wmax, ctx)
    log(f"{len(plan)} windows: " + ", ".join(f"{a:.0f}-{z:.0f}s" for a, z in plan))
    model = DEFAULT_MODEL | (proj.get("model") or {})
    seeds = seeds or proj.get("seeds") or [0]
    c = Client(url, verbose=False)
    n_api = _api_n(c)
    ref_all = decode(b["ref"])
    name, box = next(iter(b["boxes"].items()))
    prev: dict = {}
    parts: dict = {s: [] for s in seeds}
    stage_dir = Path(proj["_dir"]) / "windows"
    stage_dir.mkdir(exist_ok=True)
    for wi, (t0, t1) in enumerate(plan):
        jobs, refs = [], []
        for seed in seeds:
            r = ref_all[:, int(t0 * SR):int(t1 * SR)].copy()
            keys = _shift_keys(box["strength"], t0, t1)
            if wi:
                pt = prev[seed]
                cy = decode(pt)
                c0 = int((t0 - plan[wi - 1][0]) * SR)
                r[:, :int(ctx * SR)] = cy[:, c0:c0 + int(ctx * SR)][:, :r.shape[1]]
                keys = [[0.0, 0.0], [ctx, 0.0]] + [[t, u] for t, u in keys if t > ctx]
                keys.insert(2, [ctx, float(_sample(_shift_keys(box["strength"], t0, t1), np.array([ctx + 1e-4]))[0])])
            rp = stage_dir / f"w{wi}.s{seed}.wav"
            write_wav(rp, r)
            wbox = {**box, "strength": keys, "cond": _shift_cond(box["cond"], t0),
                    "composite": {**box["composite"], "variant": f"{name} w{wi + 1}/{len(plan)}",
                                  "window": [t0, t1], "window_of": proj["name"]}}
            wbox["composite"]["project"] = f"{proj['name']}~w"
            jobs.append((generate_args(n_api, f"composer {proj['name']} window {wi + 1}", t1 - t0, seed, model,
                                       str(rp), f"{outdir}/windows", wbox), f"window {wi + 1} seed {seed}"))
            refs.append(rp)
        since = time.time() - 1
        _run_jobs(c, n_api, jobs, window, log)
        landed = _landed(wdir, f"{proj['name']}~w", since)
        for sc in landed:
            d = json.loads(sc.read_text())
            s = int(d["gen"]["seed"])
            if s in parts and (d["gen"]["composite"].get("window") == [t0, t1]):
                prev[s] = sc.with_suffix("")
                parts[s].append((sc.with_suffix(""), t0, t1))
        for rp in refs:
            rp.unlink(missing_ok=True)
    takes = []
    for seed, ps in parts.items():
        if len(ps) != len(plan):
            log(f"FAIL seed {seed}: {len(ps)} of {len(plan)} windows landed")
            continue
        y = decode(ps[0][0])[:, :int(round((ps[0][2] - ps[0][1]) * SR))]
        for (p, a, z) in ps[1:]:
            nxt = decode(p)
            at = int(round((a + ctx / 2) * SR))
            y = _stitch_xf(y, nxt[:, int(round(ctx / 2 * SR)) - int(0.5 * SR):], at, int(0.5 * SR))
        y = y[:, :int(round(b["length"] * SR))]
        stem = f"composer {proj['name']} {name} windows.{seed}.{hashlib.sha1(str(time.time()).encode()).hexdigest()[:8]}"
        wav = out_dir / f"{stem}.wav"
        mp3 = out_dir / f"{stem}.mp3"
        write_wav(wav, y)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-q:a", "0", str(mp3)], check=True)
        wav.unlink()
        condsrc.copy_bank(ps[0][0], mp3, box["cond"]["curves"])   # window 1 resolved every anchor
        import base64
        pcm = (np.clip(y, -1, 1) * 32767).astype(np.int16).T
        spec = base64.b64encode(hispec.render_spectrogram_png(pcm, SR)).decode("ascii")
        comp = {**box["composite"], "variant": f"{name} windows",
                "windows": [{"id": render_id(p), "t0": a, "t1": z} for p, a, z in ps],
                "window_context": ctx}
        side = {"name": mp3.name, "ts": time.time(), "prompt": f"composer {proj['name']} windows",
                "spec_b64": spec, "gen": {"seed": seed, "seconds": b["length"], "dit": model["dit"],
                                          "composite": comp, "segments": {"strength": box["strength"],
                                                                          "cond": box["cond"]}}}
        (out_dir / f"{mp3.name}.json").write_text(json.dumps(side))
        takes.append(out_dir / f"{mp3.name}.json")
        log(f"stitched seed {seed} → outputs/{outdir}/{mp3.name}")
    return takes


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["build", "submit"])
    ap.add_argument("project")
    ap.add_argument("--url", default="http://127.0.0.1:7860/")
    ap.add_argument("--outdir", default=None, help="folder under outputs/ (default composer/<name>)")
    ap.add_argument("--window", type=int, default=4)
    ap.add_argument("--variants", default=None, help="comma list: submit only these variants")
    a = ap.parse_args()
    proj = load(a.project)
    if a.cmd == "build":
        b = build(proj)
        lin = b["lineage"]
        print(f"ref {b['ref']}  {b['length']:.3f} s  bpm {lin['bpm']}  meter {lin['meter']}")
        for pc in lin["pieces"]:
            print(f"  piece at {pc['at']:7.3f}  dur {pc['dur']:6.3f}  track {pc.get('track', 0)}  bpm {pc['bpm']}"
                  f" conf {pc['conf']}  stretch {pc['stretch']}  {pc['id'][:70]}")
        print("  strength", b["keys"])
        for n, bx in b["boxes"].items():
            print(f"  variant {n}: {len(bx['cond']['anchors'])} anchors")
        return
    only = set(a.variants.split(",")) if a.variants else None
    submit(proj, a.url, a.outdir, a.window, only, log=lambda m: print(m, flush=True))


if __name__ == "__main__":
    main()
