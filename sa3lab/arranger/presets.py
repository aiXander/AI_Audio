"""Built-in song-structure templates (the arranger's Presets menu). Each is a function so
callers always get a fresh dict. Schema: render.py's docstring. Bars are 1-indexed in the UI
but 0-based here (`start` = bars from the song's start)."""
from __future__ import annotations


def melodic_build_drop() -> dict:
    """#1. 120 BPM (1 bar = 2 s), 60 bars = 120 s: 0–80 s melodic build with no percussion,
    80–94 s percussion build (half-time kick → four-on-the-floor, hats thickening, snare roll,
    riser), 94–96 s near-silent break, 96–120 s full drop."""
    lead_seed = 11              # the drop's lead reuses the build's motif (same seed, same chord phase)
    return {
        "schema": 1, "name": "Melodic build → drop", "bpm": 120, "key": "A", "scale": "minor",
        "swing": 0.0, "humanize": 0.15, "seed": 1,
        "prompt": ("Melodic progressive house, emotional lead melody over lush pads, long build-up, "
                   "big drop with driving four-on-the-floor kick and deep bass, 120 BPM, A minor"),
        "chords": {"degrees": [1, 6, 3, 7], "bars": 2, "sevenths": False},
        "sections": [
            {"name": "Intro", "bars": 8, "hint": "soft ambient pads, no drums", "mute": []},
            {"name": "Melody", "bars": 16, "hint": "strong emotional lead melody over pads, no drums", "mute": []},
            {"name": "Build", "bars": 16, "hint": "melodic build, rising arpeggios, still no percussion", "mute": []},
            {"name": "Perc build", "bars": 7, "hint": "percussion builds up, snare roll, riser", "mute": []},
            {"name": "Break", "bars": 1, "hint": "near-silent pause before the drop", "mute": ["all"]},
            {"name": "Drop", "bars": 12, "hint": "massive drop, full bass, all-out percussion", "mute": []},
        ],
        "lanes": {
            "kick": {"clips": [
                {"start": 40, "bars": 3, "gen": "half", "level": [0.35, 0.6]},
                {"start": 43, "bars": 4, "gen": "four_floor", "level": [0.6, 0.9]},
                {"start": 48, "bars": 12, "gen": "four_floor", "level": 1.0}]},
            "snare": {"clips": [
                {"start": 41, "bars": 2, "gen": "halftime", "level": 0.5},
                {"start": 43, "bars": 4, "gen": "roll", "level": [0.3, 1.0], "params": {"from": 4, "to": 32}},
                {"start": 48, "bars": 12, "gen": "backbeat", "level": 1.0}]},
            "hats": {"clips": [
                {"start": 40, "bars": 7, "gen": "eighths", "level": [0.35, 0.8], "density": [0.15, 1.0]},
                {"start": 48, "bars": 12, "gen": "sixteenths", "level": 0.85, "density": 0.8},
                {"start": 48, "bars": 12, "gen": "offbeat", "level": 0.7, "params": {"kind": "open"}}]},
            "bass": {"clips": [
                {"start": 24, "bars": 16, "gen": "sustain", "level": [0.35, 0.7], "cutoff": [0.2, 0.35]},
                {"start": 40, "bars": 7, "gen": "sustain", "level": 0.75, "cutoff": [0.35, 0.5]},
                {"start": 48, "bars": 12, "gen": "offbeat", "level": 1.0, "cutoff": 0.5}]},
            "chords": {"clips": [
                {"start": 0, "bars": 47, "gen": "pad", "level": [0.6, 0.75, 0.8], "cutoff": [0.3, 0.45, 0.55, 0.7]},
                {"start": 24, "bars": 23, "gen": "arp", "level": [0.25, 0.7], "cutoff": [0.3, 0.85],
                 "params": {"sound": "pluck", "rate": 16, "octaves": 2}},
                {"start": 48, "bars": 12, "gen": "pad", "level": 0.7, "cutoff": 0.75},
                {"start": 48, "bars": 12, "gen": "stabs", "level": 0.7, "params": {"sound": "saw"}}]},
            "lead": {"clips": [
                {"start": 8, "bars": 39, "gen": "melody", "seed": lead_seed, "level": [0.75, 0.9],
                 "density": [0.55, 0.8, 1.0]},
                {"start": 48, "bars": 12, "gen": "melody", "seed": lead_seed, "level": 1.0, "density": 1.0}]},
            "fx": {"clips": [
                {"start": 7, "bars": 1, "gen": "reverse"},
                {"start": 23, "bars": 1, "gen": "sweep", "level": 0.6},
                {"start": 40, "bars": 7, "gen": "riser"},
                {"start": 48, "bars": 2, "gen": "impact"}]},
        },
        "sa3": {"init_noise": 0.8, "ladder": [0.55, 0.7, 0.8, 0.9]},
    }


def house_classic() -> dict:
    """124 BPM club structure: intro groove → breakdown → drop → outro."""
    return {
        "schema": 1, "name": "House: groove → breakdown → drop", "bpm": 124, "key": "F", "scale": "minor",
        "swing": 0.12, "humanize": 0.2, "seed": 2,
        "prompt": "Deep house, warm chords, rolling bassline, crisp hats, 124 BPM, F minor",
        "chords": {"degrees": [1, 4, 7, 3], "bars": 1, "sevenths": True},
        "sections": [
            {"name": "Intro", "bars": 16, "hint": "kick and hats groove", "mute": []},
            {"name": "Groove", "bars": 16, "hint": "bassline and chords join", "mute": []},
            {"name": "Breakdown", "bars": 8, "hint": "drums out, chords and lead", "mute": ["drums", "bass"]},
            {"name": "Drop", "bars": 16, "hint": "full groove", "mute": []},
            {"name": "Outro", "bars": 6, "hint": "strip back to drums", "mute": ["music"]},
        ],
        "lanes": {
            "kick": {"clips": [{"start": 0, "bars": 62, "gen": "four_floor"}]},
            "snare": {"clips": [{"start": 8, "bars": 54, "gen": "backbeat", "level": 0.8}]},
            "hats": {"clips": [{"start": 0, "bars": 62, "gen": "offbeat", "params": {"kind": "open"}, "level": 0.7},
                               {"start": 16, "bars": 46, "gen": "sixteenths", "density": [0.3, 0.8], "level": 0.6}]},
            "bass": {"clips": [{"start": 16, "bars": 46, "gen": "eighths", "cutoff": 0.45}]},
            "chords": {"sound": "saw", "clips": [{"start": 16, "bars": 46, "gen": "stabs", "level": 0.8,
                                                  "cutoff": [0.4, 0.7]}]},
            "lead": {"clips": [{"start": 32, "bars": 24, "gen": "melody", "density": [0.5, 0.9], "level": 0.8}]},
            "fx": {"clips": [{"start": 36, "bars": 4, "gen": "riser"}, {"start": 40, "bars": 2, "gen": "impact"}]},
        },
        "sa3": {"init_noise": 0.8, "ladder": [0.55, 0.7, 0.8, 0.9]},
    }


def ambient_to_techno() -> dict:
    """128 BPM: long ambient pad bed that a techno groove slowly grows out of."""
    return {
        "schema": 1, "name": "Ambient → techno", "bpm": 128, "key": "D", "scale": "dorian",
        "swing": 0.0, "humanize": 0.1, "seed": 3,
        "prompt": "Hypnotic melodic techno, evolving pads, driving kick, 128 BPM, D dorian",
        "chords": {"degrees": [1, 4], "bars": 4, "sevenths": True},
        "sections": [
            {"name": "Ambient", "bars": 16, "hint": "drifting pads", "mute": []},
            {"name": "Pulse", "bars": 16, "hint": "arp pulse and soft kick", "mute": []},
            {"name": "Techno", "bars": 32, "hint": "full driving techno", "mute": []},
        ],
        "lanes": {
            "kick": {"sound": "boom", "clips": [{"start": 24, "bars": 8, "gen": "four_floor", "level": [0.3, 0.8]},
                                                {"start": 32, "bars": 32, "gen": "four_floor"}]},
            "snare": {"clips": [{"start": 40, "bars": 24, "gen": "backbeat", "level": 0.6}]},
            "hats": {"clips": [{"start": 16, "bars": 48, "gen": "sixteenths", "density": [0.1, 0.5, 1.0],
                                "level": [0.3, 0.8]}]},
            "bass": {"clips": [{"start": 32, "bars": 32, "gen": "pulse", "cutoff": [0.3, 0.55]}]},
            "chords": {"clips": [{"start": 0, "bars": 64, "gen": "pad", "cutoff": [0.1, 0.6, 0.4]},
                                 {"start": 16, "bars": 48, "gen": "arp", "params": {"sound": "pluck", "rate": 16},
                                  "level": [0.2, 0.8], "cutoff": [0.2, 0.9]}]},
            "lead": {"clips": []},
            "fx": {"clips": [{"start": 28, "bars": 4, "gen": "riser"}, {"start": 32, "bars": 2, "gen": "impact"}]},
        },
        "sa3": {"init_noise": 0.8, "ladder": [0.55, 0.7, 0.8, 0.9]},
    }


PRESETS = {
    "melodic-build-drop": melodic_build_drop,
    "house-classic": house_classic,
    "ambient-to-techno": ambient_to_techno,
}
