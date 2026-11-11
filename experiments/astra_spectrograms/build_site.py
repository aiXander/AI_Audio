"""Build the public website (outputs/astra-spectrograms/site/, deployed to Vercel as a static folder).

    uv run python build_site.py [--html-only]

Per song, five versions on one chain: the Griffin-Lim inversion of Astra's image, then Stable
Audio 3 audio-to-audio over it at sigma 0.1 / 0.2 / 0.35 / 0.6 (cleanup.py --src raw_gl).
Per version: loudness-matched mp3 (two-pass EBU R128 loudnorm to -18 LUFS, 192 kbps), the studio's
mono dadaspec (dada_tiles.py, CPS columns per second: the player strip and the DJ view) and the
spectrogram re-measured on Astra's own axes (work/<tag>.spec.png). The page is site_template.html
with the data injected; everything it shows sits under site/ (media/, files/).

files/ holds what someone needs to redo it: the prompt to Astra, the pipeline scripts, and per
song Astra's untouched PNG, its reply, its own image prompt, the SA3 prompt, the crop info and the
match scores; plus one zip of all of it. Local paths are stripped from what gets published.
Codex event logs are NOT published (they include the operator's local config files).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from PIL import Image

from make_page import loudnorm, parse_desc

HERE = Path(__file__).resolve().parent
AI_AUDIO = HERE.parents[1]
OUT = AI_AUDIO / "outputs" / "astra-spectrograms"
SITE = OUT / "site"
MLX_PY = AI_AUDIO / "stable-audio-3" / "optimized" / "mlx" / ".venv" / "bin" / "python"
CPS = 160          # dadaspec columns per second (30 s -> 4800 px: sharp at the DJ view's 4-8 s zoom)
KBPS = 192
SITE_URL = "https://astra-favourite-songs.vercel.app/"   # for the share card's absolute image URL

# (work-dir tag, site key, slider label, sigma or None, one-line note)
VERSIONS = [
    ("raw_gl", "gl", "Griffin-Lim", None,
     "The picture read straight back into sound: no model, just phase retrieval. Every event sits where "
     "Astra painted it, with Griffin-Lim's metallic, phasey shimmer."),
    ("sa3_raw_gl_s0.1", "s0.1", "σ 0.1", 0.1,
     "SA3 repaints the Griffin-Lim audio from a light dusting of noise. Astra's timing and pitches stay; "
     "the shimmer turns into struck, decaying instruments."),
    ("sa3_raw_gl_s0.2", "s0.2", "σ 0.2", 0.2,
     "More noise, more SA3. The layout holds, but held tones become plucked notes, some glides and low "
     "drones drop out, and the timbres are SA3's own."),
    ("sa3_raw_gl_s0.35", "s0.35", "σ 0.35", 0.35,
     "Astra's skeleton (where events fall, roughly which register) under SA3's instruments and "
     "arrangement. Recognisable if you A/B it, not if you don't."),
    ("sa3_raw_gl_s0.6", "s0.6", "σ 0.6", 0.6,
     "Mostly SA3's own piece, steered by Astra's description as the prompt and the loudness envelope "
     "of the original."),
]
# Per-song notes where the generic line above would mislead (read off the re-measured spectrograms).
SONG_NOTES = {
    ("s1", "s0.35"): "SA3's instruments on Astra's rhythm. It keeps the pulse going after the convergence, "
                     "where Astra let it dissolve.",
    ("s1", "s0.6"): "A different piece: steady low pulses that only share the description and the rough "
                    "energy curve.",
    ("s2", "s0.35"): "Surprisingly faithful for this much noise: nearly every pluck stays at its time and "
                     "pitch, re-voiced as SA3's own mallets.",
    ("s2", "s0.6"): "SA3 lets go of the plucks and holds one bright drone chord for most of the piece.",
    ("s3", "s0.1"): "Already lossy here: SA3 keeps the opening plucks and the convergence, but drops the "
                    "gliding middle section to near silence.",
    ("s3", "s0.2"): "SA3 hears mostly silence in this one: only the opening and the convergence hit survive.",
    ("s3", "s0.35"): "Nearly empty: one sweeping impact where Astra's convergence was. SA3 does not "
                     "recognise this image's music as music.",
    ("s3", "s0.6"): "A few short bursts of noise in otherwise silent air.",
    ("s4", "s0.35"): "Astra's central bloom survives as a swelling chord; the plucked ending is gone.",
    ("s4", "s0.6"): "One long, pulsing drone with SA3's own swell in the middle.",
}
SCRIPTS = ["decode.py", "specgrid.py", "invert.py", "cleanup.py", "run_all.py", "dada_tiles.py",
           "build_site.py", "site_template.html", "make_page.py", "pyproject.toml", "uv.lock"]


def sh(*cmd):
    return subprocess.run([str(c) for c in cmd], check=True, capture_output=True, text=True)


def mp3(src: Path, dst: Path) -> None:
    sh("ffmpeg", "-y", "-v", "error", "-i", src, "-c:a", "libmp3lame", "-b:a", f"{KBPS}k", dst)


def clean_reply(md: str) -> str:
    """Astra's reply minus the local path it saved the image to."""
    return re.sub(r"(?i),?\s*saved as \[[^\]]*\]\([^)]*\)\.?", "", md)


def build_media() -> list[dict]:
    media = SITE / "media"
    files = SITE / "files"
    tmp = SITE / "_tmp"
    for d in (media, files, tmp):
        d.mkdir(parents=True, exist_ok=True)
    songs = []
    for si, song in enumerate(sorted(OUT.glob("s[0-9]-*"))):
        work = song / "work"
        md = clean_reply((song / "astra_description.md").read_text())
        desc = parse_desc(md)
        fid = json.loads((work / "fidelity.json").read_text())
        m = media / song.name
        m.mkdir(exist_ok=True)
        im = Image.open(song / "astra_spectrogram.png").convert("RGB")
        im.save(m / "astra.jpg", quality=90)
        Image.open(work / "source.spec.png").convert("RGB").resize((1400, 380), Image.LANCZOS) \
            .save(m / "source.spec.jpg", quality=88)
        info = json.loads((work / "decode_info.json").read_text())
        info["image"] = "astra_spectrogram.png"
        vers, pairs = [], []
        for tag, key, label, sigma, note in VERSIONS:
            note = SONG_NOTES.get((song.name[:2], key), note)
            wav = work / f"{tag}.wav"
            if not wav.exists():
                sys.exit(f"missing {wav}: run cleanup.py --src raw_gl first")
            norm = tmp / f"{song.name}_{key}.wav"
            loudnorm(wav, norm)
            mp3(norm, m / f"{key}.mp3")
            Image.open(work / f"{tag}.spec.png").convert("RGB").resize((1400, 380), Image.LANCZOS) \
                .save(m / f"{key}.spec.jpg", quality=88)
            pairs.append(f"{norm}:{m / f'{key}.dada.jpg'}")
            vers.append(dict(key=key, tag=tag, label=label, sigma=sigma, note=note,
                             mp3=f"media/{song.name}/{key}.mp3", dada=f"media/{song.name}/{key}.dada.jpg",
                             spec=f"media/{song.name}/{key}.spec.jpg", corr=fid[tag]["corr"],
                             mae=fid[tag]["mae_db"]))
            print(song.name, key, fid[tag], flush=True)
        sh(MLX_PY, HERE / "dada_tiles.py", "--cps", CPS, *pairs)

        # downloadable per-song files
        f = files / song.name
        f.mkdir(exist_ok=True)
        shutil.copy(song / "astra_spectrogram.png", f / "astra_spectrogram.png")
        (f / "astra_description.md").write_text(md)   # the name cleanup.py reads
        shutil.copy(song / "astra_image_prompt.txt", f / "astra_image_prompt.txt")
        shutil.copy(work / "sa3_prompt.txt", f / "sa3_prompt.txt")
        (f / "decode_info.json").write_text(json.dumps(info, indent=2))
        (f / "match.json").write_text(json.dumps({v["tag"]: fid[v["tag"]] for v in vers}, indent=2))

        songs.append(dict(n=si + 1, slug=song.name, **desc, image=f"media/{song.name}/astra.jpg",
                          png=f"files/{song.name}/astra_spectrogram.png",
                          source_spec=f"media/{song.name}/source.spec.jpg",
                          prompt=(song / "astra_image_prompt.txt").read_text().strip(),
                          sa3_prompt=(work / "sa3_prompt.txt").read_text().strip(),
                          plot_px=info["plot_px"], ms_per_column=info["ms_per_column"],
                          ramp=info["ramp"], versions=vers))
    shutil.rmtree(tmp)
    og = Image.new("RGB", (1200, 630), (0, 0, 0))   # share card: song 1's painting, letterboxed
    og.paste(Image.open(media / songs[0]["slug"] / "astra.jpg").resize((1200, 400), Image.LANCZOS), (0, 115))
    og.save(media / "og.jpg", quality=88)

    shutil.copy(OUT / "_codex" / "prompt_to_astra.md", files / "prompt_to_astra.md")
    (files / "pipeline").mkdir(exist_ok=True)
    for s in SCRIPTS:
        shutil.copy(HERE / s, files / "pipeline" / s)
    zp = files / "astra-spectrograms-repro.zip"
    zp.unlink(missing_ok=True)
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(files.rglob("*")):
            if p.is_file() and p != zp:
                z.write(p, Path("astra-spectrograms") / p.relative_to(files))
    return songs


def write_html(payload: dict) -> None:
    page = (HERE / "site_template.html").read_text()
    page = page.replace("/*SITE_URL*/", SITE_URL)
    page = page.replace("/*DATA*/null", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    (SITE / "index.html").write_text(page)
    (SITE / "site_data.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    print(SITE / "index.html")


if __name__ == "__main__":
    if "--html-only" in sys.argv:   # template edits: reuse the media and site_data.json
        write_html(json.loads((SITE / "site_data.json").read_text()))
    else:
        SITE.mkdir(parents=True, exist_ok=True)
        payload = dict(songs=build_media(), prompt=(OUT / "_codex" / "prompt_to_astra.md").read_text(),
                       cps=CPS, kbps=KBPS)
        write_html(payload)
