"""Every Noise at Once (everynoise.com) → a genre-tag dataset with one reference clip per tag.

Why: the seed of a tag dataset in SA3's conditioning space. Each of everynoise's ~6.3k
Spotify micro-genres becomes a text tag we can encode with T5Gemma, and each comes with a
~30 s Spotify preview clip of a representative track, so a tag's text vector can later be
paired with real audio (SAME-L latents, CLAP, labels) for a preference model.

What the homepage holds per genre (all parsed from one ~3.5 MB HTML page, no API):
  rank             position on the page (1 = pop, 2 = rap, 3 = rock …; roughly popularity)
  genre            the tag text, e.g. "deep minimal techno"
  slug, page_url   everynoise's per-genre page (artist map of that genre)
  preview_url      direct mp3 on Spotify's CDN (p.scdn.co), ~30 s, ~360 KB
  spotify_track_id the example track
  example_artist / example_track   from the hover title ('e.g. Artist "Track"')
  map_x_px, map_y_px, font_size_pct   raw placement on the map
  electric         0..1 from map_y: 1 = top ("more mechanical and electric"), 0 = bottom ("more organic")
  bouncy           0..1 from map_x: 1 = right ("spikier and bouncier"), 0 = left ("denser and more atmospheric")
  color, r, g, b   label colour; everynoise maps three further (undocumented) acoustic
                   dimensions to RGB, so treat r/g/b as three extra opaque audio features
  audio            relative path the `audio` command downloads the clip to
The axis wording is everynoise's own ("the calibration is fuzzy").

Outputs, in --out (default AI_Audio/audio-data/everynoise/, gitignored):
  genres.jsonl     one record per genre (fields above)
  genres.csv       the same, for spreadsheets
  genres.txt       one tag per line, in rank order (what an encoder pass reads)
  everynoise.html  the raw page snapshot, so a re-parse needs no network
  audio/NNNN_<slug>.mp3   after `audio`; audio_failures.tsv lists any that failed

Run (stdlib only, any python ≥ 3.9):
  python3 sa3lab/tags/everynoise.py scrape                 # tags only, ~3.5 MB download
  python3 sa3lab/tags/everynoise.py scrape --html page.html  # re-parse a saved page
  python3 sa3lab/tags/everynoise.py audio                  # all clips, ~2.2 GB, resumable
  python3 sa3lab/tags/everynoise.py audio --limit 50 --workers 8
`audio` skips files already on disk, so an interrupted run just continues.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "audio-data" / "everynoise"
SITE = "https://everynoise.com/"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"

# One genre = one <div id=itemN ... class="genre scanme" ...>NAME<a class=navlink href="engenremap-SLUG.html"
DIV_RE = re.compile(r'<div id=item(\d+) ([^>]*class="genre[^"]*"[^>]*)>([^<]*)(?:<a class=navlink href="([^"]+)")?')
ATTR_RE = {
    "preview_url": re.compile(r'preview_url="([^"]*)"'),
    "color": re.compile(r"color:\s*(#[0-9a-fA-F]{6})"),
    "top": re.compile(r"top:\s*(-?\d+)px"),
    "left": re.compile(r"left:\s*(-?\d+)px"),
    "font": re.compile(r"font-size:\s*(\d+)%"),
    "onclick": re.compile(r'playx\(&quot;([^&]*)&quot;'),
    "title": re.compile(r'title="([^"]*)"'),
}
FIELDS = ["rank", "genre", "slug", "page_url", "preview_url", "spotify_track_id",
          "example_artist", "example_track", "map_x_px", "map_y_px", "font_size_pct",
          "electric", "bouncy", "color", "r", "g", "b", "audio"]


def fetch(url: str, timeout: float = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _attr(name: str, attrs: str) -> str | None:
    m = ATTR_RE[name].search(attrs)
    return m.group(1) if m else None


def _split_title(title: str | None) -> tuple[str, str]:
    """'e.g. Demi Lovato "Heart Attack"' → ('Demi Lovato', 'Heart Attack')."""
    if not title:
        return "", ""
    t = html.unescape(title)
    t = t[5:] if t.startswith("e.g. ") else t
    m = re.match(r'^(.*?)\s*"(.*)"\s*$', t)
    return (m.group(1).strip(), m.group(2)) if m else (t.strip(), "")


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "x"


def parse(page: str) -> list[dict]:
    rows = []
    for m in DIV_RE.finditer(page):
        idx, attrs, name, href = m.groups()
        genre = html.unescape(name).strip()
        slug = href[len("engenremap-"):-len(".html")] if href and href.startswith("engenremap-") else slugify(genre)
        artist, track = _split_title(_attr("title", attrs))
        color = (_attr("color", attrs) or "#000000").lower()
        rows.append({
            "rank": int(idx),
            "genre": genre,
            "slug": slug,
            "page_url": SITE + (href or f"engenremap-{slug}.html"),
            "preview_url": html.unescape(_attr("preview_url", attrs) or ""),
            "spotify_track_id": _attr("onclick", attrs) or "",
            "example_artist": artist,
            "example_track": track,
            "map_x_px": int(_attr("left", attrs) or 0),
            "map_y_px": int(_attr("top", attrs) or 0),
            "font_size_pct": int(_attr("font", attrs) or 100),
            "color": color,
            "r": int(color[1:3], 16), "g": int(color[3:5], 16), "b": int(color[5:7], 16),
            "audio": f"audio/{int(idx):04d}_{slugify(slug)[:80]}.mp3",
        })
    if not rows:
        sys.exit("no genre divs found: the page layout changed, update DIV_RE")
    xs, ys = [r["map_x_px"] for r in rows], [r["map_y_px"] for r in rows]
    for r in rows:
        r["bouncy"] = round((r["map_x_px"] - min(xs)) / max(1, max(xs) - min(xs)), 4)
        r["electric"] = round(1 - (r["map_y_px"] - min(ys)) / max(1, max(ys) - min(ys)), 4)
    rows.sort(key=lambda r: r["rank"])
    return rows


def cmd_scrape(a) -> None:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.html:
        page = Path(a.html).read_text(encoding="utf-8")
    else:
        print(f"fetching {SITE} …", flush=True)
        page = fetch(SITE).decode("utf-8")
    (out / "everynoise.html").write_text(page, encoding="utf-8")
    rows = parse(page)
    with open(out / "genres.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({k: r[k] for k in FIELDS}, ensure_ascii=False) + "\n")
    with open(out / "genres.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows({k: r[k] for k in FIELDS} for r in rows)
    (out / "genres.txt").write_text("".join(r["genre"] + "\n" for r in rows), encoding="utf-8")
    n_prev = sum(1 for r in rows if r["preview_url"])
    dup = len(rows) - len({r["genre"] for r in rows})
    print(f"{len(rows)} genres ({n_prev} with a preview clip, {dup} duplicate names) → {out}")


def _download(row: dict, out: Path, retries: int = 3) -> tuple[dict, str | None]:
    dest = out / row["audio"]
    if dest.exists() and dest.stat().st_size > 0:
        return row, None
    if not row["preview_url"]:
        return row, "no preview_url"
    err = None
    for attempt in range(retries):
        try:
            data = fetch(row["preview_url"], timeout=30)
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(dest)
            return row, None
        except Exception as e:  # noqa: BLE001 — network errors of every kind get retried
            err = f"{type(e).__name__}: {e}"
            time.sleep(1.5 * (attempt + 1))
    return row, err


def cmd_audio(a) -> None:
    out = Path(a.out)
    src = out / "genres.jsonl"
    if not src.exists():
        sys.exit(f"{src} missing: run `scrape` first")
    rows = [json.loads(line) for line in src.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = rows[: a.limit] if a.limit else rows
    (out / "audio").mkdir(exist_ok=True)
    todo = [r for r in rows if not ((out / r["audio"]).exists() and (out / r["audio"]).stat().st_size > 0)]
    print(f"{len(rows) - len(todo)} of {len(rows)} clips already on disk; downloading {len(todo)} "
          f"(~{len(todo) * 0.36 / 1000:.1f} GB) with {a.workers} workers", flush=True)
    failures, done, t0 = [], 0, time.time()
    with ThreadPoolExecutor(a.workers) as ex:
        for fut in as_completed([ex.submit(_download, r, out) for r in todo]):
            row, err = fut.result()
            done += 1
            if err:
                failures.append((row["rank"], row["genre"], row["preview_url"], err))
            if done % 100 == 0 or done == len(todo):
                print(f"  {done}/{len(todo)}  {done / max(1e-6, time.time() - t0):.1f}/s  "
                      f"{len(failures)} failed", flush=True)
    fail_path = out / "audio_failures.tsv"
    if failures:
        with open(fail_path, "w", encoding="utf-8") as f:
            f.write("rank\tgenre\tpreview_url\terror\n")
            for row in sorted(failures):
                f.write("\t".join(map(str, row)) + "\n")
        print(f"{len(failures)} failed → {fail_path} (re-run to retry)")
    elif fail_path.exists():
        fail_path.unlink()
    print("done")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scrape", help="fetch + parse the genre list")
    s.add_argument("--html", help="parse this saved page instead of fetching")
    s.add_argument("--out", default=str(DEFAULT_OUT))
    s.set_defaults(fn=cmd_scrape)
    d = sub.add_parser("audio", help="download every genre's preview mp3 (resumable)")
    d.add_argument("--out", default=str(DEFAULT_OUT))
    d.add_argument("--limit", type=int, default=0, help="only the first N genres by rank (0 = all)")
    d.add_argument("--workers", type=int, default=8)
    d.set_defaults(fn=cmd_audio)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
