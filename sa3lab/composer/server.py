"""The Composer editor's backend: routes in the studio process over compose.py (one origin, spec 2.1).

The page is `GET /sa3/page/composer` (sa3lab/composer/page/), built from the UI kit served at
`/sa3/ui/*` (sa3lab/ui/). Both are read from disk per request, so editing them needs only a
page reload; these routes need `make start`.

    GET  /sa3/composer/starred              → every starred render (outputs/starred/ links, by render id):
                                              {id, prompt, seconds, bpm, conf, score, cond, cond_n, ts, kind}
                                              (cond: it has conditioning beyond a prompt, an anarchy bag or a
                                              take's anchors; cond_n: how many anchors, condsrc.summary)
    GET  /sa3/composer/projects             → [{name, mtime, takes}] under outputs/composer/
    GET  /sa3/composer/project?name=        → {project, layout (last build), takes, clips, jobs, saved}
    POST /sa3/composer/version {name, project} → ⌘S: saved.json = {saved_at, project} (one, overwritten)
                                              + a background deep copy of its outside sources (vault.py)
    POST /sa3/composer/revert {name}        → project.json ← the last ⌘S, built → {layout, project}
    POST /sa3/composer/save {name, project} → writes project.json (autosave), then builds it:
                                              → {layout} | {layout: null, error} | {superseded} (a newer
                                              save arrived while this one waited: only the newest builds)
    GET  /sa3/composer/ref?name=[&spec=1]   → the built reference WAV (what SA3 keeps), or its dadaspec
    POST /sa3/composer/render {name, seeds, section?} → queue one batch (one take per seed) into this
                                              studio's own queue → {job}; section [t0, t1] renders only that
                                              stretch (compose.submit_section: `composite.section` on the take).
                                              Batches stack: Render again while one runs queues another
    GET  /sa3/composer/status?name=         → {jobs: [{id, running, done, total, section, log, error}]}:
                                              batches running or ended in the last 10 min
    POST /sa3/composer/promote {name, take} → the take becomes the project's new base (below)
    POST /sa3/composer/take_remove {name, take, mode} → take off the lanes: mode "keep" stars it
                                              (outputs/starred/, the Library) and stamps label.shelved,
                                              "trash" = the studio's trash (score 0, moved to takes/deleted/);
                                              a take the tracks still read is frozen + repointed first
    POST /sa3/composer/freeze {name, take, label?} → the take's frozen copy in clips/ (kept takes, below):
                                              {id, row}; every clip the page lifts from a take reads this id
    POST /sa3/composer/convert {name}       → a `sequence` project → explicit `pieces` on track 0 (the
                                              last build's `at`s; the editor does this on open)
    GET  /sa3/composer/piece?name=&k=       → one piece's cached audio (aligned + fixed + faded, before
                                              its mix strip): what the page's live WebAudio monitor plays
    GET  /sa3/composer/fit?name=            → fit.report: badges per piece / seam / overlap + issues
    POST /sa3/composer/fitall {name, pis?}  → fit.recommend written into each piece's `fix`, then a build
    GET  /sa3/composer/ab?name=&pi=&fix=0|1 → piece pi's audio without / with its fix, loudness-matched
    POST /sa3/composer/import?name= (multipart file) → copied into imports/ with a sidecar (dadaspec) →
                                              {id}: a piece source that never enters the starred library
    GET  /sa3/composer/imports?name=        → [{id, seconds, name}]
    POST /sa3/composer/export {name, take}  → splice export (compose.splice) → {url}
    POST /sa3/composer/mixdown {name, section?} → the timeline's plain mix (ref.wav, no AI) → MP3 in exports/ → {url}
    GET  /sa3/composer/file?name=&path=     → a file in the project folder (exports)
    GET  /sa3/page/library                  → the Library page (sa3lab/composer/page/library.*)
    GET  /sa3/app                           → the tab host (sa3lab/ui/app.html: Studio · Library ·
                                              Composer · Arranger, each kept alive in its own iframe)

**Project folder** `outputs/composer/<name>/`: `project.json` (the editor's state, autosaved on
every edit), `saved.json` (the last ⌘S: the version you chose to keep; Revert goes back to it),
`.vault/` (the ⌘S deep copy of every source file outside this folder that it reads: vault.py),
`ref.wav` + `box.<variant>.json` + `project.resolved.json` (the last build: compose.build),
`takes/` (the renders: studio sidecars with `gen.composite`), `clips/` (kept takes: frozen copies
of every take a clip or the base reads, so trashing / deleting the take never touches the
timeline; `freeze`), `jobs/<stamp>/` (each render
batch's frozen project.json + boxes, so editing during a render can't swap the reference under
queued jobs; its ref.wav is deleted once the batch is done, the take's lineage keeps its sha256).

**Project shapes**, all plain compose.py input. The editor works on *pieces* (explicit `at` +
`track`, layered, each with mute / solo / mix strip / fix / anchor override); `convert` turns the
phase-0 / phase-1 shapes into it:
- *assemble*: a `sequence` of pieces (render id + source range) and `{"gap": s}` items, laid out
  end to end with beat alignment (compose.layout); `seam` beats each side of an inner piece edge
  are regenerated. Extend = a trailing gap.
- *base* (after **promote**): `pieces` = the take's frozen copy (`base`; `base_take` = the take it
  came from, so the page still marks that lane "base") over [0, length] at 0 on track 0, `align: false`
  (a take is already on the grid: re-snapping would shift it; its measured tempo dominates the
  live BPM estimate by length), `rewrite` = the strength curve drawn on it. New pieces can be layered over the base on other
  tracks. The take contributes the anchors + weight curves it was rendered on (condsrc.of_render:
  its own bank `<take>.conds.npz`, else its sidecar; same timeline, so a span regenerated in B's
  half is still conditioned on B; an Extend tail holds the last anchor); `anchor` stays what the
  project had, else the global fallback is the longest piece's = the base take's dominant anchor
  (a take's own prompt is only the job label and is never used as conditioning). Extend = a longer `length`.
  `history` keeps every earlier project state; `ghost` = the parent layout's pieces, drawn greyed.
  (Phase-1 bases carry a `cond_fixed`; it is ignored now, the take's sidecar holds the same curves.)

**Conditioning** is the project's `cond.gap`: "crossfade" (Auto, default: pieces propose their own
anchors, compose.cond_curves), "global" (one anchor everywhere) or "keys" (the Conditioning lane's
explicit keypoints, compose.key_curves). The view hands the page the last build's resolved anchors +
curves (with a readable label per anchor) so the lane can draw what SA3 will actually be conditioned on.

Submission runs on a thread here (gradio drops a client's waiting jobs when it disconnects), with
a loopback gradio_client to this server's own URL, so a test studio on another port renders into
itself. Every take is submitted at once (no window), so the studio's queue holds them all: it is
one FIFO with Generate clicks, and the Queue modal (ui/queue.js, any page) reorders / deletes /
skips them. A take deleted there completes empty; its batch logs it as "removed", not a failure.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

from fastapi import Request  # module level: route annotations are resolved from globals
from fastapi.responses import FileResponse, JSONResponse, Response

HERE = Path(__file__).resolve().parent
SA3LAB = HERE.parent
UI = SA3LAB / "ui"
PAGE = HERE / "page"
OUTPUTS = (SA3LAB.parent / "outputs").resolve()
ROOT = OUTPUTS / "composer"
STARRED = OUTPUTS / "starred"
AUDIO_EXT = {".mp3", ".wav"}
MAX_SECTION = 380.0                  # a section renders in one SA3 pass (compose.MAX_LEN)
TYPES = {".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml",
         ".json": "application/json", ".wav": "audio/wav", ".mp3": "audio/mpeg"}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
for _p in (str(HERE), str(SA3LAB)):                  # compose.py imports as `compose`, like its CLI
    if _p not in sys.path:
        sys.path.insert(0, _p)

import condsrc  # noqa: E402  (what a render was conditioned on)

_locks: dict[str, threading.Lock] = {}
_jobs: dict[str, list[dict]] = {}   # project → its render batches this session, oldest first
_rows: dict[str, tuple[float, dict]] = {}


def _lock(name: str) -> threading.Lock:
    return _locks.setdefault(name, threading.Lock())


_gen: dict[str, int] = {}           # project → its newest autosave (coalesced: see save)
_gen_lock = threading.Lock()


def _dir(name: str) -> Path:
    if not NAME_RE.match(name or ""):
        raise ValueError(f"bad project name {name!r} (letters, digits, - and _)")
    return ROOT / name


def _read(p: Path, default=None):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def _write(p: Path, d) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    tmp.replace(p)


# ── the side panel: starred renders, by render id ───────────────────────────────────────────
def starred_rows() -> list[dict]:
    import library
    rows = []
    for link in STARRED.iterdir() if STARRED.is_dir() else []:
        if link.suffix.lower() not in AUDIO_EXT:
            continue
        p = link.resolve()
        rid = library.render_id(p)
        sc = p.with_name(p.name + ".json")
        try:
            mt = sc.stat().st_mtime
        except OSError:
            continue
        hit = _rows.get(rid)
        if hit and hit[0] == mt:
            rows.append(hit[1])
            continue
        d = _read(sc, {}) or {}
        g, lab = d.get("gen") or {}, d.get("label") or {}
        t = (d.get("analysis") or {}).get("tempo") or {}
        secs = g.get("seconds")
        m = re.search(r"samples</b>: (\d+)", d.get("timing") or "")
        if m:
            secs = int(m.group(1)) / 44100
        cs = condsrc.summary(p)                        # bag (anarchy) | multi (a take) | prompt | none
        row = {"id": rid, "prompt": g.get("prompt", d.get("prompt")) or "", "seconds": secs,
               "bpm": t.get("bpm"), "conf": t.get("conf"), "score": lab.get("score"),
               "ts": lab.get("starred") or "", "cond": cs["kind"] in ("bag", "multi"), "cond_n": cs["n"],
               "folder": str(Path(rid).parent), "tags": lab.get("tags") or [],
               "hl": lab.get("highlights") or [], "hl_an": (d.get("analysis") or {}).get("highlights") or {},
               "loras": [x.get("name") for x in g.get("loras") or []], "mode": g.get("mode"),
               "kind": ("composite" if g.get("composite") else "anarchy" if row_cond(p) else
                        "a2a" if g.get("a2a_audio") else "inpaint" if g.get("inpaint_audio") else "text")}
        _rows[rid] = (mt, row)
        rows.append(row)
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows


def row_cond(p: Path) -> bool:
    return p.with_name(p.name + ".cond.npz").is_file()


def anchor_label(a: dict) -> str:
    return condsrc.label(a)


# ── projects ─────────────────────────────────────────────────────────────────────────────────
def projects() -> list[dict]:
    out = []
    for d in sorted(ROOT.iterdir()) if ROOT.is_dir() else []:
        pj = d / "project.json"
        if pj.is_file() and NAME_RE.match(d.name):
            out.append({"name": d.name, "mtime": pj.stat().st_mtime,
                        "takes": len(list((d / "takes").glob("*.json"))) if (d / "takes").is_dir() else 0})
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out


def takes(name: str) -> list[dict]:
    import library
    d = _dir(name) / "takes"
    out = []
    for p in d.iterdir() if d.is_dir() else []:
        if p.suffix.lower() not in AUDIO_EXT:
            continue
        sc = _read(p.with_name(p.name + ".json"), {}) or {}
        g = sc.get("gen") or {}
        c = g.get("composite") or {}
        if c.get("project") != name:
            continue
        lab = sc.get("label") or {}
        if lab.get("shelved") and lab.get("starred"):  # kept off the lanes (take_remove "keep"); unstarring brings it back
            continue
        out.append({"id": library.render_id(p), "seed": g.get("seed"), "variant": c.get("variant"),
                    "length": c.get("length"), "ts": sc.get("ts") or p.stat().st_mtime,
                    "score": lab.get("score"), "starred": bool(lab.get("starred")),
                    "played": bool(lab.get("played")), "job": c.get("project_file"),
                    "ref_sha256": c.get("ref_sha256"), "has_spec": bool(sc.get("spec_b64")),
                    "section": c.get("section"),
                    # the clips it was rendered from, where they sat then (timeline seconds): the page
                    # aligns a lifted stretch to where those clips sit now (composer.js liftHome)
                    "pieces": [{"id": q.get("id"), "at": q.get("at"), "dur": q.get("dur"), "track": q.get("track", 0),
                                "src": q.get("src_cut") or q.get("src"), "stretch": q.get("stretch") or 1.0}
                               for q in c.get("pieces") or [] if q.get("id") and q.get("at") is not None and q.get("dur")]})
    out.sort(key=lambda r: r["ts"], reverse=True)
    return out


def _src_len(rid: str) -> float | None:
    import library
    import segments
    p = library.resolve_id(rid)
    return round(segments.duration(str(p)), 4) if p else None


def _src_cond(rid: str) -> dict:
    """The piece source's own conditioning, for the inspector's "its own (…)" label."""
    s = condsrc.summary(rid)
    return {"cond_kind": s["kind"], "cond_n": s["n"]}


def _view(lin: dict, keys, cond: dict | None = None) -> dict:
    """A build's lineage → what the timeline draws (+ each piece's source length, for trims, and
    the resolved conditioning: anchors with labels + their weight curves)."""
    c = None
    if cond and cond.get("anchors"):
        c = {"anchors": [{**a, "label": anchor_label(a)} for a in cond["anchors"]], "curves": cond.get("curves") or []}
    return {"length": lin["length"], "bpm": lin["bpm"],
            "pieces": [pc | {"src_len": _src_len(pc["id"])} | _src_cond(pc["id"]) for pc in lin["pieces"]],
            "gaps": [[r["t0"], r["t1"]] for r in lin.get("regions", []) if r.get("type") == "gap"],
            "muted": lin.get("muted") or [], "xfades": lin.get("xfades") or [], "meter": lin.get("meter") or {}, "silent": lin.get("silent") or [],
            "keys": keys, "cond": c, "ref_sha256": lin.get("ref_sha256")}


def _layout_view(b: dict) -> dict:
    box = next(iter(b["boxes"].values()), {})
    return _view(b["lineage"], b["keys"], box.get("cond"))


def _has_pieces(proj: dict) -> bool:
    return (any("id" in it for it in (proj.get("sequence") or [])) or bool(proj.get("pieces"))
            or bool((proj.get("cond") or {}).get("keys") and proj.get("length")))


def save(name: str, proj: dict, build: bool = True, coalesce: bool = False) -> dict:
    """`coalesce` (the page's autosave): a save still waiting on the lock when a newer one arrives
    is skipped ({superseded: true}, nothing written): quick edits (mute, mute, unmute) build once,
    for the newest state, instead of queueing one full build each."""
    import compose
    d = _dir(name)
    d.mkdir(parents=True, exist_ok=True)
    proj = {k: v for k, v in proj.items() if not k.startswith("_")} | {"name": name}
    if coalesce:
        with _gen_lock:
            gen = _gen[name] = _gen.get(name, 0) + 1
    with _lock(name):
        if coalesce and _gen.get(name) != gen:
            return {"superseded": True}
        _write(d / "project.json", proj)
        if not build or not _has_pieces(proj):
            return {"layout": None}
        try:
            b = compose.build(compose.load(d / "project.json"))
        except SystemExit as e:              # compose's user-facing errors
            return {"layout": None, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"layout": None, "error": f"{type(e).__name__}: {e}"}
    return {"layout": _layout_view(b)}


def last_layout(name: str) -> dict | None:
    d = _dir(name)
    r = _read(d / "project.resolved.json")
    if not r or not (r.get("resolved") or {}).get("pieces"):
        return None
    box = next((_read(f) for f in sorted(d.glob("box.*.json"))), None) or {}
    return _view(r["resolved"], box.get("strength"), box.get("cond"))


# ── render: one take per seed, on a thread ───────────────────────────────────────────────────
def jobs(name: str) -> list[dict]:
    """The project's batches still rendering + those that ended in the last 10 min (errors to show)."""
    now = time.time()
    keep = [j for j in _jobs.get(name, []) if j["running"] or now - j.get("ended", now) < 600]
    _jobs[name] = keep
    return keep


def render(name: str, seeds: list[int], url: str, section: list | None = None) -> dict:
    """Queue one batch: every take goes straight into the studio's gradio queue (the same queue
    as Generate clicks: listed, reordered, deleted, skipped in the Queue modal). Batches never
    block each other: each has its own frozen snapshot + thread."""
    import compose
    d = _dir(name)
    proj = _read(d / "project.json")
    if not proj or not _has_pieces(proj):
        return {"error": "nothing to render: add a piece first"}
    stamp, n = time.strftime("%Y%m%d-%H%M%S"), 0
    while True:                                      # two clicks in one second → two snapshots
        snap = d / "jobs" / (stamp + (f"-{n}" if n else ""))
        try:
            snap.mkdir(parents=True)
            break
        except FileExistsError:
            n += 1
    _write(snap / "project.json", proj)
    job = {"id": snap.name, "running": True, "started": time.time(), "seeds": seeds, "total": len(seeds),
           "done": 0, "log": [], "error": None, "snapshot": str(snap.relative_to(OUTPUTS)), "section": section}
    _jobs.setdefault(name, []).append(job)

    def log(m: str) -> None:
        job["log"] = (job["log"] + [m])[-40:]
        w = re.match(r"^(\d+) windows: ", m)            # past 380 s: one job per window per seed (submit_windows)
        if w:
            job["total"] = int(w.group(1)) * len(seeds)
        if m.startswith(("done ", "FAIL ", "removed ")):
            job["done"] += 1
        if m.startswith("FAIL "):
            job["error"] = m

    def run() -> None:
        try:
            p = compose.load(snap / "project.json")
            p["name"] = name
            p["_cache"] = str(d / "cache")
            compose.submit(p, url, outdir=f"composer/{name}/takes", window=10_000, log=log, seeds=seeds, section=section)
        except SystemExit as e:
            job["error"] = str(e)
        except Exception as e:  # noqa: BLE001
            job["error"] = f"{type(e).__name__}: {e}"
        finally:
            (snap / "ref.wav").unlink(missing_ok=True)   # the takes keep its sha256; project.json rebuilds it
            job["running"] = False
            job["ended"] = time.time()

    threading.Thread(target=run, name=f"sa3-composer-{name}", daemon=True).start()
    return {"ok": True, "job": job}


# ── kept takes: a take's audio frozen into the project, so its clips outlive the take ────────
# A clip lifted from a take (or a promoted base) never points at the take itself: the take is
# first *frozen* into `clips/` — audio and `.conds.npz` bank hardlinked (zero extra disk while the
# take exists; the data stays when the take is trashed or deleted anywhere: composer, Library,
# studio), the sidecar copied with its labels dropped and a `clip` block {from, project, seed,
# label, frozen}. The whole take is kept, so a clip's edges can still be trimmed outward later.
# The copy is a complete render: condsrc reads its conditioning from its own bank, the spectrogram
# from its sidecar. One frozen copy per take per project, reused by every clip cut from it.
_freeze_lock = threading.Lock()
TAKE_ID_RE = re.compile(r"^composer/[A-Za-z0-9][A-Za-z0-9_-]{0,63}/takes/[^/]+$")


def _link(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:                                  # another filesystem: a real copy
        shutil.copy2(src, dst)


def clip_row(p: Path) -> dict:
    import library
    import segments
    sc = _read(p.with_name(p.name + ".json"), {}) or {}
    c = sc.get("clip") or {}
    lab = c.get("label") or (f"take seed {c['seed']}" if c.get("seed") is not None else p.stem)
    return {"id": library.render_id(p), "name": lab, "prompt": lab, "seconds": round(segments.duration(str(p)) or 0, 4),
            "from": c.get("from"), "folder": "kept take"}


def clips(name: str) -> list[dict]:
    d = _dir(name) / "clips"
    return [clip_row(p) for p in sorted(d.iterdir()) if p.suffix.lower() in AUDIO_EXT] if d.is_dir() else []


def freeze(name: str, take_id: str, label: str | None = None) -> dict:
    """A composer take (this project's or another's) → its frozen copy in this project's clips/:
    {id, row}. An id already in clips/ comes back as is."""
    import library
    from datetime import datetime
    cd = _dir(name) / "clips"
    p = library.resolve_id(take_id)
    if p is None:
        return {"error": f"no take {take_id!r} (already trashed?)"}
    rid = library.render_id(p)
    if p.parent == cd.resolve():
        return {"id": rid, "row": clip_row(p)}
    if not TAKE_ID_RE.match(rid or ""):
        return {"error": f"{rid!r} is not a composer take"}
    src_proj = rid.split("/")[1]
    condsrc.of_render(p)                             # an older take without a bank gets one now, while its sources resolve
    with _freeze_lock:
        cd.mkdir(parents=True, exist_ok=True)
        stem = p.name if src_proj == name else f"{src_proj}__{p.name}"
        dst, i = cd / stem, 1
        while dst.exists():
            j = _read(dst.with_name(dst.name + ".json"), {}) or {}
            if (j.get("clip") or {}).get("from") == rid or dst.stat().st_ino == p.stat().st_ino:
                return {"id": library.render_id(dst), "row": clip_row(dst)}
            dst, i = cd / f"{Path(stem).stem}.{i}{p.suffix}", i + 1
        sc = _read(p.with_name(p.name + ".json"), {}) or {}
        bank = p.with_name(p.name + condsrc.BANK_EXT)
        if bank.is_file():
            _link(bank, dst.with_name(dst.name + condsrc.BANK_EXT))
        sc |= {"name": dst.name, "label": {},
               "clip": {"from": rid, "project": src_proj, "seed": (sc.get("gen") or {}).get("seed"),
                        "label": label, "score": (sc.get("label") or {}).get("score"),
                        "frozen": datetime.now().isoformat(timespec="seconds")}}
        _write(dst.with_name(dst.name + ".json"), sc)
        _link(p, dst)                                # last: the audio's presence marks a complete copy
    return {"id": library.render_id(dst), "row": clip_row(dst)}


def _repoint(proj: dict, old: str, new: str) -> dict:
    """Every clip (and the base) of `proj` that reads `old` reads `new` instead."""
    for it in (proj.get("pieces") or []) + (proj.get("sequence") or []):
        if it.get("id") == old:
            it["id"] = new
    if proj.get("base") == old:
        proj["base"], proj["base_take"] = new, old
    return proj


# ── remove a take from the lanes: keep it (star) or trash it ─────────────────────────────────
def take_remove(name: str, take_id: str, mode: str) -> dict:
    """"keep": star the take (library.star_render: it joins outputs/starred/, so the Library and the
    side panel offer it as a piece) and stamp `label.shelved`, which takes() skips; the files stay in
    takes/. "trash": library.delete_render, the studio's trash (score 0, label.deleted, audio +
    sidecar + .conds.npz moved to takes/deleted/, kept as a negative row). Unstarring a kept take (Library,
    studio) puts it back on the lanes. A take the tracks still read directly (a project from before
    kept takes, or a clip dragged in some other way) is frozen first and its clips repointed, so
    trashing it never changes the timeline: the reply then carries the new `project` + `layout`
    and `repointed` {old: new} (the page rewrites its undo history with it)."""
    import library
    from datetime import datetime
    d = _dir(name)
    p = library.resolve_id(take_id)
    if p is None or p.resolve().parent != (d / "takes").resolve():
        return {"error": f"no take {take_id!r} in this project"}
    proj = _read(d / "project.json", {}) or {}
    extra = {}
    if mode == "trash":
        if proj.get("base") == take_id or any(it.get("id") == take_id for it in (proj.get("pieces") or []) + (proj.get("sequence") or [])):
            f = freeze(name, take_id)
            if f.get("error"):
                return f
            proj = _repoint(proj, take_id, f["id"])
            s = save(name, proj)
            extra = {"project": proj, "layout": s.get("layout"), "build_error": s.get("error"),
                     "repointed": {take_id: f["id"]}, "clips": clips(name)}
        if not library.delete_render(p):
            return {"error": "couldn't trash it"}
    elif mode == "keep":
        library.star_render(p, True)
        sc = library._sidecar(p)
        with library._lock:
            j = _read(sc, {}) or {}
            j["label"] = {**(j.get("label") or {}), "shelved": datetime.now().isoformat(timespec="seconds")}
            _write(sc, j)
    else:
        return {"error": f"bad mode {mode!r}"}
    return extra | {"ok": True, "takes": takes(name)}


# ── promote: a take becomes the base ─────────────────────────────────────────────────────────
def promote(name: str, take_id: str) -> dict:
    import library
    import segments
    d = _dir(name)
    p = library.resolve_id(take_id)
    if p is None:
        return {"error": f"no take {take_id!r}"}
    sc = _read(p.with_name(p.name + ".json"), {}) or {}
    comp = (sc.get("gen") or {}).get("composite") or {}
    if comp.get("section"):
        return {"error": "a section take covers only part of the timeline: render the whole piece to promote"}
    f = freeze(name, take_id)                          # the base reads the take's frozen copy (kept takes, above)
    if f.get("error"):
        return f
    with _lock(name):
        proj = _read(d / "project.json") or {}
        L = float(segments.duration(str(p)) or comp.get("length") or 0)
        anchor = proj.get("anchor")                    # unset: the base take's dominant anchor (global_anchor)
        prev = {k: v for k, v in proj.items() if k != "history"}
        ghost = [{"id": x["id"], "at": x["at"], "dur": x["dur"], "track": x.get("track", 0)} for x in (comp.get("pieces") or [])]
        master = proj.get("master")                    # the base already carries the master gain: applying it
        if master and master.get("gain"):              # again would raise it every promote (the limiter stays)
            master = {**master, "gain": 0.0}
        new = {"name": name, "model": proj.get("model"), "seeds": proj.get("seeds"),
               "cond": proj.get("cond"), "master": master,
               "base": f["id"], "base_take": take_id,
               "pieces": [{"id": f["id"], "src": [0.0, round(L, 4)], "at": 0.0, "track": 0,
                           "align": False, "fade": [0.0, 0.0]}],
               "rewrite": [], "length": round(L, 4), "seam": 0, "anchor": anchor, "ghost": ghost,
               "history": (proj.get("history") or []) + [prev | {"promoted_to": take_id, "at_ts": time.time()}]}
        new = {k: v for k, v in new.items() if v is not None}
    return save(name, new) | {"project": new}


# ── saved version (⌘S): project.json is the autosaved working state, saved.json the last ⌘S ───
def saved(name: str) -> dict | None:
    return _read(_dir(name) / "saved.json")


def save_version(name: str, proj: dict) -> dict:
    """⌘S: `proj` (what the page shows, already autosaved + built) becomes the project's one saved
    version, overwriting the previous one. → {saved_at}"""
    d = _dir(name)
    if not (d / "project.json").is_file():
        return {"error": f"no project {name!r}"}
    proj = {k: v for k, v in proj.items() if not k.startswith("_")} | {"name": name}
    at = time.time()
    with _lock(name):
        _write(d / "saved.json", {"saved_at": at, "project": proj})
    import vault
    vault.schedule(name, d, proj)                    # deep copy of its outside sources, on a thread (vault.py)
    return {"ok": True, "saved_at": at}


def revert(name: str) -> dict:
    """The working state goes back to the last ⌘S (then builds). → save()'s reply + {project}"""
    sv = saved(name)
    if not sv or not sv.get("project"):
        return {"error": "this project has never been saved with ⌘S"}
    return save(name, sv["project"]) | {"project": sv["project"]}


# ── convert / fit / A-B / imports / export ─────────────────────────────────────────────────────
def convert(name: str) -> dict:
    """A `sequence` project (phase 0 / 1) → explicit pieces on track 0 at the last build's `at`s;
    a trailing gap becomes `length`. A phase-1 base keeps its pieces (gets track + align)."""
    d = _dir(name)
    with _lock(name):
        proj = _read(d / "project.json") or {}
        if proj.get("sequence") and any("id" in it for it in proj["sequence"]):
            lay = last_layout(name)
            if not lay:
                b = save(name, proj)
                lay = b.get("layout")
            if not lay:
                return {"error": "build the project first"}
            its = [it for it in proj["sequence"] if "id" in it]
            pcs = []
            for it, pc in zip(its, lay["pieces"]):
                pcs.append({k: v for k, v in it.items() if k != "gap"} | {"at": pc["at"], "track": 0})
            proj["pieces"] = pcs
            proj["length"] = lay["length"]
            proj["seam_unit"] = "beats"
        proj.pop("sequence", None)
        if proj.get("base"):
            for pc in proj.get("pieces") or []:
                pc.setdefault("track", 0)
                if pc.get("id") == proj["base"]:
                    pc["align"] = False
        proj.pop("align", None) if proj.get("base") else None
        proj.pop("cond_fixed", None)
    return save(name, proj) | {"project": {k: v for k, v in proj.items()}}


def fit_report(name: str) -> dict:
    """fit.report + swap suggestions (spec 4.3 "swap"): for a piece with a warn / bad sound
    dimension (bands, width, key, tempo), the closest starred moments by sound (embed.py E2),
    keeping only those whose render has a confident beat near the project BPM when the project
    has one. Empty until the studio's idle worker has embedded the starred set."""
    import compose
    import embed
    import fit
    d = _dir(name)
    with _lock(name):
        proj = compose.load(d / "project.json")
        rep = fit.report(proj)
    rows = {r["id"]: r for r in starred_rows()}
    bpm = rep["target"].get("bpm")
    for r in rep["pieces"]:
        bad = [k for k in ("bands", "width", "key", "tempo") if (r["dims"].get(k) or {}).get("state") in ("warn", "bad")]
        if r["ref"] or not bad:
            continue
        it = (proj.get("sequence") or proj.get("pieces") or [])[r["pi"]]
        try:
            sim = embed.similar(it["id"], float(it["src"][0]), float(it["src"][1]), "sound", 12)["results"]
        except Exception:
            sim = []
        out = []
        for m in sim:
            row = rows.get(m["id"]) or {}
            if bpm and row.get("conf") and row["conf"] >= 0.3 and abs(compose.fold(row["bpm"], bpm) / bpm - 1) > 0.08:
                continue
            out.append(m | {"prompt": row.get("prompt", ""), "bpm": row.get("bpm"), "conf": row.get("conf")})
            if len(out) >= 3:
                break
        r["swaps"] = out
    return rep


def fit_all(name: str, pis: list | None) -> dict:
    import compose
    import fit
    d = _dir(name)
    with _lock(name):
        proj = compose.load(d / "project.json")
        rec = fit.recommend(proj)
        key = "sequence" if proj.get("sequence") else "pieces"
        for pi, fx in rec.items():
            if pis is None or pi in pis:
                proj[key][pi]["fix"] = fx or None
    r = save(name, proj)
    return r | {"project": {k: v for k, v in proj.items() if not k.startswith("_")}, "applied": rec}


def ab_audio(name: str, pi: int, fixed: bool) -> Path:
    """Piece pi's aligned audio without / with its fix chain, both scaled to the fixed version's
    LUFS (spec 4.3: loudness-matched A/B), as a 16-bit WAV in cache/ab/."""
    import compose
    import mixdsp
    import numpy as np
    d = _dir(name)
    proj = compose.load(d / "project.json")
    key = "sequence" if proj.get("sequence") else "pieces"
    it = proj[key][pi]
    one = {**proj, key: [{**it, "mute": False, "solo": False, "at": 0.0}], "length": None, "track_mix": None}
    one.pop("sequence", None) if key == "pieces" else None
    lay_f = compose.layout({**one, "pieces": [{**it, "at": 0.0}]} if key == "pieces" else one, d / "cache")
    lay_n = compose.layout({**one, "pieces": [{**it, "at": 0.0, "fix": None}]} if key == "pieces" else one, d / "cache" / "prefix")
    yf, yn = lay_f["pieces"][0]["audio"], lay_n["pieces"][0]["audio"]
    lf, ln = mixdsp.lufs(yf), mixdsp.lufs(yn)
    y = yf if fixed else (yn * np.float32(10 ** ((lf - ln) / 20)) if lf is not None and ln is not None else yn)
    out = d / "cache" / "ab" / f"{pi}.{int(fixed)}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", "44100", "-ac", "2", "-i", "-",
                    "-c:a", "pcm_s16le", str(out)], input=np.ascontiguousarray(np.clip(y, -1, 1).T).astype(np.float32).tobytes(),
                   check=True)
    return out


def add_import(name: str, filename: str, data: bytes) -> dict:
    """Copy an uploaded file into imports/ (gitignored outputs/: copyrighted material stays on
    this machine) as a 44.1 kHz stereo WAV, with a sidecar carrying its dadaspec, so every
    /sa3/* route (audio, spec, label, highlights) addresses it by render id."""
    import base64
    import subprocess
    import tempfile
    import hispec
    import numpy as np
    import library
    d = _dir(name) / "imports"
    d.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(filename).stem)[:80] or "import"
    out = d / f"{stem}.wav"
    k = 1
    while out.exists():
        out = d / f"{stem}.{k}.wav"
        k += 1
    with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix or ".bin") as f:
        f.write(data)
        f.flush()
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", f.name, "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le",
                        str(out)], check=True)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-f", "s16le", "-ac", "2", "-ar", "44100", "-"],
                         capture_output=True, check=True).stdout
    pcm = np.frombuffer(raw, np.int16).reshape(-1, 2)
    spec = base64.b64encode(hispec.render_spectrogram_png(pcm, 44100)).decode("ascii")
    side = {"name": out.name, "ts": time.time(), "prompt": f"import: {Path(filename).name}", "spec_b64": spec,
            "import": {"from": Path(filename).name, "project": name, "seconds": round(len(pcm) / 44100, 4)}}
    out.with_name(out.name + ".json").write_text(json.dumps(side))
    return {"id": library.render_id(out), "seconds": round(len(pcm) / 44100, 4), "name": Path(filename).name}


def imports(name: str) -> list[dict]:
    import library
    d = _dir(name) / "imports"
    out = []
    for p in sorted(d.glob("*.wav")) if d.is_dir() else []:
        sc = _read(p.with_name(p.name + ".json"), {}) or {}
        im = sc.get("import") or {}
        out.append({"id": library.render_id(p), "seconds": im.get("seconds"), "name": im.get("from") or p.name})
    return out


def export_splice(name: str, take_id: str) -> dict:
    """Splice export of a take: rebuild its frozen job's reference, check its sha256 against the
    take's lineage, then compose.splice into exports/<take>.splice.wav."""
    import compose
    import library
    p = library.resolve_id(take_id)
    if p is None:
        return {"error": f"no take {take_id!r}"}
    sc = _read(p.with_name(p.name + ".json"), {}) or {}
    g = sc.get("gen") or {}
    comp = g.get("composite") or {}
    pf = OUTPUTS / (comp.get("project_file") or "")
    if not pf.is_file():
        return {"error": "the take's frozen project (jobs/<stamp>/project.json) is gone"}
    proj = compose.load(pf)
    proj["name"] = name
    proj["_cache"] = str(_dir(name) / "cache")
    b = compose.build(proj, prune=False)
    if comp.get("ref_sha256") and b["lineage"]["ref_sha256"] != comp["ref_sha256"]:
        (Path(proj["_dir"]) / "ref.wav").unlink(missing_ok=True)
        return {"error": "the rebuilt reference differs from the one this take was rendered on "
                         "(a source render changed?): splice would paste the wrong audio"}
    keys = (g.get("segments") or {}).get("strength") or b["keys"]
    out = _dir(name) / "exports" / (p.stem + ".splice.wav")
    out.parent.mkdir(parents=True, exist_ok=True)
    ref = b["ref"]
    if comp.get("section"):                            # a section take: splice against that stretch of the reference
        a, z = comp["section"]
        ref = Path(proj["_dir"]) / "ref.section.wav"
        compose.write_wav(ref, compose.decode(b["ref"])[:, int(round(a * compose.SR)):int(round(z * compose.SR))])
    compose.splice(p, ref, keys, out)
    if ref != b["ref"]:
        ref.unlink(missing_ok=True)
    (Path(proj["_dir"]) / "ref.wav").unlink(missing_ok=True)
    return {"ok": True, "path": f"exports/{out.name}",
            "url": f"/sa3/composer/file?name={name}&path=exports/{out.name}"}


def export_mix(name: str, section: list | None = None) -> dict:
    """Plain mixdown, no AI: the project's reference (every piece through its strip, summed, master
    gain + limiter: exactly what `build` writes as ref.wav and the live monitor plays) encoded to a
    320 kbps MP3 in exports/, the whole timeline or just `section` [t0, t1] (clamped to it). The
    ref.wav is reused when it is newer than project.json, else the project is rebuilt first."""
    import subprocess
    import time
    import compose
    d = _dir(name)
    pj = d / "project.json"
    if not pj.is_file():
        return {"error": "nothing saved in this project yet"}
    with _lock(name):
        ref = d / "ref.wav"
        if not ref.is_file() or ref.stat().st_mtime < pj.stat().st_mtime:
            try:
                ref = compose.build(compose.load(pj))["ref"]
            except SystemExit as e:
                return {"error": str(e)}
        L = compose.src_dur(ref)
        a, z = 0.0, L
        if section:
            a, z = max(0.0, float(section[0])), min(L, float(section[1]))
            if z - a < 0.05:
                return {"error": "the selected section lies past the end of the timeline"}
        tag = f"_{a:.1f}-{z:.1f}s" if section else ""
        out = d / "exports" / f"{name}_mixdown{tag}_{time.strftime('%Y%m%d-%H%M%S')}.mp3"
        out.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.4f}", "-to", f"{z:.4f}", "-i", str(ref),
                            "-c:a", "libmp3lame", "-b:a", "320k", str(out)], capture_output=True, text=True)
    if r.returncode:
        return {"error": "ffmpeg: " + (r.stderr.strip().splitlines() or ["failed"])[-1]}
    return {"ok": True, "path": f"exports/{out.name}", "seconds": round(z - a, 2),
            "url": f"/sa3/composer/file?name={name}&path=exports/{out.name}"}


# ── routes ───────────────────────────────────────────────────────────────────────────────────
def _static(base: Path, rel: str):
    f = (base / rel).resolve()
    if base.resolve() not in f.parents or not f.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return Response(f.read_bytes(), media_type=TYPES.get(f.suffix, "application/octet-stream"),
                    headers={"Cache-Control": "no-store"})


def install_routes() -> None:
    """Add the composer page, the UI kit and /sa3/composer/* to every app gradio builds, plus the
    highlights routes (sa3lab/highlights.py) and the similarity index (sa3lab/embed.py: its idle-
    queue SAME-L encode worker starts here, in the studio process)."""
    from gradio import routes
    import embed
    import highlights
    highlights.install_routes()
    embed.install_routes()
    if sys.modules.get("sa3_gradio") is not None:
        embed.install(sys.modules["sa3_gradio"])

    def ok(r, code=200):
        return JSONResponse(r, status_code=400 if isinstance(r, dict) and r.get("error") and code == 200 else code)

    def page_get():
        return _static(PAGE, "composer.html")

    def page_file(path: str):
        return _static(PAGE, path)

    def ui_get(path: str):
        return _static(UI, path)

    def starred_get():
        return JSONResponse(starred_rows())

    def projects_get():
        return JSONResponse(projects())

    def project_get(name: str):
        try:
            d = _dir(name)
        except ValueError as e:
            return ok({"error": str(e)})
        return JSONResponse({"project": _read(d / "project.json"), "layout": last_layout(name),
                             "takes": takes(name), "clips": clips(name), "jobs": jobs(name),
                             "saved": saved(name)})

    async def version_post(request: Request):
        b = await request.json()
        return await thread(lambda: save_version(b.get("name", ""), b.get("project") or {}))

    async def revert_post(request: Request):
        b = await request.json()
        return await thread(lambda: revert(b.get("name", "")))

    async def freeze_post(request: Request):
        b = await request.json()
        return await thread(lambda: freeze(b.get("name", ""), b.get("take", ""), (b.get("label") or None)))

    async def save_post(request: Request):
        b = await request.json()
        import anyio
        try:
            r = await anyio.to_thread.run_sync(lambda: save(b.get("name", ""), b.get("project") or {},
                                                            b.get("build", True), coalesce=True))
        except ValueError as e:
            r = {"error": str(e)}
        return JSONResponse(r)

    def ref_get(name: str, spec: int = 0):
        try:
            f = _dir(name) / "ref.wav"
        except ValueError as e:
            return ok({"error": str(e)})
        if not f.is_file():
            return JSONResponse({"error": "not built yet"}, status_code=404)
        if spec:
            import segments
            r = segments.spec(str(f))
            if r is None:
                return JSONResponse({"error": "no spectrogram"}, status_code=404)
            return Response(r[0], media_type="image/jpeg",
                            headers={"X-Sa3-Duration": f"{r[1]:.4f}", "Cache-Control": "no-store"})
        return FileResponse(f, headers={"Cache-Control": "no-store"})

    async def render_post(request: Request):
        b = await request.json()
        seeds = [int(s) for s in (b.get("seeds") or [])][:16]
        sec = b.get("section")
        try:
            if sec:
                sec = [round(float(sec[0]), 4), round(float(sec[1]), 4)]
                if not (0 <= sec[0] and sec[1] - sec[0] >= 1.0):
                    return ok({"error": "the section must be at least 1 s long"})
                if sec[1] - sec[0] > MAX_SECTION:
                    return ok({"error": f"a section renders in one pass: at most {MAX_SECTION:.0f} s"})
            r = render(b.get("name", ""), seeds or [0], str(request.base_url), sec or None)
        except ValueError as e:
            r = {"error": str(e)}
        return ok(r)

    def status_get(name: str):
        return JSONResponse({"jobs": jobs(name)})

    async def promote_post(request: Request):
        b = await request.json()
        import anyio
        try:
            r = await anyio.to_thread.run_sync(lambda: promote(b.get("name", ""), b.get("take", "")))
        except ValueError as e:
            r = {"error": str(e)}
        return ok(r)

    async def thread(fn):
        import anyio
        try:
            return ok(await anyio.to_thread.run_sync(fn))
        except ValueError as e:
            return ok({"error": str(e)})
        except SystemExit as e:
            return ok({"error": str(e)})

    async def take_remove_post(request: Request):
        b = await request.json()
        return await thread(lambda: take_remove(b.get("name", ""), b.get("take", ""), b.get("mode", "")))

    async def convert_post(request: Request):
        b = await request.json()
        return await thread(lambda: convert(b.get("name", "")))

    def piece_get(name: str, k: str):
        try:
            f = _dir(name) / "cache" / f"{k}.wav"
        except ValueError as e:
            return ok({"error": str(e)})
        if not re.match(r"^[0-9a-f]{16}$", k) or not f.is_file():
            return JSONResponse({"error": "not cached (rebuild)"}, status_code=404)
        return FileResponse(f, media_type="audio/wav", headers={"Cache-Control": "max-age=86400"})

    async def fit_get(name: str):
        return await thread(lambda: fit_report(name))

    async def fitall_post(request: Request):
        b = await request.json()
        pis = b.get("pis")
        return await thread(lambda: fit_all(b.get("name", ""), [int(x) for x in pis] if pis is not None else None))

    async def ab_get(name: str, pi: int, fix: int = 1):
        import anyio
        try:
            f = await anyio.to_thread.run_sync(lambda: ab_audio(name, int(pi), bool(fix)))
        except (ValueError, IndexError, KeyError, SystemExit) as e:
            return ok({"error": str(e)})
        return FileResponse(f, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    async def import_post(request: Request):
        name = request.query_params.get("name", "")
        form = await request.form()
        f = form.get("file")
        if f is None:
            return ok({"error": "no file"})
        data = await f.read()
        return await thread(lambda: add_import(name, f.filename or "import.wav", data))

    def imports_get(name: str):
        try:
            return JSONResponse(imports(name))
        except ValueError as e:
            return ok({"error": str(e)})

    async def export_post(request: Request):
        b = await request.json()
        return await thread(lambda: export_splice(b.get("name", ""), b.get("take", "")))

    async def mixdown_post(request: Request):
        b = await request.json()
        return await thread(lambda: export_mix(b.get("name", ""), b.get("section")))

    def file_get(name: str, path: str):
        try:
            return _static(_dir(name), path)
        except ValueError as e:
            return ok({"error": str(e)})

    def library_page():
        return _static(PAGE, "library.html")

    def app_page():                    # the tab host: every page in a kept-alive iframe (ui/app.js)
        return _static(UI, "app.html")

    orig = routes.App.create_app

    def create_app(*a, **kw):
        app = orig(*a, **kw)
        app.add_api_route("/sa3/page/library", library_page, methods=["GET"])
        app.add_api_route("/sa3/app", app_page, methods=["GET"])
        app.add_api_route("/sa3/composer/convert", convert_post, methods=["POST"])
        app.add_api_route("/sa3/composer/take_remove", take_remove_post, methods=["POST"])
        app.add_api_route("/sa3/composer/freeze", freeze_post, methods=["POST"])
        app.add_api_route("/sa3/composer/version", version_post, methods=["POST"])
        app.add_api_route("/sa3/composer/revert", revert_post, methods=["POST"])
        app.add_api_route("/sa3/composer/piece", piece_get, methods=["GET"])
        app.add_api_route("/sa3/composer/fit", fit_get, methods=["GET"])
        app.add_api_route("/sa3/composer/fitall", fitall_post, methods=["POST"])
        app.add_api_route("/sa3/composer/ab", ab_get, methods=["GET"])
        app.add_api_route("/sa3/composer/import", import_post, methods=["POST"])
        app.add_api_route("/sa3/composer/imports", imports_get, methods=["GET"])
        app.add_api_route("/sa3/composer/export", export_post, methods=["POST"])
        app.add_api_route("/sa3/composer/mixdown", mixdown_post, methods=["POST"])
        app.add_api_route("/sa3/composer/file", file_get, methods=["GET"])
        app.add_api_route("/sa3/page/composer", page_get, methods=["GET"])
        app.add_api_route("/sa3/page/composer/{path:path}", page_file, methods=["GET"])
        app.add_api_route("/sa3/ui/{path:path}", ui_get, methods=["GET"])
        app.add_api_route("/sa3/composer/starred", starred_get, methods=["GET"])
        app.add_api_route("/sa3/composer/projects", projects_get, methods=["GET"])
        app.add_api_route("/sa3/composer/project", project_get, methods=["GET"])
        app.add_api_route("/sa3/composer/save", save_post, methods=["POST"])
        app.add_api_route("/sa3/composer/ref", ref_get, methods=["GET"])
        app.add_api_route("/sa3/composer/render", render_post, methods=["POST"])
        app.add_api_route("/sa3/composer/status", status_get, methods=["GET"])
        app.add_api_route("/sa3/composer/promote", promote_post, methods=["POST"])
        ours = [r for r in app.router.routes if getattr(r, "path", "").startswith("/sa3/")]
        app.router.routes[:] = ours + [r for r in app.router.routes if r not in ours]
        return app
    routes.App.create_app = staticmethod(create_app)
