"""Arranger template → rough stereo WAV (+ control curves) for SA3 audio-to-audio.

    stable-audio-3/optimized/mlx/.venv/bin/python sa3lab/arranger/render.py \
        (template.json | --preset melodic-build-drop) [--stems]
    → prints the WAV path

**The template JSON is the source of truth**; the WAV is a pure function of it and the
renderer's code, so its name carries a hash of both (canonical JSON + CODE_ID): `<slug>_<bpm>bpm_<key><scale>_<hash10>.wav`
under `outputs/arranger/<slug>/renders/`, next to
- `<same>.json`: the frozen template, the resolved events (compose.resolve), the WAV's
  sha256 and timing, so a render reproduces even after a generator changes;
- `<same>.controls.npz`: per-frame curves at SA3's latent rate (44100/4096 ≈ 10.77 Hz, one
  frame per SAME-L latent frame): `chroma` (T,12, from the note events, level-weighted),
  `bands_db` (T,4: <200, 200–1500, 1500–6000, >6000 Hz of the mix), `onsets` (T,3 kick/
  snare/hats velocity sums), `activity` (T,7 per-lane RMS), `lead_pitch` (T, MIDI or −1),
  `section` (T, index). These are the future targets for probe guidance / trained melody
  control (docs/TODO/sa3-sound-level-control.md, handles 8/9);
- `stems/<lane>.wav` only with --stems (disk is tight).
`outputs/arranger/index.json` maps each WAV's sha256 → its template + hash; the studio's
arranger add-on (sa3lab/arranger_link.py) reads it to stamp `gen.arranger` on renders whose
a2a input is an arranger WAV (gradio hands the studio a temp copy, so the hash is the link).

Template schema (v1; presets.py has full examples; missing keys take LANE_DEFAULTS):
    {"schema": 1, "name": str, "bpm": 120, "key": "A", "scale": "minor", "swing": 0..0.5,
     "humanize": 0..1, "seed": int, "prompt": str,
     "chords": {"degrees": [1, 6, 3, 7], "bars": 2, "sevenths": false},
     "sections": [{"name", "bars", "hint": str, "mute": ["all"|"drums"|"music"|lane…]}],
     "lanes": {lane: {"sound", "gain_db", "pan", "reverb", "duck", "octave", "res",
                      "clips": [{"start": bar, "bars": n, "gen": str, "seed": int|null,
                                 "level": curve, "density": curve, "cutoff": curve,
                                 "humanize": null|0..1, "params": {...}}]}},
     "master": {"leveler": 0..1 (slow 2:1-ish loudness leveler, see _leveler)},
     "sa3": {"init_noise": 0.8, "ladder": [0.55, 0.7, 0.8, 0.9]}}
A curve is a number or a list of evenly spaced breakpoints across the clip ([a, b] = ramp).
Generators and their params: compose.py. Section `hint`s are real conditioning when sent to SA3:
each section's anchor is the send's prompt + its hint, crossfading over a beat at the boundaries
(server.py section_cond → the box's "cond", condmix.py); the joined hints are still offered as the prompt.
Length = sum of section bars, padded up to a whole second (SA3's Length is an integer).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
import time
import unicodedata
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import compose  # noqa: E402
import synth  # noqa: E402
from synth import SR  # noqa: E402

RENDER_V = 1
# the hash covers the renderer's own code too, so editing a generator or a synth voice can
# never serve a stale cached WAV for an unchanged template
CODE_ID = hashlib.sha256(b"".join((HERE / f).read_bytes() for f in ("compose.py", "synth.py", "render.py"))).hexdigest()[:8]
OUT = HERE.parents[1] / "outputs" / "arranger"
FRAME = 4096                      # SAME-L latent hop: 44100 / 4096 ≈ 10.77 frames/s
LANE_DEFAULTS = {
    "kick": {"sound": "punch", "gain_db": 0.0, "pan": 0.0, "reverb": 0.03, "duck": 0.0},
    "snare": {"sound": "clap", "gain_db": -4.0, "pan": 0.0, "reverb": 0.18, "duck": 0.0},
    "hats": {"sound": "closed", "gain_db": -10.0, "pan": 0.15, "reverb": 0.06, "duck": 0.0},
    "bass": {"sound": "saw", "gain_db": -5.0, "pan": 0.0, "reverb": 0.0, "duck": 0.6, "octave": 1, "cutoff": 0.4},
    "chords": {"sound": "pad", "gain_db": -9.0, "pan": 0.0, "reverb": 0.35, "duck": 0.4, "octave": 4,
               "cutoff": 0.6, "res": 0.25},
    "lead": {"sound": "saw", "gain_db": -7.0, "pan": 0.0, "reverb": 0.28, "duck": 0.15, "octave": 4, "cutoff": 0.75},
    "fx": {"sound": "noise", "gain_db": -9.0, "pan": 0.0, "reverb": 0.4, "duck": 0.0},
}
GROUPS = {"drums": {"kick", "snare", "hats"}, "music": {"bass", "chords", "lead"}, "all": set(compose.LANES)}


# ── template plumbing ──────────────────────────────────────────────────────
def normalize(t: dict) -> dict:
    """A copy with every default filled in (what the renderer and UI actually use)."""
    t = copy.deepcopy(t)
    t.setdefault("schema", 1)
    t.setdefault("name", "untitled")
    t.setdefault("bpm", 120)
    t.setdefault("key", "A")
    t.setdefault("scale", "minor")
    t.setdefault("swing", 0.0)
    t.setdefault("humanize", 0.0)
    t.setdefault("seed", 0)
    t.setdefault("prompt", "")
    t.setdefault("chords", {"degrees": [1, 6, 3, 7], "bars": 2, "sevenths": False})
    t.setdefault("sections", [{"name": "A", "bars": 16, "hint": "", "mute": []}])
    for s in t["sections"]:
        s.setdefault("hint", "")
        s.setdefault("mute", [])
    lanes = t.setdefault("lanes", {})
    for lane in compose.LANES:
        cfg = lanes.setdefault(lane, {})
        for k, v in LANE_DEFAULTS[lane].items():
            cfg.setdefault(k, v)
        cfg.setdefault("clips", [])
    t.setdefault("master", {"leveler": 0.5})
    t.setdefault("sa3", {"init_noise": 0.8, "ladder": [0.55, 0.7, 0.8, 0.9]})
    return t


def slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "untitled"


def _canon(x):
    """1.0 → 1 everywhere, so Python- and browser-written templates hash alike (JS has no 1.0)."""
    if isinstance(x, float) and x.is_integer():
        return int(x)
    if isinstance(x, dict):
        return {k: _canon(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_canon(v) for v in x]
    return x


def template_hash(t: dict) -> str:
    canon = json.dumps({"t": _canon(normalize(t)), "v": RENDER_V, "code": CODE_ID}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()[:10]


def render_path(t: dict) -> Path:
    t = normalize(t)
    sl = slug(t["name"])
    key = compose.NOTE_NAMES[compose.key_pc(t["key"])].replace("#", "s")
    sc = {"minor": "m", "major": "", "harmonic_minor": "hm"}.get(t["scale"], t["scale"][:3])
    return OUT / sl / "renders" / f"{sl}_{int(round(float(t['bpm'])))}bpm_{key}{sc}_{template_hash(t)}.wav"


def seconds_of(t: dict) -> float:
    """Template length in seconds, as rendered (bars padded up to a whole second)."""
    t = normalize(t)
    return float(math.ceil(compose.Ctx(t).total_bars * compose.Ctx(t).bar - 1e-6))


def curve_arr(spec, x: np.ndarray) -> np.ndarray:
    """compose.curve over an array of x ∈ [0,1] (values held outside)."""
    if spec is None:
        return np.ones_like(x, dtype=np.float32)
    if isinstance(spec, (int, float)):
        return np.full(x.shape, float(spec), np.float32)
    pts = np.asarray(spec, dtype=np.float32)
    if len(pts) == 1:
        return np.full(x.shape, pts[0], np.float32)
    return np.interp(np.clip(x, 0, 1), np.linspace(0, 1, len(pts)), pts).astype(np.float32)


def _mute_sets(t: dict) -> dict[str, list[tuple[float, float]]]:
    """lane → [(start_s, end_s)] muted spans, from the sections' `mute` lists."""
    ctx = compose.Ctx(t)
    out = {lane: [] for lane in compose.LANES}
    for b0, b1, s in compose.section_bounds(t):
        lanes = set()
        for m in s.get("mute") or []:
            lanes |= GROUPS.get(m, {m})
        for lane in lanes & set(compose.LANES):
            out[lane].append((b0 * ctx.bar, b1 * ctx.bar))
    return out


def _gate(n: int, spans, ramp_s: float = 0.015) -> np.ndarray:
    g = np.ones(n, np.float32)
    r = max(1, int(ramp_s * SR))
    for a, b in spans:
        i0, i1 = int(a * SR), min(n, int(b * SR))
        g[i0:i1] = 0.0
        lo = max(0, i0 - r)
        g[lo:i0] = np.minimum(g[lo:i0], np.linspace(1, 0, i0 - lo))
        hi = min(n, i1 + r)
        g[i1:hi] = np.minimum(g[i1:hi], np.linspace(0, 1, hi - i1))
    return g


def _muted(tm: float, spans) -> bool:
    return any(a <= tm < b for a, b in spans)


# ── rendering ──────────────────────────────────────────────────────────────
def _render_clip(lane: str, cfg: dict, clip: dict, events: list[dict], ctx, kit, n_total: int,
                 rng: np.random.Generator) -> tuple[int, np.ndarray]:
    """One clip → (start sample, (2, n) buffer) incl. tails, cutoff + level automation applied."""
    c0, dur = float(clip["start"]) * ctx.bar, float(clip["bars"]) * ctx.bar
    tail = 3.0 if lane != "fx" else 3.5
    i0 = int(c0 * SR)
    n = max(1, min(n_total - i0, int((dur + tail) * SR)))
    buf = np.zeros((2, n), np.float32)
    p = clip.get("params") or {}
    if lane in compose.DRUM_LANES:
        mono = np.zeros(n, np.float32)
        for k, e in enumerate(events):
            kind = e.get("kind") or cfg["sound"]
            shots = kit.get(kind) or kit["closed"]
            s = shots[(int(e["t"] * 1000) + k) % len(shots)]
            j = int(e["t"] * SR) - i0
            if j < 0 or j >= n:
                continue
            m = min(len(s), n - j)
            mono[j:j + m] += s[:m] * (e["v"] ** 1.5)
        buf = synth.pan2(mono, float(cfg["pan"]))
    elif lane in compose.TONAL_LANES:
        sound = f"{lane}:{p.get('sound') or cfg['sound']}"
        for e in events:
            x = synth.tonal_note(sound, e["p"], e["d"], e["v"], vibrato=1.0 if lane == "lead" else 0.0)
            j = int(e["t"] * SR) - i0
            if j < 0 or j >= n:
                continue
            m = min(x.shape[1], n - j)
            buf[:, j:j + m] += x[:, :m]
        pan = float(cfg["pan"])
        if pan:
            buf[0] *= min(1.0, 1.0 - pan)
            buf[1] *= min(1.0, 1.0 + pan)
    else:
        shape = (lambda u: curve_arr(clip["cutoff"], np.asarray(u))) if clip.get("cutoff") is not None else None
        for e in events:
            x = synth.fx(e["kind"], e["d"], rng, shape=shape, root_midi=48 + ctx.root)
            j = int(e["t"] * SR) - i0
            m = min(x.shape[1], n - j)
            if m > 0 and j >= 0:
                buf[:, j:j + m] += x[:, :m]
    rel = lambda ts: (ts - 0.0) / dur        # seconds from clip start → position in the clip
    if lane != "fx":
        spec = clip.get("cutoff", cfg.get("cutoff"))
        const = spec is None or (isinstance(spec, (int, float)) and spec >= 0.999)
        if not const:
            res = float(cfg.get("res", 0.0))
            buf = synth.stft_filter(buf, synth.lowpass_gains(
                lambda ts: synth.cutoff_hz(curve_arr(spec, rel(ts))), res=res))
    t = np.arange(n) / SR
    buf *= curve_arr(clip.get("level"), rel(t))[None]
    return i0, buf


def render(t: dict, stems: bool = False, out: Path | None = None) -> dict:
    """Render a template; returns {"wav", "json", "controls", "hash", "seconds", "sha256", "cached"}."""
    t = normalize(t)
    wav = out or render_path(t)
    meta_p, ctl_p = wav.with_suffix(".json"), wav.with_suffix(".controls.npz")
    if wav.is_file() and meta_p.is_file() and (not stems or (wav.parent / "stems" / wav.stem).is_dir()):
        m = json.loads(meta_p.read_text())
        return {"wav": str(wav), "json": str(meta_p), "controls": str(ctl_p), "hash": m["hash"],
                "seconds": m["seconds"], "sha256": m["sha256"], "cached": True}
    t0 = time.time()
    ctx = compose.Ctx(t)
    res = compose.resolve(t)
    seconds = seconds_of(t)
    N = int(seconds * SR)
    seed = int(t["seed"])
    kit = synth.drum_kit(seed)
    mutes = _mute_sets(t)
    lane_bufs: dict[str, np.ndarray] = {}
    for li, lane in enumerate(compose.LANES):
        cfg = t["lanes"][lane]
        lb = np.zeros((2, N), np.float32)
        evs = res["lanes"][lane]
        for i, clip in enumerate(cfg["clips"]):
            ce = [e for e in evs if e["clip"] == i and not _muted(e["t"], mutes[lane])]
            if not ce:
                continue
            rng = np.random.default_rng([seed, li, i])
            i0, buf = _render_clip(lane, cfg, clip, ce, ctx, kit, N, rng)
            lb[:, i0:i0 + buf.shape[1]] += buf[:, : N - i0]
        if mutes[lane]:
            lb *= _gate(N, mutes[lane])[None]
        lane_bufs[lane] = lb * (10 ** (float(cfg["gain_db"]) / 20))
    # sidechain: lanes with duck > 0 dip under every (unmuted) kick
    kicks = [e for e in res["lanes"]["kick"] if not _muted(e["t"], mutes["kick"])]
    if kicks:
        env = np.zeros(N, np.float32)
        L = int(min(0.42 * ctx.beat * 2, 0.4) * SR)
        shape = np.exp(-np.arange(L) / (0.12 * SR)).astype(np.float32)
        att = int(0.004 * SR)
        shape[:att] = np.linspace(0, 1, att)
        for e in kicks:
            j = int(e["t"] * SR)
            m = min(L, N - j)
            if m > 0:
                env[j:j + m] = np.maximum(env[j:j + m], shape[:m] * min(1.0, e["v"]))
        for lane in compose.LANES:
            d = float(t["lanes"][lane]["duck"])
            if d > 0:
                lane_bufs[lane] *= (1 - d * env)[None]
    send = sum(lane_bufs[lane] * float(t["lanes"][lane]["reverb"]) for lane in compose.LANES)
    wet = synth.convolve(send, synth.reverb_ir(seed))
    mix = sum(lane_bufs.values()) + wet
    mix = mix * _leveler(mix, float((t.get("master") or {}).get("leveler", 0.5)))[None]
    peak = float(np.max(np.abs(mix))) or 1.0
    drive = 1.3
    scale = 1.0 / peak
    master = np.tanh(drive * mix * scale) / math.tanh(drive) * 0.89
    fade = int(0.05 * SR)
    master[:, -fade:] *= np.linspace(1, 0, fade)[None]
    wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(wav), master.T, SR, subtype="PCM_16")
    if stems:
        sd = wav.parent / "stems" / wav.stem
        sd.mkdir(parents=True, exist_ok=True)
        for lane, lb in lane_bufs.items():
            sf.write(str(sd / f"{lane}.wav"), np.clip(lb * scale * 0.89, -1, 1).T, SR, subtype="PCM_16")
    _write_controls(ctl_p, t, res, master, lane_bufs, N)
    sha = hashlib.sha256(wav.read_bytes()).hexdigest()
    h = template_hash(t)
    meta = {"schema": 1, "render_v": RENDER_V, "code_id": CODE_ID, "hash": h, "sha256": sha, "seconds": seconds,
            "bars": ctx.total_bars, "bar_s": ctx.bar, "rendered": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "render_ms": round((time.time() - t0) * 1000), "template": t, "resolved": res}
    meta_p.write_text(json.dumps(meta, indent=1))
    index_add(sha, {"slug": slug(t["name"]), "name": t["name"], "hash": h, "wav": str(wav),
                    "json": str(meta_p), "seconds": seconds})
    return {"wav": str(wav), "json": str(meta_p), "controls": str(ctl_p), "hash": h,
            "seconds": seconds, "sha256": sha, "cached": False, "render_ms": meta["render_ms"]}


def _leveler(mix: np.ndarray, amount: float, ref_db: float = -18.0) -> np.ndarray:
    """Slow master leveler: pulls each moment's loudness toward ref_db at ratio 1/(1-amount)
    (0.5 = 2:1), ±12 dB max, 0 dB below −50 dB so a near-silent break stays silent. A very
    quiet intro would otherwise read as silence to SA3's a2a pass; dynamics stay ordered."""
    n = mix.shape[1]
    if amount <= 0:
        return np.ones(n, np.float32)
    hop, win = int(0.1 * SR), int(0.4 * SR)
    m = mix.mean(0)
    k = int(math.ceil(n / hop))
    p = np.pad(m, (win // 2, win + k * hop - n))
    e = np.lib.stride_tricks.sliding_window_view(p ** 2, win)[::hop][:k].mean(1)
    L = 10 * np.log10(e + 1e-12)
    peak = L.max()
    L = L - peak + (-12.0)                      # treat the loudest moment as −12 dB
    g = np.clip((ref_db - L) * min(amount, 0.9), -12, 12)
    g *= np.clip((L + 60) / 10, 0, 1)            # fade to 0 dB between −50 and −60
    g = np.convolve(g, np.ones(5) / 5, mode="same")
    return (10 ** (np.interp(np.arange(n), np.arange(k) * hop, g) / 20)).astype(np.float32)


def _write_controls(path: Path, t: dict, res: dict, master: np.ndarray, lane_bufs: dict, N: int) -> None:
    T = int(math.ceil(N / FRAME))
    fr = SR / FRAME
    chroma = np.zeros((T, 12), np.float32)
    lead = np.full(T, -1.0, np.float32)
    for lane in ("bass", "chords", "lead"):
        g = 10 ** (float(t["lanes"][lane]["gain_db"]) / 20)
        for e in res["lanes"][lane]:
            a, b = int(e["t"] * fr), int(math.ceil((e["t"] + e["d"]) * fr))
            chroma[a:b, int(e["p"]) % 12] += e["v"] * g
            if lane == "lead":
                seg = lead[a:b]
                np.maximum(seg, e["p"], out=seg)
    s = chroma.sum(1, keepdims=True)
    chroma = np.where(s > 0, chroma / np.maximum(s, 1e-9), 0)
    onsets = np.zeros((T, 3), np.float32)
    for k, lane in enumerate(("kick", "snare", "hats")):
        for e in res["lanes"][lane]:
            i = int(e["t"] * fr)
            if i < T:
                onsets[i, k] += e["v"]
    mono = master.mean(0)
    pad = np.pad(mono, (0, T * FRAME - N + FRAME))
    win = np.hanning(2 * FRAME).astype(np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(np.pad(pad, (FRAME // 2, 0)), 2 * FRAME)[::FRAME][:T] * win
    P = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    f = np.fft.rfftfreq(2 * FRAME, 1 / SR)
    edges = [0, 200, 1500, 6000, SR / 2 + 1]
    bands = np.stack([P[:, (f >= lo) & (f < hi)].sum(1) for lo, hi in zip(edges, edges[1:])], 1)
    bands_db = 10 * np.log10(bands / (2 * FRAME) + 1e-10).astype(np.float32)
    act = np.zeros((T, len(compose.LANES)), np.float32)
    for k, lane in enumerate(compose.LANES):
        x = np.pad(lane_bufs[lane].mean(0), (0, T * FRAME - N))
        act[:, k] = np.sqrt((x.reshape(T, FRAME) ** 2).mean(1))
    sec = np.zeros(T, np.int16)
    ctx = compose.Ctx(t)
    for k, (b0, b1, _) in enumerate(compose.section_bounds(t)):
        sec[int(b0 * ctx.bar * fr): int(math.ceil(b1 * ctx.bar * fr))] = k
    np.savez_compressed(path, frame_rate=np.float32(fr), chroma=chroma.astype(np.float32), bands_db=bands_db,
                        band_edges_hz=np.asarray(edges, np.float32), onsets=onsets, activity=act,
                        lanes=np.asarray(compose.LANES), lead_pitch=lead, section=sec)


def index_add(sha: str, rec: dict) -> None:
    p = OUT / "index.json"
    try:
        idx = json.loads(p.read_text())
    except Exception:
        idx = {}
    idx[sha] = rec
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(idx, indent=1))
    tmp.replace(p)


def main() -> None:
    import presets
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("template", nargs="?")
    ap.add_argument("--preset", choices=list(presets.PRESETS))
    ap.add_argument("--stems", action="store_true")
    a = ap.parse_args()
    if a.preset:
        t = presets.PRESETS[a.preset]()
    elif a.template:
        t = json.loads(Path(a.template).read_text())
    else:
        ap.error("give a template.json or --preset")
    r = render(t, stems=a.stems)
    print(r["wav"])
    print(f"  {r['seconds']:g} s · hash {r['hash']} · {'cached' if r['cached'] else str(r['render_ms']) + ' ms'}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
