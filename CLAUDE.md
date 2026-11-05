# AI_Audio — creative hacking on AI audio models

## Mission

A playground for exploring open AI audio models and how they can be **tweaked, hacked and bent to make new kinds of music**: style transfer, latent-space interpolation and morphing, audio-to-audio re-synthesis, codec/autoencoder surgery, LoRA finetunes on custom material, prompt and sampler abuse, chaining models together. The goal is new sounds and new creative workflows, not benchmark scores: a weird result that sounds interesting beats a "correct" one. Nothing here is deployed; it's Xander's research bench on two machines: this M2 Mac (MLX inference, the studio UI) and his own NVIDIA GPU box (LoRA training, CUDA-only models, code experiments), kept in sync through git (see **Git: submodules and two machines** below). Modal only when neither machine can do it.

Each model lives in its own subfolder with its own `CLAUDE.md`. This file covers the folder as a whole.

**Current focus: Stable Audio 3 (SA3) and its SAME-L codec.** Unless Xander names another model, assume the task is about SA3 and work from [stable-audio-3/](stable-audio-3/). Its `CLAUDE.md` is the working map (runtime, commands, traps) and is imported below, so Claude has it loaded from the start; **any other agent: read `stable-audio-3/CLAUDE.md` now.** For any SA3 docs question, start at [stable-audio-3/docs/INDEX.md](stable-audio-3/docs/INDEX.md) — the index of every SA3 doc (paper distillation, MLX install, prompting, LoRA training) with a "start here by task" table. Note that its relative paths (`optimized/mlx/sa3`, `docs/…`) resolve under `stable-audio-3/`, not this folder.

@stable-audio-3/CLAUDE.md

## Blast radius

- **Disk is nearly full.** Ask before downloading model weights or building datasets of more than ~1 GB; check `df -h ~` first.
- **Every model download goes into `models/`**, never `~/.cache`, so Xander can reclaim disk by deleting one folder. Set `HF_HUB_CACHE=AI_Audio/models/hf-cache` (and `TORCH_HOME=AI_Audio/models/torch-cache`) for anything that downloads; `.claude/settings.json`, `sa3lab/studio`, `sa3lab/sa3` and the bench scripts already do.
- **Everything in git is public** (`aiXander/AI_Audio` and both forks): code, config and markdown only. Never commit weights, audio, datasets, renders or secrets (HF tokens, API keys).
- **Never push or open PRs to the original repos** (Stability AI, dadabots) unless Xander asks. Commit and push when Xander asks; his own repo and forks are the only push targets.
- **Paid compute (Modal, APIs) needs a go-ahead** per new job type; a re-run of something already approved is fine.
- **Free to run without asking:** local generations and experiments, tests, reading model cards, papers and HF/GitHub pages.
- **The studio UI is Xander's live workspace, not a test bench.** He is using it most of the time, often while other agents are editing it. Every edit to `sa3lab/*.py|css|js` restarts the server and reloads his page, and an agent's test render lands in his history. So keep live browser tests and studio restarts to a minimum: don't open the studio, click Generate or wait for a restart just to check a CSS or JS tweak. Batch your `sa3lab` edits so they trigger one restart, not ten. Test in the browser only to chase a real bug, or once at the end of a big sprint. Otherwise verify with `node --check`, a Python parse/import, or by reading the code, and tell Xander what to look at.

## Documentation map

| Where | Open when |
|---|---|
| [stable-audio-3/CLAUDE.md](stable-audio-3/CLAUDE.md) | working with Stable Audio 3 or its SAME-L audio codec (local MLX runtime, LoRA training) |
| [underfit/README.md](underfit/README.md) | finetuning SA3 (LoRA/DoRA) on our own audio — dadabots' training dashboard; dataset prep, prompt config, training knobs. Training runs on Xander's NVIDIA GPU box (or cloud), never on this Mac (too slow) |
| [underfit/docs/TODO/sa3-lora-finetune.md](underfit/docs/TODO/sa3-lora-finetune.md) | starting or continuing our first SA3 finetune: data and caption findings, chosen run config, next steps, known traps |
| `sa3lab/` (`studio.py`, `hispec.py`, `persist.py`, `library.py`, `tempo.py`, `devwatch.py` docstrings) | changing the web UI, its spectrogram, the Library filter modal or the restore-settings button, or adding our own SA3 scripts; reading or extending the render dataset (labels, regen settings and measured BPM in each render's sidecar json) |
| `sa3lab/bench/` (`quality_bench.py`, `score_bench.py` docstrings) | comparing SA3 quality tricks (base/ARC merge, CFG, squeakfix decoder LoRA, length, prompt style) on fixed prompts + seeds, scoring renders (Audiobox Aesthetics, CLAP, HF/squeak metrics), and the blind A/B listening page (`make_listen.py` + `labeler/`, adapted from the labeling-ui skill: loudness-matched, randomly named clips, Reveal button un-blinds) |
| [docs/TODO/sa3-promptless-anarchy.md](docs/TODO/sa3-promptless-anarchy.md) | porting semantic-anarchy (`~/Documents/GitHub/semantic-anarchy`) to SA3: sampling T5Gemma conditioning from a distribution fitted on a prompt corpus instead of writing prompts; what the DiT actually reads (an unordered bag of conditioning vectors), the synthetic-corpus plan, the E00 edge-map experiment and the lanes it unlocks, traps |
| SAME-L / SA3 research Claude Doc: https://claude.ai/artifact/DypQNkdfui1FG9pJosuHu7 | Xander shares a link, paper or idea about these models, or an experiment produced a result worth logging — append there, not in a repo file |

## Repo layout

- `stable-audio-3/` — submodule, our fork of Stability AI's Stable Audio 3 (text-to-audio/music diffusion + SAME audio codec). Own `CLAUDE.md`.
- `underfit/` — submodule, our fork of `dada-bots/underfit`, the SA3 LoRA-training dashboard (torch on NVIDIA, or drives the MLX trainer on a Mac). Must sit next to `stable-audio-3/`, because it looks for that folder there.
- `models/` — every downloaded or derived model file, so deleting it reclaims all model disk: `hf-cache/` (a Hugging Face cache: the SA3 MLX weights and the squeakfix decoder LoRA; scripts re-download anything else they need, e.g. the bench's eval models), `derived/` for weights we build ourselves (empty for now; the base:ARC merge was tested and deleted). `stable-audio-3/optimized/mlx/models/mlx/*` are links into `hf-cache/`; delete `models/` and the runtime re-downloads on next use.
- `audio-data/` — source audio we feed models (LoRA datasets, style-transfer inputs, codec tests), laid out as `<artist>/<release>/` (e.g. `djrum/under_tangled_silence/`, Bandcamp MP3 320 purchase). Purchased/copyrighted material: gitignored, never commit or share.
- `outputs/` — all generated audio from every model and experiment. Gitignored.
- `sa3lab/` — our own SA3 code, outside the upstream clone; runs with the MLX venv. `studio` is the web UI we use: upstream's MLX gradio UI re-skinned (dark theme, full-HD spectrogram via `hispec.py`, history that survives reloads via `persist.py` writing a `.json` next to each render), localhost only, without touching upstream files. That `.json` sidecar is also our **render dataset**: `library.py` adds a `gen` block (everything needed to regenerate the clip), a `label` block (0-10 score + free-text tags set in the UI; the trash button scores a clip 0, stamps `label.deleted` and moves audio + sidecar to `outputs/sa3-gradio/deleted/`, kept as negative training rows) and an `analysis` block (measured BPM vs the prompt's BPM, `tempo.py`, numpy + ffmpeg only), so `outputs/sa3-gradio/*.json` is the raw dataset for future scoring models / finetunes; a2a and inpaint inputs are archived in `outputs/sa3-gradio/inputs/`. The how and why sit in the docstrings of `studio.py`, `hispec.py`, `persist.py`, `library.py` and `tempo.py`.
- New model or experiment → its own subfolder plus a row in the documentation map above. A third-party model repo becomes a fork + submodule (procedure below), never a bare clone.

## Load-bearing rules

1. Generated audio goes to `outputs/` with a descriptive filename (model + what the experiment was, e.g. `sa3_styletransfer_jazz-to-techno_s0.6.wav`), so Xander can tell renders apart without opening them.
2. When an experiment works or fails interestingly, record the settings that produced it (prompt, seed, strength, checkpoint) in the research doc's Log — a sound you can't reproduce is lost.
3. Inference on the Mac (MLX) where possible; training and CUDA-only work on the GPU box; Modal only when neither works.

## Definition of done

- Every render you made is in `outputs/` and named in your summary as a clickable `file://` link.
- Notable findings (what worked, what broke, settings) are logged in the research doc.
- Any new subfolder has a row in the documentation map here.
- Changed code inside a submodule? Your summary says which repos hold uncommitted or unpushed work: the other machine only sees what's pushed, submodule first and then the parent pointer.

## Git: submodules and two machines

`AI_Audio` is the parent repo; each third-party model repo inside it is a **git submodule** pointing at Xander's fork, so he can change their code, train on the GPU box, and pull the same code onto the Mac for inference.

| Folder | `origin` (push here) | `upstream` (fetch only) |
|---|---|---|
| `.` | `aiXander/AI_Audio` | — |
| `stable-audio-3/` | `aiXander/stable-audio-3` | `Stability-AI/stable-audio-3` |
| `underfit/` | `aiXander/underfit` | `dada-bots/underfit` |

- **Xander's shortcut: `make push m="msg"` / `make pull`** (`scripts/sync.sh`). Push commits *everything* in each submodule then the parent, in the right order, force-adds every `CLAUDE.md`/`AGENTS.md` (underfit's `.gitignore` excludes them), and refuses audio/weights/>5 MB files. It stages the whole tree, so **agents don't use it** (parallel agents share the tree); agents commit by hand as below.
- **Commit order: submodule first, then the parent.** Commit and push inside the submodule (to its fork's `main`), then in `AI_Audio` commit the changed submodule folder ("Bump stable-audio-3: <what>") and push. Pushing the parent while it points at an unpushed submodule commit breaks `git pull` on the other machine.
- **Check the branch before committing in a submodule:** `git -C stable-audio-3 branch --show-current`. Empty means a detached HEAD (left by `git clone --recurse-submodules` or `git submodule update`): run `git checkout main && git pull` first. Already committed while detached → `git branch rescue` before switching, then merge it into `main`.
- **Pulling the other machine's work:** `git pull && git submodule foreach 'git checkout main && git pull'`. Plain `git pull --recurse-submodules` also works but leaves submodules detached.
- **Syncing with upstream:** `cd stable-audio-3 && git fetch upstream && git merge upstream/main`, resolve conflicts with our edits, push the fork, bump the pointer in the parent. Keep our edits to upstream files small and one-purpose per commit so these merges stay easy.
- **The parent's `.gitignore` does not apply inside submodules**, and the forks are public too. Write training runs, checkpoints and datasets outside the submodule (`models/`, `audio-data/`, `outputs/`) or under a path the submodule already ignores (underfit's `state/`). Note underfit's `.gitignore` ignores `CLAUDE.md`: `make push` force-adds it; by hand, use `git add -f`.
- **Commit only your own paths** (`git commit --only <paths>`; parallel agents share this tree and its index).
- **Fresh machine, one-time setup** after cloning: in each submodule run `git checkout main`, `git remote add upstream <original url>`, `git remote set-url --push upstream DISABLED` (the Mac has this; it blocks accidental pushes to Stability or dadabots), and make `origin` pushable (`git remote set-url origin git@github.com:aiXander/<repo>.git`, or `gh auth setup-git` for https). `.gitmodules` uses https URLs so cloning needs no SSH key.
- **Adding a new model repo:** agents can't create the fork: the permission classifier blocks creating public repos. Ask Xander to run `! gh repo fork <owner>/<repo> --clone=false`, then `git submodule add -b main https://github.com/aiXander/<repo>.git <repo>`, set up its remotes as above, add it to this table and the documentation map, and commit `.gitmodules` + the folder together. A repo already cloned here as a plain clone: fork it, repoint `origin` → fork and the original → `upstream`, push, then `git submodule add` with the same path (git adopts the existing checkout).

## Commands

- Git sync (Xander only, see **Git** above): `make push m="msg"` commits + pushes all submodules then the parent; `make pull` pulls everything and leaves submodules on `main`.
- Python projects use `uv` (`uv run`, `uv sync`); each subfolder manages its own environment.
- Stable Audio 3 generation: see [stable-audio-3/CLAUDE.md](stable-audio-3/CLAUDE.md) (`./sa3` wrapper under `optimized/mlx/`). Prefer `sa3lab/sa3` (same flags) so missing weights download into `models/`.
- Web UI: `make start` (launches `sa3lab/studio` in the foreground; if an instance is already running anywhere, it stops it and starts a fresh one. Once the server answers, `scripts/open-ui.sh` always brings the UI up in Chrome: it focuses an existing studio tab, which reloads itself, or opens a new one), `make stop` for a background instance. `sa3lab/studio` runs under `devwatch.py`: any edit to `sa3lab/*.py|css|js` (or upstream `sa3_gradio.py`) restarts the server once no render is running, and the open page reloads itself, so **agents never need to kill/restart the studio by hand**; `--no-watch` opts out. Direct: `sa3lab/studio` → http://127.0.0.1:7860 (Medium + SAME-L, 120 s preselected, prompt box prefilled with the last render's prompt; upstream `sa3_gradio.py` flags pass through, e.g. `--dit sm-music`, `--port`).
