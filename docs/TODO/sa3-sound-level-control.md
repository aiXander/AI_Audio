# SA3 sound-level control: using labels to move toward interesting regions

**Status (2026-11-06): plan only, nothing built.** Handles 10–12 (reference-texture loss, its distilled adapter, stem-selective reference) were added 2026-11-06 from a conversation with an SA3 practitioner. Pick-up point: Phase 0 (seed anatomy, incl. the
`x<n>` strength sweep) and Phase 1 (label scorer on audio features). Both are machine-only and use
existing labels. Xander's recommended start; he hasn't approved yet. Sibling plan:
[sa3-promptless-anarchy.md](sa3-promptless-anarchy.md) owns the T5Gemma-conditioning lane (samplers,
bag operators, label analysis). This plan owns everything that steers **closer to the sound**: noise,
the SAME latent, DiT activations, inpaint anchors, scorers, keeper LoRAs.

## The question

Xander's observation: with conditioning, seed and everything else fixed, another noise seed (or the
same seed at another length) often sounds very different. That undercuts "hunt for good conditioning
vectors". He is curating a large hand-labeled render dataset (0–10 score + tags in each render's
sidecar, `sa3lab/library.py`) and wants the labels to gradually push SA3's output toward more
interesting regions.

## Conclusions so far (why a fixed conditioning ≠ a fixed sound)

- **Measured:** seed + label noise ≈ 100% of score variance (the same conditioning re-seeded spans SD
  1.38 vs 1.24 over all renders). Fixed-seed probes found that the **noise seed sets the arrangement**
  (tempo, loudness contour, density), the conditioning *direction* sets the semantic character/texture,
  and the token *magnitude* reshapes arrangement. Numbers and probes: status line of the anarchy plan.
- **Architecture says the same.** Text reaches the DiT only as a cross-attended bag of 257 vectors,
  and AdaLN gets only timestep + duration (`stable-audio-3/docs/reference/sa3-model-map.md`). It was trained on
  AudioSparx metadata prompts, so one prompt covers thousands of tracks: p(audio | conditioning) is
  wide by training, and melody/arrangement/key/groove are left to the noise.
- **One seed = 8 noise tensors.** The ping-pong sampler (`sample_flow_pingpong` in
  `stable-audio-3/optimized/mlx/models/defs/sa3_pipeline.py`) re-noises x̂0 with fresh noise before every
  step. Which of the 8 draws decides the composition is unmeasured (Phase 0).
- **Length is a condition, not just a crop.** `sa3lab/noise.py` already makes the noise the same across
  lengths, but duration feeds every block's AdaLN and the cross-attention, and self-attention spans the
  whole sequence. So a new length re-plans the piece.
- **Reframe:** a conditioning vector is a *distribution* of pieces. Judge it by keeper-rate over ≥ 3
  seeds, never one render. And search spaces closer to the sound, where one point *is* one sound.
- **Labels score audio, not recipes.** A score belongs to the clip, so single-seed labels already train
  an audio-feature scorer, even though they say little about which conditioning is good.

## Handles closer to the sound (ordered cheap → expensive; all Mac inference except 7)

| # | Handle | Idea | Seam |
|---|---|---|---|
| 1 | **SAME-latent fingerprint** | Re-encode each render: 256 ch × T at 10.77 fr/s, deterministic. Chroma (key) and inter-aural level difference (stereo width) are linearly readable by construction. Per-channel mean+std over time ≈ 512-d timbre/texture fingerprint (≈ Gram matrices in image style transfer). Fit distributions and train scorers here. | `models/defs/same_l_encoder.py` (MLX encoder exists) |
| 2 | **x̂0 stat steering** | Between ping-pong steps, nudge x̂0's channel mean/std toward a target fingerprint (e.g. mean of keepers) before re-noising. Works with any prompt, any seed: a distribution shift, not a point. A linear scorer on pooled means has a constant per-channel gradient, so "scorer guidance" = a per-channel offset. No autodiff through the DiT. | wrap `sample_flow_pingpong`; `sa3lab/studio.py` already wraps it (stop/progress) |
| 3 | **Early-step triage (best-of-N)** | If composition locks by step 2–3, run N seeds for 2 steps, score the x̂0 preview's fingerprint (no decode), finish only the top k. "Inference-time search with a verifier" (Ma et al. 2025, image diffusion). | same wrap; needs #1's scorer |
| 4 | **Sparse-anchor inpainting** | Keep 2–4 short spans of a keeper (key/tempo/timbre anchors), regenerate the rest, optionally under another prompt or anarchy conditioning. In-distribution: training used 1–10 random kept spans. Anchor frames could even be *sampled* from a fit over keeper latent frames. | built: the studio's inpaint editor draws any number of spans (`sa3lab/segments.py` builds the native mask); compositing on top of it: the Composer, [../reference/sa3-composer.md](../reference/sa3-composer.md) |
| 5 | **Noise neighbourhoods** | Variations of a keeper: SLERP its noise toward a fresh seed at α 0.05–0.4. Or split the seed into a **structure seed** (steps 0–2) and a **detail seed** (steps 3–7), then hold one and vary the other. Complements the anarchy plan's `j0.3` (direction jitter on the conditioning, the safe variation knob there). | the sampler wrap's `key` handling |
| 6 | **Activation steering** | Pooled DiT activations (block 14, which the paper's discriminator found semantically rich). Steering vector = mean(high) − mean(low), added during sampling at strength λ. Old labeled renders replay bit-exactly from their sidecars (`gen` block + `crop4608` noise), so the existing labels yield activations without new listening. | wrap `TransformerBlock.__call__` in `dit_mlx_medium.py` from `sa3lab/` (no upstream edit) |
| 7 | **Keeper LoRA** | Reward-filtered finetune on score ≥ 7 renders (optionally score-weighted). LoRAs move the sound far more than any conditioning knob (anarchy plan, LoRA trap). GPU box only, via underfit. | `underfit/`; needs ~50+ keepers |
| 8 | **Probe guidance (melody/rhythm in, no training)** | Fit linear probes latent → chroma (12-d) and latent → band loudness/onset envelope on encoded audio (librosa targets; SAME was trained so chroma is linearly readable). Between steps, correct x̂0 along the probe directions toward a target curve taken from a MIDI render or rough melody, and leave the rest to the prompt. Decouples melody/rhythm from timbre, which a2a's single noise-level knob can't. Prior art: DITTO (Novack et al. 2024, noise-latent optimization toward melody/intensity features), Music ControlNet (Wu et al. 2023, the trained version). | the sampler wrap; probes fit on `audio-data/` |
| 9 | **Trained melody/beat control** | `modular_local_cond_configs`: a frame-aligned, zero-initialised slot built into the DiT for exactly this, unused by any released checkpoint. Train chroma / piano-roll / onset-grid inputs, self-supervised from any audio. GPU box. | `stable_audio_3/models/transformer.py`, `diffusion.py`; needs a training run |
| 10 | **Gram texture loss (reference clip → texture)** | The neural-style-transfer recipe (Gatys et al. 2015) inside SA3 itself: run a reference clip through the DiT, take the covariance (Gram matrix) of intermediate activations as the texture target, and add ‖Gram(gen) − Gram(ref)‖² as a loss optimised during sampling. Reported to work well by an SA3 practitioner (2026-11), not reproduced here. SA3's own DiT serves as the feature net (it already knows tags), so no "audio VGG" is needed. Local conditioning, ControlNet-like. Use case: render a piano/MIDI sketch to audio (numpy or the Arranger), then a2a/inpaint it while spraying a reference's texture over it. Needs autodiff through the DiT, unlike handle 2. Optimiser: try Muon beside Adam (reported faster and more stable for SA3 training). | sampler wrap + `mx.grad` through `dit_mlx_medium.py`, or torch on the GPU box |
| 11 | **Distilled texture adapter** | Distil handle 10's per-render optimisation into one forward pass: a small adapter from reference activations to conditioning, IP-Adapter-style. | training run, GPU box; needs handle 10 working first |
| 12 | **Stem-selective reference ("its drums, not its melody")** | Condition on one stem of a reference: decode the output, pass it through a differentiable stem splitter (e.g. Demucs), keep the percussion channel, apply the feature loss there and backprop through decoder + DiT. Untested by anyone (2026-11); long gradient path, memory-heavy. Cheaper first try (agent suggestion): split the *reference* offline and use only its drum stem as handle 10's target. | GPU box likely; stem splitter not in the repo yet |

**a2a (SDEdit) fidelity, unmeasured hypothesis:** noise is added in *latent* space, x = (1−σ)·z + σ·ε, so
what survives a given σ is high-variance latent directions and slow frame-to-frame structure (sections,
loudness contour, sustained bass/chords, probably kick pattern), not "low audio frequencies". Fine
timbre, transients and hat/cymbal texture should be regenerated first, partly by the SAME decoder's
GAN-trained detail. Test before relying on it: one input, σ ∈ {0.3…0.95}, per-band envelope correlation +
chroma correlation (input vs output), plus a `dadagrid.py` stack.

**Structured inputs exist now:** the Arranger (`sa3lab/arranger/`, plan [sa3-arranger.md](sa3-arranger.md))
renders song sketches with known structure plus per-frame chroma / band energy / onset curves at the
latent rate (`*.controls.npz`): ready inputs for this σ test and the targets for handles 4, 8 and 9.

Lower priority, cheap but likely mush: perturbing the 64 learned memory tokens; pushing the AdaLN
duration embedding off its 1-D trained line.

## Phases (open)

**Phase 0: where the variance lives (no listening).** For a handful of conditionings, render at one
fixed length (~20–30 s) and vary separately: (a) all noise, (b) only step-0 noise, (c) only steps ≥ k,
(d) conditioning with noise fixed, (e) length, (f) conditioning strength `x1/x2/x4/x8` (anarchy
`sampler.py`: tiles content rows into the pad slots, a strength dial at cfg 1). Measure pairwise CLAP-audio
cosine and SAME-fingerprint distance; look with `sa3lab/dadagrid.py` (CLAUDE.md rule 5). Also compare
each step's x̂0 to the final output.
Output: a variance breakdown (conditioning vs first noise vs re-noise vs length vs strength). It shows
whether triage (#3) is viable, and whether a stronger `x<n>` narrows the across-seed spread (if so,
that's a cheap fix for "same vector, different sound").

**Phase 1: a scorer from the labels.** Features per labeled clip: SAME-L fingerprint, CLAP audio,
Audiobox Aesthetics (`sa3lab/bench/score_bench.py`). Ridge/logistic regression with CV, compared against
the label test-retest noise. Analyze each LoRA set separately, as `analyze_labels.py` does. First uses:
pre-ranking batches for listening (always mixed with random picks, so it can't censor the weird), and a
taste direction in fingerprint space. Expect a weak but useful signal: labels sit in 0–6, mean ≈ 2.5.

**Phase 2: variations around keepers.** Handles 4, 5 and SDEdit (a2a from a keeper at
`init_noise_level` 0.5–0.95), one dial each, as ladders. Answers "found a good one, give me more like it".

**Phase 3: distribution shifts that hold across seeds.** Handles 2, 3, 6. Test: keeper-rate over 8
random seeds, steered vs unsteered on the same seeds (fixed-seed A/B). This is the real answer to the
question.

**Phase 4: re-score anarchy conditionings as distributions.** Keeper-rate over K seeds per conditioning,
the scorer doing the bulk judging, ears checking the top. Rescues the original vector hunt. The
conditioning work itself stays in the anarchy plan.

**Phase 5: keeper LoRA** (#7), once there are enough keepers.

## Working practice (recommended to Xander, applies now)

- Explore at one fixed length, then grow keepers with the studio's Extend button (`sa3lab/extend.py`:
  a continuation inpaint that keeps the opening exactly). A keeper re-rendered at 120 s is a cousin.
- Score with the whole 0–10 range; a compressed 0–6 range starves any scorer at the top.

## Traps

- Any steering/control comparison needs ≥ 3 seeds per condition or a fixed-seed A/B (noise floor above).
- Ghost tokens: tensor-sampler conditionings carry ~25 low-norm rows past the content (anarchy plan,
  Stage 2). They are part of the sound (`g0` stripping scored lower), so don't "clean" them out of a
  baseline without saying so.
- Bench scripts that bypass the studio (`sa3lab/bench/quality_bench.py`) still draw per-length noise.
  Phase 0 must use the studio path (`sa3lab/anarchy/headless.py`) or install `noise.py` itself.
- Re-encoding a render through SAME-L is not the latent the DiT produced (decode → encode round trip).
  For steering targets, prefer capturing x̂0 at the last step of new renders. Re-encoding is fine for
  scoring old ones.
- Medium's cross-attention is differential: a constant bag is a null conditioning. Matters for any
  "uncond" baseline (anarchy plan, "What the code says").

## Open questions for Xander

1. Approve Phase 0 + 1 as the start?
2. Save each new render's final latent (`.latent.npz`, ~1 MB/min fp16) and pooled block-14 activations
   by default? It makes handles 2/3/6 free on all future data, at a disk cost (disk is 95% full).
3. MERT as an extra scorer feature? It's a download, so ask first.
