# SA3 Composer: what is still open

Everything in the spec is built (the Composer, Library, highlights, similarity, fit, per-frame
conditioning, tab host, experiment harnesses). How it works and why: [../reference/sa3-composer.md](../reference/sa3-composer.md).
What is left is almost all Xander's ears, then the defaults those listens set.

## 1. Blind listens owed (Xander)

Every page is rendered and packaged. Serve one with
`cd outputs/<folder>/listen && uv run --no-project serve.py`; the page's Reveal button un-blinds.

| Listen | Folder | Question |
|---|---|---|
| Acceptance | `outputs/composer-tests/listen/` | does A → generated gap → B work? Gap conditioning m1 (cross-attention blend) vs m3 (velocity blend) vs hard cut vs none; beatless and beat pairs |
| 0a | `outputs/composer-exp/0a/listen/` | soft fix-up strength over a piece inside a composite (u 0 / 0.2 / 0.4 / 0.6): where does it start to sound reworked vs smeared? |
| 0b | `outputs/composer-exp/0b/listen/` | conform (as is / Fit-all DSP / DSP + soft 0.4) and the layered glue trick (overlap kept / soft 0.3 / soft 0.5) |
| 0d | `outputs/composer-exp/0d/listen/` | 260 s generated between two pieces: one 300 s pass vs chained 180 s windows with 60 s context |
| 0e | `outputs/composer-exp/0e/listen/` | tempo +6 %: rubberband vs regenerate-at-new-BPM vs latent stretch vs latent stretch + soft 0.3 |
| 0g | `outputs/composer-exp/0g/` (triplets page) | "is B or C closer to A?": sets the Library's default similarity mode (sound = SAME-L fingerprint vs prompt = source conditioning) |

After each: log the verdict + settings in the research Claude Doc (URL in `AI_Audio/CLAUDE.md`).
Metric-only experiments to log alongside: 0c (does `Key:` in a text prompt set the key?) and 0h
(does splicing `BPM: 120` tokens into an anarchy bag pull its tempo?), both in their `report.json`.

## 2. Defaults waiting on those listens (agent work once heard)

- Gap conditioning default: Auto (per-piece anchors) vs One-anchor. ← acceptance listen
- Whether soft fix-up joins "Fit all". ← 0a / 0b
- Splice export (original audio pasted back over kept zones) as the default for imports. ← needs its own A/B on an import
- Library "similar" default mode. ← 0g
- Studio Inpaint strength editor: once Xander prefers the timeline editor ("Timeline editor (beta)"
  toggle, `strength_tl.js`), delete `segments.js`'s canvas editor code.
- If 0e says latent stretch or regenerate-to-fit beats rubberband, add it as a tempo fix option in `fit.py`.
- If 0h works, offer the BPM splice for anarchy anchors.

## 3. Known issues, deliberately left

- A take trashed when `deleted/` already holds a file of the same name is stored as `x.1.mp3`, and
  `condsrc.side`'s trash fallback looks only for `x.mp3`. Needs the same name rendered and trashed
  twice (names carry seed + hash), and a correct fix needs a stored link to tell the two apart. Not
  built (speculative; ask Xander first).
- Older takes whose anchors are `anarchy:` spec prompts keep them as text in their `.conds.npz`
  backfill (the backfill doesn't sample); re-rendering the take writes the full bank.
- A take used as one anchor gives its dominant anchor. A weight-proportional bag mixture (like
  anarchy's breed) might keep more of a crossfading take: untested idea.

## 4. Not built, by decision

- E1 CLAP and E4 dadaspec-image embeddings (downloads: ask first).
- Mix automation and effects beyond the static strip (compression, reverb): only if use asks for it.
- Cluster names from tags (too few tags yet).
