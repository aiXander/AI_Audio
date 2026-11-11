"""Spectrogram image -> dB grid: crop the plot area exactly, then decode colour to energy.

The format we asked Astra for (prompt in outputs/astra-spectrograms/_codex/prompt_to_astra.md):
  x = time, 0..30 s linear; y = HTK mel, 0 Hz (bottom) .. 12 kHz (top);
  colour = matplotlib "magma", -80 dB (black) .. 0 dB (pale yellow); colourbar on the right.

Cropping (`find_plot_box`) works on both page polarities:
  * dark page (what Astra drew): the plot frame is a thin light-neutral rectangle. Rows/cols whose
    longest run of "frame-coloured" pixels is long are spines; the plot frame is the widest pair of
    horizontal spines, the colourbar is the narrow rectangle to its right.
  * light page: same, with dark-neutral frame pixels.
  If no frame is found it falls back to the bounding box of magma-like, non-neutral pixels.
  Then it walks each edge inward past the spine and its anti-aliasing (`_trim`).

Colour decoding (`decode_colors`): nearest neighbour along a colour ramp (1024 entries, KD-tree in
RGB after a 3x3 median filter against JPEG/diffusion grain). Two ramps are tried: true magma and
the ramp sampled from the image's own colourbar; whichever fits the plot pixels better wins.

    uv run python decode.py <image.png> [--out dir]    -> dir/decoded.npz + dir/crop_debug.png
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from matplotlib import colormaps
from PIL import Image, ImageDraw
from scipy.ndimage import median_filter
from scipy.spatial import cKDTree

DB_MIN, DB_MAX = -80.0, 0.0


def _longest_runs(mask: np.ndarray, axis: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per row (axis=1) or column (axis=0): length, start and end of the longest True run."""
    m = mask if axis == 1 else mask.T
    n, w = m.shape
    lengths, starts = np.zeros(n, int), np.zeros(n, int)
    for i in range(n):
        row = np.concatenate([[0], m[i].astype(np.int8), [0]])
        d = np.diff(row)
        s, e = np.where(d == 1)[0], np.where(d == -1)[0]
        if len(s):
            k = np.argmax(e - s)
            lengths[i], starts[i] = e[k] - s[k], s[k]
    return lengths, starts, starts + lengths


def _frame_mask(img: np.ndarray, dark_page: bool) -> np.ndarray:
    f = img.astype(np.int16)
    chroma = f.max(-1) - f.min(-1)
    lum = f.mean(-1)
    if dark_page:
        return (lum > 110) & (chroma < 45)
    return (lum < 110) & (chroma < 45)


def _groups(idx: np.ndarray) -> list[np.ndarray]:
    if len(idx) == 0:
        return []
    return np.split(idx, np.where(np.diff(idx) > 2)[0] + 1)


def find_plot_box(img: np.ndarray) -> tuple[dict, dict | None]:
    """Return (plot box, colourbar box or None); boxes are dicts x0,x1,y0,y1 (exclusive ends)."""
    h, w, _ = img.shape
    border = np.concatenate([img[:5].reshape(-1, 3), img[-5:].reshape(-1, 3),
                             img[:, :5].reshape(-1, 3), img[:, -5:].reshape(-1, 3)])
    dark_page = border.mean() < 128
    fm = _frame_mask(img, dark_page)
    # tolerate 1-2 px gaps in an anti-aliased spine
    fm_h = fm | np.roll(fm, 1, 1) | np.roll(fm, -1, 1)
    fm_v = fm | np.roll(fm, 1, 0) | np.roll(fm, -1, 0)
    rlen, rs, re = _longest_runs(fm_h, axis=1)
    clen, cs, ce = _longest_runs(fm_v, axis=0)

    hrows = _groups(np.where(rlen > 0.45 * w)[0])
    box = None
    if len(hrows) >= 2:
        top, bot = hrows[0], hrows[-1]
        x0 = int(np.median(rs[np.r_[top, bot]]))
        x1 = int(np.median(re[np.r_[top, bot]]))
        y0, y1 = int(top.max()) + 1, int(bot.min())
        box = dict(x0=x0, x1=x1, y0=y0, y1=y1)
        # left/right spines refine x: columns inside [x0-5, x1+5] with long vertical runs
        vcols = np.where(clen > 0.6 * (y1 - y0))[0]
        left = vcols[(vcols >= x0 - 6) & (vcols <= x0 + 6)]
        right = vcols[(vcols >= x1 - 8) & (vcols <= x1 + 6)]
        if len(left):
            box["x0"] = int(left.max()) + 1
        if len(right):
            box["x1"] = int(right.min())
    if box is None:  # fallback: bounding box of colourful magma-ish pixels
        f = img.astype(np.int16)
        colourful = (f.max(-1) - f.min(-1)) > 25
        ys, xs = np.where(colourful)
        box = dict(x0=int(np.percentile(xs, 0.5)), x1=int(np.percentile(xs, 99.5)),
                   y0=int(np.percentile(ys, 0.5)), y1=int(np.percentile(ys, 99.5)))
    box = _trim(img, box, fm)
    box = dict(x0=box["x0"] + 1, x1=box["x1"] - 1, y0=box["y0"] + 1, y1=box["y1"] - 1)  # AA safety

    # colourbar: tall frame columns right of the plot, pair of them a few px apart
    cbar = None
    vcols = np.where(clen > 0.5 * (box["y1"] - box["y0"]))[0]
    vcols = vcols[vcols > box["x1"] + 8]
    g = _groups(vcols)
    if len(g) >= 2:
        cx0, cx1 = int(g[0].max()) + 1, int(g[1].min())
        if 4 <= cx1 - cx0 <= 0.1 * w:
            ys = np.r_[cs[g[0]], cs[g[1]]]
            ye = np.r_[ce[g[0]], ce[g[1]]]
            cbar = _trim(img, dict(x0=cx0, x1=cx1, y0=int(np.median(ys)), y1=int(np.median(ye))), fm)
    return box, cbar


def _trim(img: np.ndarray, box: dict, fm: np.ndarray, max_steps: int = 8) -> dict:
    """Walk each edge inward while the edge line still looks like frame (spine / anti-alias)."""
    b = dict(box)
    lum = img.astype(np.float32).mean(-1)

    def frame_like(line_fm, line_lum, inner_lum):
        return line_fm.mean() > 0.15 or abs(line_lum.mean() - inner_lum) > 25

    for _ in range(max_steps):
        inner = lum[b["y0"] + 4:b["y0"] + 12, b["x0"]:b["x1"]].mean()
        if frame_like(fm[b["y0"], b["x0"]:b["x1"]], lum[b["y0"], b["x0"]:b["x1"]], inner):
            b["y0"] += 1
    for _ in range(max_steps):
        inner = lum[b["y1"] - 12:b["y1"] - 4, b["x0"]:b["x1"]].mean()
        if frame_like(fm[b["y1"] - 1, b["x0"]:b["x1"]], lum[b["y1"] - 1, b["x0"]:b["x1"]], inner):
            b["y1"] -= 1
    for _ in range(max_steps):
        inner = lum[b["y0"]:b["y1"], b["x0"] + 4:b["x0"] + 12].mean()
        if frame_like(fm[b["y0"]:b["y1"], b["x0"]], lum[b["y0"]:b["y1"], b["x0"]], inner):
            b["x0"] += 1
    for _ in range(max_steps):
        inner = lum[b["y0"]:b["y1"], b["x1"] - 12:b["x1"] - 4].mean()
        if frame_like(fm[b["y0"]:b["y1"], b["x1"] - 1], lum[b["y0"]:b["y1"], b["x1"] - 1], inner):
            b["x1"] -= 1
    return b


def magma_ramp(n: int = 1024) -> np.ndarray:
    return (colormaps["magma"](np.linspace(0, 1, n))[:, :3] * 255).astype(np.float32)


def colorbar_ramp(img: np.ndarray, cbar: dict, n: int = 1024) -> np.ndarray | None:
    """Sample the image's own colourbar (bottom = -80 dB, top = 0 dB) into an n-entry ramp."""
    strip = img[cbar["y0"]:cbar["y1"], cbar["x0"] + 1:cbar["x1"] - 1].astype(np.float32)
    if strip.shape[0] < 50 or strip.shape[1] < 2:
        return None
    col = np.median(strip, axis=1)[::-1]  # bottom -> top
    t = np.linspace(0, 1, len(col))
    ramp = np.stack([np.interp(np.linspace(0, 1, n), t, col[:, c]) for c in range(3)], -1)
    return ramp


def decode_colors(plot: np.ndarray, ramp: np.ndarray) -> tuple[np.ndarray, float]:
    """RGB plot -> value in [0, 1] along the ramp; also returns mean RGB fit distance."""
    sm = median_filter(plot, size=(3, 3, 1)).astype(np.float32)
    tree = cKDTree(ramp)
    d, idx = tree.query(sm.reshape(-1, 3), workers=-1)
    v = idx.reshape(plot.shape[:2]) / (len(ramp) - 1)
    return v.astype(np.float32), float(d.mean())


def decode(path: Path, out: Path) -> dict:
    img = np.asarray(Image.open(path).convert("RGB"))
    box, cbar = find_plot_box(img)
    plot = img[box["y0"]:box["y1"], box["x0"]:box["x1"]]
    ramps = {"magma": magma_ramp()}
    if cbar:
        r = colorbar_ramp(img, cbar)
        if r is not None:
            ramps["image_colorbar"] = r
    fits = {k: decode_colors(plot, r) for k, r in ramps.items()}
    best = min(fits, key=lambda k: fits[k][1])
    v = fits[best][0]
    db = DB_MIN + (DB_MAX - DB_MIN) * v  # rows: top = 12 kHz
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / "decoded.npz", db=db.astype(np.float32))

    dbg = Image.fromarray(img).convert("RGB")
    dr = ImageDraw.Draw(dbg)
    dr.rectangle([box["x0"], box["y0"], box["x1"] - 1, box["y1"] - 1], outline=(0, 255, 0), width=1)
    if cbar:
        dr.rectangle([cbar["x0"], cbar["y0"], cbar["x1"] - 1, cbar["y1"] - 1], outline=(0, 200, 255))
    dbg.save(out / "crop_debug.png")
    Image.fromarray(plot).save(out / "plot_crop.png")
    info = dict(image=str(path), size=list(img.shape[:2][::-1]), plot_box=box, colorbar_box=cbar,
                ramp=best, ramp_fit_rgb_dist={k: round(f[1], 2) for k, f in fits.items()},
                plot_px=[int(plot.shape[1]), int(plot.shape[0])],
                ms_per_column=round(30000 / plot.shape[1], 2))
    (out / "decode_info.json").write_text(json.dumps(info, indent=2))
    return info


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    print(json.dumps(decode(a.image, a.out or a.image.parent / "work"), indent=2))
