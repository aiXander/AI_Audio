# SA3 Composer: highlights library, similarity browser, compositing workspace

**Status (2026-11-06): spec rev 5; every phase (0-5) is built**, plus the studio migration and the
experiment harnesses; nothing is judged by ear yet. **Start at section 5**: what shipped (one line
each, where the docstrings are) and what is open, which is mostly Xander's listening. Sections 1-4
are the design that was built; the module docstrings named in section 5 are the current truth where
they differ.

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
- Generated BPM follows a text prompt very precisely (11 of 12 renders prompted "180 BPM" measure 180.0, one 90.0: an octave fold). Key
  adherence from the prompt is unmeasured.
- Quality peaks at 120–190 s; near 380 s output drifts toward ambient/classical.

### 1.1 What the library actually holds (measured 2026-11-06)

`outputs/` holds ~450 renders (`anarchy/` 292, `sa3-corpus-grammar-music-100/` 100, `sa3-gradio/` 26).
**33 are starred, and 31 of those are anarchy renders:** no text prompt, all Medium, 60–120 s, scores
1–6, six with a LoRA. Only one starred render has tags. **Tempo (re-measured with the fixed
`tempo.py`, 37 starred):** 18 have no beat (conf < 0.2), **17 have a usable beat (conf ≥ 0.3)**, 9 of
them ≥ 0.5. Anarchy renders mostly land on round tempos (110, 120, 140), the BPMs of the prompt corpus
their conditioning was fitted on.

What that changes in this spec:
- **Free-time pieces are the common case, not an edge case.** Every beat feature (bar snapping, grid
  badge, tempo conform, seams in beats) applies only when beat confidence is ≥ 0.3 (set from the data,
  see the tempo bullet below; becomes a constant next to `tempo.MIN_CONF` when alignment is built); otherwise snapping is free, seams are in seconds, and tempo/grid badges show "no beat".
  **But beat pieces still get aligned in the MVP** (Xander: misaligned beats around a gap may confuse
  SA3's interpolation); see "Alignment" in 4.1.
- **Text-prompt levers don't reach most pieces.** Prompt pull and regenerate-to-fit (4.6) need a text
  prompt. For anarchy pieces the levers are conditioning (4.5), soft fix-up (4.4) and DSP.
- **Every composite has a conditioning question from day one:** pieces come from different anarchy
  vectors, so even v1 must choose what the gaps are conditioned on (4.5 gives the v1 default).
- **Tags can't name clusters** (3.2) until there are tags; CLAP text probes or nothing.
- **Tempo readings are trustworthy above conf 0.3** (checked 2026-11-06 on 476 renders). Across
  the library, 82–92% of renders with conf ≥ 0.3 land on an integer BPM (chance: 30%), which only
  happens when the reading is real; below 0.2 it's 34%. The old 110.0 / 120.2 pile-up was real round
  tempos snapped ~0.2 BPM by a kink in the autocorrelation readout (fixed: `tempo.py` docstring,
  step 3; `TEMPO_V` 2 re-measures every render). Octave errors (60 read as 120, 180 as 90) remain:
  the project tempo is a consensus over every clip (octave-folded), so one misread clip no longer
  sets the grid.

## 2. One tool: shared UI kit, app shell, render ids

**Principle: every page is built from one component library.** A fix or upgrade in one place upgrades
every page. Icons, menus, cards and the timeline look and behave the same everywhere.

### 2.1 `sa3lab/ui/`: the kit

Vanilla ES modules + CSS, no build step (the repo's style). Pages import what they need.

| Module | What | Extracted from / first used by |
|---|---|---|
| `tokens.css` **(built)** | colors, type, spacing, radii, shadows: the studio theme as CSS variables, plus base buttons / inputs / chips / shell | `studio.css` / `studio.py` theme |
| `icons.svg` + `icon()` | one SVG sprite, one name per icon | inline SVGs in studio.js, extend.js, uiprefs.js |
| `dadaspec.js` **(built)** | a spectrogram view: image, mono fold (the uiprefs rule), overlays (highlights, spans, playhead), hover time, the drag-to-highlight gesture | hispec images everywhere |
| `timeline.js` **(built)** | ruler, playhead, regions (draw / move / resize / duplicate / delete / snap / undo), region types with their own color and menu | the composer; `segments.js` migrates when next touched |
| `player.js` **(built)** | one-at-a-time playback registry, loop a range | studio's `_sa3All` registry |
| `clipcard.js` | a render card: dadaspec + highlight bands + label strip (score, tags, star, extend, settings) | studio history rows |
| `taginput.js`, `modal.js`, `menu.js`, `toast.js`, `chip.js` | basics | studio modals / popovers |
| `browser.js` | the library browser (grid / map, filters, similarity sort) | Library page, then the Library modal and the composer side panel |
| `shortcuts.js` | one keyboard registry with a help overlay | ⌘↵, ⌘Z, space, ⌫ across pages |
| `api.js` **(built)** | fetch wrappers for `/sa3/*` routes | everything |

**App shell (`shell.js`, built):** one top bar on every page: logo, page tabs (Studio · Library · Composer ·
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
- **Decided: one origin** (Xander: "this is one large tool we're building").

**Migration rule:** new UI is built in the kit first; existing studio pieces move into it when they are
next touched. studio.js is Xander's live workspace and other agents edit it in parallel, so no
big-bang extraction: the kit is born with the first new feature that needs each module (5).

### 2.2 Render ids across folders (built: `library.render_id` / `resolve_id`, `?id=` on the routes)

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

Built (`highlights.py`, `ui/highlight.js`, `highlights_link.js` on the studio's history rows).

**The library = starred renders + their highlights.** A highlight is a time range on a render, with
tags and a note; it's optional; a render can hold several *(guess)*. No audio is copied: cuts happen
on demand and are cached.

- **Gesture, everywhere a dadaspec appears** (history cards, Library modal, browser, composer takes):
  - Click seeks.
  - Click-drag (≥ 4 px) selects a range and opens the **highlight modal**.
  - The same rule as the inpaint editor, implemented once in `dadaspec.js`.
- **Highlight modal:**
  - A zoomed dadaspec of the range, looped playback, in/out fine-tune. Snaps to beats or bars only when
    the render has a confident beat (1.1); ⇧ toggles. Shows the range's BPM / key only when confident.
  - Tags (autocomplete from existing tags), note, score.
  - **Save:** stars the render if it isn't starred yet, and adds the highlight.
  - Also "Open in composer". ("More like this" was removed from the UI on 2026-11-07, Xander: too soon,
    cluttered the cards; `/sa3/embed/similar` + `embed.py` still exist, unused by the library UI.)
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

Built with E2 + E3 (`embed.py`, `ui/browser.js`); E1 / E4 need a download (ask first).

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

**Alignment (MVP; Xander: basic alignment for clear-BPM pieces, none for beatless ones).** Decided
as tempo + beat phase *(guess)*:
- The project BPM is a live estimate: the octave-folded consensus of every clip reading with
  conf ≥ 0.3, weighted by confidence × length (`compose.tempo_consensus`). Xander: never frozen or
  set by hand, never written into the conditioning (SA3 reads the tempo from the audio); the BPM
  popover lists each clip's deviation from it, furthest first.
- Other confident-beat pieces are rubberband-stretched to the project BPM and placed so their beats
  continue the grid. Gap and seam lengths snap to whole beats, so SA3 sees one continuous grid on both
  sides of every gap.
- Bars: a manual nudge (± 1 beat) only, no auto-downbeat, until drifting bars prove audible.
- Beatless pieces are placed freely and never stretched; their seams are in seconds.
- Gaps get their tempo only from the bracketing pieces (one full-attention pass). The BPM splice (4.6,
  0h) is the fallback if gaps measurably drift off the grid.

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
| **Rewrite** | one strength curve drawn on the Rewrite lane (project.json `rewrite`, curve.js rules): where it is above 0 it overrides the automatic value (0 = keep … 1 = regenerate, between = soft, 4.4). Replaced the separate soft / gen regions (old projects convert on open) |

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
`{"strength": [[t, u], …], "cond": {anchors, curves}}` (gaps = plateaus of 1, soft regions = plateaus
below 1; the strength part ships, see 4.4). The take's sidecar gets
`gen.composite` (project, pieces with source render id + sha256 + range + fixes, regions).

**Takes:**
- Each is a normal studio render: a take lane under the tracks (same time axis, ▶ in its gutter plays from the shared playhead; click it for score / star / Promote / highlights in the inspector).
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
- Conform limits: warn past ±8% tempo and ±3 semitones (rubberband stays clean below that). **No hard
  limits** (Xander: "let's explore").

### 4.4 Soft fix-up: per-frame strength on SA3's own schedule

**Shipped in the studio (2026-11-06):** the inpaint input is now one denoising-strength curve
(keyframes `[t, u]`; 0 keep, 1 regenerate, between = soft), sampled by differential diffusion with
mask variant B on a log-SNR-linear strength scale (u = k/5 frees k of 8 steps). How it works, the
measured ladder and the traps: `sa3lab/segments.py` docstring. A composite's soft regions are just
keys in that curve: the composer writes `{"strength": [[t, u], …]}` into the box.

Open for the composer:
- **Limit:** 1–3 free steps (u ≤ 0.6) can't re-time or re-key a piece, because the arrangement is set
  in the early steps. DSP does the alignment.
- `ui/strength.js` is the curve editor on `timeline.js` (opt-in in the studio, section 5).
- Blind listening on the soft levels is still owed (0a).

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

- **Built: M1 + M3 in `sa3lab/condmix.py`** (contract, hook points and costs in its docstring).
  Default M1: in the acceptance test m1, m3, a hard cut and no interpolation gave near-identical gaps
  within a seed (section 5), and m1 costs +15-25 % vs m3's 2×.
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
  Run it only if MVP gaps drift off the beat grid.
- **Regenerate to fit (text pieces):**
  - Re-render a highlight's source from its own recipe with the project BPM/key in the prompt, same
    seed (the `noise.py` canvas keeps the seed's opening).
  - Then re-cut the range, scaled by the tempo ratio.
  - Works only if the arrangement survives the prompt change (test 0e).
- **Latent time-stretch:** resample SAME latents along time by the tempo ratio, decode, then fix-up.
  A cheap alternative to rubberband for tempo only (test 0e).

## 5. Phases

Renders: 6 s for plumbing checks, 20-60 s for listening, Medium + SAME-L, results in the research
doc's Log, blind listening before metrics. Agent renders go to `sa3lab/composer/teststudio.py`
(another port), never :7860.

### Shipped (2026-11-06; how-to + why in the docstrings named)

- **Phase 0, harness:** `composer/compose.py` (project → reference WAV + segments box
  `{"strength", "cond", "composite"}` → takes with `gen.composite`), `condmix.py` (M1 / M3),
  render ids (2.2), `teststudio.py`, `acceptance.py` (phase-0 blind page in `outputs/composer-tests/listen/`).
- **Phase 1, editor:** `/sa3/page/composer` (`composer/server.py`, `page/composer.js`), promote / takes / undo.
- **Phase 2, editor grows:** explicit pieces on **tracks** (overlaps sum; mute / solo leave a piece out;
  per-piece `align`; phase-0/1 projects convert on open), per-piece **mix strip** + **master**
  (`mixdsp.py`: WebAudio-spec biquads run by ffmpeg, so the page's live monitor `ui/mixer.js`
  matches the server mix; the limiter is approximate in the browser), piece-audio cache, Edits lane
  (gen / soft regions; soft over an overlap = glue), imports, splice export, ±1 beat nudge, drag from
  the side panel, kit modules (`modal`, `menu`, `taginput`, `shortcuts`, `icons`, `clipcard`).
  **Highlights** (3.1): `highlights.py` (sidecar `label.highlights`, analysis worker), `keyfind.py`
  (key / phase / downbeat / LUFS / bands / width), `ui/highlight.js` modal, bands on every dadaspec.
- **Phase 3, fit:** `composer/fit.py` (badges per piece / seam / overlap incl. masking + headroom,
  recommended fix chain: transpose, gain, 3-band EQ, width), Fit panel + Fit all + loudness-matched
  A/B, swap suggestions from the similarity index.
- **Phase 4, library + similarity:** `/sa3/page/library` + `ui/browser.js` (grid, filters, sorts,
  PCA map + k-means, hover audition, lasso; also the composer's side panel), `embed.py` (E2 SAME-L
  window fingerprints encoded in the studio process when the queue is idle, E3 source conditioning;
  "more like this" returns moments).
- **Phase 5, conditioning everywhere:** per-piece anchors in `compose.cond_curves` (loudness ×
  `pull` over overlaps, crossfades across gaps, a take's own recorded curves; merged + simplified),
  per-piece anchor overrides and the **Conditioning lane** with explicit keypoints (`cond.gap =
  "keys"`, `compose.key_curves`: crossfade between keypoints, a cut is opt-in; Xander's addition),
  the arranger's per-section prompts (`docs/TODO/sa3-arranger.md` item 2; a conditioning-only box
  now reaches a2a jobs, `segments.py`), windows past 380 s (`compose.submit_windows`).
- **Tab host + Rewrite lane (2026-11-07):** `/sa3/app` (`ui/app.js`) keeps every page alive in an
  iframe under one fixed tab bar; the Rewrite lane is a strength curve with DAW-automation gestures
  (`ui/curvelane.js`, now also under the studio's `strength.js`); live consensus BPM; clips draw at
  their dropped position at once (`at_req` in the resolved layout); the Composer timeline zooms out
  freely (`Timeline({free: true})`) and never re-fits by itself; tooltips; collapsible settings panel.
- **Takes are self-describing (2026-11-07):** `condsrc.py` answers "what was this render
  conditioned on" for every caller (pieces, overrides, keypoints, global anchor, E3, studio
  restore / Extend); a multi-anchor render keeps its file-backed anchors' tensors in
  `<audio>.conds.npz` (written on save; older takes backfilled on first read), anchors resolve
  from the trash, a render used as ONE anchor = its dominant anchor, beat-cut pieces map curves
  from `src_cut`.
- **Studio migration:** `highlights_link.js` (drag a range on any history row's dadaspec → highlight;
  bands; dragging a clip into an input now starts from its transport bar), `strength_tl.js` +
  `ui/strength.js` (the Inpaint strength editor on `timeline.js`, opt-in via its "Timeline editor
  (beta)" toggle; `segments.js` keeps Length sync and the a2a viewer; curve rules shared in `ui/curve.js`).
- **Experiments:** `composer/experiments.py` (0a, 0b, 0c, 0d, 0e, 0h) and `bench/embed_triplets.py`
  (0g), everything in `outputs/composer-exp/<exp>/`; first metric results below.

### Open (mostly for Xander's ears)

- **Blind listens owed:** phase 0 (`outputs/composer-tests/listen/`), 0a soft levels inside a
  composite, 0b conform / glue, 0d one 300 s pass vs chained windows, 0e tempo-change methods, 0g
  triplets (sets the browser's default "similar" mode: sound vs prompt).
- **Metric results to log once heard:** see the research doc's Log (0c key-in-prompt adherence, 0h
  BPM splice).
- **Defaults waiting on those listens:** Auto vs One-anchor conditioning, whether soft fix-up joins
  "Fit all", the splice export as default for imports, the strength editor (timeline vs classic):
  once Xander prefers the timeline one, segments.js's canvas code can be deleted.
- **Known issues (e2e pass):** fit's grid check can report a beat piece ~half a beat off when it is
  snapped fine (`fit.report` re-measures phase on the cut piece, the snap measured it on a padded
  decode; busy breakbeats flip between on- and off-beat): unify both on one measurement. Piece
  ranges too short for tempo.py read "tempo not measurable" and are never stretch-checked.
- **Take conditioning, left open:** a take used as one anchor gives its dominant anchor; a
  weight-proportional bag mixture (like anarchy's breed) might keep more of a crossfading take, untested.
  A take trashed under a renamed stem (`x.1.mp3` on a name collision in `deleted/`) is not found
  by the trash fallback. Older takes with `anarchy:` spec prompt anchors keep them as text (the
  backfill can't sample without MLX); a re-render writes the full bank.
- **Not built (by decision or later):** E1 CLAP / E4 dadaspec-image embeddings (need a download:
  ask first), mix automation and effects beyond the static strip, cluster names from tags.

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

## 7. Decisions (2026-11-06)

All open questions are resolved; *(guess)* marks an agent default Xander didn't answer explicitly.
- **MVP:** the phase-0 acceptance test, then the lean one-track editor (section 5).
- **One origin:** the Library and Composer pages are served by the studio server (2.1).
- **Beat alignment:** tempo + beat phase for confident-beat pieces, none for beatless ones (4.1).
- **Conform:** no hard limits, warnings only (4.3).
- **Similarity and CLAP:** v2, no download now (3.2).
- **Highlights:** several per render *(guess)*; bar snapping only with a confident beat, ⇧ toggles
  *(guess)*; post-MVP.
- **Arranger synth lanes:** none in the composer; an arranger render is just a piece or a bed.
- Earlier: harness + experiments first; imported audio allowed as pieces (4.1); browser searches the
  starred set only (3.2); the v1 mix strip is static and simple (4.1).
