"""Render history that survives a page reload.

Upstream keeps history in a per-session `gr.State`, so every reload starts empty.
We persist each presented render as `<audio file>.json` (upstream's own entry
dict: prompt, seed, settings, timing line, spectrogram; library.py adds the
"gen" regen block and the "label" score/tags) next to the audio in
`output/gradio/` (= AI_Audio/outputs/sa3-gradio/), and on page load rebuild the
current clip + history from the newest files.

Renders made before this existed have no sidecar: their prompt and seed are
parsed back out of upstream's filename (`prompt[.neg-…][.cfgX][.smxY].seed.ext`)
and the spectrogram is recomputed from the audio. The sidecar is written then,
so this happens once per file.
"""
from __future__ import annotations

import base64
import html
import json
import re
from pathlib import Path

import numpy as np

AUDIO_EXT = {".mp3", ".wav"}
_seen: set[str] = set()


def sidecar(audio_path: str | Path) -> Path:
    p = Path(audio_path)
    return p.with_name(p.name + ".json")


def save(entry: dict) -> None:
    """Write the entry's sidecar once (render_player sees each entry repeatedly)."""
    key = entry.get("key")
    if not key or key in _seen:
        return
    _seen.add(key)
    try:
        sc = sidecar(entry["path"])
        data = dict(entry)
        if sc.exists():      # keep a label / gen block already on disk (library.py)
            try:
                old = json.loads(sc.read_text())
                for k in ("label", "gen", "analysis"):
                    if k in old and k not in data:
                        data[k] = old[k]
            except Exception:
                pass
        sc.write_text(json.dumps(data))
    except Exception as e:  # never break a generation over bookkeeping
        print(f"[sa3lab] history save failed: {e}", flush=True)


def _from_filename(path: Path, out_dir: Path, up, spec_fn) -> dict:
    """Rebuild an entry for a pre-sidecar render from upstream's verbose filename."""
    # prompt may itself contain dots, and cfg/smx values do ("smx0.92"), so match the
    # optional segments from the end rather than splitting on "."
    m = re.fullmatch(r"(?P<prompt>.*?)(?:\.neg-(?P<neg>.*?))?(?:\.cfg(?P<cfg>[\d.]+))?"
                     r"(?:\.smx(?P<smx>[\d.]+))?\.(?P<seed>-?\d+)(?:\.[0-9a-f]{8})?", path.stem)
    if m:
        prompt, neg, seed = m["prompt"], m["neg"] or "", m["seed"]
        cfg, smx = float(m["cfg"] or 1.0), float(m["smx"] or 1.0)
    else:
        prompt, neg, seed, cfg, smx = path.stem, "", "?", 1.0, 1.0
    # the filename turned ':' into '-'; tag-style prompts read better restored
    prompt = re.sub(r"(\w)- ", r"\1: ", prompt)
    spec_b64 = None
    try:
        y = up.read_audio_any(str(path))                      # (2, T) float32 @ 44.1k
        pcm = (np.clip(y.T, -1, 1) * 32767).astype(np.int16)  # (T, 2)
        spec_b64 = base64.b64encode(spec_fn(pcm, 44100)).decode("ascii")
    except Exception as e:
        print(f"[sa3lab] spectrogram backfill failed for {path.name}: {e}", flush=True)
    st = path.stat()
    timing = (f'<div class="sa3-stats"><div class="p">{html.escape(prompt)}</div>'
              f'<div class="legend"><span>restored from file</span><span>seed {html.escape(seed)}</span></div></div>')
    return {
        "key": f"k{int(st.st_mtime * 1e9)}",
        "ts": st.st_mtime,
        "dit": "",
        "neg": neg,
        "cfg": cfg,
        "smx": smx,
        "path": str(out_dir / path.name),
        "mime": "audio/mpeg" if path.suffix == ".mp3" else "audio/wav",
        "name": path.name,
        "size_mb": st.st_size / 1e6,
        "mode": "",
        "prompt": prompt,
        "seed": seed,
        "lora": "",
        "spec_b64": spec_b64,
        "timing": timing,
    }


def load_recent(out_dir: Path, up, spec_fn, limit: int = 10) -> list[dict]:
    """Newest-first entries for the last `limit` renders in out_dir."""
    files = [p for p in out_dir.iterdir() if p.suffix.lower() in AUDIO_EXT and p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    entries = []
    for p in files[:limit]:
        sc = sidecar(p)
        try:
            entry = json.loads(sc.read_text()) if sc.exists() else None
        except Exception:
            entry = None
        if entry is None:
            entry = _from_filename(p, out_dir, up, spec_fn)
            try:
                sc.write_text(json.dumps(entry))
            except Exception:
                pass
        entry["path"] = str(out_dir / p.name)   # always serve via the allowed output dir
        _seen.add(entry["key"])
        entries.append(entry)
    return entries
