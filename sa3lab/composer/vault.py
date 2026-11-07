"""The ⌘S deep copy: every file outside a Composer project that the saved project reads, cloned into
the project's own folder on a background thread, so trashing, deleting or moving a source render
never breaks a saved project.

    outputs/composer/<name>/.vault/<render id>        the copy, at its render id (path under outputs/)
                                       …<ext>          its side files beside it: .json, .cond.npz, .conds.npz
    outputs/composer/<name>/.vault/manifest.json       the last pass: {at, files, copied, kept, pruned, missing}

**What is copied.** Every string in the saved project that names an audio / .npz file under
outputs/ (piece ids, anchor ids, npz anchors, bank ids: a walk of the whole JSON, so a new field
that names a file is covered without touching this), plus that render's side files. Skipped: files
already inside this project's folder (takes/, clips/ and imports/ belong to it; clips/ are
hardlinked frozen copies already) and LoRA weights (models/, not outputs/). The Library is not
copied: only what the timeline and the project's anchors read.

**How.** APFS `clonefile` (copy-on-write): a real, independent file (trash, delete or rewrite the
original and the copy stays as it was) that costs no disk until one side changes, and is instant
whatever the size. Another filesystem falls back to `shutil.copy2`. A file whose copy already has
the source's size + mtime is skipped (the clone keeps the source's mtime), so a re-save copies
only what changed: usually a sidecar whose label moved. A source already trashed is copied from
its `deleted/` twin; one gone everywhere keeps its earlier copy and is listed under `missing`.
Copies the saved project no longer names are pruned: the vault mirrors the last ⌘S.

**Never blocks.** `schedule` starts one daemon thread per project and returns; a ⌘S while a pass
runs queues one more pass with the newest project (coalesced). The pass never takes the project
lock (it only writes inside `.vault/`), so autosave, builds and renders carry on.

**Read side** (condsrc.vaulted / unvault): compose.piece_audio_path, condsrc's render resolution
and npz anchors fall back to a vault copy (any project's) when the original file is gone, so the
build, the Fit panel and the conditioning keep working on the copy. The folder is hidden, so the
output-folder picker, library.resolve_id and the Library never list the copies as renders.
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import condsrc  # noqa: E402

OUTPUTS = condsrc.OUTPUTS
VAULT = condsrc.VAULT
SIDE = ("", ".json", condsrc.BAG_EXT, condsrc.BANK_EXT)
EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aif", ".aiff", ".npz"}
MANIFEST = "manifest.json"

try:                                                  # macOS libc: clonefile(src, dst, flags)
    _libc = ctypes.CDLL(None, use_errno=True)
    _clonefile = _libc.clonefile
    _clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
    _clonefile.restype = ctypes.c_int
except (OSError, AttributeError):
    _clonefile = None


# ── what the project reads ─────────────────────────────────────────────────────────────────────
def refs(name: str, proj) -> set[str]:
    """Render ids (paths under outputs/) of every file the project names outside its own folder."""
    own = f"composer/{name}/"
    out: set[str] = set()

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str) and 0 < len(v) < 1024 and "\n" not in v and Path(v).suffix.lower() in EXT:
            p = Path(v)
            try:
                rel = p.resolve().relative_to(OUTPUTS) if p.is_absolute() else Path(v.lstrip("/"))
            except (ValueError, OSError):
                return
            r = rel.as_posix()
            if ".." in rel.parts or r.startswith(own) or f"/{VAULT}/" in f"/{r}":
                return
            out.add(r)
            try:                                       # a starred/ link: its original too (lookups try both)
                real = (OUTPUTS / r).resolve().relative_to(OUTPUTS).as_posix()
            except (ValueError, OSError):
                return
            if real != r and not real.startswith(own):
                out.add(real)

    walk({k: v for k, v in (proj or {}).items() if k != "history"})   # promote's log of earlier states: not read
    return out


def _source(rid: str, ext: str) -> Path | None:
    """The file to copy `<rid><ext>` from: the original (symlinks resolved), else its trashed twin."""
    p = OUTPUTS / (rid + ext)
    try:
        q = p.resolve()
    except OSError:
        return None
    if OUTPUTS in q.parents and q.is_file():
        return q
    for t in {p.parent / "deleted" / p.name, q.parent / "deleted" / q.name}:
        if t.is_file():
            return t
    return None


# ── copying ────────────────────────────────────────────────────────────────────────────────────
def _fresh(src: Path, dst: Path) -> bool:
    try:
        a, b = src.stat(), dst.stat()
    except OSError:
        return False
    return a.st_size == b.st_size and a.st_mtime_ns == b.st_mtime_ns


def _clone(src: Path, dst: Path) -> None:
    """`src` → `dst` as an independent file (APFS clone, else a real copy), mtime kept, atomic."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    tmp.unlink(missing_ok=True)
    if _clonefile is None or _clonefile(os.fsencode(src), os.fsencode(tmp), 0) != 0:
        tmp.unlink(missing_ok=True)
        shutil.copy2(src, tmp)
    st = src.stat()
    os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(tmp, dst)


def snapshot(name: str, proj_dir: Path, proj: dict) -> dict:
    """One pass: copy what is missing or stale, prune what the project no longer names."""
    t0 = time.time()
    root = Path(proj_dir) / VAULT
    want: set[Path] = {root / MANIFEST}
    copied = kept = nbytes = 0
    missing, errors = [], []
    for rid in sorted(refs(name, proj)):
        found = False
        for ext in SIDE:
            dst = root / (rid + ext)
            src = _source(rid, ext)
            if src is None:
                if dst.is_file():                      # gone at the source: the earlier copy is the data now
                    want.add(dst)
                    found |= ext == ""
                continue
            want.add(dst)
            found |= ext == ""
            if _fresh(src, dst):
                kept += 1
                continue
            try:
                _clone(src, dst)
                copied += 1
                nbytes += src.stat().st_size
            except OSError as e:
                errors.append(f"{rid}{ext}: {e}")
        if not found and not rid.endswith(".npz"):
            missing.append(rid)
    pruned = 0
    if root.is_dir():
        for f in sorted(root.rglob("*"), reverse=True):  # children before their folders
            if f.is_file() and f not in want:
                f.unlink(missing_ok=True)
                pruned += 1
            elif f.is_dir() and not any(f.iterdir()):
                f.rmdir()
    m = {"at": time.time(), "files": len(want) - 1, "copied": copied, "kept": kept, "pruned": pruned,
         "bytes": nbytes, "missing": missing, "errors": errors, "secs": round(time.time() - t0, 3)}
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / (MANIFEST + ".part")
    tmp.write_text(json.dumps(m, indent=1))
    tmp.replace(root / MANIFEST)
    if copied or pruned or missing or errors:
        print(f"[composer vault] {name}: {copied} copied, {kept} up to date, {pruned} pruned"
              + (f", MISSING {missing}" if missing else "") + (f", errors {errors}" if errors else ""), flush=True)
    return m


def manifest(proj_dir: Path) -> dict | None:
    try:
        return json.loads((Path(proj_dir) / VAULT / MANIFEST).read_text())
    except Exception:
        return None


# ── the background job ─────────────────────────────────────────────────────────────────────────
_mu = threading.Lock()
_state: dict[str, dict] = {}                         # name → {"running": bool, "next": (dir, proj) | None}


def schedule(name: str, proj_dir: Path, proj: dict) -> None:
    """Queue a pass for this project and return at once (one thread per project, passes coalesced)."""
    with _mu:
        st = _state.setdefault(name, {"running": False, "next": None})
        st["next"] = (Path(proj_dir), json.loads(json.dumps(proj)))
        if st["running"]:
            return
        st["running"] = True
    threading.Thread(target=_run, args=(name,), name=f"sa3-vault-{name}", daemon=True).start()


def _run(name: str) -> None:
    while True:
        with _mu:
            st = _state[name]
            job, st["next"] = st["next"], None
            if job is None:
                st["running"] = False
                return
        try:
            snapshot(name, *job)
        except Exception as e:  # noqa: BLE001  (a failed pass must never take the studio down)
            print(f"[composer vault] {name}: {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":                          # one pass by hand: vault.py <project name>
    n = sys.argv[1]
    d = OUTPUTS / "composer" / n
    sv = json.loads((d / "saved.json").read_text())
    print(json.dumps(snapshot(n, d, sv["project"]), indent=1))
