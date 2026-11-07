"""The render library: every studio render becomes a labelled dataset row on disk.

Each render in `outputs/sa3-gradio/` has a sidecar `<audio file>.json` (written by
persist.py). Together the sidecars ARE the raw dataset — one row per audio file:

    audio        the file next to the sidecar
    "gen"        everything needed to regenerate it bit-for-bit: models + resolved
                 checkpoint files, prompt, negative prompt, seed, seconds, steps,
                 cfg, apg, sigma_max, mode, LoRAs (path, sha256, strength, step
                 range), a2a / inpaint input audio (archived copy) + init noise
                 level / range, code commit, plus the measured timings. Keys that
                 don't apply (no LoRA, no a2a, no inpaint, empty negative prompt)
                 are left out, never written as null / []
    "label"      {"score": 0-10 | null, "tags": [str], "updated": iso time,
                 "deleted": iso time (only when the clip was trashed in the UI:
                 score forced to 0, audio + sidecar moved to deleted/),
                 "played": iso time of the first ≥2 s listen in the UI (unplayed
                 clips get a teal tint; see PLAYED_SINCE),
                 "starred": iso time, set by the star button ("keep for later, good or
                 bad": also symlinked into outputs/starred/, see star_render)}
    "analysis"   measured, not labelled: {"tempo": {bpm, conf, prompt_bpm, tier
                 green|yellow|orange|none|nobeat, fold, err}} (tempo.py), filled
                 in by a background thread the first time the UI asks for a label

Renders made before this module existed have no "gen" block, only upstream's
entry fields (prompt, seed, dit, cfg, sigma); their label still works.

This module also serves the browser side, as plain routes added to gradio's
FastAPI app (so a score click never waits behind a running render in the queue):

    GET  /sa3/label?name=<file>     → {"score", "tags", "bpm", "prompt", "loras", "played"}  (bpm may be {"pending"};
                                       loras: [{name, strength, steps}] of the LoRAs active in that render)
    POST /sa3/label  {name, score?, tags?, played?}   writes the sidecar's "label" at once
    GET  /sa3/settings?name=<file>  → restore_settings(): every setting that made the render
                                    (the settings button's hover card shows these)
    POST /sa3/star {name, starred}  star / unstar a render (star_render)
    POST /sa3/delete?name=<file>    trash a render: label score 0 + "deleted", then audio +
                                    sidecar move to OUT_DIR/deleted/ (out of the UI and the
                                    Library, kept on disk as a negative training example)
    GET  /sa3/progress              → live progress of the running render (+ jobs waiting)
    GET|POST /sa3/queue             → the render queue: list / delete / reorder / skip (renderq.py)
    GET  /sa3/boot                  → server boot id (the page reloads when it changes)
    GET  /sa3/library               → every render as a filterable row (Library modal)
    GET  /sa3/spec?name=  /sa3/audio?name=   its spectrogram jpeg / audio file
    POST /sa3/reveal {name | id}    show the render's file selected in Finder (`open -R`; the
                                    right-click "Show in Finder" on every clip, ui/reveal.js)

**Render ids.** `name` is a bare filename in the active folder (OUT_DIR), so the studio page is
untouched. Every route above that takes a render (label GET/POST, settings, star, spec, audio)
also takes `id` (query, or a JSON key on POSTs): the render's path relative to outputs/ with
symlinks resolved (render_id / resolve_id), so pages that show renders from many folders (the
composer, the starred set) address them without switching the view. Library rows carry `id`.

and the progress estimate: a render's expected duration is predicted from past
renders' measured timings (sampling cost per step × latent frame, doubled with
CFG ≠ 1; decode cost per latent frame; text encoder), then corrected live by the
sampler's real step count.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import queue
import re
import shutil
import statistics
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import Request  # module level: route annotations are resolved from globals
from fastapi.responses import FileResponse, JSONResponse, Response

AUDIO_EXT = {".mp3", ".wav"}
_lock = threading.Lock()
OUT_DIR: Path | None = None        # set by init()
_up = None                         # upstream sa3_gradio module
BOOT_ID = f"{os.getpid()}-{time.time():.0f}"   # changes on every server (re)start


def init(up) -> None:
    global OUT_DIR, _up
    _up = up
    OUT_DIR = Path(up.OUTPUT_DIR).resolve()
    migrate_saved()
    threading.Thread(target=_tempo_worker, name="sa3-tempo", daemon=True).start()


# ── render ids: one name for a render across every output folder ───────────
OUTPUTS = (Path(__file__).resolve().parents[1] / "outputs").resolve()


def render_id(path) -> str | None:
    """A render's id: its original's path relative to outputs/, symlinks resolved (a starred/
    link maps to the render it points at), e.g. `anarchy/session-3/foo.mp3`. None outside."""
    try:
        return str(Path(path).resolve().relative_to(OUTPUTS))
    except (ValueError, OSError):
        return None


def resolve_id(rid: str | None) -> Path | None:
    """Render id → its audio file. Sandboxed like outdir.py: inside outputs/, never deleted/
    or hidden folders, audio files only."""
    if not rid:
        return None
    p = (OUTPUTS / str(rid).lstrip("/")).resolve()
    try:
        parts = p.relative_to(OUTPUTS).parts
    except ValueError:
        return None
    if any(x == "deleted" or x.startswith(".") for x in parts[:-1]):
        return None
    if p.suffix.lower() not in AUDIO_EXT or not p.is_file():
        return None
    return p


# ── sidecar I/O ────────────────────────────────────────────────────────────
def _audio_path(name) -> Path | None:
    """A render's audio path from its bare filename, refusing anything outside OUT_DIR. A Path
    passes through: the routes resolve `?id=` (resolve_id) to one before calling in, so every
    function below works on renders in any folder ("name" keeps meaning the active folder)."""
    if isinstance(name, Path):
        return name
    name = os.path.basename(name or "")
    p = OUT_DIR / name
    if not name or p.suffix.lower() not in AUDIO_EXT or not p.is_file():
        return None
    return p


def _sidecar(p: Path) -> Path:
    """The render's sidecar. A starred/ entry is a symlink: resolve it, so label writes
    (atomic tmp + rename) land in the original's sidecar instead of replacing the link."""
    if p.is_symlink():
        p = p.resolve()
    return p.with_name(p.name + ".json")


def _lora_brief(d: dict) -> list[dict]:
    """The LoRAs active in a render (name, strength, step window), for the UI's LoRA badge."""
    return [{k: x.get(k) for k in ("name", "strength", "steps")}
            for x in (d.get("gen") or {}).get("loras") or []]


# "never played" tint in the studio: label.played is stamped the first time a clip plays
# for ≥2 s. Renders from before this existed (ts < PLAYED_SINCE) or already scored count
# as played, so only new renders light up.
PLAYED_SINCE = 1793916093


def _played(d: dict, ts) -> bool:
    lab = d.get("label") or {}
    return bool(lab.get("played")) or lab.get("score") is not None or (ts or 0) < PLAYED_SINCE


def read_label(name: str) -> dict | None:
    p = _audio_path(name)
    if p is None:
        return None
    try:
        d = json.loads(_sidecar(p).read_text())
    except Exception:
        d = {}
    lab = d.get("label") or {}
    return {"score": lab.get("score"), "tags": lab.get("tags") or [],
            "has_gen": "gen" in d, "bpm": _tempo_of(p, d),
            "played": _played(d, d.get("ts") or p.stat().st_mtime),
            "starred": bool(lab.get("starred")),
            "prompt": (d.get("gen") or {}).get("prompt", d.get("prompt")),
            "loras": _lora_brief(d)}


def restore_settings(name: str) -> dict | None:
    """The UI settings that produced a render, for the studio's "use these
    settings" button. From the "gen" block when present; renders made before it
    existed only carry model, prompt, negative prompt, seed, CFG and sigma_max
    (plus the clip length, read from the audio), so steps / LoRAs / APG are
    left as they are for those (keys absent)."""
    p = _audio_path(name)
    if p is None:
        return None
    try:
        d = json.loads(_sidecar(p).read_text())
    except Exception:
        d = {}
    g = d.get("gen")
    an = {"anarchy": d["anarchy"]} if d.get("anarchy") else {}   # latent renders (anarchy_ui.py)
    if g:
        def inp(x):              # archived under the view folder of its render time: try both
            rel = (x or {}).get("archived")
            hit = next((b / rel for b in (OUT_DIR, p.resolve().parent) if rel and (b / rel).is_file()), None)
            return str(hit) if hit else None
        return {k: g[k] for k in ("dit", "decoder", "prompt", "negative_prompt", "seed", "seconds",
                                  "steps", "cfg", "apg", "sigma_max", "mode", "inpaint_range_sec")
                if k in g} | {"negative_prompt": g.get("negative_prompt", ""), "loras": g.get("loras") or [], "a2a_audio": inp(g.get("a2a_audio")),
                             "inpaint_audio": inp(g.get("inpaint_audio")), "full": True} | an
    out = {"full": False, **an}
    for src, dst in (("dit", "dit"), ("prompt", "prompt"), ("neg", "negative_prompt"), ("seed", "seed"),
                     ("cfg", "cfg"), ("smx", "sigma_max"), ("mode", "mode")):
        if d.get(src) is not None:
            out[dst] = d[src]
    try:
        dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                              "csv=p=0", str(p)], capture_output=True, text=True, timeout=10).stdout
        out["seconds"] = round(float(dur))
    except Exception:
        pass
    return out


# ── auto BPM tag: measured tempo vs the prompt's (tempo.py) ────────────────
TEMPO_V = 2                        # bump to re-analyse every render (2: kink-free BPM, tempo.py step 3)
_tq: "queue.Queue[Path]" = queue.Queue()
_tq_seen: set[str] = set()


def _tempo_of(p: Path, d: dict) -> dict:
    """The sidecar's tempo analysis, or {"pending": True} after queueing it."""
    t = (d.get("analysis") or {}).get("tempo")
    if t and t.get("v") == TEMPO_V:
        return t
    if p.name not in _tq_seen:
        _tq_seen.add(p.name)
        _tq.put(p)
    return {"pending": True}


def _tempo_worker() -> None:
    import tempo
    while True:
        p = _tq.get()
        try:
            sc = _sidecar(p)
            try:
                d = json.loads(sc.read_text())
            except Exception:
                d = {}
            want = tempo.prompt_bpm((d.get("gen") or {}).get("prompt") or d.get("prompt"))
            r = tempo.estimate(str(p), want)
            t = {"v": TEMPO_V, **r, "prompt_bpm": want, **tempo.match(r["bpm"], want, r["conf"])}
        except Exception as e:
            t = {"v": TEMPO_V, "error": str(e)[:200]}
        with _lock:
            if not p.exists():                 # trashed while we measured: don't recreate its sidecar
                continue
            try:
                d = json.loads(sc.read_text())
            except Exception:
                d = {"name": p.name}
            d["analysis"] = {**(d.get("analysis") or {}), "tempo": t}
            tmp = sc.with_name(sc.name + ".tmp")
            tmp.write_text(json.dumps(d))
            tmp.replace(sc)


def write_label(name: str, score=..., tags=..., played=False) -> dict | None:
    """Update score and/or tags in the sidecar (read-modify-write, atomic rename);
    played=True stamps label.played (first play only) without touching label.updated."""
    p = _audio_path(name)
    if p is None:
        return None
    sc = _sidecar(p)
    with _lock:
        try:
            d = json.loads(sc.read_text())
        except Exception:
            d = {"name": p.name}
        lab = dict(d.get("label") or {})
        if score is not ...:
            lab["score"] = None if score is None else max(0, min(10, int(score)))
        if tags is not ...:
            seen, clean = set(), []
            for t in tags or []:
                t = re.sub(r"\s+", " ", str(t)).strip()[:60]
                if t and t.lower() not in seen:
                    seen.add(t.lower())
                    clean.append(t)
            lab["tags"] = clean
        if played and not lab.get("played"):
            lab["played"] = datetime.now().isoformat(timespec="seconds")
        if score is not ... or tags is not ...:
            lab["updated"] = datetime.now().isoformat(timespec="seconds")
        d["label"] = lab
        tmp = sc.with_name(sc.name + ".tmp")
        tmp.write_text(json.dumps(d))
        tmp.replace(sc)
    return {"score": lab.get("score"), "tags": lab.get("tags") or [], "played": True if played else _played(d, d.get("ts") or p.stat().st_mtime)}


STAR_DIR = Path(__file__).resolve().parents[1] / "outputs" / "starred"
_STAR_EXT = ("", ".json", ".cond.npz", ".conds.npz")   # audio, sidecar, latent conditioning (anarchy_ui.py),
                                                          # a multi-anchor render's bank (condsrc.py)


def _unstar_links(real: Path) -> None:
    """Remove starred/ symlinks that point at this real audio file."""
    for ext in _STAR_EXT:
        ln = STAR_DIR / (real.name + ext)
        if ln.is_symlink() and ln.resolve() == real.with_name(real.name + ext).resolve():
            ln.unlink()


def migrate_saved() -> None:
    """One-off upgrade, run at startup: the first star button (2026-11-05) stamped
    label.saved and put hardlinks + sidecar copies in outputs/saved/. Turn each into
    label.starred + outputs/starred/ symlinks and drop the saved/ twins. Safe to re-run."""
    old = STAR_DIR.parent / "saved"
    if not old.is_dir():
        return
    for sc in old.glob("*.json"):
        try:
            cp = json.loads(sc.read_text())
            audio = STAR_DIR.parent / cp["saved_from"] / sc.name[:-len(".json")]
            if not audio.is_file() or audio.stat().st_ino != (old / audio.name).stat().st_ino:
                continue                      # original gone or not the same file: leave it
            osc = audio.with_name(audio.name + ".json")
            d = json.loads(osc.read_text())
            lab = d.get("label") or {}
            lab["starred"] = lab.pop("saved", None) or (cp.get("label") or {}).get("saved")
            d["label"] = lab
            osc.write_text(json.dumps(d))
            STAR_DIR.mkdir(parents=True, exist_ok=True)
            for ext in _STAR_EXT:
                src, ln = audio.with_name(audio.name + ext), STAR_DIR / (audio.name + ext)
                if src.exists() and not ln.is_symlink():
                    ln.symlink_to(os.path.relpath(src, STAR_DIR))
                (old / (audio.name + ext)).unlink(missing_ok=True)
        except Exception as e:
            print(f"[sa3] saved/ migration skipped {sc.name}: {e}")
    try:
        old.rmdir()
    except OSError:
        pass


def star_render(name: str, on: bool) -> bool | None:
    """Star button: "keep this for later", independent of its score. Stamps label.starred
    in the original's sidecar and symlinks audio + sidecar (+ .cond.npz) into
    outputs/starred/, so `outputs/starred` is a folder the studio can be started in
    (folder chip) showing every starred clip from every folder. Being symlinks, scores and
    tags set in that view write through to the original (_sidecar resolves the link).
    Unstar clears the flag and removes the links; the original is never touched."""
    p = _audio_path(name)
    if p is None:
        return None
    real = p.resolve()
    sc = _sidecar(p)
    with _lock:
        try:
            d = json.loads(sc.read_text())
        except Exception:
            d = {"name": real.name}
        lab = dict(d.get("label") or {})
        if on:
            lab["starred"] = lab.get("starred") or datetime.now().isoformat(timespec="seconds")
        else:
            lab.pop("starred", None)
        d["label"] = lab
        tmp = sc.with_name(sc.name + ".tmp")
        tmp.write_text(json.dumps(d))
        tmp.replace(sc)
        if not on:
            _unstar_links(real)
        elif real.parent != STAR_DIR.resolve():
            STAR_DIR.mkdir(parents=True, exist_ok=True)
            for ext in _STAR_EXT:
                src, ln = real.with_name(real.name + ext), STAR_DIR / (real.name + ext)
                if src.exists() and not ln.exists() and not ln.is_symlink():
                    ln.symlink_to(os.path.relpath(src, STAR_DIR))
    _rows.pop(p.name, None)
    return on


def delete_render(name: str) -> bool:
    """Trash button: score the clip 0, stamp label.deleted, move audio + sidecar (+ its
    `.cond.npz` / `.conds.npz`) into OUT_DIR/deleted/. Reversible by moving the files back.
    condsrc.py reads a trashed render's conditioning from there, so projects that used it
    keep rendering."""
    p = _audio_path(name)
    if p is None:
        return False
    write_label(name, score=0)
    sc = _sidecar(p)
    p = p.resolve()                           # trashed from starred/: trash the original
    _unstar_links(p)                          # a trashed clip leaves the starred set
    dst = p.parent / "deleted"
    dst.mkdir(exist_ok=True)
    with _lock:
        try:
            d = json.loads(sc.read_text())
        except Exception:
            d = {"name": p.name}
        lab = {**(d.get("label") or {})}
        lab.pop("starred", None)
        d["label"] = {**lab, "deleted": datetime.now().isoformat(timespec="seconds")}
        sc.write_text(json.dumps(d))
        stem, i = p.name, 1
        while (dst / stem).exists():          # never overwrite an earlier trashed clip
            stem = f"{p.stem}.{i}{p.suffix}"
            i += 1
        shutil.move(str(sc), str(dst / (stem + ".json")))
        for ext in (".cond.npz", ".conds.npz"):           # a latent render's conditioning (anarchy_ui.py),
            side = p.with_name(p.name + ext)              # a multi-anchor render's bank (condsrc.py): kept
            if side.exists():                             # beside it, so takes that used it still resolve
                shutil.move(str(side), str(dst / (stem + ext)))
        shutil.move(str(p), str(dst / stem))
    _rows.pop(p.name, None)
    _rows.pop(name, None)
    return True


# ── regeneration metadata ──────────────────────────────────────────────────
_code_rev: dict | None = None


def _code() -> dict:
    """Commit of the stable-audio-3 fork + AI_Audio, and whether either tree is dirty."""
    global _code_rev
    if _code_rev is None:
        rev = {}
        mlx = Path(_up.REPO)
        for key, d in (("stable-audio-3", mlx), ("AI_Audio", mlx.parents[2])):
            try:
                sha = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"],
                                     capture_output=True, text=True, timeout=5).stdout.strip()
                dirty = bool(subprocess.run(["git", "-C", str(d), "status", "--porcelain",
                                             "--untracked-files=no"],
                                            capture_output=True, text=True, timeout=5).stdout.strip())
                rev[key] = {"commit": sha, "dirty": dirty}
            except Exception:
                pass
        _code_rev = rev
    return _code_rev


def _file_id(path: str | None) -> dict | None:
    """Resolved weight file (the HF-cache link target pins the snapshot)."""
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = Path(_up.REPO) / p
    return {"path": str(path), "resolved": os.path.realpath(p)}


_sha_cache: dict[tuple, str] = {}


def _sha256(path: str) -> str | None:
    try:
        st = os.stat(path)
        k = (path, st.st_size, st.st_mtime)
        if k not in _sha_cache:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            _sha_cache[k] = h.hexdigest()
        return _sha_cache[k]
    except Exception:
        return None


def _archive_input(path: str | None) -> dict | None:
    """Copy an a2a / inpaint input (a gradio temp file that will vanish) into
    OUT_DIR/inputs/<sha256-prefix><ext>, content-addressed so repeats dedupe."""
    if not path:
        return None
    sha = _sha256(path)
    if sha is None:
        return {"original": os.path.basename(path), "archived": None}
    dst = OUT_DIR / "inputs" / f"{sha[:16]}{Path(path).suffix.lower()}"
    try:
        if not dst.exists():
            dst.parent.mkdir(exist_ok=True)
            shutil.copy2(path, dst)
    except Exception:
        return {"original": os.path.basename(path), "sha256": sha, "archived": None}
    return {"original": os.path.basename(path), "sha256": sha,
            "archived": str(dst.relative_to(OUT_DIR))}


def gen_record(bound: inspect.BoundArguments, timings: dict | None) -> dict:
    """Build the "gen" block from run_generation's bound arguments."""
    a = bound.arguments
    dit = a["dit_name"]
    dec = a["decoder_name"]
    a2a, inp, rng = a.get("a2a_audio_path"), a.get("inpaint_audio_path"), a.get("inpaint_range_sec")
    mode = ("a2a+inpaint" if (a2a and rng) else "inpaint" if rng else
            "audio-to-audio" if a2a else "text-to-audio")
    loras = []
    for s in a.get("lora_specs") or []:
        loras.append({"name": os.path.basename(s["path"]), "path": s["path"], "sha256": _sha256(s["path"]),
                      "strength": s["strength"], "steps": list(s["steps"]) if s["steps"] else None})
    t = timings or {}
    g = {
        "schema": 1,
        "created": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "dit": dit,
        "dit_ckpt": _file_id(_up.DIT_CHOICES.get(dit, {}).get("ckpt")),
        "decoder": dec,
        "decoder_ckpt": _file_id((_up.DECODER_CHOICES.get(dec) or (None,) * 4)[3]),
        "text_encoder_ckpt": _file_id(getattr(_up, "T5GEMMA_NPZ_REL", None)),
        "prompt": a["prompt"],
        "negative_prompt": a["negative_prompt"],
        "seed": int(a["seed"]),
        "seconds": float(a["seconds"]),
        "steps": int(a["steps"]),
        "cfg": float(a["cfg"]),
        "apg": float(a["apg"]),
        "sigma_max": float(a["sigma_max"]),
        "sampler": "pingpong",
        "a2a_audio": _archive_input(a2a),
        "inpaint_audio": _archive_input(inp),
        "inpaint_range_sec": list(rng) if rng else None,
        "loras": loras,
        "sample_rate": getattr(_up, "SAMPLE_RATE", 44100),
        "code": _code(),
        "timings_ms": {k: round(float(t[k]), 1) for k in
                       ("t5_ms", "sample_ms", "decode_ms", "inference_ms", "dit_load_ms", "enc_ms")
                       if k in t} | ({"T_lat": int(t["T_lat"])} if "T_lat" in t else {}),
    }
    # only what applies: no LoRA / a2a / inpaint / negative-prompt keys on a plain render
    # (readers treat an absent key as "off"). On a2a renders sigma_max IS the guide's
    # init_noise_level, so it's also recorded under that name.
    if a2a:
        g["init_noise_level"] = g["sigma_max"]
    optional = ("negative_prompt", "a2a_audio", "inpaint_audio", "inpaint_range_sec", "loras")
    return {k: v for k, v in g.items() if k not in optional or v not in (None, [], "")}


# ── speed model + live progress ────────────────────────────────────────────
_TIMING_RE = re.compile(r"Inference</b>:\s*(\d+)\s*ms.*?t5=(\d+)\s*·\s*sample=(\d+)\s*·\s*decode=(\d+)"
                        r".*?seq_len</b>:\s*(\d+)", re.S)
_speed: dict = {"mtime": None, "rows": []}


def _speed_rows() -> list[dict]:
    """Past renders' per-unit costs, newest first (re-scanned when OUT_DIR changes)."""
    try:
        m = OUT_DIR.stat().st_mtime
    except Exception:
        return []
    if _speed["mtime"] == m:
        return _speed["rows"]
    rows = []
    for sc in OUT_DIR.glob("*.json"):
        try:
            d = json.loads(sc.read_text())
        except Exception:
            continue
        if not isinstance(d, dict):              # e.g. a jobs.json batch file, not a sidecar
            continue
        g = d.get("gen") or {}
        tm = g.get("timings_ms") or {}
        if tm.get("T_lat"):
            t5, smp, dec, T = tm.get("t5_ms", 0), tm.get("sample_ms", 0), tm.get("decode_ms", 0), tm["T_lat"]
            steps, cfg = g.get("steps", 8), g.get("cfg", 1.0)
        else:
            mm = _TIMING_RE.search(d.get("timing") or "")
            if not mm:
                continue
            _, t5, smp, dec, T = map(int, mm.groups())
            steps, cfg = 8, d.get("cfg", 1.0)      # pre-library renders: steps unknown, studio default
        passes = 2 if cfg != 1.0 else 1
        rows.append({"ts": d.get("ts", 0), "dit": g.get("dit") or d.get("dit") or "",
                     "T": T, "t5": t5, "smp_k": smp / max(1, steps * T * passes),
                     "dec_k": dec / max(1, T)})
    rows.sort(key=lambda r: -r["ts"])
    _speed.update(mtime=m, rows=rows[:60])
    return _speed["rows"]


def estimate_ms(dit: str, seconds: float, steps: int, cfg: float) -> dict:
    """Predicted {t5, sample, decode, total} ms for a render; defaults if no history."""
    sr = getattr(_up, "SAMPLE_RATE", 44100)
    spl = getattr(_up, "SAMPLES_PER_LATENT", 4096)
    T = max(1, -(-int(seconds * sr) // spl))
    rows = _speed_rows()
    same = [r for r in rows if r["dit"] == dit] or rows
    near = [r for r in same if 0.6 <= r["T"] / T <= 1.6] or same     # cost per frame drifts with length
    pick = near[:12]
    if pick:
        smp_k = statistics.median(r["smp_k"] for r in pick)
        dec_k = statistics.median(r["dec_k"] for r in pick)
        t5 = statistics.median(r["t5"] for r in pick)
    else:   # M2 MacBook Pro, medium + same-l, measured 2026-11-05
        smp_k, dec_k, t5 = 1.8, 10.3, 250.0
    smp = smp_k * steps * T * (2 if cfg != 1.0 else 1)
    dec = dec_k * T
    return {"t5": t5, "sample": smp, "decode": dec, "total": t5 + smp + dec, "n": len(pick)}


_prog: dict = {"active": False}


def progress_start(bound: inspect.BoundArguments) -> None:
    a = bound.arguments
    est = estimate_ms(a["dit_name"], float(a["seconds"]), int(a["steps"]), float(a["cfg"]))
    now = time.time()
    _prog.clear()
    _prog.update(active=True, t0=now, phase="text", step=0, steps=int(a["steps"]), est=est,
                 end=now + est["total"] / 1000, rev=0)


def _replan(end: float) -> None:
    """Move the projected finish time. Only called on real state transitions (a step
    finished, decode began), so the ETA counts down smoothly in between instead of
    being re-derived on every poll."""
    _prog["end"] = end
    _prog["rev"] = _prog.get("rev", 0) + 1


def progress_step(i: int) -> None:
    """Called before sampling step i, i.e. once i steps are finished."""
    if not _prog.get("active"):
        return
    now, est, n = time.time(), _prog["est"], max(1, _prog["steps"])
    if i == 0:
        _prog["sample_t0"] = now
        per = est["sample"] / 1000 / n
    else:   # measured step time; trust it more as steps accumulate
        meas = (now - _prog["sample_t0"]) / i
        w = min(1.0, i / 2)
        per = w * meas + (1 - w) * est["sample"] / 1000 / n
    _prog.update(phase="sampling", step=i)
    _replan(now + per * (n - i) + est["decode"] / 1000)


def progress_phase(phase: str) -> None:
    if _prog.get("active"):
        _prog["phase"] = phase
        if phase == "decode":
            _replan(time.time() + _prog["est"]["decode"] / 1000)


def progress_end() -> None:
    _prog["active"] = False


# batch queue: every Generate click is a job in gradio's queue (trigger_mode="multiple",
# studio.py); renderq.py reads that queue directly. studio.py points this at it.
waiting_fn = lambda: 0   # noqa: E731  (jobs still waiting behind the running one)


def jobs_waiting() -> int:
    try:
        return int(waiting_fn())
    except Exception:
        return 0


def progress() -> dict:
    """Projected finish time (moves only on transitions) + current phase. The client
    animates the bar and countdown locally from t0/end; `rev` tells it a replan happened."""
    if not _prog.get("active"):
        return {"active": False, "waiting": jobs_waiting()}
    now = time.time()
    return {"active": True, "run": _prog["t0"], "waiting": jobs_waiting(), "elapsed_s": round(now - _prog["t0"], 2),
            "left_s": round(max(0.0, _prog["end"] - now), 2), "rev": _prog["rev"],
            "phase": _prog["phase"], "step": _prog.get("step", 0), "steps": _prog.get("steps"),
            "history_n": _prog["est"]["n"]}


# ── library browser: every render as one filterable row (the studio's Library modal) ──
_rows: dict[str, tuple[float, dict]] = {}     # name -> (sidecar mtime, row)


def library_rows() -> list[dict]:
    """All renders in OUT_DIR, newest first: the fields the Library modal filters
    and shows. Cached per sidecar mtime (sidecars carry a ~400 KB spectrogram)."""
    rows = []
    for p in OUT_DIR.iterdir():
        if p.suffix.lower() not in AUDIO_EXT or not p.is_file():
            continue
        sc = _sidecar(p)
        try:
            mt = sc.stat().st_mtime
        except OSError:
            mt = -1.0
        hit = _rows.get(p.name)
        if hit and hit[0] == mt:
            rows.append(hit[1])
            continue
        try:
            d = json.loads(sc.read_text())
        except Exception:
            d = {}
        g, lab = d.get("gen") or {}, d.get("label") or {}
        row = {"name": p.name, "id": render_id(p), "ts": d.get("ts") or p.stat().st_mtime,
               "prompt": g.get("prompt", d.get("prompt")) or "",
               "dit": g.get("dit", d.get("dit")), "seed": g.get("seed", d.get("seed")),
               "seconds": g.get("seconds"), "steps": g.get("steps"), "cfg": g.get("cfg", d.get("cfg")),
               "mode": g.get("mode", d.get("mode")), "lora": bool(g.get("loras") or d.get("lora")), "loras": _lora_brief(d),
               "score": lab.get("score"), "tags": lab.get("tags") or [], "starred": bool(lab.get("starred")),
               "has_spec": bool(d.get("spec_b64")), "bpm": _tempo_of(p, d)}
        row["played"] = _played(d, row["ts"])
        if not row["bpm"].get("pending"):
            _rows[p.name] = (mt, row)
        rows.append(row)
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows


def spec_jpeg(name: str) -> bytes | None:
    p = _audio_path(name)
    if p is None:
        return None
    try:
        import base64
        b = json.loads(_sidecar(p).read_text()).get("spec_b64")
        return base64.b64decode(b) if b else None
    except Exception:
        return None


# ── routes ─────────────────────────────────────────────────────────────────
def install_routes() -> None:
    """Add /sa3/* routes to every gradio app gradio builds (ahead of its catch-alls)."""
    from gradio import routes

    def ref(name, rid):             # ?id= (any folder) wins over ?name= (the active folder)
        return resolve_id(rid) if rid else name

    async def label_get(name: str = "", id: str = ""):
        r = read_label(ref(name, id))
        return JSONResponse(r if r is not None else {"error": "unknown file"},
                            status_code=200 if r is not None else 404)

    async def label_post(request: Request):
        d = await request.json()
        kw = {}
        if "score" in d:
            kw["score"] = d["score"]
        if "tags" in d:
            kw["tags"] = d["tags"]
        if d.get("played"):
            kw["played"] = True
        r = write_label(ref(d.get("name", ""), d.get("id")), **kw)
        return JSONResponse(r if r is not None else {"error": "unknown file"},
                            status_code=200 if r is not None else 404)

    def settings_get(name: str = "", id: str = ""):   # sync: legacy renders probe the length with ffprobe
        r = restore_settings(ref(name, id))
        return JSONResponse(r if r is not None else {"error": "unknown file"},
                            status_code=200 if r is not None else 404)

    async def star_post(request: Request):     # POST /sa3/star {name, starred}
        d = await request.json()
        r = star_render(ref(d.get("name", ""), d.get("id")), bool(d.get("starred")))
        return JSONResponse({"starred": r} if r is not None else {"error": "unknown file"},
                            status_code=200 if r is not None else 404)

    def delete_post(name: str):      # POST /sa3/delete?name=…; sync: disk moves run in the threadpool
        ok = delete_render(name)
        return JSONResponse({"ok": ok}, status_code=200 if ok else 404)

    async def progress_get():
        return JSONResponse(progress())

    async def boot_get():            # studio.js reloads the page when this changes
        return JSONResponse({"id": BOOT_ID})

    def library_get():               # sync: runs in the threadpool (disk reads)
        return JSONResponse(library_rows())

    def spec_get(name: str = "", id: str = ""):
        b = spec_jpeg(ref(name, id))
        return (Response(b, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})
                if b else JSONResponse({"error": "no spectrogram"}, status_code=404))

    def audio_get(name: str = "", id: str = ""):
        p = _audio_path(ref(name, id))
        return FileResponse(p) if p else JSONResponse({"error": "unknown file"}, status_code=404)

    async def reveal_post(request: Request):   # POST /sa3/reveal {name | id}: the file selected in Finder
        import subprocess
        d = await request.json()
        p = _audio_path(ref(d.get("name", ""), d.get("id")))
        if not p or not p.is_file():
            return JSONResponse({"error": "unknown file"}, status_code=404)
        subprocess.Popen(["open", "-R", str(p.resolve())])   # a starred symlink reveals the real file
        return JSONResponse({"ok": True, "path": str(p.resolve())})

    orig = routes.App.create_app

    def create_app(*a, **kw):
        app = orig(*a, **kw)
        app.add_api_route("/sa3/reveal", reveal_post, methods=["POST"])
        app.add_api_route("/sa3/label", label_get, methods=["GET"])
        app.add_api_route("/sa3/label", label_post, methods=["POST"])
        app.add_api_route("/sa3/delete", delete_post, methods=["POST"])
        app.add_api_route("/sa3/star", star_post, methods=["POST"])
        app.add_api_route("/sa3/settings", settings_get, methods=["GET"])
        app.add_api_route("/sa3/progress", progress_get, methods=["GET"])
        app.add_api_route("/sa3/library", library_get, methods=["GET"])
        app.add_api_route("/sa3/boot", boot_get, methods=["GET"])
        app.add_api_route("/sa3/spec", spec_get, methods=["GET"])
        app.add_api_route("/sa3/audio", audio_get, methods=["GET"])
        ours = [r for r in app.router.routes if getattr(r, "path", "").startswith("/sa3/")]
        app.router.routes[:] = ours + [r for r in app.router.routes if r not in ours]
        return app
    routes.App.create_app = staticmethod(create_app)
