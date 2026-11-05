# SA3 promptless anarchy: sampling T5Gemma conditioning instead of writing prompts

**Status: Stages 1–2 done for `grammar-music-100` (corpus, 100 reference renders, first fit); studio Promptless panel + switchable output folder built; no latent clip listened to yet; Stage 2's three numeric sanity renders not done.**
The base corpus is synthetic, so nothing waits on the studio's labeled dataset. Order: Stages 1–2 need no listening;
Stage 3 (`E00-edge`) is the first experiment; Stage 4's lanes are picked by E00's result.

## The idea

Port the core trick of Xander's image project **semantic-anarchy**
(`~/Documents/GitHub/semantic-anarchy`, read its `CLAUDE.md` first) to Stable Audio 3:
encode a corpus of prompts through T5Gemma **once**, fit a distribution over the resulting
conditioning, then **sample brand-new conditioning** and feed it to the DiT in place of an
encoded prompt. No prompt exists at generation time. The question for audio: **does SA3's
DiT stay musical off the prompt manifold, and where is the edge between "new genre" and
"mush"?** A weird result that sounds interesting beats a correct one.

Evidence status: semantic-anarchy's experiment ledger is still empty (its E00 census never
ran), so "pca at T > 1 extrapolates coherently" is an unlabeled anecdote from the SD era.
E00 here is the first labeled test of the idea in either project; design it to be worth
that.

## What the code says (verified 2026-11-05; code wins over this doc)

**The seam.** Conditioning is built in `stable-audio-3/optimized/mlx/scripts/sa3_mlx.py`
(`# ── 2. Conditioning ──`) and identically in `scripts/sa3_gradio.py`:
`T5Gemma.encode(..., max_len=256)` → `(1, 256, 768)` + mask → `apply_prompt_padding`
(positions past the prompt become one **learned padding vector**, per DiT, stored under
`cond.` in each DiT `.npz`) → concat one duration token → `cross_attn (1, 257, 768)`.
`global_cond` (AdaLN) is the duration embedding only. Replacing `embeds_padded` is the whole
seam. Helpers are importable from `models/defs/sa3_pipeline.py` (`apply_prompt_padding`,
`load_conditioner_from_npz`, `build_pingpong_schedule`, `sample_flow_pingpong`,
`patched_decode`) and `models/defs/t5gemma_mlx.py` (`T5Gemma`). `sa3lab/bench/quality_bench.py`
loads the MLX models once and renders a matrix in-process: copy its loading pattern.

**The DiT reads an unordered bag of 257 vectors, not a sequence.** Context goes through a
per-token SiLU MLP (`to_cond_embed`), then cross-attention with no positional encoding on
keys and no mask (`dit_mlx_medium.py` `DifferentialCrossAttention`, `dit_mlx.py`
`CrossAttention`). So the output is invariant to permuting the 257 rows; position *t*
matters only through what T5Gemma's encoder baked into each vector. Consequences that
shape this whole plan:
- A per-position Gaussian (semantic-anarchy's unit) pools "token 5 of prompt A" with "token
  5 of prompt B", unrelated words. It is still a valid density over encoder outputs, but a
  **token-level** model (pool every content vector of every prompt) matches what the DiT
  reads and has far more samples: 3,000 prompts × ~25 tokens ≈ 75k vectors in 768-D,
  versus 3,000 samples in 196,608-D.
- "Prompt length" is really the **count of content vectors vs padding copies** in the bag.
- **Duplicating a vector k times adds ln k to its attention logit**: an integer emphasis
  weight with no CFG (the shipped ARC checkpoints run at cfg 1).
- `scaled_dot_product_attention` takes any context length, so dropping pads (Tc = L+1) is
  mechanically possible. The model only ever trained on 257; untested whether it holds up.

**Medium's cross-attention is differential**: `softmax(q·k)·v − softmax(q'·k')·v`. Value
components common to all tokens cancel, so **a bag of identical vectors (any vector,
including upstream's `zeros_like` uncond) gives zero cross-attention output**, i.e. no text
conditioning at all. Tiling one row ("mantra") is therefore a no-op on Medium, while on
`sm-music`/`sm-sfx` (plain attention) it adds a constant value: a real conditioning.

**Keys are RMS-normalized per head (qk-norm), values are not.** Scaling a token's deviation
(temperature) barely changes *how much* it is attended; it changes *what it contributes*,
through an unnormalized value after a nonlinear MLP. Prediction: the mush edge is driven by
value magnitude, so **per-token norm matching** (rescale each sampled vector to the corpus
token-norm distribution) is the SA3-specific correction most likely to push the edge out.

**SA3's training prompts have a known grammar** (paper, via `stable-audio-3/docs/reference/sa3-model-map.md`,
"Prompt distribution it saw"): a random subset of AudioSparx metadata fields (description,
title, keywords, BPM, genre, moods, instruments) joined by commas, field name prepended half
the time, shuffled/subsampled half the time, lowercased half the time; `TrackType: Music,
VocalType: Instrumental` / `TrackType: SFX` prefixes help. An in-distribution corpus can
therefore be **generated by a script**, in any size.

**A base (non-distilled) Medium exists**: `models/mlx/dit_medium-base_f16.npz` (used by
`quality_bench.py`), so CFG and the negative branch are available as levers there.

**The fitting math needs no port.** `semantic_anarchy/distribution.py`
(`EmbeddingDistribution`) is pure NumPy, shape-generic (`fit(embeddings: (N, *shape))`), with
every sampler (`diagonal`, `pca`, `blend`, `hybrid`, `split`), `temperature`, `distance()`
(RMS z-score from the corpus centre: ≈1 typical), `retarget()`, `neighborhood()`, `walk()`,
`interpolate()`, the length-aware fit, the radius band and `noise_floor_axes()`. Because it
is shape-generic it also serves the token-level model: `fit` on `(ΣL, 768)` gives diagonal
and pca over single tokens for free.

## Design principles (why this plan differs from a straight port)

1. **Ears are the bottleneck, not compute.** An image grid is judged in a glance; audio
   plays in real time. Every listening set is budgeted in minutes of audio and earned by
   triage: machine metrics on everything → spectrogram skim → blind listening of a
   stratified subset that **always includes random picks**, so the metrics can't quietly
   censor the interesting-weird (Xander's standing rule: blind listening first, metrics
   secondary).
2. **The corpus defines the space; taste is a later fit.** The base space comes from a big
   synthetic corpus now. Xander's taste enters through selection fits on *labeled anarchy
   renders* (semantic-anarchy's `selection_fit.py` pattern), not by gating the base corpus
   on hand-scored prompts. The render dataset as of 2026-11-05 is 24 renders, ~6 distinct
   prompts, 1 score; waiting for ≥100 scored prompts would block the project indefinitely.
3. **Common random numbers for conditioning, not just noise.** Fix a draw direction and
   vary only its radius: a *ladder* makes the edge audible as a transition, where
   independent draws per temperature only give scattered points.
4. **Model the bag.** Token-level samplers are the SA3-native addition, run beside the
   tensor samplers, never replacing them (the "statistically wrong" ones are textures).

## Stage 1 — corpora (no renders)

Code lives in the folder `sa3lab/anarchy/` (one module per step: `corpus.py` exists; `mine`,
`fit`, `autopsy`, `render` to come), run with `stable-audio-3/optimized/mlx/.venv/bin/python`.
No upstream edits. It is a folder on purpose: `sa3lab/devwatch.py` restarts the studio on
any edit to `sa3lab/*.py` (non-recursive glob), so code here never bounces Xander's studio.

**Built: `grammar-music-100`.** `sa3lab/anarchy/worlds.py` is a catalogue of 100 musical
*worlds* (genre tags, instrument/mood pools, tempo range or beatless, one AudioSparx-style
description, library keywords), built to push every axis to an extreme somewhere rather
than to be a genre atlas (its docstring lists the axes): club 8, electronica 5, avant-garde
/ sound art 9, ritual 5, early 3, classical 5, cinematic 6, jazz 5, rock 4, metal 4,
hip hop/soul/pop 6, folk 4, world 14, solo stems 6, field recording 4, and 12 `edge`
worlds (impossible hybrids, physical-process and synesthetic prompts) kept separable by
`family`. Descriptions vary register on purpose (5-word to 50-word, technical to poetic).
`sa3lab/anarchy/corpus.py` draws `--per-world` prompts from each in the paper's grammar
(field names, shuffle, subsample, lowercase each 50%; `TrackType` prefix 50%, always on
stems) with a style quota `tags` 55% / `prose` 25% / `mixed` 20%, recorded per prompt in
the `.jsonl` because surface style is a PC1 suspect; edge worlds only get `prose`/`mixed`.
Output in `sa3lab/anarchy/corpora/` (`.txt` + `.jsonl`, T5Gemma token counts 7–80, median
36). One deliberate deviation from the paper: subsampling always keeps `Genre` and ≥ 3
fields, since at 1 prompt per world a prompt stripped to `bpm: 171` wastes a world.
Scale-up = `--per-world 30`, or more worlds. `sa3lab/anarchy/diversity.py` measures a
corpus (mean-pooled cosine NN / pairwise distance, total variance, PCA effective rank,
most-similar pairs); vs the earlier conventional catalogue: NN distance +22%, total
variance +17%, token-level effective rank 112 → 140, consistent over seeds 0–3. Trap for
the autopsy: on grammar prompts the pooled vector is dominated by the *template*, so
nearest pairs are template twins; `diversity.py --worlds` measures fixed-template views
(description-only PC1 = stock-genre ↔ edge register, PC2 = description length).
Not committed (open question 3 below).

| corpus | what | role |
|---|---|---|
| `grammar-music` | prompts generated in the training grammar from `worlds.py`: 100 now (1 per world), ~3k later | the base space |
| `grammar-sfx` | ~1k `TrackType: SFX` Freesound-style prompts | cross-corpus experiments (Stage 4C) |
| `xander` | every distinct studio prompt (`outputs/sa3-gradio/*.json`, `gen.prompt` or top-level `prompt`) + the bench prompts, near-duplicate families collapsed | `real` control row; sub-fits later |
| `captions` (optional) | MusicCaps / Song Describer free-text captions, text only | tests whether free text and tag grammar occupy different regions |

The vocab lists and generator are code-like text and could be committed, but they encode
Xander's taste: ask first. The `captions` download is small but still a download: ask.

**Built: reference renders of the corpus.** `sa3lab/anarchy/render_corpus.py` renders
every prompt of a corpus with the studio's *default* controls into a separate folder
(`outputs/sa3-corpus-grammar-music-100/` for this corpus, `render.log` beside it), in
exactly the studio's format: it runs `studio.main()` headlessly with `gr.Blocks.launch`
swapped for the batch loop and `sa3_gradio.OUTPUT_DIR` retargeted before `studio` is
imported, so the files, the `gen` block, the uid naming and the tempo `analysis` come from
the studio's own code, never a copy. It adds one extra sidecar block, `corpus` (file,
line, world, family, style). Resumable (skips prompts already in the folder). Trap: if
the studio's handler wiring changes (`studio._wire_handlers`, upstream's `generate`
inputs), re-run it with `--dry-run` and check the printed control values.

## Stage 2 — mine, fit, autopsy (no listening)

**Built (first fit, `grammar-music-100`, Medium):** `sa3lab/anarchy/fit.py` does mine + fit +
concept vocabulary + a correlation autopsy in one command (seconds; the encode is cached), into
`models/derived/anarchy/<corpus>/` (`mine.npz`, `medium__tensor`, `medium__token`,
`medium__concepts.npz`, `medium__report.json`; 44 MB). `fit.expand()` turns a cropped sample back
into `(256, 768)`. It encodes one prompt per call (not batched), the way the studio does. Findings:
- **The learned padding vector is ~zero** (norm 0.5 vs content tokens 122 ± 16). Padding
  positions add attention keys (qk-norm rescales even a tiny vector to unit size) but ~zero
  values, so the pad count dilutes the content: evidence for the strength/emphasis lane (4A).
- **PC1 and PC2 are wording axes, not music**: PC1 (7.5% var) has r = +0.84 with prompt length
  and +0.72 with the `TrackType` prefix; PC2 has −0.59 with the prefix and −0.53 with tags style.
  Surface style and lowercase load PC3–PC5. Only 31 of the 99 axes clear the noise floor.
  Before E00: decide whether to normalize the wording (fixed prefix policy, fixed case) in v2,
  or sample with `comp_lo` past the wording axes.
- k-means clusters split cleanly into template/punctuation clusters (`type, genre, moods`,
  `:`, `,`), instruments, genres, timbre words (`drone, granular, tape, glitch`), motion words
  (`fast, interlocking, rolling`), places and sub-word fragments: the vocabulary for
  concept cocktails, once the template clusters are excluded.
- Sampling sanity: pca draws at T 1 land at `distance()` ≈ 1, T 2 at ≈ 2; corpus prompts sit at
  0.55–0.94.

- **`mine`**: encode each corpus batched; cache **raw** embeddings + masks + token ids (so
  every vector can be traced back to the word it came from), fp16. **Crop** to
  `L_crop` = the corpus's max token length rounded up to 8: every position past it is
  always padding and is reconstructed, never stored. At L_crop 64: ~98 KB/prompt, so 5k
  prompts ≈ 0.5 GB.
- **`fit`** (per DiT, because the padding vector is per DiT):
  - *tensor fit*: `apply_prompt_padding`, then `EmbeddingDistribution.fit(padded, lengths=,
    n_components=)` on `(N, L_crop, 768)`. Cropping also keeps `distance()` meaningful
    (otherwise ~75% of the coordinates are constant padding diluting the gauge).
  - *token fit*: content vectors pooled to `(ΣL, 768)` → `EmbeddingDistribution.fit`, plus
    a small numpy k-means (K ≈ 32–128) = a **concept vocabulary**: each centroid labeled by
    its most common source words, so a cluster reads as "sitar / tabla / raga" and not as
    "cluster 17". Store the corpus length histogram and per-token norm distribution.
- **`autopsy`** (one `stats.json` + a few PNGs): length histogram; PCA spectrum vs
  `noise_floor_axes()`; correlation of PC1/PC2 with length (it was length in disguise on
  sd15); content-token norms vs the padding vector's norm; the corpus radius band; the
  concept vocabulary table. This sets the temperature ranges for E00 before a single clip
  is heard.
- **Three numeric sanity renders** (1 seed, 5 s, compare latents numerically, no listening):
  1. shuffle the content rows of a real prompt → latent diff ≈ fp noise (confirms the bag
     reading);
  2. on Medium, zeros bag vs tiled-pad bag vs tiled-random-vector bag → identical
     (confirms the differential null);
  3. pads dropped (Tc = L+1) → runs, and how far it lands from Tc = 257 (gates the
     emphasis/strength lane).
- **Where files go**: corpora caches and fits are derived model files →
  `models/derived/anarchy/<corpus>__<dit>.npz` (+ `.meta.json` with the semantic-anarchy
  commit hash). Ask before anything > ~1 GB; `df -h ~` first (93% full on 2026-11-05).
- **Importing semantic-anarchy**: `sys.path.insert(0, $SA_REPO or ~/Documents/GitHub/semantic-anarchy)`,
  `from semantic_anarchy.distribution import EmbeddingDistribution`. Numpy-only at import;
  don't pull its torch-tier modules, don't vendor a copy (that forks the math).

## Stage 3 — `E00-edge`, the first listening experiment

**Built: the render tooling (all listening data so far was discarded; start fresh).**
- `sa3lab/anarchy/sampler.py`: an anarchy sample is a prompt string,
  `anarchy: <fit> <sampler> T<temp> d<dir> [L<prompt tokens>] [c<skip PCs>]` or
  `anarchy: @<saved .cond.npz>`. Deterministic: the string reproduces the conditioning.
- Studio Promptless panel (`sa3lab/anarchy_ui.py/.js/.css`): an Active switch; while on, the
  Prompt box mirrors the spec (read-only; the text prompt is stashed and restored on switch-off)
  and the studio's own Generate renders the chosen mode (one draw / N random directions /
  N-step temperature ladder with one pinned noise seed). Temperature is a range (two-thumb
  slider) drawn per render, uniform or Gaussian (σ = range/4); the box shows it as
  `T0.5–2(uniform)`, which anarchy_ui.py also resolves server-side (Infinite Radio's pregen
  re-reads the box) and the sidecar records the concrete T drawn. A blank Direction box
  = a new random direction per render (`d?` in the box, resolved the same way). So the queue, Stop, history and labels are
  unchanged. Restoring a LATENT clip's settings loads its spec into the panel and switches it
  on; restoring a TEXT clip switches it off. Every latent render saves
  `<audio>.cond.npz` (fp16 = exactly what the DiT consumed) + an `anarchy` sidecar block;
  clip cards carry TEXT / LATENT badges, a colour-coded `distance()` pill on the spectrogram
  (green at 1, blue tamer, red at 3), and the settings card shows the latent block.
- Two 4A bag operators are panel samplers (built 2026-11-05, nothing heard yet): `cocktail`
  (`k<n>`) and `mutate` (`m<pct> | <anchor text>`, the panel's Anchor box). Semantics and
  the concept-cluster filter in the `anarchy/sampler.py` docstring; numbers in 4A below.
- Switchable output folder (`sa3lab/outdir.py/.js`): a folder chip by the Library button picks
  the folder the history + Library show; each Generate binds its destination at click time,
  so queued jobs keep their folder, and a clip finishing in a folder that's not on screen does
  not enter the current view. Latent sessions go in `outputs/anarchy/<session>/`.
- Batch scripts on the studio-headless harness `sa3lab/anarchy/headless.py`:
  `edge_map.py` (the CRN ladder design below, 10 s) and `random_batch.py` (every number drawn
  from a distribution). They pre-date the `.cond.npz`-beside-audio convention and save
  conditioning in `cond/` instead. Injection verified exact (prompt vs its own injected
  encoding: bit-identical audio).
- Xander's call: the first E00 (48 clips) and random batch 2 (6 clips) were moved to the
  macOS Trash (`~/.Trash/sa3-latent-samples-2026-11-05/`), so no clip has been listened to
  or labeled yet.

**Question:** per sampler family, at what reach does music turn to mush, and what lives
just before the edge?

**Design:** Medium (ARC) + SAME-L, 10 s, cfg 1, 8 steps, all frozen. Per family, draw 4
fixed directions (one numpy rng for conditioning, a separate MLX seed for noise, one noise
seed per direction) and render each as a radius ladder.

| family | conditioning | ladder |
|---|---|---|
| `real` | 4 `grammar-music` + 4 `xander` prompts, encoded as normal | — (8 clips) |
| `tensor-pca` | `sampler="pca"`, `lengths=` drawn from the corpus | T 0.5, 1, 1.5, 2, 3 |
| `tensor-diag` | `sampler="diagonal"`, `lengths=` drawn | T 0.5, 1, 1.5, 2 |
| `bag` | L from the length histogram; L token vectors from the token fit (`pca`); rest padding | T 0.5, 1, 1.5, 2, 3 |

64 clips ≈ 11 minutes of audio. Alongside it, an **unattended wide batch** (32 directions per
family, same ladders) for machine-only curves; nobody listens to it whole.

**Measure** (machine, every clip): tempo confidence (`sa3lab/tempo.py`: beat-driven renders
0.5–0.85, beatless < 0.1, so it's a free structure gauge), Audiobox Aesthetics + CLAP
(`sa3lab/bench/score_bench.py`), CLAP nearest corpus prompt (*where did it land*), CLAP
zero-shot against the concept vocabulary (an automatic one-line description of each clip,
the raw material for naming), `distance()`. Plot metric vs T per family: the curves say
where to listen.

**Listen:** first the 64 clips **blind** for keeper-rate (≥ 7), P90 and the best clips per
family, then the 16 ladders in order, un-blinded, to place each edge. Blind first, so the
ladder doesn't prime the scores. `sa3lab/bench/make_listen.py` + `labeler/` is hardwired to
`outputs/sa3_bench_quality/manifest.json`: give it a `--manifest` argument rather than
copying it. Optional skim aid: a spectrogram contact sheet (`sa3lab/hispec.py`), since mush
vs structure (beat striations, harmonic lines) is visible before it's heard.

**Hypotheses** (falsifiable by ear): H1 `tensor-diag` is mush by T 1. H2 `tensor-pca` stays
musical to T ≈ 2, in genres CLAP can't place. H3 `bag` at T ≤ 1 sounds like legit
never-written prompts, and its edge sits further out than pca's, because each vector stays
near the token manifold.

**Decision after E00** (log the branch taken):

| result | next lane |
|---|---|
| edge far out (musical at T ≥ 2) | reach: norm matching, radius band, `split` diagnostic, sub-corpus fits (4B, 4C) |
| edge near T ≈ 1 | in-hull: bag operators, neighbourhoods, hybrids (4A) |
| the mush is interesting | texture: `sm-sfx`, ρ sweeps on diagonal, mush as a2a/inpaint source |
| metrics disagree with ears | trust ears; drop that metric from triage |

Lanes 4D (time) and 4F (taste loop) are worth running whatever E00 says.

## Stage 4 — lanes (one experiment each, usually one ladder or grid)

**4A · Bag operators (SA3-native; no semantic-anarchy equivalent)**
- *Crossover*: one bag built from token subsets of 2–3 real prompts (genre fusion without
  writing the hybrid prompt). Control: encode the concatenated text.
- *Emphasis*: duplicate chosen tokens ×{1, 2, 4, 8} = prompt weighting on the distilled
  model; and a padding-count sweep (fewer/more pads) as a conditioning-strength dial. Gated
  on sanity render 3.
- *Replace-m* — **built as the `mutate` sampler**: a real prompt's bag with m% of its L
  vectors resampled from the token fit at T. A reach knob anchored on a known prompt, in
  units anyone can read ("40% resampled"). Verified: m0 is bit-identical to the prompt's own
  encoding. Experiment: one prompt, m ∈ {0, 20, 40, 70, 100} at T 1, fixed noise seed.
- *Concept cocktails* — **built as the `cocktail` sampler**: k concept clusters share the L
  content slots (each repeated ≈ L/k times); slot = cluster mean + T·(real member token −
  mean), norm-matched to that token (a raw cluster mean is ~25% shorter: 90 vs 122). Only
  16 of `grammar-music-100`'s 48 clusters survive the template/punctuation filter, and a few
  of those are sub-word fragments; a bigger corpus or a higher `--k` gives a richer
  vocabulary (semantic-anarchy's E10 "sparse axis cocktails").

**4B · Reach corrections**: per-token norm matching (the SA3-specific one, above); radius
band; length mode corpus vs fixed vs off; `split` four corners (temp-on/off ∈ {0.5, 2}²):
*does the good weirdness live on the corpus manifold or off it?* (semantic-anarchy E04;
`docs/reference/sampler-corrections.md` there).

**4C · The corpus as an instrument**: sub-corpus fits (only jungle prompts → pca = jungles
nobody named); `grammar-sfx` fit sampled through the *music* DiT and vice versa; fit
interpolation μ_music → μ_sfx; `xander` vs `grammar` (does his taste occupy a distinct
region?).

**4D · Time, the audio-native payoff** (needs a copy of the ping-pong loop in `sa3lab/`,
no upstream edit):
- *Velocity crossfade over time*: evaluate the DiT under two conditionings each step and
  blend the two velocities with a mask over the latent time axis ramping A → B
  (MultiDiffusion applied to time). One clip, one noise, self-attention keeping it
  coherent, a genre morph inside a single render. 2× DiT cost per step. Untested:
  prototype on two real prompts before anarchic ones.
- *Per-step schedules*: anarchic conditioning early (structure) → real late (timbre), and
  the reverse.
- *Drifting long pieces*: render segments along a SLERP path, each inpaint-continuing the
  previous one's tail (`--inpaint-range`, `local_add_cond` in `sa3_mlx.py`;
  `travel.frame_plan` in semantic-anarchy).
- *Duration decoupling*: condition on `seconds_total` ≠ the length rendered (long durations
  bias toward ambient).

**4E · CFG on the base checkpoint**: negative = per-position corpus mean μ, a hot
independent sample, or a "boring" sub-corpus mean. Note a *constant* bag is a null on
Medium (above), so a mean that is identical across positions behaves as upstream's zeros.

**4F · Taste loop**: the studio writes the exact `(256, 768)` conditioning `.npz` next to
every render → selection fit on keepers or one tag → pca on it = "more of what I liked";
a labeled-steering vector once labels exist; a "promptless" mode in the studio
(`sa3lab/studio.py` docstring: how upstream gradio is patched without editing it).

Name what earns a name (a region, a recipe: "bag-cocktail sitar×breakcore at T 1.5") in a
lexicon in the research doc, semantic-anarchy-ledger style.

## Every render (all stages)

- Path: `outputs/anarchy/<exp-id>/sa3_anarchy_<family>_d<dir>_T<temp>_s<seed>.wav`, absolute
  (relative `--out` in `./sa3` resolves under `optimized/mlx/output/`).
- Two sidecars written **before** the audio: `.npz` with the exact conditioning (fp16;
  what makes a clip explorable later: neighbourhood, crossover, morphs, selection fits) and
  `.json` with a `gen` block in `library.py`'s schema (prompt `null` for sampled
  conditioning) plus an `anarchy` block (fit file, family, sampler + all kwargs,
  conditioning rng seed, direction id, `distance()`, length drawn, semantic-anarchy
  commit, experiment id).
- Log every experiment in the SA3 research Claude Doc
  (https://claude.ai/artifact/DypQNkdfui1FG9pJosuHu7): hypothesis, design, numbers
  (keeper-rate, P90), what was learned, best clips with full settings, next.

## Traps

- **The padding vector is per DiT.** A fit on padded embeddings belongs to one DiT; the
  cache stores raw embeddings for that reason. Mixing gives no error, just wrong conditioning.
- **Length still changes the clip, less than before.** Since 2026-11-05 the studio crops all
  noise from one fixed 4608-frame canvas (`sa3lab/noise.py`; sidecar `gen.noise:
  "crop4608"`), so a seed shares its noise across lengths. Before that, noise was drawn at
  `(1, 256, T_lat)`, so another length meant different noise (r ≈ 0.001). Even with shared
  noise, the duration token and full-sequence attention change the piece: never vary
  seconds inside a ladder; a keeper re-rendered at 120 s is a cousin. Batch scripts that
  bypass the studio (`sa3lab/bench/quality_bench.py`) still draw per-length noise.
- **Past-prompt positions are a two-population mixture** (content vs the constant pad), and
  the pad side has zero variance. A pooled Gaussian peaks in the gap: fit and sample with
  `lengths=`. Its `MIN_LENGTH_GROUP` is 20: positions with fewer prompts on one side
  silently fall back to the pooled fit, so small corpora get no correction there.
- **Per-position `hybrid`/SLERP between prompts of different lengths** blends content with
  padding in the gap between their lengths: it fades in B's extra tokens rather than
  blending genres. For genre fusion use bag crossover (4A).
- **Knob interactions.** `temperature` is cancelled exactly by `retarget()` /
  `target_distance` / the radius band and is a redundant factor under `split`. One reach
  control at a time (semantic-anarchy `docs/reference/sampler-knobs.md`).
- **Cast** the sampled float32 array to the DiT dtype before concatenating the duration
  token; **don't fit the duration token** (it's appended from `seconds`).
- **LoRAs**: underfit adapters touch only the seconds embedder on the conditioning side
  (`apply_conditioner_lora`), so a text fit stays valid under a LoRA, but the DiT weights
  change, so a LoRA'd run is a different experiment.
- **Metrics censor the weird.** Triage ranks by metrics; every listening set still gets
  random picks.
- Never commit corpora caches, fits or audio: every repo here is public.

## Open questions for Xander

1. Use the generated `grammar-music` corpus as the base space now (recommended), instead
   of waiting for studio prompts?
2. Download MusicCaps / Song Describer captions (text only) for the `captions` corpus?
3. Commit the corpus generator and its vocab lists to the public repo?

Defaults taken unless he objects: renders in `outputs/anarchy/<exp-id>/` with the
generalized blind page (not the studio Library); sweeps at 10 s on Medium ARC + SAME-L;
long renders only for keepers.

## Reference index

| What | Where |
|---|---|
| semantic-anarchy overview, every subsystem | `~/Documents/GitHub/semantic-anarchy/CLAUDE.md` |
| The fit + samplers (reuse directly) | `semantic_anarchy/distribution.py`; tests `tests/test_distribution.py`, `tests/test_wide_fit.py` |
| Why samples go wrong + the corrections (length, ρ, empirical head, radius band, split, noise floor) | `docs/reference/sampler-corrections.md` (semantic-anarchy) |
| Which sampler knobs are live together | `docs/reference/sampler-knobs.md` (semantic-anarchy) |
| Fitting on picked outputs | `semantic_anarchy/selection_fit.py`, `docs/reference/selection-fit.md` |
| Interpolation / easing / frame plans | `semantic_anarchy/travel.py`, `scripts/morph_film.py` |
| Labeling conventions, tail-weighted summaries | `semantic_anarchy/labels.py`, `docs/reference/labeling.md` |
| The image-side exploration plan these lanes adapt (E01–E19), protocol, ledger format | `docs/TODO/01_exploration_plan.md`, `docs/TODO/02_experiment_ledger.md` (semantic-anarchy) |
| Corpus statistics tooling | `semantic_anarchy/analysis.py`, `scripts/analyze_distribution.py` |
| SA3 architecture, training prompt grammar, conditioning routing | `stable-audio-3/docs/reference/sa3-model-map.md` |
| SA3 prompt style | `stable-audio-3/docs/guides/prompting.md` |
| Cross-attention (bag, differential, qk-norm) | `stable-audio-3/optimized/mlx/models/defs/dit_mlx_medium.py`, `dit_mlx.py` |
| MLX generation paths (conditioning, noise, CFG/APG, inpaint) | `stable-audio-3/optimized/mlx/scripts/sa3_mlx.py`, `sa3_gradio.py` |
| Conditioning helpers, sampler, decode | `stable-audio-3/optimized/mlx/models/defs/sa3_pipeline.py`, `t5gemma_mlx.py` |
| In-process render-matrix example to copy | `sa3lab/bench/quality_bench.py` |
| Render dataset schema (`gen`, `label`, `analysis`) | `sa3lab/library.py` docstring; data in `outputs/sa3-gradio/*.json` |
| Tempo / structure gauge | `sa3lab/tempo.py` |
| Blind listening page / auto-metrics | `sa3lab/bench/make_listen.py` + `labeler/`; `sa3lab/bench/score_bench.py` |
| Experiment log destination | SA3 research Claude Doc https://claude.ai/artifact/DypQNkdfui1FG9pJosuHu7 |
