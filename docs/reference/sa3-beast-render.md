# Remote rendering on the RTX 3090 box

The Mac's studio keeps its UI, gradio queue, sidecars and history; only the GPU work of each queued job moves to `rednax-beast` (Tailscale 100.117.34.17), which runs **the same MLX code on MLX's CUDA backend**. One cut point in `studio.py` (the `mlx_call` hook in `_install_stop_hooks`) either runs upstream's `run_generation` on the Mac's MLX thread or hands the job to `remote.render`, which returns the same `(audio (2, T) float32, timings)`. Everything after that (peak guard, gen record, uid filename, mp3, sidecar, history) is unchanged. A systemd user service on the box (`sa3-worker`) serves jobs; `make start` deploys the Mac's code to it; a background rsync mirrors `outputs/` to the box's 12 TB drive.

## Where the code lives

- `sa3lab/engine.py`: the render path both machines install (`engine.install`): noise canvas, segments sampler, condmix, condsrc, anarchy, the in-render stop/progress checks (through injected `Hooks`), and the `CAPTURES` registry.
- `sa3lab/remote.py`: Mac side. `target()` (Auto / Mac only + `/health`), `build_job` (files → blobs), `render` (stream, cancel, fetch, restore captures), `gen.backend`, the `/sa3/remote` routes, the archive thread. `remote.js/.css`: the Render-on chip next to Queue, a local / remote toggle (menu "Remote · GPU box" = pref `auto`, "Local only" = pref `mac`).
- `sa3lab/beast/worker.py`: box side (FastAPI on :7870). Route table and behaviour in its docstring. `sa3-worker.service` is its unit.
- `sa3lab/beast/codehash.py`: the render-path code hash, stdlib only (the shell scripts run it too).
- `sa3lab/beast/smoke.py`: drives the worker directly with one job. `sa3lab/beast/parity.py`: the noise/render parity + speed probe, run on both machines.
- `scripts/beast.sh` (deploy | status | logs | restart | install) and `scripts/archive.sh` (push | check); Make targets `archive`, `archive-check`, `beast`, `beast-logs`.
- `library.py`: `estimate_ms(…, host)` and `progress_start(…, host)` keep one speed model per host.

Box layout: deploy copy `~/SSD2TB/sa3-worker/AI_Audio/` (same layout as the repo, so every `Path(__file__).parents[…]` works), venv `~/SSD2TB/sa3-worker/.venv` (mlx[cuda13] + both MLX requirements files), blobs `…/AI_Audio/blobs/`, log `~/SSD2TB/sa3-worker/worker.log`, weights in the repo clone's `models/hf-cache` (`HF_HUB_CACHE` in the unit), `semantic_anarchy` in `~/SSD2TB/sa3-worker/semantic-anarchy/` (`SA_REPO`). Archive `~/12TBHDD/AI_Audio/{raw,outputs}/`.

## Why it's built this way

- **Same MLX code, not a torch port.** Every studio render hack patches MLX functions, and the seed → noise mapping draws with `mx.random.normal`. On MLX-CUDA nothing is ported and a new hook works remotely for free. A torch port would give other noise per seed and every sampler hack twice.
- **The cut sits at `run_generation`**, because it is a pure function (arguments + input files in, audio out). The worker never knows about output folders, sidecars or gradio.
- **The Mac deploys code to the box on every `make start`**, rather than the box serving from its git clone: agents edit `sa3lab/` uncommitted all day, so a git-pinned box would almost always mismatch and Auto would silently fall back. The worker restarts only when the hash changed (a restart costs the model reload). The deploy runs in the background and never blocks or fails the studio.
- **The Mac resolves every file; the worker never reads the Mac's outputs/.** Input audio, LoRAs, `anarchy: @file` references and condmix anchors (as their exact `(256, 768)` tensors via `condsrc.expand` + `condsrc.tokens`) go up as content-addressed blobs (`blob:<sha256>.<ext>` in the job). A mirror-based design was rejected because the mirror is only as fresh as the last rsync.
- **Audio comes back float32.** SAME-L decodes routinely peak above 0 dBFS; integer PCM in transit would hard-clip before the Mac's peak guard sees it.
- **One job at a time, no second queue:** gradio already serializes jobs, and Mac + box in parallel would add only ~7 % throughput.
- **No auth beyond Tailscale.** The worker binds the Tailscale address only; every path it writes is chosen by the worker.
- **Failure policy (Auto):** a box failure (network, HTTP error, a worker crash, a CUDA error such as out-of-memory, flagged `fatal`) logs one `[remote]` line and re-renders on the Mac. A render error (bad spec, bad LoRA, a condmix anchor that won't resolve) is re-raised with the worker's type and message, so upstream's error line reads as a local one, and it is not retried.
- **GPU sharing:** another process above 8 GB of VRAM (a training run, ComfyUI) makes the box busy, so the Mac renders. The worker also drops its own models at once then, and after 15 idle minutes.

## Invariants and traps

- **Side channels must be captured.** `persist.save` on the Mac reads state left behind during a render: `anarchy_ui._state["info"]` (the `anarchy` block + `<audio>.cond.npz`) and `condsrc._banks` (the `.conds.npz` bank). Remotely that state is born on the box, so each lives in `engine.CAPTURES` (export on the worker, restore on the Mac). **A new module that stashes state for `persist.save` must register a capture**, or its remote renders silently lose the side file: no error, no failing test.
- **condsrc finds a bank by object identity** (`id(gen.segments.cond)`). The restore registers the box's tensors under the Mac's own plan object, with the Mac's anchor descriptors (`condmix.parse` of the Mac plan), never the job's rewritten ones.
- **The anarchy info is keyed by the original prompt string.** The job's prompt has blob paths in it; the restore keys by the Mac's prompt and maps `source_file` back to the outputs-relative form a local render records (`remote.recorded`).
- **Blobs must live inside the AI_Audio tree on the box:** `condsrc.tokens` only loads `.npz` files under `AI_AUDIO`. Hence `SA3_BLOBS=…/sa3-worker/AI_Audio/blobs`.
- **The code-hash file set and the deploy rsync excludes must agree** (`codehash.EXCLUDE` ↔ `beast.sh` `CODE_EXCL`), or the hashes never match and the chip stays on Mac. Only `*.py` counts, so a JS/CSS edit never restarts the worker.
- **The studio hashes its code once at start.** An agent's `.py` edit after `make start` doesn't change it; the next `make start` deploys and re-hashes both sides.
- **Wrapper order:** `engine.install` must finish all `up.run_generation` wrapping before `_install_stop_hooks` reads it. Keep it called before `_install_stop_hooks` in `studio.main`.
- **The worker enters `segments.active(plan)` itself** with the plan from the job; `segments.current()` is a Mac handler-thread value and is meaningless on the box.
- **Speed models are per host** (`gen.backend.host`, absent = `mac`). Mixing the ~10× faster box rows into the Mac's estimate would ruin both progress bars.
- **Never write into an unmounted 12 TB mount point** (`nofail`: unmounted it is an empty dir on `/home`). The worker checks `os.path.ismount` and falls back to `sa3-worker/scratch/`; `archive.sh` refuses to run.
- **The mirror never deletes:** `rsync -a` with no delete flags, ever. `outputs/starred/` symlinks dangle in the mirror, which is harmless (the real files and `label.starred` are mirrored).
- **ComfyUI on the box keeps ~17.5 GB of VRAM cached even when idle**, which is above the busy threshold, so Beast is unavailable whenever ComfyUI is running.

## Runbook

| Symptom | Cause | Fix |
|---|---|---|
| Chip says Mac, "box unreachable" | box off, Tailscale down, or worker crashed | `make beast-logs`; on the box `systemctl --user status sa3-worker` |
| Chip says Mac, "box runs other code" | edits since the last deploy, or deploy failed | `make start` (or `scripts/beast.sh deploy`) |
| Chip says Mac, "GPU in use by another process" | training / ComfyUI above 8 GB | wait, or free that process's VRAM; `SA3_OTHER_GPU_GB` in the unit changes the threshold |
| `[remote] box failed (… out of memory)` | another process grew mid-render | the job already re-rendered on the Mac; nothing to do |
| `archive: the box's ~/12TBHDD isn't … mounted` | the HDD didn't mount | on the box: `sudo mount -a` |
| Test the box alone | — | `python3 sa3lab/beast/smoke.py --code-hash auto`; fake busy: `echo 20 > ~/SSD2TB/sa3-worker/fake_busy` (remove to clear) |
