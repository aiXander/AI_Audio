"""Push a list of render jobs into the RUNNING studio's queue, as if Generate were clicked.

Jobs go through the studio's own `/generate` endpoint (gradio_client), so they appear in the
Queue modal, can be reordered/deleted there, render with every studio patch (anarchy specs,
sidecars, tempo analysis) and land in the folder given here (the hidden outdir input that
outdir.py binds per job), not necessarily the folder on screen.

  stable-audio-3/optimized/mlx/.venv/bin/python sa3lab/anarchy/enqueue.py jobs.json \
      [--outdir anarchy/session-1] [--window 8] [--url http://127.0.0.1:7860/]

At most `--window` of our jobs wait in the studio queue at once (a gradio_client stalls with
~17 open jobs; the rest never joined) and your own Generate clicks interleave with them.
Re-running the same jobs.json skips jobs already rendered in the folder
(prompt + seed + cfg + LoRA set with strengths, so one prompt+seed at two checkpoints are two jobs).

jobs.json: a list of {"prompt": str, "seed": int|null, "seconds": 60, "cfg": 1.0,
"steps": 8, "apg": 1.0, "neg": "", "loras": [{"path": str, "strength": 1.0}]}; only "prompt"
is required. "loras" (max 3) fills the studio's LoRA slots, each slot sent as upstream's
(file, dropdown pick, strength, from step, to step): the path goes in the dropdown slot, which
must be a file under stable-audio-3/loras/ (a bare name resolves there), active on all steps.
No "loras" = three empty slots = a base render.

Keep this process alive until it exits: gradio drops a client's waiting jobs when its
connection closes. Run it in the background; it prints one line per finished job.
The open page picks them up by itself: its Queue badge counts the server queue (studio.js)
and its history reloads when a render lands in the folder it shows (outdir.js live view).
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from collections import deque
from pathlib import Path

from gradio_client import Client

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = ROOT / "outputs"
LORA_LIB = ROOT / "stable-audio-3" / "loras"      # the studio's dropdown lists str() of these paths


def _lora_path(p: str) -> str:
    q = Path(p)
    q = (q if q.is_absolute() else LORA_LIB / q).resolve()
    if not q.is_file():
        raise SystemExit(f"LoRA not found: {q}")
    return str(q)


def _lora_key(loras) -> tuple:
    return tuple(sorted((Path(x["path"]).name, round(float(x.get("strength", 1.0)), 3)) for x in loras or []))


def _key(j: dict) -> tuple:
    return (j["prompt"], str(j.get("seed")), float(j.get("cfg", 1.0)), _lora_key(j.get("loras")))


def _done(out: Path) -> set[tuple]:
    """(prompt, seed, cfg, LoRA set) of every render already in the folder, so a re-run skips them."""
    s = set()
    for f in out.glob("*.json"):
        try:
            d = json.loads(f.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        g = d.get("gen") if isinstance(d, dict) else None      # a jobs.json in the folder is not a sidecar
        if g:
            s.add((g.get("prompt"), str(g.get("seed")), float(g.get("cfg") or 1.0), _lora_key(g.get("loras"))))
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("jobs")
    ap.add_argument("--outdir", default=None, help="folder under outputs/ (default: the studio's active one)")
    ap.add_argument("--url", default="http://127.0.0.1:7860/")
    ap.add_argument("--window", type=int, default=8, help="max jobs of ours waiting in the studio queue")
    args = ap.parse_args()
    jobs = json.load(open(args.jobs))
    outdir = args.outdir or json.load(urllib.request.urlopen(args.url.rstrip("/") + "/sa3/outdir"))["active"]
    for j in jobs:
        if len(j.get("loras") or []) > 3:
            raise SystemExit(f"max 3 LoRAs per job: {j['prompt'][:60]}")
        for x in j.get("loras") or []:
            x["path"] = _lora_path(x["path"])
    have = _done(OUTPUTS / outdir)
    todo = [(i, j) for i, j in enumerate(jobs) if _key(j) not in have]
    print(f"{len(jobs) - len(todo)} already rendered, queueing {len(todo)} → outputs/{outdir}", flush=True)
    c = Client(args.url, verbose=False)
    live: deque = deque()

    def finish(f, i, j):
        try:
            # the studio reports its own errors as a status string ("error: …"), not an exception
            err = [x for x in (f.result() or ()) if isinstance(x, str) and "error:" in x]
            if err:
                raise RuntimeError(re.sub(r"<[^>]+>", "", err[0]))
            print(f"done  {i:>3}  {_tag(j)}  {j['prompt'][:80]}", flush=True)
        except Exception as e:  # noqa: BLE001  (one failed job must not drop the rest)
            print(f"FAIL  {i:>3}  {_tag(j)}  {j['prompt'][:80]}  {e}", flush=True)

    for i, j in todo:
        while len(live) >= args.window:          # gradio_client stalls past ~17 open jobs per client
            finish(*live.popleft())
        vals = ["medium", "same-l", j["prompt"], j.get("neg", ""), float(j.get("seconds", 60)),
                j.get("steps", 8), "" if j.get("seed") is None else str(j["seed"]),
                float(j.get("cfg", 1.0)), float(j.get("apg", 1.0)), 1.0, 0.92, None, None, 0, 0,
                [], "Save to MP3 (V0)"]
        steps = int(j.get("steps", 8))
        slots = [[None, x["path"], float(x.get("strength", 1.0)), 1, steps] for x in j.get("loras") or []]
        vals += sum(slots + [[None, "", 1.0, 1, 8]] * (3 - len(slots)), [])   # empty slots = no LoRA
        vals += [outdir]
        live.append((c.submit(*vals, api_name="/generate"), i, j))
        time.sleep(0.2)                           # keep submission order = queue order
    while live:
        finish(*live.popleft())
    # a job the studio rejects (e.g. a spec its booted sampler.py can't parse) still comes back
    # as "done" from gradio_client, so count what actually landed
    done = _done(OUTPUTS / outdir)
    landed = sum(_key(j) in done for _, j in todo)
    print(f"landed {landed} of {len(todo)}" + ("" if landed == len(todo) else
          "  ← MISSING renders: check the studio log for the failed spec"), flush=True)


def _tag(j: dict) -> str:
    return "+".join(f"{n.split('-epoch')[0]}x{s:g}" for n, s in _lora_key(j.get("loras"))) or "base"


if __name__ == "__main__":
    main()
