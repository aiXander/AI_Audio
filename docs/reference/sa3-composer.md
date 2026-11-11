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
| `sa3lab/composer/server.py` | every `/sa3/composer/*` route (list in its docstring), takes listing, promote, keep / trash a take, imports, project-shape conversion, plain mixdown export (`export_mix`: ref.wav, whole or the ruler selection, to a 320 kbps mp3 in `exports/`, no AI) |
| `sa3lab/composer/vault.py` | the ⌘S deep copy: every outside file the saved project reads, APFS-cloned into `.vault/` on a background thread; read side `condsrc.vaulted` |
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
`takes/deleted/`), `jobs/<stamp>/` (each batch's frozen project), `cache/`, `imports/`, `exports/`, `saved.json` (last ⌘S),
`.vault/` (⌘S deep copy of every outside source, at its render id: vault.py).

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
  kept piece comes back as the codec's reconstruction next to new neighbours (measured: waveform
  correlation .74–.86 and −3 to −10 dB above 16 kHz per pass, so it **compounds** over promote rounds).
  Two fixes. (1) Every inpaint render (studio included) gets the reference's own audio pasted over
  its kept frames before it is saved (`segments.paste_kept`, a 150 ms raised-cosine crossfade inside
  each kept edge, `splice_weights`; marked `gen.segments.paste`). (2) The take file is still an MP3,
  so a take used as a piece (promoted base, lifted clip) reads `compose.take_source`: the reference
  it was rendered on, rebuilt recursively from its frozen job, spliced over its kept frames. A kept
  stretch is then always the audio that first produced it, plus one MP3 encode, never more. It
  falls back to the raw take when the rebuilt reference's sha256 differs from the take's record
  (a source changed, or a take rendered on an older base), and for window takes. Promote also
  zeroes the master gain, which the base already carries (the limiter stays).
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
- **The master limiter does not survive SA3.** SAME-L's decode re-peaks ~1–2 dB above its input, so
  the studio limits every render again at −1 dBFS before saving (`studio._peak_guard`; raw peak in
  `gen.peak_guard`). Before 2026-11-07 renders were hard-clipped at 0 dBFS instead. Render settings →
  File = WAV (`model.format`) saves takes as 16-bit WAV for mastering.
- **SA3 only sees the mix.** Any generated or soft frame over an overlap regenerates every layer in it;
  per-layer inpaint would need stem separation. The upside is the **glue trick**: a soft region over an
  overlap asks SA3 to re-perform the stacked layers as one.
- **Alignment.** The project BPM is a live consensus (`compose.tempo_consensus`) over every clip with
  confidence ≥ 0.3, weighted by confidence × length. It is never set by hand in the editor and **never
  written into the conditioning** (SA3 reads tempo from the audio). Confident pieces are octave-folded,
  rubberband-stretched to it, cut to start on their own beat and to whole beats, and snapped to the grid,
  so the grid runs unbroken from t = 0. Beatless and `align: false` pieces (a promoted take) are never
  stretched. Bars: manual ± 1-beat nudge only, no auto-downbeat.
- **Length** is a minimum (Extend, a promoted take's length): the timeline runs to the later of it and
  the last clip's end, on both sides (`compose.layout`, the page's `projLength`), so a clip placed past
  it is never cut out of the reference the page shows whole.
- **Strength.** Kept = every piece's span minus a seam at uncovered edges; uncovered time = 1; the
  Rewrite lane (one curve, 0 keep … 1 regenerate) overrides wherever it is above 0. **Two copies of
  this rule:** the page recomputes it on every paint (`composer.js` `autoStrength`, and `joins` for
  the crossfades: mirrors of `compose.strength_keys` / `transitions`) so the noise overlay, the auto
  band and the noise preview follow an edit at once; the build's `layout.keys` lagged a whole build
  (decode + mix + conditioning). Change one, change the other.
- **Takes** land as lanes under the tracks on the same time axis. **Promote** makes a take the base: one
  piece over the whole timeline, the old pieces kept as greyed history, the take's own recorded
  anchors as its conditioning. A take can be **kept** (starred, off the lanes, back on unstar) or
  **trashed** (the studio's trash: score 0, moved to `takes/deleted/`, kept as a negative row).
- **Kept takes: the timeline never reads a take directly.** A stretch dragged off a take lane onto a
  track (the page's `liftTake`), a take dropped from the library, and a promoted base all read the
  take's frozen copy in the project's `clips/` (`server.freeze`): audio + `.conds.npz` bank
  hardlinked (free while the take exists; the data survives trashing or `rm` of the take anywhere),
  sidecar copied without labels plus a `clip` block. The whole take is frozen, not the stretch, so a
  clip can still be trimmed outward; `base_take` remembers which lane was promoted. Rejected: guarding
  deletion (the Library and studio can trash a take the composer can't see) and copying only the
  stretch (no outward trims, extra disk). Lifted clips are `align: false`: a take already sits on the grid.
- **Dropping a take back in place.** A vertical drag carries the whole take; a carried take snaps to
  where it belongs (within 2 bars of that or of its render time); a selected stretch behaves the same.
  "Where it belongs" is `composer.js` `liftHome`: each take lists the clips it was rendered from and
  where they sat (`server.takes` → `pieces`, from `gen.composite.pieces`); the clip that covered most
  of the stretch is found among today's clips (same id, source still covering that moment) and the
  stretch follows it, so it lines up with that clip even after it moved in time or to another track;
  no match (a generated gap, a deleted clip) → the render time. Only when it is dropped **on the clips
  it came from** (a track holding one of those clips under the aligned span) does it **replace** that span on every
  track (`composer.js` `cutSpan`: trims, splits, the base included), because a take is the whole mix
  there: layering it would double every track under it. Dropped in time on any other track (an empty
  one, or the "+ Track" lane under the tracks = a new track) it is layered and the originals stay, so
  a take can sit next to its sources for A/B. Off its home (or ⇧) it is a plain layered clip.
- **Take lanes scroll, no limit.** Every take is a lane (`scroll: true`): the timeline kit stacks
  scroll lanes in a bottom region capped at ~42% of the canvas that scrolls vertically (wheel over it;
  `tl.revealLane` on ↑ ↓), and paints only the visible ones, so hundreds of takes cost nothing.
- **Lane heights are the viewer's, not the project's.** A lane's bottom edge in the label column
  drags its height (kit: `onResize` + `rgroup`; tracks resize as one group, takes as one, Rewrite and
  Conditioning alone), kept in the page's localStorage prefs (`prefs.lh`); double-click = default.
  Until the tracks get a height they flex to fill; once every non-scroll lane is fixed (tracks sized,
  Reference hidden) the take region fills whatever is left instead of sitting at its ~42% cap.
- **Track mix** (`track_mix[k]`, the track gutter's M + fader) is applied in `compose.strip` /
  `active_items`, not only in the monitor: a muted track = every clip on it muted (beats solo), its
  gain adds to each clip's strip gain, so the reference, the conditioning pull (mixed RMS) and Fit's
  masking all hear it. Promote starts a fresh `track_mix` (the take has it baked in).
  `track_mix[k].env` is the track's **volume automation**: curve.js keys, gain = sin(π/2 · u) (`compose.env_gain`
  / the page's `envGain`, keep them equal). Equal-power on purpose: two tracks whose curves mirror
  each other (u and 1 − u, linear or smooth) sum to constant power, so a crossfade drawn as an X keeps
  the level steady (the earlier u² law dipped ~9 dB at the crossing; u 0.5 is now −3 dB, not −12). It is multiplied onto each of the track's pieces after its strip
  (handles included, so on top of the crossfades), in the reference and per voice in the monitor
  (`mixer.js` `env`). Interpolation is per clip (`env_shape`: linear | "smooth" cosine ease), because
  the curve is applied clip by clip anyway; the line draws each clip's stretch in its own mode. It is the Rewrite lane's `CurveLane` sitting on the track lane through the
  timeline's `claim` hook: the curve gets only gestures on its points and lines, so clips stay
  draggable with no mode switch. Entry is right-click → Add volume keypoint (a click-to-add mode was
  rejected: it would steal clip drags).
- **Two saved states.** `project.json` is autosaved on every edit (working state, crash-safe);
  ⌘S writes `saved.json`, the one version Xander chose to keep (overwritten each ⌘S), which the header
  chip compares against and Revert restores (undoable). ⌘S is caught in the capture phase so text
  fields don't fall through to Chrome's "Save page as". ⌘S also queues `vault.py`: every file outside
  the project folder that the saved project names is cloned into `.vault/` (copy-on-write, so free on
  disk and instant; unchanged files skipped by size + mtime; pruned to the last ⌘S), never blocking
  the page or the project lock. Hardlinks were not used: a clone is a truly independent copy.
- **Split** (right-click a clip): two pieces with identical settings, no fade at the cut; strength_keys
  treats a same-source contiguous edge as no edge, so a split adds no seam.
- **Crossfades at back-to-back joins** (`compose.transitions`, the page's ⟷ handles): built from
  source audio *past* each clip's cut (handles), so the timeline geometry (`at`, `dur`) never moves.
  Centred on the join when both clips have audio to spare, else shifted onto the side that has it
  (`xf_split` / page `xfSplit`, one rule in both): a take dropped in place onto its source clips has
  nothing past its own edges, so the default fade lies inside the take with the source clip running on
  under it, applied with no click; `pre` / `post` are then unequal (one may be 0); the clip's cached audio carries `pre` / `post` seconds outside it and every consumer
  that places audio must offset by `pre` (`compose.mix`, the monitor's `syncMixer`). Fit measures with
  `layout(xfades=False)`. A crossfaded join drops its seam (the user chose how it sounds); a hard cut
  (`len 0`) keeps it, so "let SA3 blend it" = pick Hard cut. A split is never a transition (it would
  only add an equal-power +3 dB bump over identical audio).
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
After the last keypoint (or the last clip, in auto) its anchor holds for any length: `key_curves`
fills to the layout's end and `frame_strength` holds flat past the last keyframe.
The lane is edited only through explicit gestures: a click seeks (never adds a keypoint; the timeline
ignores non-primary buttons, so a right-click is only ever the menu). Right-click edits the prompt or
swaps the anchor (a library render via the Browser in a modal, or a typed prompt) on whatever *owns*
that moment: in Keypoints mode the **block** under the cursor (a run of consecutive keypoints with one
anchor, all rewritten together so a block never splits into two colours), else each clip there as its
`anchor` override, else the project's one anchor; it also adds keypoints and sets fade presets. A
fade between two blocks has a ⟷ handle: dragging it moves both keypoints around the fade's centre
(`withFade`), the only control of *how gradual* a change is (the curve stays cosine). Keypoint curves
are resolved in the page with `key_curves`' rule so drags redraw live; source names are not drawn on the
lane (they overlapped), only in the 0.7 s hover tooltip (`tipAt`, ui/timeline.js) and the menu. Auto mode never turns itself into keypoints
behind your back; only an explicit act does: "Edit as keypoints", **inserting** a source (drag across
the lane, or right-click a gap between clips → `insertCond`), or dragging one of the lane's handles. In
Auto / One-anchor mode the lane still shows ◆ ⟷ and ⠿ handles, on the build's curves as keypoints
(`editKeys` = `autoKeys`, once per build), so a clip override (right-click → Replace) can be trimmed or
moved like any block; the drop writes those keypoints and switches to Keypoints mode in one undo step.
A block's ⠿ grip (kit marker `shape: "grip"`) moves it with its fades (`movedBlock`: the keypoint just
outside each end moves too, clamped by the next one out; start snaps to clip edges / beats). An insert holds the new anchor at weight 1
over the span and puts the old one back a fade (one bar, ≤ ¼ span) outside each edge, dropping keypoints
inside that window, so nothing outside it changes. The pickers resolve after an `await`
and write by index (`keys[i]`, `pieces[pi]`): safe only because the modal's backdrop blocks every
timeline edit while it is open (`composer.js` `condMenu`).

| Mechanism | Verdict |
|---|---|
| **M1** cross-attention output blend per block (`condmix.py`) | **default**: exact at weight 1, a convex mix of in-distribution outputs, +15–25 % cost |
| M3 velocity blend over N full passes | built as the in-distribution reference; N× cost |
| M2 joint attention with log-weight bias | rejected: a high-logit anchor dominates whatever the weights say, and Medium's differential cross-attention doesn't give a convex mix |
| M4 token-wise lerp of the sequences | rejected: bags are unordered, position-wise lerp is arbitrary |

**Mute conditioning** (lane right-click "Mute conditioning", the inspector's Mute box, or "No conditioning"
when inserting over a span / gap): `"mute": true` on an anchor makes that stretch SA3's *unconditional*
input, all 257 cross-attention tokens zero, duration token included. That is exactly what training's 10 %
CFG dropout showed the DiT (`dit.py` `cfg_dropout_prob`; the MLX CLI's uncond branch is the same
`zeros_like`). An empty prompt is *not* that: T5Gemma("") + the learned padding vector is a real, rarely
seen conditioning. The flag sits on the anchor and keeps the source it mutes, so Unmute is exact;
`{"mute": true}` alone mutes a clip's / the project's own conditioning (a muted take keeps all its anchors
and curves, each flagged). condmix shares one zero slot for every muted anchor in a job; condsrc never
banks or dedupes a muted anchor with its unmuted self. Traps: under CFG ≠ 1 a muted stretch has
cond = uncond, so CFG does nothing there (checked: cfg 1 vs 3, max latent Δ 0.006; Render warns when a
CFG ≠ 1 render or section is muted throughout, `composer.js` `fullyMuted`); self-attention still
carries the neighbours' style into it, which is the point (the audio context steers).

In the acceptance test, m1, m3, a hard cut and no interpolation gave near-identical gaps within a seed
(the noise seed dominates), which is why the cheaper M1 is the default.

**How hard a prompt steers is set by CFG, not by the curves.** In a full-strength inpaint span with the prompt
over the span and muted conditioning around it, the audio context wins at CFG 1: the span carries on the
neighbouring texture for several seconds and the prompt only tints it. CFG 2 steers noticeably; CFG 4 is
very strong (a solo-piano prompt into a glitchy track gave clean, separate piano notes, and the
carried-over texture died within ~3 s). **Default to CFG 1.5–2.0 for steering renders and experiments**;
4 when the prompt must override the context. The span's edges still bend toward the kept audio at any CFG
(a span ending before a quiet kept section fades out). The same holds for a sampled anarchy bag as the
anchor (CFG 2 steers it as clearly as a text prompt). The noise seed sets the span's block layout; the
anchor sets what fills the blocks, so compare conditionings at a fixed seed. Renders + A/B pages:
`outputs/composer-exp/prompt-steer/` and `latent-steer/` (shared `prompt-steer/run.py`).

### 4.6 Smaller levers (experiments, not features)

0e tests regenerate-to-fit (re-render a text piece's recipe at the project BPM, same seed) and latent
time-stretch (`bench/latent_stretch.py`) against rubberband; 0h tests splicing T5 tokens of
`BPM: 120` into an anarchy bag. Prompt pull (writing `BPM:` into anchors) was dropped with the
"never write the BPM into the conditioning" decision.

## Invariants and traps

- **Don't fork the render path.** Pages submit jobs to the studio; nothing else samples. Two MLX
  processes on the M2 means two models in RAM. Embedding work that needs SAME-L runs in the studio
  process while the queue is idle.
- **One queue: the studio's.** Each Render click is a batch with its own frozen snapshot and
  thread, and every take goes into gradio's queue at once, so batches stack and the shared Queue
  modal (`ui/queue.js`) reorders / deletes / skips them like Generate clicks. A take deleted there
  completes with no output: `compose._run_jobs` logs it "removed", not a failure. The page shows a
  batch's lanes before its save + build finishes (an optimistic placeholder, replaced by the server's).
- **Agent test renders go to `teststudio.py`** on another port, never :7860 (Xander's history).
- **The monitor never waits for the build.** Every edit autosaves + rebuilds the whole reference
  (~2 s warm on a 7-min project: limiter, LUFS, the ref.wav write), so the page plays each active
  clip's audio from the newest build that held it (`composer.js` `mixPieces`): mute / solo / delete
  are heard at once; only a clip no build has seen yet waits. Autosaves coalesce (`server.save`
  `coalesce`): a save still queued behind a running build is skipped when a newer one arrives.
  Big buffers through ffmpeg go via `mixdsp.pipe`, never `subprocess.run(input=…)`: on macOS its
  `communicate()` feeds stdin 512 bytes at a time (~3× slower on a full mix).
- **Bare filenames only resolve in the active folder** (`library._audio_path`); use render ids.
- **The reference must be exactly the project length**: the studio's Length follows the reference
  (`segments.locked_length`), and one input mode at a time (`segments.BOTH`); the composer uses the
  inpaint input only.
- **Browser preview and server mix must run the same filters** (RBJ biquads both sides, `mixdsp.py` vs
  `ui/mixer.js`), or what Xander mixes by ear isn't what SA3 receives. Null-tested 2026-11-07 (headless
  Chromium OfflineAudioContext vs `mixdsp` + `compose.env_gain`): strip, pan, envelope, master and a
  mid-play restart match to −84 dB (float rounding); only the limiter on overs differs (−32 dB residual
  at +15 dB drive, same ceiling). Never put a DynamicsCompressorNode in the monitor: the WebAudio spec
  gives it automatic makeup gain (+0.57 dB per dB of ceiling), so it monitors louder than the reference.
- **Beats, keys and downbeats are guesses**, absent for half the library: always nudgeable, grey when
  unconfident, never auto-placed on a guessed downbeat. Busy breakbeats flip between on- and off-beat
  when phase is measured on different audio, so measure once (the snap) and reuse it.
- **Upstream's a2a σ is not a strength scale**; use the strength curve.
- **LoRAs are per project.** A piece rendered with another LoRA loses that sound in its soft regions;
  the Fit panel flags it.
- **A trashed or deleted source resolves to its ⌘S copy** (`compose.piece_audio_path`,
  `condsrc._audio` → `condsrc.vaulted`, any project's `.vault/`), so a *starred render* used as a
  piece and later trashed breaks the build only if the project was never ⌘S-saved since the piece was
  added. `library.resolve_id` refuses `deleted/` and hidden folders, so studio routes (a piece's
  spectrogram by id) still miss a trashed source. Takes are safe regardless (kept takes, above).
  Conditioning also resolves from the trash (`condsrc.side`).
- **Different ports = different localStorage**, which is why everything lives on one origin.
- **Imported audio is copyrighted material**: imports, their cuts and caches stay under `outputs/`
  (gitignored), never shared.
