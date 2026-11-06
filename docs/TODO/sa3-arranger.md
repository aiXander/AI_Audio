# SA3 Arranger: open v2 items

**Status (2026-11-06): v1 built.** `sa3lab/arranger/` (map in `server.py`'s docstring): a mini-DAW
on :7861 where a template JSON (schema: `render.py` docstring) renders deterministically to a rough
WAV + control curves (`*.controls.npz`, per-frame chroma / band energy / onsets / lead pitch at
44100/4096 Hz), and "Send to SA3" loads it into the studio as an a2a job (single σ, or a σ ladder
at one fixed seed) via `sa3lab/arranger_link.py`. Renders made that way carry `gen.arranger` in
their sidecar. The studio-side add-on was verified by import/parse and a headless restore-settings
call only, not yet clicked live.

## Next (open, in rough order)

1. **Measure the σ sweet spot.** Run σ ladders (0.4…0.95) on preset #1 with 2–3 prompts; per σ,
   correlate input vs output chroma and band envelopes against the template's `controls.npz`, and
   stack them with `sa3lab/dadagrid.py`. Log the result in the research Claude Doc; set the
   arranger's default σ / ladder from it. This is also the a2a-fidelity test in
   [sa3-sound-level-control.md](sa3-sound-level-control.md).
2. **Per-section prompts as real conditioning.** Section `hint`s are metadata in v1 (joined into the
   one global prompt). v2: a custom sampler that blends the DiT's velocity under each section's
   prompt along the time axis (crossfade masks of a few frames at boundaries). Needs a wrap of
   `sample_flow_pingpong` (the studio already wraps it for stop/progress) and N text encodings per
   step, so cost grows with the number of distinct prompts.
3. **Multi-span inpaint anchors.** Keep chosen sections of a previous SA3 render exactly (a drop
   you liked) and regenerate the rest from the sketch. The multi-span mask builder exists now
   (`sa3lab/segments.py`, the studio's inpaint editor); the compositing workspace that builds on it is
   [sa3-composer.md](sa3-composer.md).
4. **Probe guidance from the control curves** (handle 8 there): steer x̂0 toward the template's
   chroma / onset curves between steps instead of relying on a2a's single σ knob.
5. **LLM "describe the track → template" helper.** Paid API: ask Xander before building. v1's AI
   assist is the seeded rule-based generators only.

## Known limits / ideas not yet worth building

- Sounds are deliberately crude (polyBLEP oscillators, noise drums, STFT filters). Improve only if a
  σ sweep shows a sound choice (e.g. pad vs saw chords) changes what SA3 keeps.
- Lead melodies loop an 8-bar hook per lane seed (`compose._melody_events`); no piano-roll editing.
  If hand-written melodies matter, add a `notes` generator with explicit events + a small roll editor.
- The studio restore resets the codec to the model's default (restore() can't read the current
  codec); fine while Xander uses SAME-L.
