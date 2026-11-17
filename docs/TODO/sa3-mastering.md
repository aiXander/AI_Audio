# SA3 mastering step: a final master for finished tracks

**Status (2026-11-14): proposal, undecided, nothing built.** Agent proposal from one research pass
(web + source reading, no listening). Xander asked for the write-up; he has approved none of it. The
first thing to settle is "Decisions for Xander" below, and the first work is a listening test, not code.

## The problem

A finished track leaves the bench with a safety limiter and nothing else. No step sets loudness or
tonal balance, so two tracks can differ by several LUFS and in spectral tilt, and a set played from
them needs riding by hand. The set plan lists "one loudness target for every track" as open
([sa3-one-hour-set.md](sa3-one-hour-set.md), gap 1). This doc owns the mastering step; the set plan
owns the set (manifest, export, stems).

## What already exists

| Need | Already there |
|---|---|
| Stop clipping | every studio render passes `studio._peak_guard`: brick-wall limiter at −1 dBFS, raw decode peak in the sidecar as `gen.peak_guard` |
| Master bus in the Composer | `mixdsp.apply_master`: gain + the same limiter (ffmpeg `alimiter`), monitored live in the browser |
| Measure | `mixdsp.meter` / `lufs` (peak, integrated LUFS), `keyfind.analyze` (LUFS, 3-band balance, stereo width) |
| Blind comparison page | `sa3lab/bench/abpage.py` served by `abserve.py` |

## Proposed approach (recommendation, not decided)

**Reference mastering with [Matchering 2.0](https://github.com/sergree/matchering), one reference
track per set.** Matchering makes a target match a reference in loudness (RMS), frequency response,
peak level and stereo width, then applies its own brick-wall limiter. It is classic DSP, not a neural
model. Mastering every track of a set against the same reference gives the hour one loudness and one
tonal balance in a single step, with no weights to download.

Facts read from its source (v2.0.6):

- `mg.process(target, reference, results, config=Config())`; a result is
  `mg.Result(file, subtype, use_limiter=True, normalize=True)` (`mg.pcm16` / `mg.pcm24` are shortcuts).
  `use_limiter=False` returns the matched audio before the limiter.
- It works at 44.1 kHz stereo, which is our rate, so nothing is resampled. Other rates are resampled
  with resampy; mono is upmixed.
- A file longer than 15 minutes raises an error (`Config.max_length`). Tracks are fine; a continuous
  set mix needs a larger value.
- A target that is already clipped or limited only logs a warning. Renders that hit the peak guard
  (`gen.peak_guard.limited`) will trigger it.
- GPL-3.0. The last PyPI release is from October 2022, with classifiers up to Python 3.10. The MLX
  venv is Python 3.11 and does not have it. Untested on this Mac.

### Order of work, if approved

1. **Listening test first.** Take 3 to 5 finished or long renders and 1 or 2 reference candidates.
   Per track render three variants: the original, the Matchering master, and a control that is only
   loudness-normalised (ffmpeg `loudnorm`). Loudness-match all three, then compare blind on an
   `abpage.py` page. Run Matchering from a throwaway environment (`uv run --with matchering …`).
   Folder: `outputs/mastering-exp/`, with a `context.md`. If the master does not beat the control,
   stop here and keep plain loudness normalisation.
2. **`sa3lab/master.py`**: track(s) + reference → `<name>.master.wav` (24-bit) next to the source,
   and a `master` block in the sidecar (tool, version, reference file hash, LUFS and peak before and
   after). It calls Matchering as a subprocess in its own uv environment (see GPL under Traps).
3. **Hook it into the set export** (set plan, gap 3). A Master button in the Composer only if the CLI
   proves itself.

## Decisions for Xander

1. **Mastering at all, or only loudness?** Plain `loudnorm` to one LUFS target needs no new
   dependency and leaves each track's tone alone. Matchering also changes EQ and width.
2. **Which reference?** A released track in the target style, or his own best render. The reference
   sets the loudness too, since Matchering matches RMS, so this is also the loudness decision.
3. **GPL-3.0 in a public repo: acceptable** when it runs as a separate process?
4. **Where it runs:** only on tracks exported for the set, or on any render from the Library.

## Alternatives looked at

| Option | Verdict |
|---|---|
| [SonicMaster](https://github.com/AMAAI-Lab/SonicMaster) (ICML 2026, Apache-2.0): text-prompted generative restoration + mastering, auto mode without a prompt | **Not as a final master.** It re-encodes through the Stable Audio Open VAE, and that round trip alone scores below the degraded input in the paper (SSIM 0.546 vs 0.777). It works in chained 30 s chunks, and the authors report volume drifting over a track and "robotic vocals or muted instruments". Possible repair or creative tool (de-reverb, de-clip, bend by prompt) as its own experiment: `model.safetensors` is 3.45 GB (the HF repo also holds a 4.17 GB optimizer file: fetch the one file), probably CUDA, so the 3090 box, and the download needs a go-ahead. The VAE's own licence was not checked. |
| [ITO-Master](https://arxiv.org/pdf/2506.16889) (Sony, ISMIR 2025): mastering style transfer by inference-time optimisation | Public code not confirmed. Skip. |
| [ST-ITO](https://github.com/csteinmetz1/st-ito), [Diff-MST](https://arxiv.org/html/2407.08889v1) | General effects style transfer and *mixing* style transfer. Not mastering. |
| Agent-set DSP chain: an LLM reads `keyfind` measurements and sets EQ / compressor / limiter (the idea behind [mixmaster-ai](https://github.com/Tanzil-Ahmed/mixmaster-ai)) | The fallback if reference matching sounds wrong. Needs a multiband or dynamics stage we do not have. |
| ffmpeg `loudnorm` alone | The control in step 1, and the answer if decision 1 is "only loudness". |

Truly neural open-source mastering is thin: the known "AI mastering" products are closed.

## Traps

- **Master after the last SA3 pass, on the decoded audio.** SAME-L's decode re-peaks 1 to 2 dB above
  its input, so nothing limited before SA3 stays limited
  ([../reference/sa3-composer.md](../reference/sa3-composer.md)).
- **A master is an export artefact (proposed rule).** Keep the pre-master file, and never feed a
  master back in as a Composer piece or an a2a / inpaint input. What loud, limited input does to SA3
  is unmeasured.
- **A blind test without loudness matching measures loudness.** Louder wins by default.
- **Reference EQ pulls every track toward the reference's spectrum.** A deliberately strange track
  can lose what made it interesting. Judge per track by ear and allow an opt-out; a weird sound
  still beats a correct one.
- **GPL-3.0.** `import matchering` inside `sa3lab` arguably puts that code under GPL. A subprocess in
  its own environment keeps it separate and keeps the MLX venv clean.
- Masters and reference tracks never go in git (audio, and references are copyrighted).
