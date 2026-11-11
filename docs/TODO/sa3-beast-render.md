# SA3 remote render: the studio's queue renders on the RTX 3090 box

**Status: built and tested on the box (2026-11-11); Mac-side checks open.** How it works, why, traps and runbook: [reference/sa3-beast-render.md](../reference/sa3-beast-render.md).

## Open

1. **Noise parity Mac ↔ box (the Phase 0 gate's second half).** On the Mac: `stable-audio-3/optimized/mlx/.venv/bin/python sa3lab/beast/parity.py dump --out outputs/beast-parity/mac`, then `rsync -a rednax-beast:SSD2TB/Github_repos/AI_Audio/outputs/beast-parity/beast outputs/beast-parity/` and `… parity.py compare outputs/beast-parity/mac outputs/beast-parity/beast`. Bit-identical noise = seeds mean the same on both machines. If it differs: record that seeds are backend-specific and add `gen.backend.host` to the restore-settings match (library.restore_settings).
2. **Acceptance items only the Mac can run:** an `anarchy:` *fit* spec render (the fits live only on the Mac; `make start` deploys them), Extend from the UI, Skip with jobs waiting in the real gradio queue, the chip in the page, and Generate-click-to-history wall time of a 120 s render over the home network.
3. **Upstream LoRA un-merge drifts the cached DiT** (`_reconcile_lora` in `stable-audio-3/optimized/mlx/scripts/sa3_gradio.py`): after one LoRA job, a plain render of the same seed differs slightly (max |Δ| 0.013, corr 0.999996), on the Mac and the box alike. A fix would keep the base weights of LoRA'd modules and restore them on clear.

## Shipped

Phase 0 speed and parity numbers are logged in the research Claude Doc (Log, 2026-11-11).
All four phases: `engine.py` (shared render path + `CAPTURES`), `beast/worker.py` (systemd unit `sa3-worker`, live on the box), `remote.py` + the Render-on chip, `gen.backend` + per-host speed models, the raw archive + `outputs/` mirror (`scripts/archive.sh`, `make archive` / `archive-check`), deploy on `make start` (`scripts/beast.sh`). Box-side acceptance (`outputs/beast-tests/e2e/`, see its context.md): t2a, a2a, inpaint curve, LoRA step window come back bit-identical to a local render; anarchy `@file` (same `.cond.npz` and `anarchy` block), condmix + bank anchors (same `.conds.npz`), cfg 3 + negative, Stop, render errors, busy / mismatch / unreachable / worker-restart fallbacks, raw copies, mirror never deletes and picks up label edits.

## Later (not this spec)

- Library rows for clips that exist only in the archive, with a "fetch back" button.
- Moving `embed.py`'s SAME-L similarity encoding to the box.
- Batch or anarchy sessions submitted straight to the worker, without the gradio queue.
- ComfyUI idles at ~17.5 GB on the box, which keeps the worker "busy"; an opt-in "ask ComfyUI to free its cache when its queue is empty" would make Beast available far more often.
