# SA3 Composer, Library, highlights and similarity

One music tool in the studio process: curate moments out of renders (**highlights** on starred
renders), find related moments (**similarity**, the **Library** page), and composite them into a track
on a timeline (**Composer**) where SA3 inpaints the gaps and seams and glues mismatched pieces. A
project builds a reference WAV (every piece aligned, fixed, mixed, summed) plus a segments box
(strength curve + conditioning anchors); the studio renders it as an ordinary inpaint job, and each
take keeps its lineage back to the renders it came from. All pages sit under the tab host `/sa3/app`
and are built from one UI kit (`sa3lab/ui/`).

Open work (blind listens, defaults waiting on them, known issues): [../TODO/sa3-composer.md](../TODO/sa3-composer.md).

## Where the code lives

The module docstrings are the manual; this is the map.

| File | Role |
|---|---|
| `sa3lab/composer/compose.py` | project.json → layout (measure, align, fix, place) → mix → reference WAV + segments box; strength keys, conditioning curves, windows past 380 s, section renders, server-side job submission, splice export |
| `sa3lab/composer/server.py` | every `/sa3/composer/*` route (list in its docstring), takes listing, promote, keep / trash a take, imports, project-shape conversion |
| `sa3lab/composer/fit.py` | mismatch badges per piece / seam / overlap and the recommended fix chain ("Fit all") |
| `sa3lab/composer/page/composer.js` | the editor (tracks, Rewrite lane, Conditioning lane, take lanes, inspectors, Fit panel) |
| `sa3lab/composer/page/library.html` + `ui/browser.js` | the Library page: grid, filters, PCA map + k-means, lasso; also the composer's side panel |
| `sa3lab/condmix.py` | per-frame conditioning: M1 cross-attention blend, M3 velocity blend (hooks into the DiT) |
| `sa3lab/condsrc.py` | "what was this render conditioned on" for every caller; reads / writes a take's `<audio>.conds.npz` bank |
| `sa3lab/mixdsp.py` | the mix strip + master as ffmpeg filters matching WebAudio's biquads; LUFS |
| `sa3lab/keyfind.py` | key, beat phase, downbeat, LUFS, 3 bands, width for one range |
| `sa3lab/highlights.py`, `highlights_link.js`, `ui/highlight.js` | highlights in sidecars, their analysis worker, the drag-on-any-dadaspec capture |
| `sa3lab/embed.py` | SAME-L window fingerprints (E2) + source conditioning (E3), encoded in the studio process while the queue is idle, cached in `outputs/.embeddings/` |
| `sa3lab/ui/*` | the kit: `timeline.js`, `dadaspec.js`, `player.js`, `mixer.js`, `curvelane.js` / `curve.js` / `strength.js`, `app.js` (tab host), basics |
| `sa3lab/composer/experiments.py`, `acceptance.py`, `bench/embed_triplets.py` | the experiment and blind-listen harnesses (outputs in `outputs/composer-exp/`, `outputs/composer-tests/`) |
| `sa3lab/composer/teststudio.py` | a second studio on another port for agent test renders, never :7860 |

Project folder `outputs/composer/<name>/`: `project.json` (autosaved), the last build (`ref.wav`,
`box.*.json`, `project.resolved.json`), `takes/` (studio sidecars with `gen.composite`; trashed ones in
`takes/deleted/`), `jobs/<stamp>/` (each batch's frozen project), `cache/`, `imports/`, `exports/`.

## Design record

The code cites these section numbers as `spec N.N`.

### 1. Model facts that shape the design

- One pass covers 380 s on Medium (~4,090 latent frames, full self-attention), so a 5-minute composite
  is one render and every gap sees every piece. Past 380 s: chained windows with ~60 s of context,
  no global coherence across them. Quality peaks at 120–190 s; near 380 s it drifts toward ambient.
- **One latent frame = 92.9 ms.** Spans, seams and soft regions quantise to that. Audio is placed
  sample-accurately; only the mask is frame-quantised.
- Inpainting was in training (10 % of examples with 1–10 random spans, 10 % causal), but the training
  mask is **binary**: fractional strength comes from differential diffusion on the sampler schedule
  (`segments.py` docstring), not from a fractional mask.
- Text conditioning is a cross-attended bag of 257 tokens with no positions; AdaLN gets only the
  duration. Per-frame conditioning is not native, hence `condmix.py`.
- **Kept ≠ original audio.** Kept frames are exact latents, but the take is decoded by SAME-L, so a
  kept piece comes back as the codec's reconstruction next to new neighbours. Inaudible for SA3 renders,
  audible for imports and heavy DSP; the splice export pastes the fixed piece audio back in the
  sample domain over kept zones.
- **The library is mostly free-time anarchy renders** (about half have no usable beat, almost none have
  a text prompt). So beat features apply only above beat confidence 0.3 (`compose.BEAT_CONF`: above it
  82–92 % of readings land on an integer BPM, below 0.2 it is chance), and the text-prompt levers
  reach few pieces: conditioning, soft fix-up and DSP are the main tools.

### 2.1 One origin, one kit

The Library and Composer are served by the studio server (`/sa3/page/*`, kit at `/sa3/ui/*`), because
that gives shared localStorage (the gear's prefs apply everywhere), no CORS, the API next to the data,
and one tab. Pages and kit are read from disk per request (a reload is enough); backend routes and
`studio.js` / `studio.css` need `make start`. The Arranger stays its own process on :7861 and links
over. Migration rule: new UI is built in the kit; studio pieces move into it when next touched, never
a big-bang extraction (studio.js is Xander's live workspace and other agents edit it).

### 2.2 Render ids

A render id is the original's path relative to `outputs/` with symlinks resolved (a `starred/` link
maps to its original). Routes take `?id=`; `?name=` keeps meaning "bare filename in the active folder"
for the studio page. Pieces, highlights and `gen.composite` store ids plus the audio sha256.

### 3.1 Highlights

A highlight is a time range on a render with tags, note and score, stored in the original sidecar as
`label.highlights` (several per render), analysis in `analysis.highlights[id]`. No audio is copied;
cuts happen on demand and are cached. Captured by drag (≥ 4 px) on any dadaspec, which opens the
highlight modal; saving stars the render. Bar snapping only with a confident beat (⇧ toggles).

### 3.2 Similarity

Search space = **starred renders only** (starring is the curation step). Embeddings are computed on
overlapping windows (8 s, hop 4 s) so "more like this" returns a moment, not a file. E2 (SAME-L latent
per-channel mean + std, 512-d: "will these glue") and E3 (pooled source conditioning: "what it was asked
to be") are built. E1 (CLAP) and E4 (dadaspec-image model) need downloads and were deferred. The
default between "sound" and "prompt" mode is for the 0g blind triplets to decide, not assumption.
"More like this" is out of the card UI (Xander: too soon, clutter); the route and `embed.py` remain.

### 4.1 Workspace

- **Tracks and layering.** Pieces sit on tracks; overlaps sum. Order per piece: fix chain (conforms it
  to the project) → mix strip (gain, pan, HP / LP, 3-band EQ; static, no automation) → sum → master gain
  + limiter → `ref.wav`, exactly the project length (the studio's Length follows the reference).
- **SA3 only sees the mix.** Any generated or soft frame over an overlap regenerates every layer in it;
  per-layer inpaint would need stem separation. The upside is the **glue trick**: a soft region over an
  overlap asks SA3 to re-perform the stacked layers as one.
- **Alignment.** The project BPM is a live consensus (`compose.tempo_consensus`) over every clip with
  confidence ≥ 0.3, weighted by confidence × length. It is never set by hand in the editor and **never
  written into the conditioning** (SA3 reads tempo from the audio). Confident pieces are octave-folded,
  rubberband-stretched to it, cut to start on their own beat and to whole beats, and snapped to the grid,
  so the grid runs unbroken from t = 0. Beatless and `align: false` pieces (a promoted take) are never
  stretched. Bars: manual ± 1-beat nudge only, no auto-downbeat.
- **Strength.** Kept = every piece's span minus a seam at uncovered edges; uncovered time = 1; the
  Rewrite lane (one curve, 0 keep … 1 regenerate) overrides wherever it is above 0.
- **Takes** land as lanes under the tracks on the same time axis. **Promote** makes a take the base: one
  piece over the whole timeline, the old pieces kept as greyed history, the take's own recorded
  anchors as its conditioning. A take can be **kept** (starred, off the lanes, back on unstar) or
  **trashed** (the studio's trash: score 0, moved to `takes/deleted/`, kept as a negative row).
- **Submission is server-side**: a thread in the studio process with a loopback `gradio_client`,
  because gradio drops a client's waiting jobs when its connection closes, so jobs sent from the page
  would die with the tab. Each batch freezes its project in `jobs/<stamp>/`, so editing during a render
  can't swap the reference under queued jobs.
- **Imports** (any dropped audio) live in the project's `imports/` and never enter the starred library;
  they have no source conditioning and follow the global anchor.

### 4.2 Mismatch detection and 4.3 fixes

Thresholds and dimensions are in `fit.py`'s docstring. Principles: anything without a confident
measurement shows grey, never a guess; overlaps are checked harder than seams (two layers sounding
together clash where a sequence merely changes); every fix is a non-destructive setting on the piece with
a loudness-matched A/B; conform limits are warnings only, no hard limits (Xander: "let's explore").
Grid and overlap phase use the snap's own beat measurement (`compose.first_beat`), not a re-measure of
the cut piece. Badges use values *predicted* after the fix chain, so applying a fix turns its badge
green without a re-measure; the A/B is the check.

### 4.4 Soft fix-up

A soft region is a key in the strength curve the studio samples by differential diffusion
(`segments.py` docstring: scale, measured ladder, traps). Limit: 1–3 free steps (u ≤ 0.6) can't re-time
or re-key a piece, because the arrangement is set in the early steps; DSP does alignment.

### 4.5 Conditioning over time

Each piece contributes an **anchor** (its source conditioning: an anarchy bag or a prompt) with a weight
curve: 1 over its own span, crossfading across gaps, shared by loudness × `pull` over overlaps,
normalised per frame. The Conditioning lane adds explicit keypoints (crossfade between them; a hard cut is
opt-in). An anchor is the 256 content tokens only; duration is always the project's.

| Mechanism | Verdict |
|---|---|
| **M1** cross-attention output blend per block (`condmix.py`) | **default**: exact at weight 1, a convex mix of in-distribution outputs, +15–25 % cost |
| M3 velocity blend over N full passes | built as the in-distribution reference; N× cost |
| M2 joint attention with log-weight bias | rejected: a high-logit anchor dominates whatever the weights say, and Medium's differential cross-attention doesn't give a convex mix |
| M4 token-wise lerp of the sequences | rejected: bags are unordered, position-wise lerp is arbitrary |

In the acceptance test, m1, m3, a hard cut and no interpolation gave near-identical gaps within a seed
(the noise seed dominates), which is why the cheaper M1 is the default.

### 4.6 Smaller levers (experiments, not features)

0e tests regenerate-to-fit (re-render a text piece's recipe at the project BPM, same seed) and latent
time-stretch (`bench/latent_stretch.py`) against rubberband; 0h tests splicing T5 tokens of
`BPM: 120` into an anarchy bag. Prompt pull (writing `BPM:` into anchors) was dropped with the
"never write the BPM into the conditioning" decision.

## Invariants and traps

- **Don't fork the render path.** Pages submit jobs to the studio; nothing else samples. Two MLX
  processes on the M2 means two models in RAM. Embedding work that needs SAME-L runs in the studio
  process while the queue is idle.
- **Agent test renders go to `teststudio.py`** on another port, never :7860 (Xander's history).
- **Bare filenames only resolve in the active folder** (`library._audio_path`); use render ids.
- **The reference must be exactly the project length**: the studio's Length follows the reference
  (`segments.locked_length`), and one input mode at a time (`segments.BOTH`); the composer uses the
  inpaint input only.
- **Browser preview and server mix must run the same filters** (RBJ biquads both sides, `mixdsp.py` vs
  `ui/mixer.js`), or what Xander mixes by ear isn't what SA3 receives. The limiter is approximate in the
  browser.
- **Beats, keys and downbeats are guesses**, absent for half the library: always nudgeable, grey when
  unconfident, never auto-placed on a guessed downbeat. Busy breakbeats flip between on- and off-beat
  when phase is measured on different audio, so measure once (the snap) and reuse it.
- **Upstream's a2a σ is not a strength scale**; use the strength curve.
- **LoRAs are per project.** A piece rendered with another LoRA loses that sound in its soft regions;
  the Fit panel flags it.
- **Trashing a take that is the base or a clip on the tracks is refused**: the build reads its audio
  and `library.resolve_id` never resolves into `deleted/`. Conditioning *does* resolve from the trash
  (`condsrc.side`), so a trashed anchor keeps working.
- **Different ports = different localStorage**, which is why everything lives on one origin.
- **Imported audio is copyrighted material**: imports, their cuts and caches stay under `outputs/`
  (gitignored), never shared.
