"""Music theory + seeded event generators: a template's clips → note/hit events.

Every clip names a generator (`gen`) for its lane. `resolve(template)` turns the whole
template into events in absolute seconds, one list per lane:
    {"t": s, "d": s, "p": midi | None, "v": 0..1, "clip": i, "kind": str}
`p` is None for drums/FX; `kind` picks the drum sound or FX type. Everything random draws
from `random.Random(seed)` keyed per clip, so the same template always resolves to the same
events (render.py stores the resolved events in each render's json for good measure).

Timing: one bar = 4 beats = 16 steps; `swing` (0..0.5) delays every off-16th by that
fraction of a step; `humanize` (0..1, template-wide, clip override) jitters time by up to
±18 ms·h and velocity by ±0.25·h.

Density (0..1, a clip curve): pattern generators rank every potential hit by metric weight
(downbeat > backbeat > 8th > 16th, seeded tie-break) and keep the top `density` fraction at
that moment, so density 0.3 on sixteenths thins to quarter/8th hats, not random holes.
Melody density sets how many rhythmic slots the motif fills.

Harmony: `template["chords"]` = {"degrees": [1, 6, 3, 7], "bars": 2, "sevenths": false}:
scale-degree triads, each held `bars` bars, looping over the whole song. bass / chords /
lead generators all read the chord at their time, so they always agree.
"""
from __future__ import annotations

import hashlib
import math
import random

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_ALIASES = {"DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#"}
SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "harmonic_minor": [0, 2, 3, 5, 7, 8, 11],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "phrygian": [0, 1, 3, 5, 7, 8, 10],
    "lydian": [0, 2, 4, 6, 7, 9, 11],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
}
LANES = ["kick", "snare", "hats", "bass", "chords", "lead", "fx"]
DRUM_LANES = {"kick", "snare", "hats"}
TONAL_LANES = {"bass", "chords", "lead"}
GENS = {   # lane → generator names (first = the "fill" default); UI reads this via /api/meta
    "kick": ["four_floor", "half", "breaks", "roll", "single", "steps"],
    "snare": ["backbeat", "halftime", "roll", "single", "steps"],
    "hats": ["offbeat", "eighths", "sixteenths", "shaker", "roll", "steps"],
    "bass": ["offbeat", "sustain", "eighths", "pulse", "steps"],
    "chords": ["pad", "stabs", "arp"],
    "lead": ["melody", "arp", "sustain"],
    "fx": ["riser", "downlifter", "impact", "sweep", "reverse"],
}
SOUNDS = {  # lane → sound variants (lane["sound"])
    "kick": ["punch", "boom", "soft"],
    "snare": ["clap", "snare", "both"],
    "hats": ["closed", "open", "shaker"],
    "bass": ["saw", "square", "sine"],
    "chords": ["pad", "saw", "pluck"],
    "lead": ["saw", "square", "pluck"],
    "fx": ["noise"],
}
# chord progressions by mode family (scale degrees, 1-based); progression() picks one
PROGRESSIONS = {
    "minor": [[1, 6, 3, 7], [1, 7, 6, 7], [1, 4, 6, 5], [6, 4, 1, 5], [1, 6, 4, 5], [1, 3, 7, 6],
              [1, 4, 7, 3], [6, 7, 1, 1], [1, 5, 6, 4], [4, 6, 1, 7]],
    "major": [[1, 5, 6, 4], [6, 4, 1, 5], [1, 4, 6, 5], [4, 1, 5, 6], [1, 6, 4, 5], [1, 3, 4, 4],
              [4, 5, 3, 6], [1, 5, 4, 4]],
}


def key_pc(key: str) -> int:
    """'A', 'f#', 'Bb' → pitch class (0 = C); unknown → A."""
    k = (key or "A").strip().replace("♯", "#").replace("♭", "b")
    k = k[:1].upper() + k[1:]
    k = _ALIASES.get(k.upper(), k) if len(k) > 1 and k[1] == "b" else k
    return NOTE_NAMES.index(k) if k in NOTE_NAMES else 9


def _family(scale: str) -> str:
    return "major" if scale in ("major", "lydian", "mixolydian") else "minor"


def progression(seed: int, scale: str) -> list[int]:
    """A seeded pick from PROGRESSIONS (plus an occasional rotation), for the UI's dice button."""
    r = random.Random(f"prog:{seed}:{scale}")
    p = list(r.choice(PROGRESSIONS[_family(scale)]))
    if r.random() < 0.3:
        k = r.randrange(1, 4)
        p = p[k:] + p[:k]
    return p


def roman(deg: int, scale: str, seventh: bool = False) -> str:
    """Scale degree → roman numeral with quality (i, VI, ii°)."""
    tri = chord_pcs(deg, 0, scale, False)
    third = (tri[1] - tri[0]) % 12
    fifth = (tri[2] - tri[0]) % 12
    num = ["I", "II", "III", "IV", "V", "VI", "VII"][(deg - 1) % 7]
    s = num if third == 4 else num.lower()
    if fifth == 6:
        s += "°"
    elif fifth == 8:
        s += "+"
    return s + ("7" if seventh else "")


def chord_pcs(deg: int, root_pc: int, scale: str, seventh: bool) -> list[int]:
    """Scale degree → pitch classes (root first), stacked in thirds inside the scale."""
    sc = SCALES.get(scale, SCALES["minor"])
    i = (deg - 1) % 7
    idx = [i, i + 2, i + 4] + ([i + 6] if seventh else [])
    return [(root_pc + sc[j % 7] + 12 * (j // 7)) for j in idx]


def scale_pitches(root_pc: int, scale: str, lo: int, hi: int) -> list[int]:
    sc = SCALES.get(scale, SCALES["minor"])
    return [m for m in range(lo, hi + 1) if (m - root_pc) % 12 in sc]


class Ctx:
    """Template-wide timing + harmony lookups for the generators."""

    def __init__(self, t: dict):
        self.t = t
        self.bpm = float(t.get("bpm", 120))
        self.beat = 60.0 / self.bpm
        self.bar = 4 * self.beat
        self.step = self.beat / 4
        self.swing = float(t.get("swing", 0) or 0)
        self.root = key_pc(t.get("key", "A"))
        self.scale = t.get("scale", "minor")
        ch = t.get("chords") or {}
        self.degrees = [int(d) for d in (ch.get("degrees") or [1, 6, 3, 7])] or [1]
        self.chord_bars = max(1, int(ch.get("bars", 1)))
        self.sevenths = bool(ch.get("sevenths", False))
        self.total_bars = sum(int(s["bars"]) for s in t.get("sections", [])) or 1

    def chord_at_bar(self, bar: float) -> int:
        return self.degrees[int(bar // self.chord_bars) % len(self.degrees)]

    def chord(self, bar: float) -> list[int]:
        return chord_pcs(self.chord_at_bar(bar), self.root, self.scale, self.sevenths)

    def step_time(self, bar0: float, step: int) -> float:
        """Absolute seconds of 16th `step` counted from bar `bar0`, with swing."""
        t = bar0 * self.bar + step * self.step
        if step % 2 == 1:
            t += self.swing * self.step
        return t


def curve(spec, x: float) -> float:
    """A clip automation value at x ∈ [0, 1] across the clip: a number (constant) or a list of
    evenly spaced breakpoints, linearly interpolated ([a, b] = ramp a → b)."""
    if spec is None:
        return 1.0
    if isinstance(spec, (int, float)):
        return float(spec)
    pts = [float(v) for v in spec]
    if len(pts) == 1:
        return pts[0]
    x = min(max(x, 0.0), 1.0) * (len(pts) - 1)
    i = min(int(x), len(pts) - 2)
    return pts[i] + (pts[i + 1] - pts[i]) * (x - i)


def clip_seed(t: dict, lane: str, i: int, clip: dict) -> int:
    if clip.get("seed") is not None:
        return int(clip["seed"])
    # default: one seed per lane (not per clip), so every clip of a lane shares its motif/feel
    # and moving, splitting or deleting clips never changes another clip's content
    h = hashlib.sha256(f"{t.get('seed', 0)}:{lane}".encode()).hexdigest()
    return int(h[:8], 16)


# metric weight per 16th step in a bar (higher = more important)
_W16 = [4, 0, 1, 0, 2.5, 0, 1, 0, 3, 0, 1, 0, 2.5, 0, 1, 0]


def _rank_keep(cands, density_fn, r: random.Random):
    """cands: [(t, weight, payload)] → those kept at the density curve's value at their time.
    Rank = weight + small seeded noise; keep if its rank percentile (within the clip) clears."""
    if not cands:
        return []
    scored = sorted(((w + r.random() * 0.5, k) for k, (t, w, _) in enumerate(cands)), reverse=True)
    pct = {k: (n + 0.5) / len(cands) for n, (_, k) in enumerate(scored)}
    return [c for k, c in enumerate(cands) if pct[k] <= density_fn(c[0]) + 1e-9]


def _steps_pattern(s: str) -> list[float]:
    """'x...x.o.' style: x = accent 1.0, o = 0.6, . or - = rest; repeated to 16 steps."""
    s = (s or "x...").replace(" ", "").replace("|", "")
    vals = [{"x": 1.0, "X": 1.0, "o": 0.6, "O": 0.6}.get(ch, 0.0) for ch in s]
    return [vals[i % len(vals)] for i in range(16)] if vals else [0.0] * 16


def _drum_pattern(gen: str, lane: str, params: dict) -> list[float]:
    """Velocity per 16th step (one bar) for the static pattern generators."""
    P = [0.0] * 16
    if gen == "steps":
        return _steps_pattern(params.get("steps", "x..."))
    if lane == "kick":
        if gen == "four_floor":
            for b in range(4):
                P[4 * b] = 1.0
        elif gen == "half":
            P[0], P[8] = 1.0, 0.9
        elif gen == "breaks":
            P[0], P[6], P[10], P[11] = 1.0, 0.7, 0.85, 0.5
    elif lane == "snare":
        if gen == "backbeat":
            P[4], P[12] = 1.0, 1.0
        elif gen == "halftime":
            P[8] = 1.0
    elif lane == "hats":
        if gen == "offbeat":
            for b in range(4):
                P[4 * b + 2] = 1.0
        elif gen == "eighths":
            for i in range(0, 16, 2):
                P[i] = 1.0 if i % 4 == 2 else 0.65
        elif gen in ("sixteenths", "shaker"):
            for i in range(16):
                P[i] = 1.0 if i % 4 == 2 else (0.7 if i % 2 == 0 else 0.45)
    return P


def _drum_events(ctx: Ctx, lane: str, clip: dict, r: random.Random) -> list[dict]:
    gen, p = clip.get("gen", GENS[lane][0]), clip.get("params") or {}
    s0, nb = float(clip["start"]), float(clip["bars"])
    kind = p.get("kind")          # per-clip sound override (else the lane's sound)
    dens = lambda t: curve(clip.get("density"), (t / ctx.bar - s0) / nb)
    if gen == "single":
        return [{"t": s0 * ctx.bar, "d": ctx.beat, "p": None, "v": 1.0, "kind": kind}]
    if gen == "roll":
        # accelerating hits: `from` → `to` hits per bar (exponential in rate), velocity ramps up
        a, b = float(p.get("from", 4)), float(p.get("to", 32))
        out, x = [], 0.0
        while x < nb - 1e-6:
            u = x / nb
            rate = a * (b / a) ** u           # hits per bar at this point
            out.append({"t": (s0 + x) * ctx.bar, "d": 1.0 / rate * ctx.bar, "p": None,
                        "v": 0.45 + 0.55 * u, "kind": kind})
            x += 1.0 / rate
        return [e for e in out if r.random() <= dens(e["t"]) + 1e-9 or e is out[0]]
    pat = _drum_pattern(gen, lane, p)
    cands = []
    for bar in range(int(math.ceil(nb))):
        for st in range(16):
            if pat[st] <= 0 or bar + st / 16 >= nb - 1e-9:
                continue
            t = ctx.step_time(s0 + bar, st)
            cands.append((t, _W16[st] + pat[st], pat[st]))
    kept = _rank_keep(cands, dens, r)
    return [{"t": t, "d": ctx.step, "p": None, "v": v, "kind": kind} for t, _, v in kept]


def _nearest(pitches, target):
    return min(pitches, key=lambda m: (abs(m - target), m))


def _bass_events(ctx: Ctx, clip: dict, r: random.Random, octave: int) -> list[dict]:
    gen, p = clip.get("gen", "offbeat"), clip.get("params") or {}
    s0, nb = float(clip["start"]), float(clip["bars"])
    base = 12 * (octave + 1)                  # MIDI of C in that octave (octave 1 → C1 = 24)
    out = []
    root_m = lambda bar: base + (ctx.chord(bar)[0] % 12)
    if gen == "sustain":
        bar = 0.0
        while bar < nb - 1e-9:
            span = min(ctx.chord_bars - ((s0 + bar) % ctx.chord_bars), nb - bar)
            out.append({"t": (s0 + bar) * ctx.bar, "d": span * ctx.bar * 0.98, "p": root_m(s0 + bar), "v": 0.9})
            bar += span
        return out
    if gen == "steps":
        pat = _steps_pattern(p.get("steps", "..x...x...x...x."))
    elif gen == "offbeat":
        pat = [0.0, 0, 1.0, 0] * 4
    elif gen == "eighths":
        pat = [1.0, 0, 0.75, 0] * 4
    else:                                     # pulse: rolling 16ths
        pat = [1.0, 0.6, 0.8, 0.6] * 4
    dens = lambda t: curve(clip.get("density"), (t / ctx.bar - s0) / nb)
    cands = []
    for bar in range(int(math.ceil(nb))):
        for st in range(16):
            if pat[st] <= 0 or bar + st / 16 >= nb - 1e-9:
                continue
            cands.append((ctx.step_time(s0 + bar, st), _W16[st] + pat[st], (pat[st], s0 + bar)))
    step_len = {"offbeat": 2, "eighths": 2}.get(gen, 1)
    for t, _, (v, b) in _rank_keep(cands, dens, r):
        m = root_m(b)
        if gen == "pulse" and r.random() < 0.15:
            m += 12
        out.append({"t": t, "d": ctx.step * step_len * 0.9, "p": m, "v": v})
    return out


def _voicing(ctx: Ctx, bar: float, center: int) -> list[int]:
    """Close voicing of the chord at `bar` around MIDI `center` (smooth voice leading)."""
    pcs = ctx.chord(bar)
    out = []
    for k, pc in enumerate(pcs):
        m = _nearest([pc % 12 + 12 * o for o in range(2, 9)], center + (k - 1) * 4)
        while m in out:
            m += 12
        out.append(m)
    return sorted(out)


def _chord_events(ctx: Ctx, clip: dict, r: random.Random, octave: int) -> list[dict]:
    gen, p = clip.get("gen", "pad"), clip.get("params") or {}
    s0, nb = float(clip["start"]), float(clip["bars"])
    center = 12 * (octave + 1) + 7
    out = []
    if gen == "pad":
        bar = 0.0
        while bar < nb - 1e-9:
            span = min(ctx.chord_bars - ((s0 + bar) % ctx.chord_bars), nb - bar)
            for m in _voicing(ctx, s0 + bar, center):
                out.append({"t": (s0 + bar) * ctx.bar, "d": span * ctx.bar, "p": m, "v": 0.8})
            bar += span
        return out
    dens = lambda t: curve(clip.get("density"), (t / ctx.bar - s0) / nb)
    if gen == "stabs":
        pat = _steps_pattern(p.get("steps", "x..x..x...x..x.."))
        cands = [(ctx.step_time(s0 + bar, st), _W16[st] + pat[st], (pat[st], s0 + bar))
                 for bar in range(int(math.ceil(nb))) for st in range(16)
                 if pat[st] > 0 and bar + st / 16 < nb - 1e-9]
        for t, _, (v, b) in _rank_keep(cands, dens, r):
            for m in _voicing(ctx, b, center):
                out.append({"t": t, "d": ctx.step * 1.6, "p": m, "v": v * 0.9})
        return out
    return _arp_events(ctx, clip, r, center)


def _arp_events(ctx: Ctx, clip: dict, r: random.Random, center: int) -> list[dict]:
    p = clip.get("params") or {}
    s0, nb = float(clip["start"]), float(clip["bars"])
    rate = int(p.get("rate", 16))                  # notes per bar (8 or 16)
    octs = int(p.get("octaves", 2))
    direction = p.get("dir", "up")
    dens = lambda t: curve(clip.get("density"), (t / ctx.bar - s0) / nb)
    cands = []
    n = int(round(nb * rate))
    for k in range(n):
        bar = s0 + k / rate
        notes = _voicing(ctx, bar, center)
        seq = [m + 12 * o for o in range(octs) for m in notes]
        if direction == "down":
            seq = seq[::-1]
        elif direction == "updown":
            seq = seq + seq[-2:0:-1]
        m = seq[k % len(seq)]
        st = int(round((k % rate) * 16 / rate))
        t = ctx.step_time(math.floor(bar + 1e-9), st) if rate in (4, 8, 16) else bar * ctx.bar
        cands.append((t, _W16[st % 16] + 1, m))
    return [{"t": t, "d": ctx.bar / rate * 0.85, "p": m, "v": 0.75} for t, _, m in _rank_keep(cands, dens, r)]


# rhythm motifs: (16th step offset within 2 bars, duration in 16ths)
_MOTIFS = [
    [(0, 3), (3, 3), (6, 2), (8, 4), (14, 2), (16, 3), (19, 3), (22, 2), (24, 8)],
    [(0, 4), (4, 2), (6, 2), (8, 6), (16, 4), (20, 2), (22, 2), (24, 4), (28, 4)],
    [(0, 2), (2, 2), (4, 4), (10, 2), (12, 4), (16, 2), (18, 2), (20, 4), (26, 6)],
    [(0, 6), (6, 2), (8, 8), (16, 6), (22, 2), (24, 8)],
    [(2, 2), (4, 2), (6, 4), (12, 4), (18, 2), (20, 2), (22, 4), (28, 4)],
    [(0, 3), (3, 3), (6, 4), (10, 2), (12, 4), (16, 3), (19, 3), (22, 4), (26, 2), (28, 4)],
]


def _melody_events(ctx: Ctx, clip: dict, r: random.Random, octave: int) -> list[dict]:
    """A motif-based lead that loops an 8-bar hook: one 2-bar rhythm motif (a B rhythm in the
    3rd phrase); strong-beat notes land on chord tones of the chord underneath, the rest move
    stepwise in the scale; the 4th phrase cadences on the chord root. Phrases sit on the song's
    absolute 2-bar grid and each phrase's choices are seeded by (seed, phrase mod 4), so the
    same seed gives the same hook anywhere in the song (the drop can restate the build's
    melody) and splitting or moving a clip keeps its notes. `params.vary` (0..1, default 0.2)
    re-rolls some weak-beat notes per absolute phrase so repeats aren't identical.
    Density thins the motif (fewer notes that ring longer)."""
    p = clip.get("params") or {}
    vary = float(p.get("vary", 0.2))
    seed = r.getrandbits(32)
    s0, nb = float(clip["start"]), float(clip["bars"])
    lo, hi = 12 * (octave + 1) - 3, 12 * (octave + 2) + 7
    scale = scale_pitches(ctx.root, ctx.scale, lo, hi)
    center = 12 * (octave + 1) + ctx.root + 7
    rm = random.Random(f"{seed}:motif")
    motif, alt = list(rm.choice(_MOTIFS)), list(rm.choice(_MOTIFS))
    out = []
    for ph in range(int(s0 // 2), int(math.ceil((s0 + nb) / 2))):
        q = ph % 4
        rq, rv = random.Random(f"{seed}:q{q}"), random.Random(f"{seed}:p{ph}")
        rhythm = alt if q == 2 else motif
        b0 = 2 * ph
        d = curve(clip.get("density"), (b0 + 1 - s0) / nb)
        contour = [rq.choice([-1, 1]) for _ in range(len(rhythm) + 1)]
        ch0 = ctx.chord(b0)
        prev = _nearest([m for m in scale if m % 12 in {c % 12 for c in ch0}], center + rq.choice([-3, 0, 2, 4]))
        pitches = []                    # pitch every motif note first, so density never changes the tune
        for k, (st, ln) in enumerate(rhythm):
            bar = b0 + st / 16
            chord = ctx.chord(bar)
            if k == len(rhythm) - 1 and q == 3:
                m = _nearest([m for m in scale if m % 12 == chord[0] % 12], prev)
            elif st % 8 == 0 or rq.random() < 0.3:
                m = _nearest([m for m in scale if m % 12 in {c % 12 for c in chord}],
                             prev + contour[k] * rq.choice([2, 3, 4, 5]))
            else:
                step = contour[k] * rq.choice([1, 1, 1, 2])
                i = scale.index(_nearest(scale, prev))
                m = scale[min(max(i + step, 0), len(scale) - 1)]
            prev = m                    # the hook's own line; `vary` below never feeds back into it
            if st % 8 != 0 and rv.random() < vary:
                i = scale.index(m)
                m = scale[min(max(i + rv.choice([-2, -1, 1, 2]), 0), len(scale) - 1)]
            pitches.append(m)
        keep = [k for k in range(len(rhythm)) if k == 0 or k == len(rhythm) - 1
                or ((k * 7919 + q * 31) % 100) / 100 < d + 0.15]
        for j, k in enumerate(keep):
            st, ln = rhythm[k]
            nxt = rhythm[keep[j + 1]][0] if j + 1 < len(keep) else 32
            ln = max(ln, nxt - st) if d < 0.6 else ln          # sparse: notes ring into the gaps
            bar = b0 + st / 16
            if bar < s0 - 1e-9 or bar >= s0 + nb - 1e-9:
                continue
            t = ctx.step_time(math.floor(bar + 1e-9), st % 16)
            dur = min(ln * ctx.step * 0.92, (s0 + nb) * ctx.bar - t)
            out.append({"t": t, "d": dur, "p": pitches[k], "v": 0.85 if st % 8 == 0 else 0.7})
    return out


def _fx_events(ctx: Ctx, clip: dict) -> list[dict]:
    gen = clip.get("gen", "riser")
    s0, nb = float(clip["start"]), float(clip["bars"])
    return [{"t": s0 * ctx.bar, "d": nb * ctx.bar, "p": None, "v": 1.0, "kind": gen}]


def clip_events(ctx: Ctx, lane: str, i: int, clip: dict, lane_cfg: dict) -> list[dict]:
    r = random.Random(clip_seed(ctx.t, lane, i, clip))
    octave = int(lane_cfg.get("octave", {"bass": 1, "chords": 4, "lead": 4}.get(lane, 4)))
    if lane in DRUM_LANES:
        ev = _drum_events(ctx, lane, clip, r)
    elif lane == "bass":
        ev = _bass_events(ctx, clip, r, octave)
    elif lane == "chords":
        ev = _chord_events(ctx, clip, r, octave)
    elif lane == "lead":
        g = clip.get("gen", "melody")
        if g == "arp":
            ev = _arp_events(ctx, clip, r, 12 * (octave + 1) + 7)
        elif g == "sustain":
            c = dict(clip, gen="pad")
            ev = [e for e in _chord_events(ctx, c, r, octave)]
            top = {}
            for e in ev:                       # top voice of each chord = a slow lead line
                if e["t"] not in top or e["p"] > top[e["t"]]["p"]:
                    top[e["t"]] = e
            ev = sorted(top.values(), key=lambda e: e["t"])
        else:
            ev = _melody_events(ctx, clip, r, octave)
    else:
        ev = _fx_events(ctx, clip)
    # humanize: seeded timing + velocity jitter (not on FX / sustained pads)
    h = clip.get("humanize")
    h = float(ctx.t.get("humanize", 0) if h is None else h)
    hr = random.Random(f"hum:{clip_seed(ctx.t, lane, i, clip)}")
    out = []
    for e in ev:
        e = dict(e, clip=i)
        e.setdefault("kind", None)
        if h > 0 and lane != "fx" and not (lane == "chords" and clip.get("gen") == "pad"):
            e["t"] = max(0.0, e["t"] + (hr.random() * 2 - 1) * 0.018 * h)
            e["v"] = min(1.0, max(0.05, e["v"] * (1 + (hr.random() * 2 - 1) * 0.25 * h)))
        out.append(e)
    return out


def section_bounds(t: dict) -> list[tuple[float, float, dict]]:
    """[(start_bar, end_bar, section)]."""
    out, b = [], 0.0
    for s in t.get("sections", []):
        out.append((b, b + int(s["bars"]), s))
        b += int(s["bars"])
    return out


def resolve(t: dict) -> dict:
    """The template → {"lanes": {lane: [events]}, "chords": [{bar, bars, deg, roman, name}],
    "seeds": {lane: [effective clip seed]}, timing}."""
    ctx = Ctx(t)
    lanes, seeds = {}, {}
    for lane in LANES:
        cfg = (t.get("lanes") or {}).get(lane) or {}
        ev = []
        for i, clip in enumerate(cfg.get("clips") or []):
            ev += clip_events(ctx, lane, i, clip, cfg)
        lanes[lane] = sorted(ev, key=lambda e: (e["t"], e["p"] or 0))
        seeds[lane] = [clip_seed(t, lane, i, c) for i, c in enumerate(cfg.get("clips") or [])]
    chords, bar = [], 0
    while bar < ctx.total_bars:
        span = min(ctx.chord_bars, ctx.total_bars - bar)
        deg = ctx.chord_at_bar(bar)
        pcs = chord_pcs(deg, ctx.root, ctx.scale, ctx.sevenths)
        chords.append({"bar": bar, "bars": span, "deg": deg,
                       "roman": roman(deg, ctx.scale, ctx.sevenths),
                       "name": NOTE_NAMES[pcs[0] % 12] + ("m" if (pcs[1] - pcs[0]) % 12 == 3 else "")
                       + ("°" if (pcs[2] - pcs[0]) % 12 == 6 else "")})
        bar += span
    return {"lanes": lanes, "chords": chords, "seeds": seeds, "bpm": ctx.bpm, "bar_s": ctx.bar,
            "bars": ctx.total_bars, "seconds": ctx.total_bars * ctx.bar}
