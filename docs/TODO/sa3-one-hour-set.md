# SA3 one-hour set: from single renders to a playable live set

**Status (2026-11-08): plan only, nothing built. Agent proposal; Xander hasn't approved the scope.**
The goal is Xander's (voice memo, 2026-11-08): about **one hour of original SA3 music he can DJ on
top of live**, in his own audiovisual performance with projection mapping by WZRD (his VJ agent,
`~/Documents/GitHub/Gigabrain/VJ/WZRD`). One track is finished so far. The rest of this repo is the
toolbox for making tracks; this plan owns only what turns tracks into a set: the set as an object,
finishing a track to DJ quality, export to the DJ rig, stems, and a sound that holds together for an
hour. The event concept itself (venue, audience participation) lives in Xander's private notes, not here.

## What already exists (use it, don't rebuild it)

| Need | Already there |
|---|---|
| Build one track | the Composer: pieces on tracks, SA3 inpaints gaps and seams, takes, Fit panel, chained windows past 380 s, plain mixdown to 320 kbps MP3, splice export ([../reference/sa3-composer.md](../reference/sa3-composer.md)) |
| Sketch a song structure | the Arranger ([sa3-arranger.md](sa3-arranger.md)) |
| More like a keeper; steer toward a sound | [sa3-sound-level-control.md](sa3-sound-level-control.md) (noise neighbourhoods, keeper LoRA = its handle 7) and [sa3-promptless-anarchy.md](sa3-promptless-anarchy.md) |
| A style LoRA | `djrum-uts-v1` checkpoints in `stable-audio-3/loras/` (step 2000–14000; Composer project `test_01` uses step 8000 at 0.65); the finetune recipe in `underfit/docs/TODO/sa3-lora-finetune.md` |
| Preview like a DJ | the DJ button on every clip (`sa3lab/djview.py`: CDJ-style scrolling view) |
| Measure a track | BPM `sa3lab/tempo.py` / `compose.tempo_consensus`, key + LUFS `sa3lab/keyfind.py`, highlights `sa3lab/highlights.py` |

## Gaps (open, proposed order)

1. **Finish a track to DJ quality.** Mixable intro and outro (16–32 bars sparse or beat-only, made with
   Extend or an inpaint span under its own conditioning), steady tempo through the whole track
   (unmeasured: run `tempo.py` per 30 s window over a finished composite and see whether SA3 drifts),
   and one loudness target for every track (Xander picks it; `mixdsp.py` already measures LUFS).
2. **The set as an object.** `outputs/sets/<name>/set.json`: ordered tracks (a Composer project or a
   finished render), each with measured BPM, key, LUFS and length. A CLI summary first: total length,
   key clashes between neighbours (Camelot distance), BPM jumps past ~6 %. A "Set" tab in the tab host
   only once the json earns it.
3. **Export to the DJ rig.** Per track a WAV/AIFF or 320 kbps MP3 with tags written by ffmpeg (title,
   BPM, key); cue points from highlights if the DJ software can import them (rekordbox reads them from
   its XML). Format depends on open question 1.
4. **Stems for live play.** SA3 renders one mix, so stems come from splitting the finished track
   (Demucs `htdemucs`, a small download; still ask first, the disk is nearly full). Uses: live
   remixing in stem-capable DJ software or Ableton, and a clean drum stem into
   `Realtime_PyAudio_FFT`, which feeds WZRD's audio reactivity (band energies + onsets over OSC).
5. **A sound identity across the hour.** One or a few LoRAs that make the hour coherent and his own.
   The keeper LoRA on his own high-scored renders is handle 7 of the sound-level plan (built there, not
   here); later a LoRA on the finished set tracks themselves. The star + score dataset is the input.
6. **Show cues for WZRD (idea, only if Xander wants it).** Export each track's section boundaries,
   drops and highlights as a cue list WZRD could use to time scene changes, beyond pure audio
   reactivity. A contract between two repos: agree on the format in both before building.

Out of scope: anything generated live during the set. SA3 takes seconds per clip; the real-time
instrument is a separate spec in Xander's notes.

## Open questions for Xander

1. What does "DJ on top of live" mean on stage: rekordbox / CDJs, Traktor, or Ableton Live? It sets the
   export format and whether stems matter.
2. Which render or project is the finished track, and is it the sound the hour should have? Not
   identifiable from `outputs/`. Candidates: Composer project `test_01` (~306 s takes, djrum LoRA) or
   the top-scored render (8/10, "sitar melody, drum computer beats, psychedelic break core …").
3. One style for the hour (ambient / downtempo?) or a journey across styles? It decides the LoRA
   strategy in gap 5.

## Traps

- **A LoRA trained on one artist's purchased album sounds like that artist.** Fine on the bench; for a
  public set built on it, prefer a LoRA trained on his own keepers (gap 5).
- BPM for export comes from measurement (`compose.tempo_consensus`), never from the prompt: a render's
  measured BPM can differ from its prompt's (sidecars record both), and the Composer never sends a BPM to SA3.
- Tracks past 380 s render as chained windows; listen at the window seams before calling one finished.
- Set manifests can go in git; audio, stems and exports never (everything in git is public).
