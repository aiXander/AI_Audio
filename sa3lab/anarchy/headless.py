"""Run the SA3 studio headlessly and render a list of jobs into any folder, studio format.

Format fidelity is the point: renders must be indistinguishable from studio renders
(audio named `prompt.seed.uid8.mp3`, `<audio>.json` sidecar with upstream's entry fields +
library.py's `gen` regen block + `analysis` tempo block), so every tool reading
`outputs/sa3-gradio/` reads these too. Rather than re-implementing that format, `run()`:

1. points upstream's `sa3_gradio.OUTPUT_DIR` at the target folder *before* `studio` is
   imported (studio's prefs path and `library.init` both read it);
2. swaps `gr.Blocks.launch` for the batch loop and calls `studio.main()`: every studio
   patch is applied and studio's own launch wrapper wires its handlers
   (`_wire_handlers`: gen-block capture, uid naming, `persist.save`) before handing the
   built demo to us instead of starting a server;
3. calls the wired `generate` handler once per job with the UI's default control values
   (read off the components), only the prompt, seed and any `overrides` swapped in;
4. queues each render's tempo analysis the way the UI does (`library.read_label`) and
   waits for it to drain.

**Injected conditioning** (the anarchy seam): a job with `cond` (a `(256, 768)` padded
conditioning tensor) is rendered by replacing `sa3_gradio.get_t5()` with a shim whose
`encode()` returns that tensor and an all-ones mask for the job's label, so
`apply_prompt_padding` passes it through untouched and the DiT sees exactly `cond`. The
label becomes the "prompt" (and so the filename); the job's `extra` dict is merged into
the sidecar (e.g. an `anarchy` block pointing at the saved conditioning).

Resumable: jobs whose label already has a sidecar in the folder are skipped. One call per
process (studio.main() is not re-entrant).
"""
from __future__ import annotations

import copy
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
AI_AUDIO = HERE.parents[1]
MLX = AI_AUDIO / "stable-audio-3" / "optimized" / "mlx"
os.environ.setdefault("HF_HUB_CACHE", str(AI_AUDIO / "models" / "hf-cache"))


@dataclass
class Job:
    label: str                       # the prompt (or, with cond, the name shown + filename)
    seed: int | None = None          # None = UI default (random)
    cond: np.ndarray | None = None   # (256, 768) padded conditioning, replaces the T5 encode
    extra: dict = field(default_factory=dict)   # merged into the sidecar


def _done_labels(out_dir: Path) -> set[str]:
    done = set()
    for sc in out_dir.glob("*.json"):
        try:
            d = json.loads(sc.read_text())
            done.add((d.get("gen") or {}).get("prompt") or d.get("prompt"))
        except Exception:
            pass
    return done


def run(out_dir: Path, jobs: list[Job], overrides: dict | None = None,
        dry_run: bool = False) -> None:
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(MLX)  # upstream resolves weight paths relative to here
    sys.path[:0] = [str(MLX), str(MLX / "scripts"), str(AI_AUDIO / "sa3lab")]
    import mlx.core as mx
    import sa3_gradio as up

    up.OUTPUT_DIR = out_dir
    os.environ["SA3_OUTDIR"] = str(out_dir)   # outdir.py: pin the studio's active folder, don't persist
    import gradio as gr
    import library
    import studio

    # ── conditioning injection ──
    injected: dict[str, np.ndarray] = {}
    real_get_t5 = up.get_t5

    class _Shim:
        def encode(self, prompts, max_len=256):
            c = injected.get(prompts[0]) if len(prompts) == 1 else None
            if c is None:
                return real_get_t5().encode(prompts, max_len=max_len)
            assert c.shape == (max_len, 768), c.shape
            return mx.array(c[None].astype(np.float16)), mx.ones((1, max_len), dtype=mx.int32)

        def __getattr__(self, name):
            return getattr(real_get_t5(), name)

    shim = _Shim()
    up.get_t5 = lambda: shim

    def batch(demo, *a, **kw):
        bf = next(f for f in demo.fns.values() if f.name == "generate")
        comps = [demo.blocks[r] if isinstance(r, int) else r for r in bf.inputs]
        defaults = [copy.deepcopy(getattr(c, "value", None)) for c in comps]
        labels = [getattr(c, "label", None) for c in comps]
        for k, v in (overrides or {}).items():
            defaults[labels.index(k)] = v
        i_prompt, i_seed = labels.index("Prompt"), labels.index("Seed")
        print("[headless] control values:", flush=True)
        for lab, v in zip(labels, defaults):
            if lab != "Prompt":
                print(f"    {lab!s:>22} = {v!r}"[:140], flush=True)
        if dry_run:
            return
        done = _done_labels(out_dir)
        todo = [j for j in jobs if j.label not in done]
        print(f"[headless] {len(jobs)} jobs, {len(jobs) - len(todo)} done, {len(todo)} to go "
              f"→ {out_dir}", flush=True)
        t_start = time.time()
        for n, job in enumerate(todo, 1):
            vals = copy.deepcopy(defaults)
            vals[i_prompt] = job.label
            if job.seed is not None:
                vals[i_seed] = str(job.seed)
            if job.cond is not None:
                injected[job.label] = job.cond
            t0 = time.time()
            out = bf.fn(*vals)
            injected.pop(job.label, None)
            state = out[-1] if isinstance(out, tuple) else None
            entry = state.get("current") if isinstance(state, dict) else None
            if not entry or entry.get("prompt") != job.label:
                err = next((o for o in out if isinstance(o, str) and "error" in o), "no entry")
                print(f"[headless] {n}/{len(todo)} FAILED {job.label}: {err}", flush=True)
                continue
            if job.extra:
                sc = Path(entry["path"]).with_name(Path(entry["path"]).name + ".json")
                d = json.loads(sc.read_text())
                d.update(job.extra)
                sc.write_text(json.dumps(d))
            library.read_label(Path(entry["path"]).name)   # queue tempo analysis, as the UI does
            eta = (time.time() - t_start) / n * (len(todo) - n)
            print(f"[headless] {n}/{len(todo)} seed {entry['seed']} {time.time() - t0:.0f}s "
                  f"(eta {eta / 60:.0f} min)  {job.label[:80]}", flush=True)
        while not library._tq.empty():
            time.sleep(1)
        time.sleep(5)
        print(f"[headless] done in {(time.time() - t_start) / 60:.1f} min → {out_dir}", flush=True)

    gr.Blocks.launch = batch
    sys.argv = [sys.argv[0]]
    studio.main()
