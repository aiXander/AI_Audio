"""Build outputs/astra-spectrograms/index.html: one tab per Astra song, every audio version, and a
DJ deck on top that plays all versions back to back (all_versions.mp3).

    uv run python make_page.py [--html-only]

Per version: loudness-matched mp3 (two-pass EBU R128 loudnorm to -18 LUFS, linear), the
Astra-style re-rendered spectrogram (work/<tag>.spec.png, same axes and colours as Astra's plot
area) and the studio's mono dadaspec (dada_tiles.py, 120 columns/s) used as the deck's tiles.
The deck is a static port of sa3lab/djview.js (CDJ-style: the spectrogram scrolls under a fixed
centre playhead, whole-set overview below, one zoom slider), with segment labels added.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
AI_AUDIO = HERE.parents[1]
OUT = AI_AUDIO / "outputs" / "astra-spectrograms"
MLX_PY = AI_AUDIO / "stable-audio-3" / "optimized" / "mlx" / ".venv" / "bin" / "python"
SEG = 30.0
CPS = 120

VERSIONS = [
    ("raw_gl", "Griffin-Lim", "Phase retrieval only (fast Griffin-Lim, 400 iterations): the most literal reading of the image, with GL's phasey shimmer."),
    ("raw_additive", "Sinusoids + noise", "Every painted line becomes one clean oscillator (partial tracking, continuous phase); the rest of the image becomes shaped noise."),
    ("raw_bigvgan", "BigVGAN-v2 vocoder", "NVIDIA's universal neural vocoder (44 kHz), fed the image as its mel spectrogram. Trained on real recordings, it adds a noise floor and smears the bass on Astra's perfectly clean lines."),
    ("raw_vocos", "Vocos vocoder", "Small neural vocoder (24 kHz, trained on speech). Keeps the shapes better than BigVGAN, with a grainy texture in the bass."),
    ("sa3_raw_additive_s0.1", "SA3 cleanup, σ 0.1", "Stable Audio 3 audio-to-audio over the sinusoid version at noise level 0.1: Astra's layout kept, but SA3 re-voices it (held tones become struck notes with decays)."),
    ("sa3_raw_gl_s0.1", "SA3 cleanup of Griffin-Lim, σ 0.1", "Same, starting from the Griffin-Lim version: SA3 removes most of the phasiness."),
    ("sa3_raw_additive_s0.2", "SA3, σ 0.2", "SA3 starts rewriting: sustained tones become struck notes with decays; glides partly lost."),
    ("sa3_raw_additive_s0.35", "SA3, σ 0.35", "SA3 keeps Astra's rhythm and register map but plays its own instruments."),
    ("sa3_raw_additive_s0.6", "SA3, σ 0.6", "Mostly SA3's own piece, loosely steered by Astra's skeleton and its description as prompt."),
]


def sh(*cmd, **kw):
    return subprocess.run([str(c) for c in cmd], check=True, capture_output=True, text=True, **kw)


def loudnorm(src: Path, dst_wav: Path) -> None:
    """Two-pass linear loudnorm to -18 LUFS / -1.5 dBTP, exactly SEG seconds, 44.1 kHz stereo."""
    pre = f"aresample=44100,apad=whole_dur={SEG},atrim=0:{SEG}"
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(src), "-af",
                        f"{pre},loudnorm=I=-18:TP=-1.5:LRA=20:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S).group(0))
    ln = (f"loudnorm=I=-18:TP=-1.5:LRA=20:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    sh("ffmpeg", "-y", "-v", "error", "-i", src, "-af", f"{pre},{ln},aresample=44100", "-ac", "2",
       "-c:a", "pcm_s16le", dst_wav)


def mp3(src: Path, dst: Path, kbps: int = 160) -> None:
    sh("ffmpeg", "-y", "-v", "error", "-i", src, "-c:a", "libmp3lame", "-b:a", f"{kbps}k", dst)


def parse_desc(md: str) -> dict:
    def field(name):
        m = re.search(rf"\*\*{name}:\*\*\s*(.+?)(?=\n- \*\*|\Z)", md, re.S)
        return m.group(1).strip() if m else ""
    title = field("Title").strip("*_ ")
    timeline = field("Timeline")
    parts = [p.strip(" .;") for p in re.split(r"[;.]\s+(?=\d)", timeline) if p.strip()]
    image = re.sub(r"Saved as .*", "", field("Image")).strip().rstrip(".")
    return dict(title=title, sounds=field("What it sounds like"), timeline=parts,
                why=field("Why you love it"), image_note=image)


def main() -> None:
    songs = sorted(OUT.glob("s[0-9]-*"))
    data, concat, tiles = [], [], []
    tmp = OUT / "_tmp"
    tmp.mkdir(exist_ok=True)
    seg_i = 0
    for si, song in enumerate(songs):
        work, aud = song / "work", song / "audio"
        aud.mkdir(exist_ok=True)
        desc = parse_desc((song / "astra_description.md").read_text())
        fid = json.loads((work / "fidelity.json").read_text())
        img = Image.open(song / "astra_spectrogram.png")
        img.convert("RGB").save(song / "astra_spectrogram.jpg", quality=92)
        vers = []
        pairs = []
        for tag, label, blurb in VERSIONS:
            wav = work / f"{tag}.wav"
            if not wav.exists():
                print("missing", wav)
                continue
            norm = tmp / f"{song.name}_{tag}.wav"
            loudnorm(wav, norm)
            mp3(norm, aud / f"{tag}.mp3")
            Image.open(work / f"{tag}.spec.png").convert("RGB").resize((1100, 300), Image.LANCZOS) \
                .save(aud / f"{tag}.spec.jpg", quality=88)
            pairs.append(f"{norm}:{aud / f'{tag}.dada.jpg'}")
            concat.append(norm)
            tiles.append(f"{song.name}/audio/{tag}.dada.jpg")
            vers.append(dict(tag=tag, label=label, blurb=blurb, seg=seg_i,
                             mp3=f"{song.name}/audio/{tag}.mp3", spec=f"{song.name}/audio/{tag}.spec.jpg",
                             corr=fid.get(tag, {}).get("corr")))
            seg_i += 1
            print(song.name, tag, flush=True)
        sh(MLX_PY, HERE / "dada_tiles.py", "--cps", CPS, *pairs)
        Image.open(work / "source.spec.png").convert("RGB").resize((1100, 300), Image.LANCZOS) \
            .save(aud / "source.spec.jpg", quality=88)
        data.append(dict(n=si + 1, slug=song.name, **desc, image=f"{song.name}/astra_spectrogram.jpg",
                         source_spec=f"{song.name}/audio/source.spec.jpg",
                         prompt=(song / "astra_image_prompt.txt").read_text(),
                         sa3_prompt=(work / "sa3_prompt.txt").read_text(), versions=vers))

    # one long file: every version back to back, SEG seconds each
    lst = tmp / "concat.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in concat))
    sh("ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c:a", "libmp3lame",
       "-b:a", "160k", OUT / "all_versions.mp3")
    # whole-set overview strip for the deck
    ims = [Image.open(OUT / t) for t in tiles]
    ov = Image.new("RGB", (len(ims) * 120, ims[0].height))
    for i, im in enumerate(ims):
        ov.paste(im.resize((120, im.height), Image.LANCZOS), (i * 120, 0))
    ov.save(OUT / "deck_overview.jpg", quality=88)
    for p in tmp.iterdir():
        p.unlink()
    tmp.rmdir()

    payload = dict(songs=data, seg=SEG, cps=CPS, tiles=tiles, overview="deck_overview.jpg",
                   all="all_versions.mp3")
    (OUT / "page_data.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    write_html(payload)


def write_html(payload: dict) -> None:
    page = (HERE / "page_template.html").read_text()
    page = page.replace("/*DATA*/null", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    (OUT / "index.html").write_text(page)
    print(OUT / "index.html")


if __name__ == "__main__":
    import sys
    if "--html-only" in sys.argv:   # template edits: reuse the media and page_data.json
        write_html(json.loads((OUT / "page_data.json").read_text()))
    else:
        main()
