// SA3 UI kit: the strength-curve editor on timeline.js (spec 4.4; replaces segments.js's own canvas
// when the studio's "Timeline editor" toggle is on: strength_tl.js). One curve of keys [t, u] over a
// reference's dadaspec, 0 keep, 1 regenerate, between = soft. The rules live in curve.js, the
// gestures + drawing in curvelane.js (shared with the Composer's Rewrite lane; its header lists them):
//   Span mode   drag on empty space → a plateau at the brush strength
//   Points mode click → a point at the cursor's height
// Drag a point (time + strength) or a line (up / down), drag inside a plateau to move it (⌥:
// duplicate), double-click adds / deletes a point, ⌫ deletes the selection (the curve heals),
// ←→ / ↑↓ nudge it. Here: ⌘Z / ⇧⌘Z, space plays (the selected plateau, else from the playhead).
// Times magnet to keys, the playhead, the audio end and Length. Zoom ⌘ + wheel, pan wheel.
//
//   const ed = new StrengthEditor(host, {onChange(keys)})
//   ed.setSource(url)  ed.setKeys(keys)  ed.setLength(len)  (Length: the dashed end marker)

import { Timeline } from "./timeline.js";
import * as C from "./curve.js";
import { CurveLane } from "./curvelane.js";

const fmt = (t) => { const m = Math.floor(t / 60), s = t - m * 60; return m ? `${m}:${s.toFixed(1).padStart(4, "0")}` : `${s.toFixed(1)}s`; };
const f2 = (u) => u.toFixed(2);

export class StrengthEditor {
  constructor(host, { onChange = null, maxLen = 380 } = {}) {
    this.o = { onChange, maxLen };
    this.undo = []; this.redo = []; this.dur = 0; this.len = 0; this.img = null; this.url = null; this.hover = null;
    const el = (this.el = document.createElement("div"));
    el.className = "ui-strength";
    el.tabIndex = 0;
    el.innerHTML = `<div class="bar"><button class="ui-btn small play" title="play / pause (space)">▶</button><span class="tm mono">0.0s</span>
      <div class="ui-seg modes"><button data-m="span" class="on">Span</button><button data-m="point">Points</button></div>
      <label class="ub" title="Denoising strength: 0 keeps, 1 regenerates. Sets new plateaus, or the selected box / key">Strength <input type="range" min="0" max="1" step="0.01" value="1"><b class="mono">1.00</b></label>
      <button class="ui-btn small inv" title="u → 1 − u">Invert</button><button class="ui-btn small clr">Clear</button>
      <span class="grow"></span><span class="sum muted"></span></div><div class="tlh"></div><audio preload="auto"></audio>`;
    host.appendChild(el);
    this.au = el.querySelector("audio");
    this.tl = new Timeline(el.querySelector(".tlh"), { gutter: 8, onSeek: (t) => this.seek(t) });
    this.lane = new CurveLane(this.tl, { end: () => this.T || 1, magnets: () => [this.dur, this.len, this.au.currentTime || 0],
      onSeek: (t) => this.seek(t), onPlay: (a, b) => this.play(a, b), focus: () => this.el.focus({ preventScroll: true }),
      onCommit: (K, before) => { if (before != null) { this.undo.push(before); if (this.undo.length > 80) this.undo.shift(); this.redo.length = 0; } this.changed(); } });
    this.tl.setLanes([{ key: "s", label: "", flex: 1, items: [], draw: (g, r, t) => this.paint(g, r, t), pointer: (k, e, p) => this.pointer(k, e, p) }]);
    el.querySelector(".play").onclick = () => this.toggle();
    el.querySelectorAll(".modes button").forEach((b) => (b.onclick = () => { this.mode = b.dataset.m; el.querySelectorAll(".modes button").forEach((x) => x.classList.toggle("on", x === b)); }));
    const inp = el.querySelector(".ub input");
    let pre = null;
    inp.onpointerdown = () => { pre = this.ser(); };
    inp.oninput = () => { el.querySelector(".ub b").textContent = f2(+inp.value); this.setStrength(+inp.value); };
    inp.onchange = () => { if (pre != null && this.ser() !== pre) { this.undo.push(pre); this.redo.length = 0; } pre = null; };
    el.querySelector(".inv").onclick = () => { this.snapshot(); this.K = this.K.length ? this.K.map((k) => [k[0], 1 - k[1]]) : [[0, 1]]; this.sel = null; this.commit(); };
    el.querySelector(".clr").onclick = () => { if (!this.K.length) return; this.snapshot(); this.K = []; this.sel = null; this.commit(); };
    this.au.addEventListener("play", () => { document.querySelectorAll("audio").forEach((o) => { if (o !== this.au) o.pause(); }); this.tick(); });
    this.au.addEventListener("loadedmetadata", () => { if (!this.dur && isFinite(this.au.duration)) { this.dur = this.au.duration; this.span(); } });
    el.addEventListener("keydown", (e) => this.key(e));
  }
  ser() { return this.lane.ser(); }
  snapshot() { this.undo.push(this.ser()); if (this.undo.length > 80) this.undo.shift(); this.redo.length = 0; }
  load(v) { try { const d = JSON.parse(v || "[]"); this.K = (Array.isArray(d) ? d : d.strength || []).map((k) => [+k[0], +k[1]]); this.K.sort((a, b) => a[0] - b[0]); } catch (e) { /* keep */ } }
  setKeys(keys) { this.K = (keys || []).map((k) => [+k[0], +k[1]]); this.sel = null; this.redraw(); }
  setLength(len) { if (Math.abs((this.len || 0) - (len || 0)) > 1e-3) { this.len = len || 0; this.span(); } }
  commit() { this.lane.commit(null); }                 // → changed() via the lane's onCommit
  changed() { if (this.o.onChange) this.o.onChange(this.K.map((k) => k.slice())); this.redraw(); }
  history(from, to) { if (!from.length) return; to.push(this.ser()); this.load(from.pop()); this.sel = null; this.commit(); }
  span() {
    let T = Math.max(this.dur || 0, this.len || 0, 1);
    if (this.dur) T = Math.max(T, Math.min(this.o.maxLen, T + Math.max(8, 0.25 * this.dur)));
    this.T = T; this.tl.setLength(T); this.redraw();
  }
  async setSource(url) {
    if (url === this.url) return;
    this.url = url; this.dur = 0; this.img = null; this.au.pause();
    if (!url) { this.au.removeAttribute("src"); this.span(); return; }
    this.au.src = url;
    try {
      const r = await fetch(`/sa3/inspec?u=${encodeURIComponent(url)}`);
      if (this.url !== url || !r.ok) return;
      this.dur = +r.headers.get("X-Sa3-Duration") || this.dur;
      const im = new Image();
      im.src = URL.createObjectURL(await r.blob());
      await im.decode();
      if (this.url === url) this.img = im;
    } catch (e) { console.warn("[sa3] strength editor: no spectrogram", e); }
    this.span();
  }
  // ── playback ──
  toggle(a = null, b = null) { if (this.au.paused) this.play(a, b); else this.au.pause(); }
  play(a = null, b = null) { if (a != null) this.au.currentTime = Math.min(a, Math.max(0, this.dur - 0.05)); this.stopAt = b; this.au.play().catch(() => {}); }
  seek(t) { this.au.currentTime = Math.min(t, this.dur || 0); this.tl.setPlayhead(t); }
  tick() {
    const t = this.au.currentTime || 0;
    this.tl.setPlayhead(t);
    this.el.querySelector(".tm").textContent = `${fmt(t)} / ${fmt(this.dur || 0)}`;
    this.el.querySelector(".play").textContent = this.au.paused ? "▶" : "■";
    if (this.stopAt != null && t >= this.stopAt) { this.au.pause(); this.stopAt = null; }
    if (!this.au.paused) requestAnimationFrame(() => this.tick());
  }
  // ── gestures: the shared curve lane (curvelane.js) ──
  get drag() { return this.lane.drag; }
  get K() { return this.lane.K; }
  set K(v) { this.lane.K = v; }
  get sel() { return this.lane.sel; }
  set sel(v) { this.lane.sel = v; }
  get brush() { return this.lane.brush; }
  set brush(v) { this.lane.brush = v; }
  get mode() { return this.lane.mode; }
  set mode(v) { this.lane.mode = v; }
  selBox() { return this.lane.selBox(); }
  setStrength(u) { this.lane.setStrength(u, { record: false }); }   // the slider's onchange records the undo step
  pointer(kind, e, p) { this.lane.pointer(kind, e, p); }
  key(ev) {
    const k = ev.key, b = this.selBox();
    if (k === " ") { ev.preventDefault(); return b ? this.toggle(b.a, b.b) : this.toggle(); }
    if ((ev.metaKey || ev.ctrlKey) && k.toLowerCase() === "z") { ev.preventDefault(); return ev.shiftKey ? this.history(this.redo, this.undo) : this.history(this.undo, this.redo); }
    this.lane.key(ev);
  }
  // ── painting ──
  redraw() {
    this.tl.redraw();
    const L = this.len || this.T || 0, n = Math.ceil(L / C.SPL);
    let full = 0, soft = 0;
    for (let f = 0; f < n; f++) { const u = C.at(this.K, (f + 0.5) * C.SPL); if (u >= 1) full++; else if (u > 0) soft++; }
    this.el.querySelector(".sum").textContent = this.lane.label || (this.K.length ? [full && `${fmt(full * C.SPL)} regenerated`, soft && `${fmt(soft * C.SPL)} soft`, `${fmt(Math.max(0, n - full - soft) * C.SPL)} kept`].filter(Boolean).join(" · ")
      : "No curve: the reference is unused · drag across the spectrogram to draw one");
    const inp = this.el.querySelector(".ub input");
    if (document.activeElement !== inp) {
      let u = this.brush;
      const b = this.selBox();
      if (this.sel && this.sel.kind === "k" && this.K[this.sel.j]) u = this.K[this.sel.j][1]; else if (b) u = b.u;
      inp.value = u; this.el.querySelector(".ub b").textContent = f2(u);
    }
  }
  paint(g, r, tl) {
    const x0 = tl.x(0);
    if (this.img && this.dur) g.drawImage(this.img, x0, r.y, tl.x(this.dur) - x0, r.h);
    if (this.dur && this.dur < this.T) { g.fillStyle = "rgba(255,255,255,0.03)"; g.fillRect(tl.x(this.dur), r.y, tl.x(this.T) - tl.x(this.dur), r.h); }
    if (this.len && this.len < this.T - 1e-3) {
      const xl = tl.x(this.len);
      g.fillStyle = "rgba(9,9,13,0.55)"; g.fillRect(xl, r.y, tl.x(this.T) - xl, r.h);
      g.setLineDash([4, 4]); g.strokeStyle = "#ffb86b"; g.beginPath(); g.moveTo(xl, r.y); g.lineTo(xl, r.y + r.h); g.stroke(); g.setLineDash([]);
    }
    this.lane.paint(g, r, tl);
  }
}
