# Astra's spectrograms: listening to GPT-6 Astra's favourite music

GPT-6 Astra (OpenAI, via the Codex CLI) has no audio output but can generate images. We asked it,
4 times in parallel, to compose an original piece it loves and paint its spectrogram in a strict,
decodable format; this folder turns those images back into audio and builds a listening page.

- Renders, Astra's images and the page: `outputs/astra-spectrograms/` (gitignored), page = `index.html`.
- Context for the next agent: `outputs/astra-spectrograms/context.md` (settings, findings, next steps).

## Pipeline

| Step | File | What it does |
|---|---|---|
| 0 | (Codex) | `codex exec -m gpt-6-astra -c model_reasoning_effort=high -s workspace-write --skip-git-repo-check -C <empty dir> -o final.md "<prompt>"`, ×4 in parallel. Prompt: `outputs/astra-spectrograms/_codex/prompt_to_astra.md`. |
| 1 | `decode.py` | Crop the plot area (frame-spine detection on dark or light pages, colour-bounding-box fallback, edge trim past anti-aliasing), then colour → dB by nearest neighbour on the magma ramp or on the image's own colourbar, whichever fits the pixels better. |
| 2 | `specgrid.py` | The geometry: image grid (HTK mel 0-12 kHz rows, 0-30 s columns) → linear STFT magnitude for any sr/n_fft/hop (power-averaged mel rows per bin); audio → same grid for re-rendered spectrograms and a match score. |
| 3 | `invert.py` | Four inversions: fast Griffin-Lim; sinusoids + noise (partial tracking, continuous-phase oscillators, random-phase residual); BigVGAN-v2 44 kHz; Vocos 24 kHz. |
| 4 | `cleanup.py` | Stable Audio 3 medium audio-to-audio (`sa3lab/sa3 --init-audio … --init-noise-level σ`), Astra's own description as prompt, seed 7, 8 steps. |
| 5 | `run_all.py` | Steps 1-4 for every song folder: since the website, Griffin-Lim only + SA3 ladder σ 0.1/0.2/0.35/0.6 over it (`EXTRA_INVERSIONS` lists the first-round methods). |
| 6 | `make_page.py` + `page_template.html` + `dada_tiles.py` | Loudness-matched mp3s, Astra-style re-rendered spectrograms, the studio's dadaspec tiles, `all_versions.mp3` (every version back to back) and `index.html` with a DJ deck ported from `sa3lab/djview.js`. |

| 7 | `build_site.py` + `site_template.html` | The public website (below). |

`montage.py` stacks spectrogram PNGs for an agent to look at; `rescore.py` re-measures a work dir.

```bash
cd experiments/astra_spectrograms
uv run python run_all.py          # all songs in outputs/astra-spectrograms/s*/
uv run python make_page.py        # -> outputs/astra-spectrograms/index.html
```

Models (downloaded into `models/hf-cache/`): `nvidia/bigvgan_v2_44khz_128band_512x` generator only
(489 MB, loaded with `weights_only=True`; NVIDIA's model code from the same repo is imported with
`meldataset` stubbed so librosa is not needed) and `charactr/vocos-mel-24khz` (54 MB). torch comes
from the uv cache (2.14.1).

## Findings (2026-11-08)

- **Astra's format compliance is excellent.** All four images: 2172×724, black page, a clean plot
  frame, ticks at the requested mel positions, a magma colourbar, nothing inside the plot. Its
  image tool exposes no size setting (it asked for 3840×1280 and got 2172×724), so the plot is
  ~1850-1900 px for 30 s = **~16 ms per column**, ~500 mel rows for 0-12 kHz.
- **Astra's taste converges.** Four independent runs produced four variations on one idea: a low
  hum, sparse glassy plucks and clicks, gliding partials that converge into one harmonic "agreement"
  around the middle, then dissolve. Two titles are near-identical ("A Common Divisor", "The
  Almost-Common Divisor"). The pieces are spectrally sparse and synthetic: stacked harmonic lines,
  thin broadband clicks, a few faint noise clouds.
- **Inversion: phase retrieval and additive synthesis beat neural vocoders on this material.**
  Match = correlation of the re-rendered spectrogram with the decoded source, on Astra's grid
  (1 = identical; it measures faithfulness to the picture, not how good it sounds):

  | version | s1 | s2 | s3 | s4 |
  |---|---|---|---|---|
  | sinusoids + noise | 0.69 | 0.53 | 0.67 | 0.61 |
  | Griffin-Lim | 0.60 | 0.51 | 0.56 | 0.64 |
  | Vocos | 0.48 | 0.24 | 0.48 | 0.43 |
  | BigVGAN-v2 | 0.27 | 0.26 | 0.28 | 0.57 |
  | SA3 σ 0.1 (from sinusoids) | 0.52 | 0.42 | 0.37 | 0.33 |
  | SA3 σ 0.1 (from Griffin-Lim) | 0.46 | 0.38 | 0.13 | 0.33 |
  | SA3 σ 0.2 | 0.46 | 0.38 | 0.22 | 0.31 |
  | SA3 σ 0.35 | 0.18 | 0.20 | 0.02 | 0.12 |
  | SA3 σ 0.6 | −0.30 | −0.42 | −0.30 | −0.25 |

  The vocoders were trained on recordings; Astra's images are perfectly clean lines over digital
  silence. BigVGAN smears the bass and adds a noise floor on the sparse pieces (with a silence gate
  it was worse, 0.16-0.24; raising the input level 5-15x did not help) but does well on song 4,
  the densest, most "recording-like" image.
- **SA3 is strong even at tiny σ.** σ 0.05-0.15 keeps the layout but already turns sustained tones
  into struck notes with decays and drops one of two crossing glides; σ 0.2 rewrites noticeably;
  σ 0.35+ keeps Astra's rhythm and register map with SA3's own instruments; 0.7 is a different piece.
  Three chained passes at 0.1 behaved like one pass at ~0.2. The low end of the useful range is
  0.05-0.15, not the 0.4-0.8 the CLI help suggests for variations.
- Settings that held: gate −76 dB (the page background decodes to about −80 dB; −70 lost Astra's
  faint noise clouds), 0 dB = a sinusoid of amplitude 0.5 for the vocoders' mel levels.

## Page

`outputs/astra-spectrograms/index.html` (open from disk, no server): a DJ deck on top playing
`all_versions.mp3` (all 36 versions back to back, 30 s each, loudness-matched at −18 LUFS; static
port of `sa3lab/djview.js` with segment labels, prev/next version, `#t=<seconds>` deep link), then
one tab per song: Astra's image, its title/description/timeline/"why", its own image prompt, and a
card per version (player, Astra-style re-rendered spectrogram, match score, "▶ deck").
`make_page.py --html-only` rebuilds the HTML from `page_data.json` after template edits.

## Website: https://astra-favourite-songs.vercel.app

Public, static, its own Vercel project `astra-favourite-songs` (account `aixander`). Built into
`outputs/astra-spectrograms/site/` (gitignored, linked to the project via `site/.vercel/`):

```bash
uv run python build_site.py               # media + files + index.html  (--html-only: template edits)
cd ../../outputs/astra-spectrograms/site && vercel deploy --prod --yes
```

- **One chain per song:** Griffin-Lim (`raw_gl`) → SA3 a2a over it at σ 0.1 / 0.2 / 0.35 / 0.6
  (`cleanup.py --src raw_gl`, seed 7, 8 steps, CFG 1, Astra's title + "What it sounds like" as prompt).
  Xander picked Griffin-Lim as the base (2026-11-08); Vocos was tried first and dropped.
- **Player per song:** one discrete σ slider (keeps the playhead when switching; keys 1-5 / ↑↓),
  the studio's mono dadaspec (`dada_tiles.py`, 160 columns/s), match score, a one-line note
  (`VERSIONS` / per-song `SONG_NOTES` in `build_site.py`, read off the re-measured spectrograms),
  decoded picture vs this version on Astra's axes, and a full-screen DJ view (port of
  `sa3lab/djview.js`, version buttons + keys 1-5 inside it, Esc closes).
- **Methods section** on the page is the reproducibility spec (prompt verbatim, format rationale,
  crop/decode, Griffin-Lim, SA3 command, match metric). `files/` serves the prompt, the pipeline
  scripts, and per song Astra's PNG, reply (local save path stripped), image prompt, SA3 prompt, crop
  info and match scores, plus one zip. **Codex event logs are never published**: they contain the
  local `~/.claude/machine.md` and skill files Astra read.
- Audio: −18 LUFS two-pass loudnorm, 192 kbps mp3. Whole site ~25 MB.

| match (corr) | s1 | s2 | s3 | s4 |
|---|---|---|---|---|
| Griffin-Lim | 0.60 | 0.51 | 0.56 | 0.64 |
| SA3 σ 0.1 | 0.46 | 0.38 | 0.13 | 0.33 |
| SA3 σ 0.2 | 0.37 | 0.32 | 0.04 | 0.25 |
| SA3 σ 0.35 | −0.05 | 0.01 | −0.32 | 0.08 |
| SA3 σ 0.6 | −0.49 | −0.42 | −0.57 | −0.25 |

- **Song 3 collapses under SA3**: from Griffin-Lim, σ 0.1 already drops its gliding middle to near
  silence and σ 0.2-0.35 is almost empty (one impact). Songs 1, 2, 4 keep the layout through σ 0.2;
  song 2 even through 0.35.
- **Why small σ already rewrites so much:** SA3's pingpong sampler re-noises after every step and
  `build_pingpong_schedule(8, σ, use_logsnr_shift=True)` re-anchors only the first value to σ; the
  log-SNR-shifted second value is higher: σ 0.1 → 0.217, 0.2 → 0.362, 0.35 → 0.625, 0.6 → 0.909
  (`stable-audio-3/optimized/mlx/models/defs/sa3_pipeline.py`). Effective strength ≈ 1.5-2× nominal.
- Vocos → SA3 (tried first, deleted): σ 0.1 kept timing and plucks but lost the bass hum and some
  glides; match 0.22 / −0.05 on s1 / s2 vs 0.46 / 0.38 from Griffin-Lim.

## Next to try

- Ask Astra for **several shorter clips (10 s) or a wider canvas** per piece: at 2172 px the time
  resolution (16 ms/column) is fine but frequency resolution in the treble is ~60 Hz/px.
- **Push for diversity**: the four runs converged on one aesthetic. Give each run a different seed
  idea (a tuning system, a tempo, "no harmonic stacks"), or ask for 4 contrasting pieces in one run.
- **SA3 inpainting instead of whole-clip a2a**: keep the tonal lines (σ 0) and regenerate only the
  clicks / noise clouds, using the Inpaint strength curve (`sa3lab/segments.py`).
- **Different σ per band or per time** (differential diffusion curve) so SA3 adds air above 12 kHz
  without re-voicing Astra's sustained tones.
- **A mel-domain prior instead of a waveform one**: let SA3's SAME-L latent be optimised so its
  decoded mel matches Astra's image (spectrogram-guided diffusion), rather than SDEdit from a rough wav.
