# AI_Audio — creative hacking on AI audio models

## Mission

A playground for exploring open AI audio models and how they can be **tweaked, hacked and bent to make new kinds of music**: style transfer, latent-space interpolation and morphing, audio-to-audio re-synthesis, codec/autoencoder surgery, LoRA finetunes on custom material, prompt and sampler abuse, chaining models together. The goal is new sounds and new creative workflows, not benchmark scores: a weird result that sounds interesting beats a "correct" one. Nothing here is deployed; it's Xander's local research bench (M2 Mac, MLX where possible; cloud GPU via Modal when a model needs CUDA).

Each model lives in its own subfolder with its own `CLAUDE.md`. This file covers the folder as a whole.

**Current focus: Stable Audio 3 (SA3) and its SAME-L codec.** Unless Xander names another model, assume the task is about SA3 and work from [stable-audio-3/](stable-audio-3/). Its `CLAUDE.md` is the working map (runtime, commands, traps) and is imported below, so Claude has it loaded from the start; **any other agent: read `stable-audio-3/CLAUDE.md` now.** For any SA3 docs question, start at [stable-audio-3/docs/INDEX.md](stable-audio-3/docs/INDEX.md) — the index of every SA3 doc (paper distillation, MLX install, prompting, LoRA training) with a "start here by task" table. Note that its relative paths (`optimized/mlx/sa3`, `docs/…`) resolve under `stable-audio-3/`, not this folder.

@stable-audio-3/CLAUDE.md

## Blast radius

- **Disk is nearly full.** Ask before downloading model weights or building datasets of more than ~1 GB; check `df -h ~` first.
- **Every model download goes into `models/`**, never `~/.cache`, so Xander can reclaim disk by deleting one folder. Set `HF_HUB_CACHE=AI_Audio/models/hf-cache` (and `TORCH_HOME=AI_Audio/models/torch-cache`) for anything that downloads; `.claude/settings.json`, `sa3lab/studio`, `sa3lab/sa3` and the bench scripts already do.
- **This folder is the public repo `aiXander/AI_Audio`: code, config and markdown only.** Weights, audio, datasets and `outputs/` are gitignored. Never commit secrets (HF tokens, API keys).
- **Each model repo is a git submodule pointing at Xander's fork** (`aiXander/stable-audio-3`, `aiXander/underfit`), with the original as the `upstream` remote. Editing their code is fine: commit and push inside the submodule (to the fork), then commit the new submodule pointer in AI_Audio. Never push or open PRs to `upstream` unless Xander asks. A new model clone gets the same treatment (fork, `git submodule add`); never leave a bare clone in this folder, `git add` would record it as a broken embedded repo.
- **Two machines share this repo:** this M2 Mac (inference, MLX) and Xander's NVIDIA GPU box (LoRA training, code experiments). Sync is plain git: push from one; on the other `git pull && git submodule foreach 'git checkout main && git pull'` (plain `--recurse-submodules` leaves submodules on a detached HEAD). Weights, datasets and renders don't sync; copy them by hand.
- **Paid compute (Modal, APIs) needs a go-ahead** per new job type; a re-run of something already approved is fine.
- **Free to run without asking:** local generations and experiments, tests, reading model cards, papers and HF/GitHub pages.

## Documentation map

| Where | Open when |
|---|---|
| [stable-audio-3/CLAUDE.md](stable-audio-3/CLAUDE.md) | working with Stable Audio 3 or its SAME-L audio codec (local MLX runtime, LoRA training) |
| [underfit/README.md](underfit/README.md) | finetuning SA3 (LoRA/DoRA) on our own audio — dadabots' training dashboard; dataset prep, prompt config, training knobs. Training runs on Xander's NVIDIA GPU box (or cloud), never on this Mac (too slow) |
| `sa3lab/` (`studio.py`, `hispec.py`, `persist.py` docstrings) | changing the web UI, its spectrogram, or adding our own SA3 scripts |
| `sa3lab/bench/` (`quality_bench.py`, `score_bench.py` docstrings) | comparing SA3 quality tricks (base/ARC merge, CFG, squeakfix decoder LoRA, length, prompt style) on fixed prompts + seeds, and scoring renders (Audiobox Aesthetics, CLAP, HF/squeak metrics) |
| SAME-L / SA3 research Claude Doc: https://claude.ai/artifact/DypQNkdfui1FG9pJosuHu7 | Xander shares a link, paper or idea about these models, or an experiment produced a result worth logging — append there, not in a repo file |

## Repo layout

- `stable-audio-3/` — submodule, our fork of Stability AI's Stable Audio 3 (text-to-audio/music diffusion + SAME audio codec). Own `CLAUDE.md`.
- `underfit/` — submodule, our fork of `dada-bots/underfit`, the SA3 LoRA-training dashboard (torch on NVIDIA, or drives the MLX trainer on a Mac). Must sit next to `stable-audio-3/`, because it looks for that folder there.
- `models/` — every downloaded or derived model file, so deleting it reclaims all model disk: `hf-cache/` (a Hugging Face cache; the SA3 MLX weights, squeakfix LoRA, eval models), `derived/` (our own builds, e.g. the medium-base:medium merge), `torch-cache/`. `stable-audio-3/optimized/mlx/models/mlx/*` are links into `hf-cache/`; delete `models/` and the runtime re-downloads on next use.
- `audio-data/` — source audio we feed models (LoRA datasets, style-transfer inputs, codec tests), laid out as `<artist>/<release>/` (e.g. `djrum/under_tangled_silence/`, Bandcamp MP3 320 purchase). Purchased/copyrighted material: gitignored, never commit or share.
- `outputs/` — all generated audio from every model and experiment. Not a git repo.
- `sa3lab/` — our own SA3 code, outside the upstream clone; runs with the MLX venv. `studio` is the web UI we use: upstream's MLX gradio UI re-skinned (dark theme, full-HD spectrogram via `hispec.py`, history that survives reloads via `persist.py` writing a `.json` next to each render), localhost only, without touching upstream files. The how and why sit in the docstrings of `studio.py`, `hispec.py` and `persist.py`.
- New model or experiment → its own subfolder (clone or scratch project) plus a row in the documentation map above.

## Load-bearing rules

1. Generated audio goes to `outputs/` with a descriptive filename (model + what the experiment was, e.g. `sa3_styletransfer_jazz-to-techno_s0.6.wav`), so Xander can tell renders apart without opening them.
2. When an experiment works or fails interestingly, record the settings that produced it (prompt, seed, strength, checkpoint) in the research doc's Log — a sound you can't reproduce is lost.
3. Prefer running models locally (MLX / Apple Silicon); reach for Modal only when a model has no workable local path.

## Definition of done

- Every render you made is in `outputs/` and named in your summary as a clickable `file://` link.
- Notable findings (what worked, what broke, settings) are logged in the research doc.
- Any new subfolder has a row in the documentation map here.

## Commands

- Python projects use `uv` (`uv run`, `uv sync`); each subfolder manages its own environment.
- Stable Audio 3 generation: see [stable-audio-3/CLAUDE.md](stable-audio-3/CLAUDE.md) (`./sa3` wrapper under `optimized/mlx/`). Prefer `sa3lab/sa3` (same flags) so missing weights download into `models/`.
- Web UI: `make start` (launches `sa3lab/studio` in the foreground and opens Chrome once it's up; just opens Chrome if it's already running), `make stop` for a background instance. Direct: `sa3lab/studio` → http://127.0.0.1:7860 (Medium + SAME-L preselected; upstream `sa3_gradio.py` flags pass through, e.g. `--dit sm-music`, `--port`).
