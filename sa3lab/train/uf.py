#!/usr/bin/env python3
"""Drive the underfit dashboard API from the shell (stdlib only, any python3).

Launching a run here goes through the same /api/runs/new path as the dashboard's
+ Finetune button, so the run shows up in the UI with loss curves and demos.
The dashboard must be running (underfit/CLAUDE.md, "Commands").

  uf.py launch <payload.json> [--name N] [--max-steps N] [--checkpoint-every N]
                              [--demo-every N] [--dry-run]
  uf.py status [run-id]       progress, loss, latest checkpoints/demos (default: newest run)
  uf.py runs                  list all runs
  uf.py collect [run-id] [--out DIR]   copy demo mp3s to outputs/underfit-<name>/ with
                              descriptive names (step, demo, sampler, prompt, seed); re-runnable
  uf.py resume <run-id> [--checkpoint PATH]   restart a dead run from its latest checkpoint
  uf.py kill <run-id>         stop a run (keeps its checkpoints)

The trainer runs in its own session (os.setsid), so it survives the dashboard and
any agent shell dying. Start the dashboard itself detached too (setsid nohup).

Payload format = what index.html's confirmNewFinetune() POSTs. Keys starting
with "_" (comments) are stripped before sending, also inside demo_cond entries.
Unlike the UI, the API has no default layer filter: put lora_exclude in the payload.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("UNDERFIT_URL", "http://127.0.0.1:8787")


def _req(path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} on {path}: {e.read().decode()[:2000]}")
    except urllib.error.URLError as e:
        sys.exit(f"dashboard not reachable at {BASE} ({e.reason}) — start it first")


def _strip_comments(obj):
    if isinstance(obj, dict):
        return {k: _strip_comments(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_comments(v) for v in obj]
    return obj


def launch(a):
    with open(a.payload) as f:
        p = _strip_comments(json.load(f))
    for key, val in (("name", a.name), ("max_steps", a.max_steps),
                     ("checkpoint_every", a.checkpoint_every), ("demo_every", a.demo_every)):
        if val is not None:
            p[key] = val
    ds = {d["id"]: d for d in _req("/api/datasets")}
    if p["dataset_id"] not in ds:
        sys.exit(f"dataset {p['dataset_id']!r} not found; have: {sorted(ds)}")
    if ds[p["dataset_id"]].get("status") != "ready":
        sys.exit(f"dataset {p['dataset_id']!r} is {ds[p['dataset_id']].get('status')!r}, not ready")
    if any(r.get("display_name") == p["name"] for r in _req("/api/runs")):
        sys.exit(f"a run named {p['name']!r} already exists — pass --name")
    print(f"launching {p['name']}: {p['max_steps']} steps, ckpt every {p['checkpoint_every']}, "
          f"demos every {p['demo_every']} ({len(p.get('demo_cond', []))} demos)")
    if a.dry_run:
        print(json.dumps(p, indent=1))
        return
    print(json.dumps(_req("/api/runs/new", p), indent=1))


def _newest_run_id():
    runs = _req("/api/runs")
    if not runs:
        sys.exit("no runs yet")
    return max(runs, key=lambda r: r.get("created_at", ""))["id"]


def status(a):
    rid = a.run or _newest_run_id()
    q = urllib.parse.quote(rid)
    s = _req(f"/api/status?run={q}")
    hist = s.get("loss_history") or []
    tail = [h["loss"] for h in hist[-200:]]
    avg = sum(tail) / len(tail) if tail else float("nan")
    print(f"{rid}: {s.get('status')}  step {s.get('global_step')}/{s.get('max_steps')} "
          f"({s.get('progress_pct')}%)  loss {s.get('loss')}  avg(last {len(tail)}) {avg:.4f}  "
          f"lr {s.get('lr')}")
    ck = _req(f"/api/checkpoints?run={q}").get("checkpoints", [])
    for c in sorted(ck, key=lambda c: c["step"]):
        print(f"  ckpt step {c['step']:>6}  {c['path']}")


def _slug(text):
    # Drop the boilerplate every prompt shares so the distinguishing part fits the name.
    for boiler in ("TrackType: Music,", "VocalType: Instrumental,", "TrackType: Music", "VocalType: Instrumental"):
        text = text.replace(boiler, "")
    keep = "".join(c if c.isalnum() else "-" for c in text.lower())
    return "-".join(p for p in keep.split("-") if p)[:40] or "uncond"


def collect(a):
    """Copy a run's demo mp3s into an outputs/ folder with descriptive names."""
    import shutil
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    rid = a.run or _newest_run_id()
    runs_dir = root / "underfit" / "state" / "runs"
    cfg = json.loads((runs_dir / f"{rid}_model.json").read_text())
    demos = cfg["training"]["demo"]["demo_cond"]
    name = rid.rsplit("-", 1)[0]
    out = Path(a.out or root / "outputs" / f"underfit-{name}")
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for mp3 in sorted((runs_dir / rid / "demos").glob("demo_*_*.mp3")):
        _, idx, step = mp3.stem.split("_")
        d = demos[int(idx)]
        kind = f"ARC-cfg{d.get('cfg', 1):g}" if d.get("arc") else f"RF-cfg{d.get('cfg', 7):g}"
        dst = out / f"sa3med_{name}_step{int(step):05d}_d{idx}_{kind}_{_slug(d.get('prompt', ''))}_s{d.get('seed')}.mp3"
        if not dst.exists():
            shutil.copy2(mp3, dst); n += 1
    print(f"copied {n} new demos → {out}")


def resume(a):
    """Restart a dead/stopped run from its latest checkpoint (or --checkpoint PATH)."""
    body = {"checkpoint_path": a.checkpoint} if a.checkpoint else {}
    print(json.dumps(_req(f"/api/runs/{urllib.parse.quote(a.run)}/resume", body), indent=1))


def kill(a):
    print(json.dumps(_req(f"/api/runs/{urllib.parse.quote(a.run)}/kill", {}), indent=1))


def runs(_a):
    for r in sorted(_req("/api/runs"), key=lambda r: r.get("created_at", "")):
        print(f"{r['id']:<45} {r.get('status'):<10} max_steps={r.get('max_steps')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("launch"); l.add_argument("payload")
    l.add_argument("--name"); l.add_argument("--max-steps", type=int)
    l.add_argument("--checkpoint-every", type=int); l.add_argument("--demo-every", type=int)
    l.add_argument("--dry-run", action="store_true"); l.set_defaults(fn=launch)
    s = sub.add_parser("status"); s.add_argument("run", nargs="?"); s.set_defaults(fn=status)
    r = sub.add_parser("runs"); r.set_defaults(fn=runs)
    c = sub.add_parser("collect"); c.add_argument("run", nargs="?"); c.add_argument("--out")
    c.set_defaults(fn=collect)
    rs = sub.add_parser("resume"); rs.add_argument("run"); rs.add_argument("--checkpoint")
    rs.set_defaults(fn=resume)
    k = sub.add_parser("kill"); k.add_argument("run"); k.set_defaults(fn=kill)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
