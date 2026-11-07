// SA3 UI kit: dadaspec images (hispec.py's full-HD spectrogram, 1920 columns over the whole
// clip, L on top / R mirrored below) as drawable sources, plus SpecView: one render's dadaspec
// on a canvas with hover time, playhead, highlight bands and the range-drag gesture (spec 3.1:
// click seeks, drag ≥ 4 px selects a range → onRange; pages open the highlight modal from it).
//
// The gear's mono fold (uiprefs.js, localStorage "sa3-uiprefs".spec === "mono") applies here too:
// row y of L averaged with the mirrored row of R, bass at the bottom. Same rule, same origin.

const cache = new Map();

export function monoPref() {
  try { return JSON.parse(localStorage.getItem("sa3-uiprefs") || "{}").spec === "mono"; } catch (e) { return false; }
}

function fold(img) {
  const w = img.naturalWidth, h = img.naturalHeight, half = h >> 1;
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  const g = c.getContext("2d", { willReadFrequently: true });
  g.drawImage(img, 0, 0);
  const src = g.getImageData(0, 0, w, h).data;
  const out = g.createImageData(w, half), d = out.data, row = w * 4;
  for (let y = 0; y < half; y++) {
    const a = y * row, b = (h - 1 - y) * row;
    for (let x = 0; x < row; x += 4) {
      d[a + x] = (src[a + x] + src[b + x]) >> 1;
      d[a + x + 1] = (src[a + x + 1] + src[b + x + 1]) >> 1;
      d[a + x + 2] = (src[a + x + 2] + src[b + x + 2]) >> 1;
      d[a + x + 3] = 255;
    }
  }
  c.height = half;
  c.getContext("2d").putImageData(out, 0, 0);
  return c;
}

// url → Promise<canvas | img> (cached per url + fold). Rejects when the route has no image.
export function load(url, mono = monoPref()) {
  const k = url + "|" + mono;
  if (!cache.has(k)) {
    const p = new Promise((res, rej) => {
      const img = new Image();
      img.onload = () => res(mono ? fold(img) : img);
      img.onerror = () => rej(new Error("no spectrogram: " + url));
      img.src = url;
    });
    p.catch(() => cache.delete(k));
    cache.set(k, p);
  }
  return cache.get(k);
}

// Draw seconds [s0, s1] of a `dur`-second dadaspec into the rect (x, y, w, h).
export function drawCrop(ctx, src, dur, s0, s1, x, y, w, h) {
  if (!src || !dur || w <= 0) return;
  const W = src.width, H = src.height;
  const a = Math.max(0, (s0 / dur) * W), b = Math.min(W, (s1 / dur) * W);
  if (b <= a) return;
  const dx = x + ((a - (s0 / dur) * W) / (((s1 - s0) / dur) * W)) * w;
  const dw = ((b - a) / (((s1 - s0) / dur) * W)) * w;
  ctx.drawImage(src, a, 0, b - a, H, dx, y, dw, h);
}

const fmt = (t) => (t >= 60 ? `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}` : t.toFixed(1) + " s");

// One render's whole dadaspec, with a range-drag selection and highlight bands.
//   new SpecView(host, {url, duration, onRange(range|null), onSeek(t), bands: [{id, s0, s1, ...}],
//                       onBand(band, event)})
//   .setRange([a, b] | null)  .setPlayhead(t | null)  .setBands([...])
// A click on a band calls onBand (pages loop-play it, or open its menu); a drag always selects.
export class SpecView {
  constructor(host, { url, duration, onRange = null, onSeek = null, height = 64, bands = [], onBand = null }) {
    this.dur = duration || 1;
    this.bands = bands || [];
    this.onBand = onBand;
    this.range = null;
    this.ph = null;
    this.hover = null;
    this.src = null;
    this.onRange = onRange;
    this.onSeek = onSeek;
    this.cv = document.createElement("canvas");
    this.cv.className = "ui-specview";
    this.cv.style.cssText = `width:100%;height:${height}px;display:block;border-radius:6px;cursor:crosshair;background:#0b0b10`;
    host.appendChild(this.cv);
    load(url).then((s) => { this.src = s; this.draw(); }).catch(() => { this.missing = true; this.draw(); });
    new ResizeObserver(() => this.draw()).observe(this.cv);
    this.cv.addEventListener("pointerdown", (e) => this.down(e));
    this.cv.addEventListener("pointermove", (e) => { this.hover = this.tAt(e); if (!this.drag) this.draw(); });
    this.cv.addEventListener("pointerleave", () => { this.hover = null; this.draw(); });
  }
  tAt(e) {
    const r = this.cv.getBoundingClientRect();
    return Math.max(0, Math.min(this.dur, ((e.clientX - r.left) / r.width) * this.dur));
  }
  down(e) {
    e.preventDefault();
    this.cv.setPointerCapture(e.pointerId);
    const t0 = this.tAt(e);
    let moved = false;
    const mv = (ev) => {
      const t = this.tAt(ev);
      if (Math.abs(ev.clientX - e.clientX) > 3) moved = true;
      if (moved) { this.range = [Math.min(t0, t), Math.max(t0, t)]; this.hover = t; this.draw(); }
    };
    const up = (ev) => {
      this.cv.removeEventListener("pointermove", mv);
      this.cv.removeEventListener("pointerup", up);
      this.drag = false;
      if (!moved) {
        const bd = this.onBand && this.bands.find((b) => t0 >= b.s0 && t0 <= b.s1);
        if (bd && !(this.range && t0 >= this.range[0] && t0 <= this.range[1])) { this.onBand(bd, ev); return; }
        if (this.range && t0 >= this.range[0] && t0 <= this.range[1]) { if (this.onSeek) this.onSeek(t0); return; }
        this.range = null; this.draw();
        if (this.onSeek) this.onSeek(t0);
        if (this.onRange) this.onRange(null);
        return;
      }
      const r = [Math.round(this.range[0] * 100) / 100, Math.round(this.range[1] * 100) / 100];
      this.range = r[1] - r[0] >= 0.5 ? r : null;
      this.draw();
      if (this.onRange) this.onRange(this.range);
    };
    this.drag = true;
    this.cv.addEventListener("pointermove", mv);
    this.cv.addEventListener("pointerup", up);
  }
  setRange(r) { this.range = r; this.draw(); }
  setBands(b) { this.bands = b || []; this.draw(); }
  setPlayhead(t) { this.ph = t; this.draw(); }
  draw() {
    const cv = this.cv, dpr = devicePixelRatio || 1;
    const w = cv.clientWidth, h = cv.clientHeight;
    if (!w) return;
    if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
    const g = cv.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    if (this.src) g.drawImage(this.src, 0, 0, w, h);
    else { g.fillStyle = "#5d5d6c"; g.font = "11px Inter"; g.fillText(this.missing ? "no spectrogram" : "loading…", 8, h / 2 + 4); }
    const X = (t) => (t / this.dur) * w;
    for (const b of this.bands) {                           // highlight bands: amber top strip + tint
      const a = X(b.s0), z = X(b.s1);
      g.fillStyle = "rgba(255,212,121,0.13)"; g.fillRect(a, 0, z - a, h);
      g.fillStyle = "rgba(255,212,121,0.9)"; g.fillRect(a, 0, z - a, 3);
    }
    if (this.range) {
      const [a, b] = this.range;
      g.fillStyle = "rgba(0,0,0,0.55)";
      g.fillRect(0, 0, X(a), h);
      g.fillRect(X(b), 0, w - X(b), h);
      g.strokeStyle = "#c4bbff"; g.lineWidth = 1.5;
      g.strokeRect(X(a) + 0.75, 0.75, X(b) - X(a) - 1.5, h - 1.5);
    }
    if (this.ph != null) { g.fillStyle = "#fff"; g.fillRect(X(this.ph) - 0.5, 0, 1.5, h); }
    if (this.hover != null) {
      g.fillStyle = "rgba(255,255,255,0.35)"; g.fillRect(X(this.hover), 0, 1, h);
      const s = this.range && this.drag ? `${fmt(this.range[0])} – ${fmt(this.range[1])}` : fmt(this.hover);
      g.font = "10.5px JetBrains Mono, monospace";
      const tw = g.measureText(s).width + 8, x = Math.min(w - tw, Math.max(0, X(this.hover) + 4));
      g.fillStyle = "rgba(9,9,13,0.8)"; g.fillRect(x, 2, tw, 15);
      g.fillStyle = "#e8e8f0"; g.fillText(s, x + 4, 13);
    }
  }
}
