---
name: anarchy-labels
description: Analyze the 0-10 labels Xander gave a folder of studio renders (anarchy specs or text prompts) and design + queue the next render batch from them. Use when asked to analyze labels/scores/a session, ask which settings work, or to queue/trigger more renders based on what scored well.
---

# Analyze render labels, then queue the next round

Two scripts do the work; their docstrings are the reference (`sa3lab/anarchy/analyze_labels.py`, `sa3lab/anarchy/enqueue.py`, spec grammar in `sa3lab/anarchy/sampler.py`).

## 1. Analyze

```bash
uv run sa3lab/anarchy/analyze_labels.py outputs/<folder> [more folders] [--keep 7]
```

Standalone (numpy + matplotlib), safe while the studio renders. Writes `<folder>/_analysis/`: `report.md` (overview table, then every dataset's full report), `rows.csv` (all rows, with a `lora_set` column) and one subfolder per dataset (`base/`, `lora-<names>/`, each its own `report.md` + PNGs). Prints the same report. Done when it printed and you have read the overview table, then each dataset's **Noise floor** section.

**LoRAs are separate datasets, never pooled.** A LoRA changes the model weights, and in Xander's listening it moves the outcome far more than the prompt or conditioning does (any sampler, T, L). The script therefore splits rows by the set of LoRA files active in the render (`gen.loras`; strength is the `lora_strength` knob inside that LoRA's dataset). Read each dataset on its own. Don't compare knob effects across datasets, don't explain a base result by LoRA renders or the reverse, and don't treat "LoRA vs base" as a knob to optimize: the overview table is the only cross-dataset view, and its rows differ in prompts and seeds too.

## 2. Read it in this order

0. **Design-independent view first** (top of each dataset's report). Batches follow up on liked directions, so per-render tables over-count them. Sampler and knob conclusions come from the *first look* column and the first-look knob table (each idea's first render, made before its scores existed) and from paired knob effects. A per-render ◆ that vanishes on first looks is one liked direction in disguise. A paired Δ against an anchor picked for scoring high is mostly regression to the mean.
1. **Noise floor.** Within-group SD vs total SD of repeat groups (same conditioning, other seed). When seed + label noise is most of the variance, single renders say almost nothing: any knob conclusion needs ≥ 3 seeds per conditioning.
2. **Knobs.** Only ◆ rows (bootstrap CI excludes 0) are signal; every other ρ is noise at this n. Say so plainly rather than reading trends into quartile bins.
3. **Categorical / held constant.** Confounds hide here: render length, `noise` (`∅` = pre-canvas renders, older). A sampler or length that looks bad may just be the older renders.
4. **Re-seed candidates + top renders.** Best conditionings rendered once are unconfirmed: lucky seed or good direction.
5. Cross-check text-prompt baselines elsewhere (`outputs/sa3-gradio/`, `outputs/starred/`) so the anarchy scores have a reference point.

## 3. Design the next batch

Build a `jobs.json` (list of `{"prompt", "seed", "seconds"}`; format in `enqueue.py`) where every block answers one question:

- **Re-seed**: top single-render conditionings × 3 fresh seeds → direction vs luck.
- **Neighbourhood**: a confirmed direction (high mean over ≥ 3 seeds) swept along one knob (T, L, c, m) at a **fixed seed**, so only that knob moves.
- **Controls**: the plain text prompt a `mutate` spec anchors on, same length → calibrates the score scale.
- **New lanes** only for samplers/knobs with too few rows to judge.

- **Explore share**: keep about a third of every batch as fresh random directions drawn from a distribution fixed *before* looking at scores (all samplers, T / L ranges as before; `random_batch.py` or random d per sampler). These first looks are the only unbiased data on what works; an all-exploit batch adds nothing to them.

Fix the render settings you are not testing (60 s, cfg 1, steps 8). Write the batch to the scratchpad, not the repo.

One batch = one dataset: never mix LoRA and no-LoRA renders in one block. A job without `"loras"` sends empty LoRA slots (a `base` render); `"loras": [{"path": "<file under stable-audio-3/loras/>", "strength": 0.8}]` loads up to 3 (format in `enqueue.py`). Comparing LoRA *files* (checkpoints) only works through fixed (conditioning, seed) pairs rendered at each file, read by ear + `dadagrid.py`, since the analyzer never pools two files. A LoRA experiment (strength sweep, one LoRA vs another) is its own study: ask Xander, and put it in its own output folder (e.g. `outputs/anarchy/<session>-lora-<name>/`) so the sessions stay readable by folder too.

## 4. Queue into the running studio

```bash
stable-audio-3/optimized/mlx/.venv/bin/python sa3lab/anarchy/enqueue.py jobs.json --outdir <folder under outputs/>
```

The running studio only knows the samplers it booted with (`sampler.py` is imported once; no auto-restart). Any spec using a sampler or knob added since Xander's last `make start` (check `git log`/mtime of `sampler.py` vs the studio's boot) must be baked first: `stable-audio-3/optimized/mlx/.venv/bin/python sa3lab/anarchy/precompute.py "<spec>" …` prints `anarchy: @anarchy/_specs/….cond.npz` prompts to queue instead, and the analyzer reads their knobs back.

Run it in the background and keep it alive until it exits (gradio drops a disconnected client's queued jobs). It needs the studio up (`curl -s http://127.0.0.1:7860/sa3/outdir`); if it is down, ask Xander to `make start`: the studio is his live workspace, never start or restart it yourself. Re-running the same file skips renders already in the folder. Same folder as the analyzed session keeps re-seeds pooled with their originals.

## 5. Report and log

Summary for Xander: the noise floor in one line, what is signal vs noise, the batch as a table of blocks (question each answers, job count), and how to tell the answer once he has labeled them (re-run step 1). Append findings and the batch design to the research doc's Log (see the `same-l-research-doc` memory / `stable-audio-3/docs/INDEX.md` for the URL).
