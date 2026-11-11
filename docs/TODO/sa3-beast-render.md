# SA3 remote render: the studio's queue renders on the RTX 3090 box

**Status: spec, nothing built yet.** Xander will clone this repo on the GPU box (`ssh rednax-beast`) and an agent builds it there. **Option A** (decided 2026-11-10): the box runs **the same MLX code as the Mac**, on MLX's CUDA backend. Option B (porting the render path to upstream's PyTorch code) is only the fallback if the Phase 0 spike fails.

**Build all phases in one push and test once at the end** (Xander's standing preference). The exception is Phase 0: it is a go/no-go gate and runs first.

## Goal

The studio on the Mac stays exactly what it is: UI, gradio queue, Queue modal, sidecars, history, labels, Library, Composer. Only the GPU work of each queued job moves to the box. The finished clip comes back and lands where it would have landed locally, with the same sidecar.

The box also keeps a **permanent archive** on its 12 TB drive:
- every render, lossless and as the decoder produced it;
- a mirror of the Mac's whole `outputs/` tree (audio, sidecars, folder names, labels).

With the archive in place Xander can clear space on the Mac and keep all raw data on the box.

Measured baseline (2026-11-07, Medium, 8 steps, 120 s clip):

| | M2 studio (median of sidecars) | 3090, upstream torch fp16 |
|---|---|---|
| sampling | 18.3 s | 1.0 s |
| full render | 31.4 s (SAME-L decode 12.7 s) | 1.7 s (SAME-S decode; SAME-L not measured) |
| CFG 3 | ~39 s (90 s clip) | 2.6 s |

MLX-CUDA will be slower than torch; Phase 0 measures by how much.

## What Xander sees

- **A Render-on chip in the top bar, next to Queue.** It shows `Beast` (green) while the box is online and running the same code, or `Mac` otherwise. Clicking it opens a two-item menu: **Auto** (Beast when available, else Mac; the default) and **Mac only**. No other new controls.
- Generate behaves as before, only much faster. The progress bar, Stop, Skip, the Queue modal and Extend all work unchanged.
- Each sidecar records where the clip was rendered (`gen.backend`).
- `make archive` pushes the Mac's `outputs/` to the box on demand; the studio also does it in the background.
- `make archive-check` lists Mac files that are **not** yet safely on the box. It is the "safe to delete?" answer before a cleanup.

## Architecture

```
Mac (studio, :7860)                                     rednax-beast (RTX 3090)
──────────────────                                      ───────────────────────
gradio queue → generate handler
  └ studio mlx_call wrapper (sa3lab/studio.py
    _install_stop_hooks), the ONE cut point:
      local:  MLX owner thread → up.run_generation
      remote: sa3lab/remote.py ──── HTTP over Tailscale ──→ sa3lab/beast/worker.py
               1 PUT /blobs/<sha> (inputs, LoRAs, tokens)    blob cache (SSD)
               2 POST /render (job json) ← NDJSON stream ─   same engine.install(up) patches
                  progress events → library.progress_*       up.run_generation(**args) on MLX-CUDA
                  final: timings + extras + audio url        raw copy → ~/12TBHDD/AI_Audio/raw/
               3 GET audio (float32 WAV)
               4 re-inject extras, return (audio, timings)
  └ unchanged: peak guard, gen record, file name, mp3, sidecar, history
background: rsync outputs/ ─────────────────────────────→ ~/12TBHDD/AI_Audio/outputs/ (mirror, never deletes)
make start: rsync render-path code ─────────────────────→ ~/SSD2TB/sa3-worker/ (deploy dir) + restart if changed
```

**Why the cut sits at `run_generation`.** Upstream's `run_generation(...)` (in `stable-audio-3/optimized/mlx/scripts/sa3_gradio.py`) is a pure function: arguments plus input files in, `(audio (2, T) float32, timings)` out. Everything after it happens on the Mac in `_generate_entry`: the peak guard, `library.gen_record`, the uid filename (`verbose_basename`), the mp3 encode, the sidecar and the history. So the Mac keeps all of that unchanged, and the worker never needs to know about outputs folders, sidecars or gradio.

## Decisions (with the why)

1. **Same code on both machines (option A).** All of the studio's render hooks patch MLX functions. Running them on MLX-CUDA means nothing gets ported, new hooks work remotely for free, and the seed → noise mapping (`noise.py`, a canvas drawn with `mx.random.normal`) should stay identical. A torch port would give different noise per seed, and every later sampler hack would have to be written twice. The hooks in question:
   - `noise.py`: a seed's noise is a fixed canvas cropped to the clip length;
   - `segments.py`: the strength-curve sampler for inpaint;
   - `condmix.py`: Composer conditioning blended per latent frame;
   - `anarchy_ui.py`: sampled conditioning for `anarchy: …` prompts;
   - LoRA step windows.
2. **The Mac is the source of truth for code; the box runs a deploy copy.** On every `make start` the Mac rsyncs the render-path code to a deploy dir on the box. Rejected alternative: the worker runs from the box's git clone and refuses jobs on a code mismatch. Xander commits rarely and agents edit `sa3lab/` uncommitted all day, so the box would be stale nearly always and Auto would silently fall back to the Mac. The box's git clone stays for *developing* the worker; the deploy dir is what serves jobs.
3. **The Mac resolves files; the worker never reads the Mac's `outputs/`.** The render hooks read files during a render: a2a/inpaint audio, LoRA weights, `.cond.npz` / `.conds.npz` conditioning, sidecars looked up by render id. The dispatcher resolves every reference on the Mac, uploads the bytes as content-addressed blobs and rewrites the job to blob paths. Rejected alternative: point the worker at the archive mirror. It is only as fresh as the last rsync, so a just-starred render would be missing.
4. **Audio travels as float32, never integer PCM.** SAME-L decodes routinely peak above 0 dBFS (+0.1 to +0.8 dB). The Mac's peak guard (`_peak_guard` in `studio.py`) limits them before saving. FLAC or int16 in transit would hard-clip first and defeat it. 120 s of float32 stereo is about 42 MB, roughly a second on the home network.
5. **One job at a time, no second queue.** gradio already serializes generate jobs, and the worker is a single slot (`409 busy` if a second job arrives). Running Mac and box in parallel would add only about 7% throughput (the Mac is ~10× slower), so it is out of scope.
6. **No auth beyond Tailscale.** The worker binds to the box's Tailscale IP only, never `0.0.0.0`. The tailnet holds only Xander's own devices. Blob names are always `<sha256>.<ext>` chosen by the worker, and no client-supplied path ever touches the filesystem.

## Components

### 1. `sa3lab/engine.py`: the render path in one place (refactor, Mac and box)

Today `studio.main()` installs render-path patches and UI patches interleaved. Move the render-path ones into `engine.install(up, library, persist, hooks)`, which both `studio.main()` and the worker call:
- `library.init(up)` and `noise.install(library)`;
- `segments.install(up, library)` and `condmix.install(up)`;
- `condsrc.install(persist)` and `anarchy_ui.install(up, persist)`;
- from `_install_stop_hooks`, the parts that hook *inside* a render: the stop/progress checks around `sample_flow_pingpong`, `get_decoder` (`_StoppableDecoder`), `get_encoder` and `get_dit`.

Make those checks call injected callbacks (`hooks.check_stop()`, `hooks.progress_step(i)`, `hooks.progress_phase(name)`) instead of the module globals `_check_stop` and `library.progress_*`. The studio passes its current functions; the worker passes its cancel flag and its event stream.

Order matters: the wrappers stack on `up.run_generation` (anarchy_ui, segments and condmix each wrap it), so keep today's order exactly.

**Side-channel registry.** Two modules leave state behind during a render that `persist.save` reads later on the Mac:
- `anarchy_ui._state["info"][prompt] = (info, cond)` → the sidecar's `anarchy` block + `<audio>.cond.npz`;
- `condsrc.remember(cond, anchors, curves, bags)` → `<audio>.conds.npz`.

On a remote render that state is born on the box. Add a small registry, `engine.CAPTURES`: each such module registers `export() -> json + arrays` (worker side, after the render) and `restore(payload, job)` (Mac side, before `persist.save`). Both current channels register. A future module that stashes state for `persist.save` must register too: say so in a comment at the registry and in `docs/reference/`.

**Acceptance:** after the refactor the local studio behaves identically. Check with a 6 s render, a 6 s inpaint with a strength curve, an anarchy spec render and a 2-anchor condmix job, all in the test studio. Each sidecar diffs clean against the same job before the refactor, apart from uid, ts and timings.

### 2. `sa3lab/beast/worker.py`: the box service

FastAPI + uvicorn (already in the gradio deps), started by systemd `--user` (lingering is already on). Bound to `100.117.34.17:7870`. At startup it imports `sa3_gradio as up` without building the UI and calls `engine.install(...)`. All MLX work runs on one owner thread, the way upstream's `mlx_call` does it.

| Route | Does |
|---|---|
| `GET /health` | `{ok, busy, code_hash, mlx_version, device, loaded: [dit, decoder], vram_free_gb, other_gpu_procs, archive_free_gb, archive_ok}` |
| `HEAD /blobs/{sha}` | 200 if cached, else 404 |
| `PUT /blobs/{sha}?ext=` | store as `<blob dir>/<sha>.<ext>` after verifying the hash; idempotent |
| `POST /render` | body = job json (below). Response = NDJSON stream: `{"ev":"phase","name"}`, `{"ev":"step","i","n"}`, then one final `{"ev":"done","timings","extras","audio":"/audio/<job>"}` or `{"ev":"error","type","msg"}` |
| `GET /audio/{job}` | float32 WAV (`soundfile` subtype `FLOAT`); the copy is kept for a while after delivery, then left only in the archive |
| `POST /cancel/{job}` | sets the cancel flag; the next check raises, and the stream ends with `{"ev":"error","type":"Stopped"}` |

Behaviour:
- **Job execution:** `with segments.active(job.seg, bound): up.run_generation(**job.args)`. This mirrors the studio's `run()` in `_install_stop_hooks`, then runs the `CAPTURES` exports.
- **Busy / GPU sharing:** Xander trains LoRAs on this GPU (underfit). If another process is using more than ~8 GB of VRAM, `/health` reports `busy` and `/render` returns 503, so the Mac renders locally.
- **Idle release:** after 15 idle minutes (configurable) the worker drops upstream's model caches and calls `mx.clear_cache()`, so a training run can have the GPU. The next job pays the reload.
- **Raw archive copy:** see component 5.
- **Logging:** one line per job (job id, seconds, steps, wall time) to `~/SSD2TB/sa3-worker/worker.log`.

### 3. The job (wire format)

The Mac builds it from the `inspect.BoundArguments` of `run_generation` that the stop hooks already capture:

```json
{"job": "<uuid4 hex>", "code_hash": "<render-path hash>",
 "args": {"dit_name": "medium", "decoder_name": "same-l", "prompt": "...", "negative_prompt": "",
          "seconds": 120.0, "steps": 8, "seed": 4825, "cfg": 1.0, "apg": 1.0, "sigma_max": 1.0,
          "a2a_audio_path": "blob:<sha>.wav", "inpaint_audio_path": null, "inpaint_range_sec": null,
          "lora_specs": [{"path": "blob:<sha>.safetensors", "strength": 0.8, "steps": null}]},
 "seg": {"keys": [...], "cond": {"mode": "m1", "anchors": [{"npz": "blob:<sha>.npz"}, {"prompt": "..."}], "curves": [...]}},
 "meta": {"outdir": "sa3-gradio", "mac_host": "...", "submitted": 1793909974.9}}
```

The worker replaces every `blob:` reference with its local blob path before running.

How each kind of reference gets resolved:
- **Audio inputs (a2a, inpaint):** upload the bytes; keep the original extension (`read_audio_any` decodes by format).
- **LoRAs:** upload by hash. On the Mac, cache `sha` per `(path, size, mtime)`, because hashing a several-hundred-MB file every job costs time. Stable blob paths also keep upstream's per-path LoRA merge cache warm.
- **Condmix anchors:** run `condsrc.expand` on the Mac. File-backed anchors (`npz`, `id`, `bank`) become `{"npz": "blob:…"}` holding the `(256, 768)` tokens from `condsrc.tokens` under key `cond`; keep `splice` and `mute`. Prompt anchors stay prompts (the box encodes them with T5Gemma). Muted anchors stay as they are.
- **Anarchy `@file.cond.npz` references inside a prompt:** rewrite the path in the *job's* prompt string only. Restoring `anarchy_ui._state` on the Mac must key by the **original** prompt string, because that is what `persist.save` looks up.
- **Anarchy fits** (`models/derived/anarchy/`, 44 MB) and the `semantic-anarchy` repo (`SA_REPO`, cloned from `github.com/yaoe/semantic-anarchy`): deployed with the code (component 6), not per job.

The `meta.outdir` field exists so the box's raw copy files under the same folder name the Mac will use. It is the active output folder (`outdir.py`), relative to `outputs/`.

### 4. `sa3lab/remote.py`: the dispatcher (Mac)

The hook goes in `studio.py`'s `mlx_call` wrapper, where `res = orig_call(run, *a, **kw)` sits today:

```python
res = remote.render(bound, seg, epoch) if remote.target() == "beast" else orig_call(run, *a, **kw)
```

It runs on the gradio handler thread, so the Mac's MLX thread stays free; `embed.py`'s idle encoding can keep using it. `remote.render`:
1. builds the job and uploads the missing blobs (HEAD first);
2. streams `/render`, feeding `library.progress_start/step/phase/end`;
3. between events, checks the studio's existing stop condition (`_stop` / epoch, which Skip and Stop already set) and posts `/cancel` when it trips, then raises `Stopped` just as a local render does;
4. on `done`, fetches the audio, runs each `CAPTURES.restore`, and returns `(audio, timings)` with `timings["net_ms"]` added.

The rest of the existing path is untouched.

The other pieces:
- **`remote.target()`:** returns `"beast"` when the chip is on Auto, `/health` is ok, the box isn't busy and `code_hash` matches the Mac's. Cache `/health` for ~10 s.
- **Failure fallback (Auto only):** a network error, a 5xx or a worker crash mid-job logs one line and re-runs the job locally. A worker *render* error (bad anarchy spec, invalid inpaint range) is re-raised with the worker's message, so upstream shows it in the error line exactly as a local error. Don't re-run it locally: it would fail the same way.
- **`gen.backend`:** add it to the gen record. `{"host": "rednax-beast", "device": "RTX 3090", "mlx": "<ver>", "job": "<uuid>", "code_hash": "…", "net_ms": …}` for remote renders, `{"host": "mac"}` locally.
- **Progress estimate:** `library.estimate_ms` builds its speed model from past sidecars. It must filter rows by `gen.backend.host`. Mixing ~10×-faster box rows into the Mac's estimate makes both bars useless. Sidecars without `backend` count as `mac`.
- **UI chip:** `remote.js` + a `GET/POST /sa3/remote` route for state and the Auto / Mac-only pref, kept in `.studio_prefs.json` like other prefs. Mount it next to the Queue button. Optionally the Queue modal's running row can say `on Beast`.

### 5. The archive (box, 12 TB HDD)

Root: `~/12TBHDD/AI_Audio/` (ext4, mounted `nofail`, 9.2 TB free). It has two parts with different jobs.

- **`raw/<outdir>/<YYYY-MM-DD>/<job>.wav` + `<job>.json`**, written by the worker the moment a render finishes, before streaming `done`.
  - The WAV is float32, exactly what the decoder produced (no limiter, no mp3).
  - The json is the full job (args with blob shas, seg, meta), the timings and the capture exports.
  - The blobs a job used are hard-linked or copied to `raw/_blobs/` so the job stays reproducible once the SSD blob cache is pruned.
  - This copy is the lossless dataset (future training data) and insurance if the Mac never receives the clip.
  - The Mac's sidecar links to it through `gen.backend.job`.
- **`outputs/`**, an rsync mirror of the Mac's `outputs/` at identical relative paths, so folder names survive:
  - `rsync -a` with no `--delete` and no other deleting flag, ever. Deleting or moving files on the Mac never removes anything here. A clip the trash button moved to `deleted/` simply exists in both places on the box.
  - Excludes are regenerable caches only: `.embeddings/`, `_dadagrid/`.
  - Triggers: a background thread in the studio runs it every 5 minutes while the box is reachable (rsync's own diffing makes a no-op pass cheap: 3.6k files, 7.1 GB at 2026-11-10); `make archive` runs it on demand. It must never block or fail a render.
  - Sidecars change after render (labels, stars, `played`); the mirror simply takes the newer copy.
  - `make archive-check`: `rsync --dry-run --checksum --itemize-changes` the other way round, listing every Mac file whose bytes aren't on the box. Empty output = safe to delete locally.

Put the rsync logic in one script (`scripts/archive.sh push|check`) that both the studio thread and `make` call.

### 6. Deploy (`scripts/beast.sh deploy|status|logs|restart`, called by `make start`)

- **Deploy dir:** `~/SSD2TB/sa3-worker/AI_Audio/`, with the same layout as the repo so every `Path(__file__).parents[…]` resolves. rsync `sa3lab/` and `stable-audio-3/optimized/mlx/` (code only: exclude `.venv`, `output/`, `models/mlx/*` weight links, `__pycache__`), plus `models/derived/anarchy/` and the `semantic-anarchy` repo (set `SA_REPO` in the unit).
- **Venv and weights:** the venv lives at `~/SSD2TB/sa3-worker/.venv`, created once in Phase 0. `models/hf-cache` is a real dir on the SSD; weights download there on first use via `sa3lab/sa3`'s download path, with `HF_HUB_CACHE` set in the unit.
- **Code hash:** sha256 over the sorted contents of the deployed `*.py` files, computed the same way on both sides (a function in `engine.py`). `make start` deploys, then restarts the unit only when the hash changed, because a restart costs the model reload.
- **Never block the studio:** `make start` must not wait on or fail because of the box. If deploy fails (box off, ssh timeout of a few seconds), print one line and start the studio anyway; Auto then renders on the Mac.
- **systemd:** `~/.config/systemd/user/sa3-worker.service` with `Restart=on-failure` and env `HF_HUB_CACHE`, `SA_REPO`, `SA3_ARCHIVE=~/12TBHDD/AI_Audio`, `SA3_BLOBS=~/SSD2TB/sa3-worker/blobs`. Keep the unit file in the repo (`sa3lab/beast/sa3-worker.service`) and install it with `beast.sh install`.

## Phases and order

**Phase 0 — spike, gate (on the box, before anything else).** Throwaway venv:
1. Install `mlx[cuda13]` (driver 580 and `/usr/local/cuda-13.0` are present; `mlx[cuda12]` is the fallback). Known packaging trap: the cuda13 wheel can fail on its first GPU op without the CUDA runtime/CCCL headers. The system toolkit should cover it; the workaround otherwise is the `nvidia-cuda-runtime` / `nvidia-cuda-cccl` pip packages plus the header path pointed into the venv.
2. Install the MLX runtime's `requirements.txt` + `requirements-gradio.txt` (not `install.sh`: it is macOS-shaped).
3. Get the Medium + SAME-L + T5Gemma MLX weights (~6 GB, the box SSD has room) via `sa3lab/sa3` into the box's `models/hf-cache`.

Then measure, headless, with `sa3lab/anarchy/headless.py` or the `./sa3` CLI:
- a 6 s render (does it run at all) and a 120 s Medium + SAME-L render, cfg 1 and cfg 3;
- with a LoRA: copy `djrum-uts-v1-step=14000` over from the Mac, or use the box's own trained checkpoint;
- **noise parity:** dump the noise tensor for seed 4825 at 120 s on both machines (`np.save` from inside `noise.install`'s `normal`) and compare bit for bit;
- **render parity:** the same job on both machines, compared with `sa3lab/dadagrid.py` side by side.

Go if the 120 s full render is **≤ ~8 s** (≥ 4× the Mac) and noise matches. If noise differs but speed is fine, it is still a go, but record that seeds are backend-specific and add `gen.backend` to the restore-settings match. If it is slower than that or broken, stop and report back to Xander with numbers. Option B (torch port) is then the fallback, and that is his call.

Log the Phase 0 numbers in the research Claude Doc (URL in `CLAUDE.md`'s documentation map) and in the box memory.

**Phase 1:** `engine.py` refactor + `CAPTURES`. **Phase 2:** worker + deploy + systemd. **Phase 3:** dispatcher + chip + `gen.backend` + speed-model filter. **Phase 4:** archive (raw copy, mirror, `make archive`, `archive-check`).

Phases 1 and 3 change the Mac's live studio code. Xander loads them with `make start`; never restart his studio.

## Acceptance (one full test at the end)

- **Mac side:** every test render goes through the **test studio** (`sa3lab/composer/teststudio.py --port 7862`), never :7860. Use 6 s clips except the one 120 s speed check.
- **Box side:** the worker can be driven directly by a small `sa3lab/beast/smoke.py` client (one job json in, WAV out).

Each check runs via Beast, compared against the same job local where it says "matches local":

1. text-to-audio; seed and settings restore correctly from the sidecar;
2. a2a with a noise level;
3. inpaint with a drawn strength curve (kept frames pasted back);
4. Extend on an existing clip;
5. LoRA with a From/To step window;
6. `anarchy:` fit spec → `.cond.npz` written next to the audio + `anarchy` block in the sidecar;
7. `anarchy: @<file>.cond.npz` re-render;
8. a Composer take with 2 anchors (one bank anchor, one prompt) → `.conds.npz` written;
9. CFG 3 with a negative prompt;
10. Stop mid-sampling, and Skip with jobs waiting;
11. box unreachable → Auto renders on the Mac, with one log line;
12. box busy (fake `other_gpu_procs`) → the Mac renders;
13. a mismatched code hash → the chip shows Mac and says why;
14. the raw copy exists under `~/12TBHDD/AI_Audio/raw/<outdir>/…` for every Beast render;
15. after `make archive`, `make archive-check` is empty;
16. a sidecar label changed on the Mac shows up in the mirror after the next sync;
17. timing: the 120 s Beast render's wall time, from Generate click to clip in history, recorded in the summary.

## Traps known up front

- **Wrapper order on `up.run_generation`.** `_install_stop_hooks` intercepts `fn is up.run_generation`, the *outermost* wrapper at call time. `engine.install` must finish all wrapping before the studio's `mlx_call` hook reads `up.run_generation`.
- **Cross-thread MLX.** MLX arrays and streams are bound to the thread that created them ("There is no Stream(gpu, 0) in current thread"). The worker must run every MLX call on one owner thread, as upstream does on the Mac.
- **`segments.current()` is read on the handler thread** and `segments.active(...)` is entered on the MLX thread. The worker receives the plan in the job and enters `active` itself. Never rely on the worker's `segments.current()`.
- **`paste_kept` reads the inpaint audio by path** after the decode (inside segments' `run_generation` wrapper). On the box that path is the blob, which is correct; just don't delete blobs mid-job.
- **Starred symlinks.** `outputs/starred/` holds absolute-path symlinks into the Mac's tree, so in the mirror they dangle. Harmless: the real files are mirrored and `label.starred` is in each sidecar.
- **Old sidecars point outside `outputs/`.** Some early renders' `path` points into `stable-audio-3/optimized/mlx/output/gradio/`. The mirror covers `outputs/` only; `archive-check` should warn about a sidecar whose audio lives outside `outputs/`, so a cleanup doesn't orphan it.
- **The mp3 is encoded on the Mac** (ffmpeg, after the peak guard), so the mirror's mp3 and the raw float WAV differ by the limiter and the mp3 encode. Both are kept on purpose.

## Box facts (2026-11-10)

- **Hardware and OS:** `ssh rednax-beast` = Tailscale 100.117.34.17, LAN 192.168.0.202 (`rednax-beast-lan`). Ubuntu 24.04, glibc 2.39, Python 3.12.3, uv 0.13.5, RTX 3090 24 GB, driver 580.178, CUDA 13.0 at `/usr/local/cuda-13.0`, i7-9700K, 31 GB RAM.
- **Repo clone:** `~/SSD2TB/Github_repos/AI_Audio`; SSD2TB has ~300 GB free, while `/home` is tighter at 45 GB free.
- **Existing torch env:** `underfit/.venv` (torch 2.7.1+cu128, used for training). Its `models/hf-cache` holds the torch Medium weights, not MLX ones.
- **Archive drive:** `~/12TBHDD`, ext4, 9.2 TB free; `AI_Audio/` doesn't exist yet.

## Later (not this spec)

- Library rows for clips that exist only in the archive, with a "fetch back" button (`rsync` from the box), so a Mac cleanup doesn't hide old renders from the Library.
- Moving `embed.py`'s SAME-L similarity encoding to the box as well.
- Batch or anarchy sessions submitted straight to the worker, without the gradio queue.
