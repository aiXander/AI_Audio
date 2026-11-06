# SA3 Composer: highlights library, similarity browser, compositing workspace

**Status (2026-11-06): spec rev 3, not built.** Rev 2 (Xander's additions: conditioning interpolation
over time, capture on every dadaspec, similarity search, mismatch detection + fixes, one shared UI kit)
was then checked against the code and the render data. Rev 3 changes: design for the library we
actually have (mostly promptless, beatless renders, 1.1), a render id that works across folders (2.2),
server-side job submission (4.1), corrected soft fix-up evidence plus a second mask variant (4.4),
a cheaper default for conditioning interpolation (4.5), a headless harness first (5), and (Xander)
layering: stacked pieces on tracks with a per-piece mix strip (4.1).
Open questions are at the end.

**Goal.** One music-generation tool, not separate pages. Curate great moments out of the studio's
renders (highlights on starred renders), find related moments by feel, and composite them into tracks
on a timeline. SA3 inpaints the gaps and seams and glues mismatched pieces. Every take keeps its lineage
back to the renders it came from.

**The hard problem.** Moments from different renders rarely share tempo, key, loudness or timbre, and
they never share conditioning. Inpainting between them asks SA3 to bridge material that doesn't fit.
Section 4 covers detecting mismatches and fixing them, with DSP and with the model's own machinery.

## 1. What exists to build on

| Piece | Where | Gives us |
|---|---|---|
| Multi-span inpaint on SA3's native mask | `sa3lab/segments.py` + `.js` (spans in a JSON box → per-latent-frame keep mask, swapped into upstream's `local_add_cond` closure and paste-back) | gaps and seams = spans; spans past the end = continuation; Length follows the reference; one input mode at a time |
| Star | `library.star_render`: symlinks audio + sidecar + `.cond.npz` into `outputs/starred/`; labels write through to the original | the library *is* the starred set; no new store |
| Library modal | studio.js (filter modal over `/sa3/library` rows) | the starting point for the browser |
| Arranger | `sa3lab/arranger/` (canvas timeline, template JSON, own server; plan [sa3-arranger.md](sa3-arranger.md)) | timeline drawing code to learn from; its WAV renders can be composer pieces; its plan's item 2 (prompt per section) is the same problem as 4.5 |
| Render engine | the studio's gradio queue; `sa3lab/anarchy/enqueue.py` submits jobs with `gradio_client` | one MLX model in RAM, queue modal, sidecars, labels |
| Analysis | `sa3lab/tempo.py` (BPM + periodicity confidence, numpy + ffmpeg), sidecar `analysis` block, background worker in `library.py` | the pattern for every new per-segment measurement |
| DSP | ffmpeg has `rubberband`, `ebur128`, `equalizer`, `stereotools`; the `rubberband` CLI is installed | conform without new Python deps (the MLX venv has no librosa/scipy) |
| Anarchy conditioning | every promptless render stores its exact `(256, 768)` conditioning as `<audio>.cond.npz` (`anarchy_ui.py`) | source conditioning for anchors (4.5) and embedding E3, no re-encoding |
| Sound-level handles | [sa3-sound-level-control.md](sa3-sound-level-control.md): handle 1 = SAME-latent fingerprint, handle 6 = DiT activation hooks | embeddings (3.2) and the cross-attention hook (4.5) |

**Model facts that shape the design** (from [the model map](../../stable-audio-3/docs/reference/sa3-model-map.md)
and `optimized/mlx/models/defs/`):
- One pass covers 380 s on Medium (~4,090 latent frames, full self-attention). A 5-minute composite is
  one render, and every gap sees every piece.
- **One latent frame = 92.9 ms** (10.77 frames/s). Spans, seams and soft regions are quantised to that;
  a beat at 120 BPM is 5.4 frames.
- Inpainting is in all training: 10% of examples had 1–10 random spans, 10% a causal mask. **The mask is
  binary in training** (fractional values are out of distribution).
- **Conditioning:** cross-attention to 257 tokens (256 T5Gemma tokens + 1 duration token, no
  positions); the AdaLN global input is the duration embedding only. Per-frame conditioning is
  therefore not native (4.5 adds it). The shipped model is distilled: CFG 1, no uncond pass, by default.
- **Inpainted output is codec audio, not the source audio.** Kept frames are bit-exact *latents*; the
  take is decoded by SAME-L, so a kept piece comes back as the codec's reconstruction of the (fixed)
  piece, re-decoded next to new neighbours. Fine for SA3 renders (already SAME-L output); audible for
  external audio and heavy DSP. A sample-domain splice is the fallback (4.1).
- Generated BPM follows a text prompt very precisely (5 renders prompted "180 BPM" measured 179.8). Key
  adherence from the prompt is unmeasured.
- Quality peaks at 120–190 s; near 380 s output drifts toward ambient/classical.

### 1.1 What the library actually holds (measured 2026-11-06)

`outputs/` holds ~450 renders (`anarchy/` 292, `sa3-corpus-grammar-music-100/` 100, `sa3-gradio/` 26).
**33 are starred, and 31 of those are anarchy renders:** no text prompt, all Medium, 60–120 s, scores
1–6, six with a LoRA. Their tempo analysis: 16 are `nobeat` (periodicity confidence < 0.1), most others
sit at 0.2–0.6, and the BPM values cluster suspiciously on 110.0 and 120.2. Only one starred render has
tags.

What that changes in this spec:
- **Free-time pieces are the common case, not an edge case.** Every beat feature (bar snapping, grid
  badge, tempo conform, seams in beats) applies only when beat confidence is high (≥ 0.5, to be set
  from the data); otherwise snapping is free, seams are in seconds, and tempo/grid badges show "no beat".
- **Text-prompt levers don't reach most pieces.** Prompt pull and regenerate-to-fit (4.6) need a text
  prompt. For anarchy pieces the levers are conditioning (4.5), soft fix-up (4.4) and DSP.
- **Every composite has a conditioning question from day one:** pieces come from different anarchy
  vectors, so even v1 must choose what the gaps are conditioned on (4.5 gives the v1 default).
- **Tags can't name clusters** (3.2) until there are tags; CLAP text probes or nothing.
- **The BPM estimator needs a sanity check** before beat features depend on it: the 110 / 120.2 pile-up
  looks like an artefact of the comb, not music (worth a look in `tempo.py`).

## 2. One tool: shared UI kit, app shell, render ids

**Principle: every page is built from one component library.** A fix or upgrade in one place upgrades
every page. Icons, menus, cards and the timeline look and behave the same everywhere.

### 2.1 `sa3lab/ui/`: the kit

Vanilla ES modules + CSS, no build step (the repo's style). Pages import what they need.

| Module | What | Extracted from / first used by |
|---|---|---|
| `tokens.css` | colors, type, spacing, radii, shadows: the studio theme as CSS variables | `studio.css` / `studio.py` theme |
| `icons.svg` + `icon()` | one SVG sprite, one name per icon | inline SVGs in studio.js, extend.js, uiprefs.js |
| `dadaspec.js` | a spectrogram view: image, mono fold (the uiprefs rule), overlays (highlights, spans, playhead), hover time, the drag-to-highlight gesture | hispec images everywhere |
| `timeline.js` | ruler, playhead, regions (draw / move / resize / duplicate / delete / snap / undo), region types with their own color and menu | the composer; `segments.js` migrates when next touched |
| `player.js` | one-at-a-time playback registry, loop a range | studio's `_sa3All` registry |
| `clipcard.js` | a render card: dadaspec + highlight bands + label strip (score, tags, star, extend, settings) | studio history rows |
| `taginput.js`, `modal.js`, `menu.js`, `toast.js`, `chip.js` | basics | studio modals / popovers |
| `browser.js` | the library browser (grid / map, filters, similarity sort) | Library page, then the Library modal and the composer side panel |
| `shortcuts.js` | one keyboard registry with a help overlay | ⌘↵, ⌘Z, space, ⌫ across pages |
| `api.js` | fetch wrappers for `/sa3/*` routes | everything |

**App shell (`shell.js`):** one top bar on every page: logo, page tabs (Studio · Library · Composer ·
Arranger), queue / progress indicator, settings gear.

**Serving and pages (recommendation):**
- **One origin.** The studio's server exposes `/sa3/ui/*` (static, read from disk per request) and the
  new pages as `/sa3/page/library` and `/sa3/page/composer` (static HTML + kit + `/sa3/*` API routes).
- The studio's injected gradio JS loads kit modules with dynamic `import()`. Gradio takes one JS
  function, and `import()` works inside it.
- **Why one origin:** shared localStorage (the gear's prefs apply everywhere), no CORS, the API is
  next to the data, and pages switch in one tab.
- **What needs `make start`:** backend routes, and studio.js / studio.css (`studio.py` bundles them at
  startup). Kit modules and the new pages are read per request: a reload is enough.
- The Arranger stays its own process, but it adopts the kit and the shell (it links over to :7861).
- Open question 1.

**Migration rule:** new UI is built in the kit first; existing studio pieces move into it when they are
next touched. studio.js is Xander's live workspace and other agents edit it in parallel, so no
big-bang extraction: the kit is born with the first new feature that needs each module (5).

### 2.2 Render ids across folders (prerequisite)

Every `/sa3/*` route today addresses a render by **bare filename inside the one active output folder**
(`library._audio_path`). The browser (starred renders from every folder), composer pieces (any folder)
and takes (`composer/<project>/`) all live outside that folder, so cards, label strips and highlight
writes would silently 404 or hit the wrong file.

- **Render id = the original's path relative to `outputs/`** (symlinks resolved: a `starred/` link maps
  to its original), e.g. `anarchy/session-3/foo.mp3`.
- Routes accept `?id=` alongside `?name=`; `name` keeps meaning "in the active folder" so the studio
  page is untouched. Same sandboxing as `outdir.py` (inside `outputs/`, no `deleted/`).
- Pieces, highlights and `gen.composite` store ids, plus the audio sha256 so a moved file can be re-found.
- Related trap: `star_render` skips the symlink when `starred/` already holds a file of the same name
  from another folder. Rare (names carry seed + hash), but ids make it harmless.

## 3. Highlights library and similarity browser

### 3.1 Highlights on starred renders (capture is built into every dadaspec)

**The library = starred renders + their highlights.** A highlight is a time range on a render, with
tags and a note; it's optional. No audio is copied: cuts happen on demand and are cached.

- **Gesture, everywhere a dadaspec appears** (history cards, Library modal, browser, composer takes):
  - Click seeks.
  - Click-drag (≥ 4 px) selects a range and opens the **highlight modal**.
  - The same rule as the inpaint editor, implemented once in `dadaspec.js`.
- **Highlight modal:**
  - A zoomed dadaspec of the range, looped playback, in/out fine-tune. Snaps to beats or bars only when
    the render has a confident beat (1.1); ⇧ toggles. Shows the range's BPM / key only when confident.
  - Tags (autocomplete from existing tags), note, score.
  - **Save:** stars the render if it isn't starred yet, and adds the highlight.
  - Also "Open in composer" and "More like this".
- **Visible everywhere:** highlight bands drawn over the dadaspec on every card. Clicking a band
  loop-plays it; its menu has edit / remove / send to composer / more like this.
- **Data:** in the original sidecar (symlinks write through), written under `library._lock` with the
  same read-modify-write + atomic rename as `write_label` (the tempo worker writes the same file):
  `label.highlights = [{id, s0, s1, tags, note, score, ts}]`.
  Per-highlight analysis goes in `analysis.highlights[id] = {bpm, bpm_conf, phase, key, key_conf,
  lufs, bands, width}`, filled by the background worker (the tempo-tagging pattern). Embeddings go in
  the cache (3.2), not the sidecar.
- **Unstarring** a render with highlights asks first; the highlights stay in the sidecar either way.

**New analysis, `sa3lab/keyfind.py`** (numpy + ffmpeg, like `tempo.py`):
- **Key:** chroma from a log-frequency STFT, correlated with Krumhansl–Kessler / Temperley profiles
  over 24 keys. Confidence = best-vs-second margin. Expect low confidence on much of the library
  (noise, drones); "no key" is a valid answer.
- **Beat phase:** fold `tempo.py`'s onset envelope at the beat period (confident beats only).
- **Downbeat:** strongest low-band phase of a 4-beat fold. Best-effort, always nudgeable.
- Loudness via `ebur128`; band balance on the dadaspec's 3 bands; stereo width as the side/mid ratio.

### 3.2 Similarity: "more like this", clusters and a map

**Search space: starred renders only** (Xander's call). Starring is the curation step: unstarred
renders never show up in the browser or in "more like this". The set is small today (33), so the grid
does most of the work at first; similarity and the map earn their keep as the starred set grows.
Embeddings are computed when a render is starred (and dropped from the index on unstar).

**Moments, not whole files:** embeddings are computed on **overlapping windows** (e.g. 8 s, hop 4 s)
over every starred render, plus one per highlight. "More like this" then returns a render *and a time
range*, which opens the highlight modal pre-selected. Cached in `outputs/.embeddings/` keyed by
(audio sha256, window).

| # | Embedding | Captures | Cost |
|---|---|---|---|
| E1 | **CLAP** (LAION music CLAP) on the audio | semantic feel / genre / mood; **text search** for free ("dark rolling bass") via the text tower | torch + a ~0.7 GB download (ask first); runs as a separate `uv` worker like `bench/score_bench.py`, never in the studio's MLX process |
| E2 | **SAME-latent fingerprint:** per-channel mean + std of SAME-L latents over the window (512-d), sound-level handle 1 | sonic texture as SA3 itself represents it: the best guess at "will these glue" | needs a SAME-L encode of each starred render: run in the studio process when the queue is idle (no second model in RAM), cache the latents once and derive every window from them |
| E3 | **Source conditioning:** mean-pooled `.cond.npz` (anarchy) or T5Gemma output of the prompt | what the render was asked to be; per render, not per window | free for anarchy renders (stored); text renders need one T5Gemma pass each |
| E4 | **Dadaspec image** (Xander's idea): an image model (DINOv2-small or CLIP-image) on the window's dadaspec | what the eye sees: density, brightness, rhythm stripes, section shape | in E1's worker env |

**Don't pick by assumption.** Experiment 0g collects ~40 blind **triplets** ("is B or C closer to A?")
on the `sa3lab/bench/` listening page; triplets are faster to answer than pairs and compare embeddings
directly. Retrieval agreement picks the default embedding or a weighted mix. Prior: E1 for feel, E2 for
sound/glue, E4 weaker on semantics. The data decides. E2 + E3 ship first (no download).

**Browser (`browser.js`; the Library page, then the Library modal and the composer's side panel):**
- **Grid** of render / highlight cards with BPM / key / score chips (chips only when confident).
  **Filters:** highlighted, tags, BPM range, key-compatible with X, score, folder, model /
  LoRA / source type (text, anarchy, a2a, composite).
- **Sort:** newest, score, or similarity to a selected item. Two similarity modes, named "feel" (E1) and
  "sound" (E2).
- **Map view:** 2-D projection (PCA in numpy; UMAP needs a package) with k-means clusters in color.
  Hover auditions; lasso selects. Cluster names come from CLAP text probes once E1 exists, from tags
  when there are enough of them, otherwise just numbers.

## 4. Composer: making pieces fit

### 4.1 Workspace

The timeline is built on `timeline.js`; the arranger's canvas code is a reference, not a fork.

**Project** (`outputs/composer/<project>/project.json`, autosaved):
- BPM (optional: a free-time project has none), key (optional), length (≤ 380 s on Medium), model and
  LoRAs (one set per project: LoRAs can be gated per sampling step but not per frame), seed.
- Conditioning: one global anchor in v1 (a text prompt, or any piece's source conditioning; default:
  the longest piece's), per-piece anchors once 4.5 exists.

**Pieces** are highlights, whole renders, arranger renders, or **imported audio** (any file Xander drops
in: his own recordings, samples, `audio-data/` material). Imports are copied into
`outputs/composer/<project>/imports/` (gitignored; copyrighted material never leaves the machine), get
the same per-piece analysis as highlights, and are listed in the composer side panel's Imports tab;
they don't enter the starred library. An import has no source conditioning, so it follows the
project's global anchor. Imports are where the codec round trip is most audible: splice export below.

**Tracks and layering (Xander, rev 3).** Pieces sit on **tracks** (timeline lanes), so several can
overlap in time and sound together: a drone under a beat, two textures summed, an arranger bed under
everything.
- **Per-piece mix strip:** gain (dB), mute / solo, pan, high-pass and low-pass (cutoff + slope), and a
  3-band EQ (low shelf, mid peak, high shelf). Example: pull the lows out of a pad so it doesn't fight
  the bass of the piece under it. **v1 is static values** (Xander: simple mixing is enough); automation
  and more effects (compression, reverb) only if v1 use asks for them. These are mix settings, kept apart from the fix chain (4.3) that conforms a piece to
  the project; the render order is fix chain → mix strip → sum.
- **Same filters in the browser and on the server.** Monitoring is live in the page (WebAudio
  `GainNode` / `BiquadFilterNode` / `StereoPannerNode`, no render needed to hear a change); the
  reference WAV is mixed with ffmpeg (`volume`, `highpass` / `lowpass`, `lowshelf` / `equalizer` /
  `highshelf`, `pan`, summed with `amix=normalize=0`). Both implement the same RBJ biquads, so what you
  hear while mixing is what SA3 gets. Only filters with an equivalent on both sides go in the strip.
- **Master bus:** summed layers clip easily (two loud renders add up). The mix goes through a master
  gain + `alimiter` before the reference WAV is written, with a clip / headroom meter in the page.
- **What SA3 sees: one stereo mixture.** The reference is the summed mix, so a kept overlap comes back
  as that exact mix (through the codec). SA3 can't add a layer on top of kept audio or regenerate only
  one layer: a gap, gen or soft region over an overlap re-renders the *whole* sum in those frames.
- **The glue trick:** a **soft region over an overlap** (4.4, 1–2 free steps) asks SA3 to re-render the
  stacked layers as one coherent performance: same room, shared transients, the model's own mix. It is
  the layering counterpart of a seam, and worth its own case in 0b.

**Regions** (`timeline.js` region types):

| Region | Meaning to the model |
|---|---|
| **Piece** | a piece placed on a track: snapped to bars when both piece and project have a beat, trim, fades, its fix chain (4.3) and mix strip. Overlapping pieces are summed. Kept exactly, or soft when its fix-up > 0 |
| **Gap** | uncovered time: generated |
| **Seam** | N beats (or seconds, for free-time pieces) each side of a piece edge, default 2 beats / 1 s: generated |
| **Soft** | held toward the reference, then released (4.4) |
| **Gen** | drawn over a piece: rework it fully |

**Render:**
1. Apply each piece's fix chain (cached), then its mix strip.
2. Sum all tracks into the reference WAV through the master bus (zeros in gaps, 10 ms fades), exactly
   the project length.
3. Build the region map, conditioning anchors and weight curves.
4. Submit N seeds to the studio queue with outdir `composer/<project>`.

**Submission is server-side.** gradio drops a client's waiting jobs when its connection closes
(`enqueue.py`), so jobs submitted from the composer page would die with the tab. The composer backend
(in the studio process, one origin) owns a submitter thread with a loopback `gradio_client`, as
`enqueue.py` does, with the same small window so Xander's own Generate clicks interleave.

The contract with the studio = the segments box JSON, extended to
`{"inpaint": [...], "soft": [[s0, s1, strength]], "cond": {anchors, curves}}`. The take's sidecar gets
`gen.composite` (project, pieces with source render id + sha256 + range + fixes, regions).

**Takes:**
- Each is a normal studio render: a card in the takes strip, with label strip and highlights.
- **Promote** makes a take the project's new base: the take becomes one piece spanning the whole
  timeline (source = the take's render id), the old pieces stay as greyed annotations, and the next
  edits are gaps / soft / gen regions drawn on it. Earlier takes stay as version history. This is the
  iterate-on-a-take loop.
- Dragging on a take's dadaspec makes a highlight, like everywhere. That closes the loop.

**Export:** the take as rendered (codec audio everywhere). Optional **splice export**: the fixed piece
audio pasted back in the sample domain over the kept zones, crossfaded a few frames inside each kept
edge, for external audio or when the codec round trip is audible. Blind A/B before making it default.

**Extend:** grow the project length and draw into the new zone (same as the studio).
**Beyond 380 s (later):** chain windows that keep the last ~60 s as context; no global coherence
across windows.

### 4.2 Mismatch detection

Every piece is measured against the project, and every seam against the pair of pieces it joins. The
results show as colored badges on pieces and seams, plus a **Fit panel** listing every issue.
Dimensions without a confident measurement (no beat, no key) show grey, never a guess.

| Dimension | Measured by | Badge (ok / warn / bad) |
|---|---|---|
| Tempo | `tempo.py` on the range, octave-folded ratio to the project BPM | < 2% / < 8% / ≥ 8% |
| Grid | beat phase + downbeat vs the project's bar grid | < 20 ms / < 60 ms / more (the audio is placed sample-accurately; only the mask is frame-quantised) |
| Key | `keyfind.py`: semitone distance + compatibility (same, relative major/minor, ±1 fifth) | compatible / ±1–2 st / further |
| Loudness | integrated LUFS (ebur128) | < 2 dB / < 5 dB / more |
| Spectral balance | 3-band energy (the dadaspec's bands) | per-band dB deltas |
| Stereo width | side/mid ratio | delta |
| Sound / feel | E2 (and E1) distance to the neighbours | percentile in the library's distance distribution |
| LoRA | the piece's LoRA set vs the project's | flag when they differ: soft regions lose that sound |

**Overlaps are checked harder than seams.** Two layers sounding together clash where two pieces in
sequence merely change, so every overlap gets its own badge set: tempo and beat phase between the two
layers (tighter thresholds: < 1% / < 10 ms ok), key compatibility between them, **band masking**
(both layers loud in the same band, per 3-band energy: suggests an EQ cut on the quieter-ranked one),
and **headroom** (summed peak / LUFS against the master bus). Two beatless layers skip tempo and grid.

For the current library, loudness, spectral balance, masking and sound/feel are the dimensions that
will fire; tempo and key mostly read "no beat / no key".

### 4.3 Fixes (each one a non-destructive setting on the piece)

| Problem | Fix options, in recommendation order |
|---|---|
| Tempo | rubberband stretch · set the project BPM to this piece's · regenerate to fit (4.6, text pieces) · longer seam |
| Grid | auto-align to the downbeat · manual nudge |
| Key | transpose (smallest move to a compatible key, formant-preserving option) · regenerate to fit (text pieces) · conditioning interpolation + soft fix-up |
| Loudness | gain match to the project LUFS |
| Spectral balance | 3-band EQ match (ffmpeg `equalizer`) · soft fix-up |
| Stereo width | width match (ffmpeg `stereotools`) |
| Band masking (overlap) | cut the shared band on one layer via its mix strip (e.g. high-pass the pad at 150 Hz) · lower its gain · soft fix-up over the overlap (the glue trick) |
| Headroom (overlap) | lower layer gains · master limiter (always on) |
| Sound / feel | soft fix-up (blend) · conditioning interpolation (4.5) · longer seams · **swap**: suggest the closest library moments that fit better (browser, "sound" mode, filtered by tempo/key) |

- **Fit all** applies the recommended chain.
- Every fix has an A/B toggle (before/after, loudness-matched) on the piece.
- Fix chains are cached per (piece, settings).
- Conform limits: warn past ±8% tempo and ±3 semitones (rubberband stays clean below that). Hard
  limits are open question 4.

### 4.4 Soft fix-up: per-frame strength on SA3's own schedule

Upstream's a2a σ isn't a strength scale: at 0.6 it predicts once from the guide, then re-noises up to
0.91 (the pingpong schedule re-noises with fresh noise every step). Soft regions are therefore a new
mode on the **inpaint** input, not a2a, so the one-input-mode rule doesn't bite.

**The design is differential diffusion** (Levin & Fried 2023), on SA3's distilled 8-step schedule
(σ_max = 1): `1.0, .994, .984, .958, .891, .746, .512, .274 → 0`.
- **A soft frame with strength s** has its `x` overwritten with the noised reference
  `ref·(1−t) + n·t` on every step with t ≥ s, then is released. The model always sees one uniform
  noise level.
- **Strength = free steps:** s ≈ 0.3 → 1, 0.6 → 2, 0.8 → 3, 0.9 → 4, 1.0 → 8. The schedule is
  front-loaded (three of eight steps sit above t = 0.95), so **8 steps give only four usable settings
  and nothing between 4 and 8 free steps.** 16 steps (`.997 … .386, .274, .184`) give roughly twice
  the resolution at twice the cost. Ladder names (polish · blend · rework · regenerate) wait for 0a.
- **Mask channel, two variants to test in 0a:**
  - **A, mask 0 throughout:** soft frames are pulled toward the reference only through the held `x`.
    Risk: in the early steps, where the arrangement is decided, `x` is ~99% noise, so neighbouring gap
    frames hardly see the soft piece and may not line up with it.
  - **B, kept while held:** mask 1 with the reference in the conditioning channel while held, flipped
    to mask 0 on release. Gaps see the clean piece through the early steps; each step's input is still
    a state the model trained on.
  Kept frames stay mask 1 with paste-back in both.
- **Evidence so far is thin and was read as a failure.** The research log's 2026-11-06 a2a test
  (sm-music + SAME-S, 6 s, one seed, log-spectrogram correlation with the guide) found that holding
  frames while t ≥ σ made the clip "too sticky" (0.29 → 0.79) and dropped it for a2a. Sticky is what
  "polish" wants, but that is one data point, on the weaker model, and no ears yet. 0a re-runs it on
  Medium + SAME-L, both variants, with blind listening first.
- **Limit:** 1–3 free steps can't re-time or re-key a piece, because the arrangement is set in the
  early steps. DSP does the alignment.

It lives in `segments.py`, as a sampler branch for `soft` (the same `sample_flow_pingpong` wrap and
`local_add_cond` cell swap it already does, now per step). `timeline.js` gets a soft region type, so the
studio's inpaint editor offers it once it migrates.

### 4.5 Conditioning interpolation over time

Pieces carry their **source conditioning**: the `.cond.npz` bag (anarchy) or the prompt (text). Each
becomes an **anchor** with a weight curve over latent frames:
- weight 1 over its own piece and soft regions;
- across a gap or seam, a crossfade to the next anchor (cosine by default, or hold-then-fade in the
  middle N beats / seconds);
- over an overlap of layers, the anchors share the frame, weighted by each layer's loudness after its
  mix strip by default (a −12 dB pad barely pulls the conditioning), editable per overlap. Weights are
  normalised to sum to 1 per frame.

An anchor is the 256 conditioning tokens only. The 257th token (duration) and the AdaLN input are the
project's length for every anchor, not the source render's.

**Mechanisms** (text conditioning is global, so per-frame conditioning needs a hook):

| | Mechanism | Cost | In-distribution? |
|---|---|---|---|
| **M1** | **Cross-attention output blend** in every DiT block: `CA_f = Σ_i w_i(f) · CA(q_f, K_i, V_i)`. Self-attention + FFN run once; only cross-attention runs N times (257 keys, tiny next to self-attention over ~4k frames) | ~free | each frame gets a convex mix of in-distribution outputs; exact at weight 1; the weights mean what they say |
| M2 | **Joint attention with log-weight bias:** frame f attends over all anchors' tokens, logit bias `log w_i(f)` on anchor i's | lowest | exact at weight 1, but in between anchor i's share is ∝ w_i × its attention mass, so a high-logit anchor dominates whatever w says; Medium's differential cross-attention (two softmaxes subtracted) needs the bias in both maps and doesn't yield a convex mix. Only if M1 sounds wrong |
| **M3** | **Velocity blend:** N full forward passes, velocities blended per frame (the arranger plan's item 2) | N× (a 300 s take with 2 anchors doubles its render time) | each pass fully in-distribution: the reference to judge M1 against |
| M4 | Token-wise lerp / slerp of the conditioning sequences | low | weak: anarchy conditionings are unordered bags, so position-wise lerp is arbitrary. Last resort |

- **Default: M1.** Same quality ceiling as M3 is the bet; 0f checks it against M3 on one gap.
- **Hook:** M1 wraps each block's `cross_attn` module from `sa3lab/` (no upstream edit), the same wrap
  point as activation steering (sound-level handle 6). M3 wraps `model_fn` in the sampler.
- CFG ≠ 1 applies the blend on both branches.
- **Same machinery, three uses:**
  - **studio morph** (conditioning A → B over one render): the first deliverable, since it needs
    neither the composer nor any piece, and it is the test bed for 0f;
  - composer anchors;
  - the arranger's per-section prompts (its item 2: build once, share).
- **Pairs with soft fix-up:** a soft region re-rendered under its own anchor's conditioning reworks a
  piece in its own idiom.

### 4.6 Smaller native levers

- **Prompt pull (text anchors):** write `BPM: <project>` (and `Key:` if 0c says it works) into every
  text anchor, so gaps and seams land on the project grid.
- **BPM splice (anarchy anchors, experiment 0h):** the DiT reads the conditioning as an unordered bag,
  so replacing a few of an anarchy bag's 256 vectors with the T5Gemma tokens of `BPM: 120` may pull the
  tempo the way a prompt does. Cheap to test; if it works, prompt pull reaches the whole library.
- **Regenerate to fit (text pieces):**
  - Re-render a highlight's source from its own recipe with the project BPM/key in the prompt, same
    seed (the `noise.py` canvas keeps the seed's opening).
  - Then re-cut the range, scaled by the tempo ratio.
  - Works only if the arrangement survives the prompt change (test 0e).
- **Latent time-stretch:** resample SAME latents along time by the tempo ratio, decode, then fix-up.
  A cheap alternative to rubberband for tempo only (test 0e).

## 5. Phases

**Order rationale:** the cheapest path to *hearing a composite* uses only what exists (multi-span
inpaint + a reference WAV + the studio queue). So a headless harness comes first: it is the experiment
rig for phase 0 and later the composer's backend. UI follows the experiments, and the kit grows with
the first feature that needs each module.

**Phase 0: harness + experiments that set the defaults.** Short renders (6 s for plumbing checks,
30–60 s for listening), Medium + SAME-L (sm-music is the weak model for edits), results in the research
doc's Log, blind listening before metrics.
- **Harness, `sa3lab/composer/compose.py`:** `project.json` (pieces as render id + range + position,
  regions, anchors) → fixed pieces → reference WAV + box JSON → server-side submit → takes with
  `gen.composite`. Plus render ids on the library routes (2.2).
- **0a Soft ladder:** soft over a whole clip and over one piece in a composite, s ∈ {0.3, 0.6, 0.8,
  0.9}, mask variants A and B; `dadagrid.py` + listening, chroma / band / onset similarity second.
- **0f Conditioning interpolation:** the studio morph between two anarchy bags, M1 vs M3 vs a hard
  cut, then on one composite gap.
- **0b Conform + fix-up:** two pieces mismatched in loudness / balance (and, for beat pieces, ±4/±8%
  tempo and ±2/±5 semitones), conformed, with fix-up 0 / 1 / 2 free steps. Plus a layered case: two
  pieces summed (one high-passed), raw vs soft fix-up over the overlap. Blind A/B.
- **0h BPM splice** into an anarchy bag (4.6), measured with `tempo.py`.
- **0c Key in the prompt:** 4 keys × 3 seeds, measured with `keyfind.py` (text pieces only: low
  priority while the library is anarchy).
- **0d Long window:** a 300 s composite vs 2 × 150 s.
- **0e Regenerate to fit; latent stretch vs rubberband.**
- **0g Embedding pick:** ~40 blind triplets; compare E1–E4 retrieval.

**Phase 1: highlights.** `tokens.css`, `icons.svg`, `player.js`, `dadaspec.js` (with the gesture) born
in the kit and served at `/sa3/ui/`; the gesture on studio history cards and the Library modal; the
highlight modal; bands on cards; the sidecar schema; `keyfind.py` + the per-highlight analysis worker.

**Phase 2: Library page + similarity.** The app shell, `/sa3/page/library`, `browser.js` (grid,
filters, sorts, map), the window-embedding worker (E2 + E3 first; E1 / E4 after the download is
approved), "more like this" returning moments.

**Phase 3: Composer v1.** `/sa3/page/composer` on `timeline.js` over the phase-0 harness: project,
pieces from the browser, tracks with layering (mix strip: gain, mute / solo, pan, high / low-pass,
3-band EQ; master limiter; live WebAudio monitoring), gaps / seams / soft regions, one global anchor
(or M1 anchors if 0f passed), takes, promote, extend.

**Phase 4: fit.** Mismatch detection, the Fit panel, DSP fixes with A/B, swap suggestions.

**Phase 5: conditioning everywhere.** Per-piece anchors in the composer (if not in v1), the arranger's
per-section prompts; then windows past 380 s. `segments.js` moves onto `timeline.js` whenever it is
next touched.

## 6. Traps known up front

- **Don't fork the render path.** Pages submit jobs to the studio; nothing else samples. Two MLX
  processes on the M2 means two models in RAM. Embedding work that needs SAME-L runs in the studio
  process when the queue is idle.
- **Jobs die with their client.** A `gradio_client` (or a browser tab) that disconnects drops its
  waiting jobs; submit from a server-side thread (4.1).
- **Bare filenames only resolve in the active folder** (`library._audio_path`); use render ids (2.2).
- **Upstream's a2a σ is not a strength scale** (4.4).
- **Beats, keys and downbeats are guesses,** and absent for half the library. Always nudgeable, grey
  when unconfident; never silently auto-place on a guessed downbeat.
- **Length follows the reference** (`segments.locked_length`). The composer's reference WAV must be
  exactly the project length.
- **One input mode at a time** in the studio (`segments.BOTH`). The composer uses the inpaint input
  only; soft regions ride on it.
- **Kept ≠ original audio:** kept frames are exact latents, decoded by SAME-L next to new material
  (1, 4.1 splice export).
- **SA3 only sees the mix.** Any generated frame over an overlap regenerates every layer in it; there
  is no per-layer inpaint without stem separation (4.1).
- **Browser preview and the server mix must use the same filters** (RBJ biquads on both sides), or
  what Xander mixes by ear isn't what SA3 receives.
- **Different ports = different localStorage.** UI prefs won't carry across pages served from
  another port (hence the one-origin recommendation in 2.1).
- **LoRAs are per project.** A piece rendered with a LoRA the project doesn't use loses that sound in
  its soft regions; the Fit panel flags it.
- **Audio from `audio-data/` is copyrighted material.** Imported pieces, their cuts and caches stay
  under `outputs/` (gitignored) and nothing is shared.

## 7. Open questions for Xander

1. **One origin:** serve the Library and Composer pages from the studio server (shared prefs and kit,
   no CORS; backend changes need `make start`), or their own processes like the arranger?
   Recommendation: one origin.
2. **Highlights per render:** a list (several great moments in one render) or exactly one? Spec'd as a
   list.
3. **Cut snapping:** whole bars when the render has a confident beat, free otherwise, ⇧ toggles. OK?
4. **Conform hard limits:** refuse past ±25% tempo / ±6 semitones, or allow anything for weirdness?
5. **CLAP download** (~0.7 GB into `models/`, plus a torch env) for feel-similarity and text search:
   OK? DINOv2 for E4 is a similar size.
6. ~~The arranger's synth lanes in the composer~~ Resolved: an arranger render is just a piece (or a
   bed under the pieces); no lanes in the composer.

**Decided (2026-11-06):** phase order is the headless harness + experiments first (Xander left it to
the agent; section 5). Imported audio is allowed as pieces (4.1). The browser and "more like this"
search the starred set only (3.2). The v1 mix strip is static and simple (4.1).
