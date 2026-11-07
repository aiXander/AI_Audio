// SA3 UI kit: a strength curve edited on one lane of a timeline.js timeline (spec 4.4). The gestures,
// snapping and drawing of every strength-curve editor live here; curve.js holds the rules. Used by
// strength.js (the studio's Inpaint strength editor) and the Composer's Rewrite lane.
//
//   const cl = new CurveLane(tl, {onCommit(K, before), onSeek(t), focus(),
//                                 magnets: () => [t, …], end: () => T, mode: "span" | "point",
//                                 brushFromHeight: false, closeEnds: false})
//   lane = {…, pointer: (k, e, p) => cl.pointer(k, e, p), draw: (g, r, t) => cl.paint(g, r, t)}
//
// Gestures, DAW-automation style (Xander: clicks must never sprout points):
//   drag a point          time + strength. A hard edge is two keys at one time: only its top point
//                         is drawn and grabbed, and its hidden partner moves with it, so edges stay edges
//   drag a line           that segment up / down (a plateau's top: its strength)
//   drag inside a plateau move it sideways (⌥: duplicate)
//   drag on empty space   draw a plateau (at `brush`, or at the height you pressed: brushFromHeight)
//   click on empty space  deselect + seek (point mode: add a point there, the studio editor's Points)
//   double-click          on a point: delete it; anywhere else: add a point at the cursor
// key(ev): ⌫ deletes the selection, ←/→ moves it one latent frame (⇧: 1 s), ↑/↓ its strength by 0.05.
// Times snap to the latent grid and magnet to keys + the page's magnets, strengths to quarters;
// ⇧ while dragging = free. closeEnds keeps the curve at 0 before its first and after its last key
// (curve.js reads flat outside the keys: one lone key would mean "everywhere"). onCommit(K, before)
// fires once per finished edit with the normalised keys and the serialized state before it (undo).

import * as C from "./curve.js";

const fmt = (t) => { const m = Math.floor(t / 60), s = t - m * 60; return m ? `${m}:${s.toFixed(1).padStart(4, "0")}` : `${s.toFixed(1)}s`; };
const f2 = (u) => u.toFixed(2);
const FIRST_W = 2;                                  // a first point added into an empty curve: a peak this wide each side (s)
const R_KEY = 10, R_SEG = 6, DRAG = 5;               // grab radii + the drag threshold (px)

export class CurveLane {
  constructor(tl, o = {}) {
    this.tl = tl;
    this.o = { mode: "span", brushFromHeight: false, closeEnds: false, magnets: () => [], end: () => tl.length, ...o };
    this.K = []; this.brush = 1; this.mode = this.o.mode; this.sel = null; this.drag = null; this.label = null; this.hot = null;
  }
  get T() { return Math.max(1e-3, this.o.end()); }
  ser() { return JSON.stringify(this.K.map((k) => [C.r3(k[0]), C.r4(k[1])])); }
  setKeys(K) { if (this.drag) return; this.K = (K || []).map((k) => [+k[0], +k[1]]); if (this.sel && this.sel.kind === "k" && !this.K[this.sel.j]) this.sel = null; }
  commit(before) {
    let K = this.K;
    if (this.o.closeEnds && K.length) {
      if (K[0][1] > 0) K = [[K[0][0], 0], ...K];
      if (K[K.length - 1][1] > 0) K = [...K, [K[K.length - 1][0], 0]];
    }
    const sel = this.selKey() ? this.selKey().slice() : null;
    this.K = C.norm(K);
    if (sel) { const j = this.K.findIndex((k) => k[0] === C.r3(sel[0]) && k[1] === C.r4(sel[1])); this.sel = j >= 0 ? { kind: "k", j } : null; }
    if (this.o.onCommit) this.o.onCommit(this.K.map((k) => k.slice()), before);
    this.tl.redraw();
  }
  // ── snapping ──
  snapT(t, skip, free) {
    if (free) return t;
    const m = [0, ...this.o.magnets(), ...this.K.map((k) => k[0]).filter((x) => !skip.includes(x))];
    const spp = (this.tl.v1 - this.tl.v0) / Math.max(1, this.tl.W);
    let best = null, bd = 7 * spp;
    for (const v of m) { const d = Math.abs(v - t); if (v >= 0 && d < bd) { bd = d; best = v; } }
    return best != null ? best : Math.round(t / C.SPL) * C.SPL;
  }
  snapU(u, free, h) {
    if (free) return C.clamp(u, 0, 1);
    const tol = 6 / Math.max(1, h);
    for (const v of [0, 0.25, 0.5, 0.75, 1, this.brush]) if (Math.abs(u - v) < tol) return v;
    return C.clamp(Math.round(u * 100) / 100, 0, 1);
  }
  // ── selection ──
  selBox() { return this.sel && this.sel.kind === "p" ? C.boxes(this.K).find((b) => Math.abs(b.a - this.sel.a) < 1e-3) || null : null; }
  selKey() { return this.sel && this.sel.kind === "k" ? this.K[this.sel.j] || null : null; }
  group(j) {                                         // j plus its hard-edge partners (keys at the same time)
    let a = j, b = j;
    while (a > 0 && Math.abs(this.K[a - 1][0] - this.K[j][0]) < 1e-6) a--;
    while (b + 1 < this.K.length && Math.abs(this.K[b + 1][0] - this.K[j][0]) < 1e-6) b++;
    return [a, b];
  }
  hidden(j) {                                        // the 0-corner under a hard edge: not drawn, not grabbed
    const [a, b] = this.group(j);
    return b > a && this.K[j][1] < Math.max(...this.K.slice(a, b + 1).map((k) => k[1]));
  }
  setStrength(u, { record = true } = {}) {           // the selection's strength, else the brush for new plateaus
    u = C.clamp(u, 0, 1);
    const before = record ? this.ser() : null, k = this.selKey(), b = this.selBox();
    if (k) { k[1] = u; return this.commit(before); }
    if (b) { this.K = C.setRange(this.K, b.a, b.b, u); if (u <= 0) this.sel = null; return this.commit(before); }
    this.brush = u; this.tl.redraw();
  }
  del() {
    const b = this.selBox(), k = this.selKey();
    if (!b && !k) return false;
    const before = this.ser();
    if (b) this.K = C.heal(this.K, b.a, b.b); else this.K.splice(this.sel.j, 1);
    this.sel = null; this.commit(before);
    return true;
  }
  moveKey(j, t) {                                    // key j (and its edge partners) to time t, between its neighbours
    const [a, b] = this.group(j);
    const lo = a > 0 ? this.K[a - 1][0] : 0, hi = b + 1 < this.K.length ? this.K[b + 1][0] : this.T;
    t = C.clamp(t, lo, hi);
    for (let i = a; i <= b; i++) this.K[i][0] = t;
  }
  nudge(dt) {
    const b = this.selBox(), kk = this.selKey();
    if (!b && !kk) return false;
    const before = this.ser();
    if (b) { const w = b.b - b.a, a = C.clamp(b.a + dt, 0, this.T - w); this.K = C.setRange(C.heal(this.K, b.a, b.b), a, a + w, b.u); this.sel = { kind: "p", a: C.r3(a) }; }
    else this.moveKey(this.sel.j, kk[0] + dt);
    this.commit(before);
    return true;
  }
  nudgeU(du) {
    const b = this.selBox(), kk = this.selKey();
    if (!b && !kk) return false;
    this.setStrength(Math.round(((kk ? kk[1] : b.u) + du) * 100) / 100);
    return true;
  }
  key(ev) {                                          // ⌫ / ←→ / ↑↓ on the selection; true when handled
    const k = ev.key;
    if (k === "Escape" && this.sel) { this.sel = null; this.tl.redraw(); return true; }
    let done = false;
    if (k === "Backspace" || k === "Delete") done = this.del();
    else if (k === "ArrowLeft" || k === "ArrowRight") done = this.nudge((k === "ArrowLeft" ? -1 : 1) * (ev.shiftKey ? 1 : C.SPL));
    else if (k === "ArrowUp" || k === "ArrowDown") done = this.nudgeU(k === "ArrowUp" ? 0.05 : -0.05);
    if (done) ev.preventDefault();
    return done;
  }
  addKey(t, u, before) {
    if (this.o.closeEnds && C.at(this.K, t) <= 0 && C.before(this.K, t) <= 0) {
      // into an empty stretch: a small peak, its shoulders kept clear of the neighbouring keys
      const lo = Math.max(0, ...this.K.map((k) => k[0]).filter((x) => x < t)), hi = Math.min(this.T, ...this.K.map((k) => k[0]).filter((x) => x > t));
      const w = Math.max(C.SPL, Math.min(FIRST_W, (t - lo) / 2, (hi - t) / 2));
      this.K.push([Math.max(0, t - w), 0], [t, u], [t + w, 0]);
    } else this.K.push([t, u]);                      // on the curve: a new bend in it
    this.K.sort((a, b) => a[0] - b[0]);            // stable: a hard edge keeps its before → after order
    this.sel = { kind: "k", j: this.K.findIndex((k) => k[0] === t && k[1] === u) };
    this.commit(before);
  }
  // ── hit testing (screen px) ──
  ky(r, u) { return r.y + (1 - u) * r.h; }
  hitKey(p) {
    let best = -1, bd = R_KEY;
    this.K.forEach((k, j) => {
      if (this.hidden(j)) return;
      const d = Math.hypot(p.px - this.tl.x(k[0]), p.py - this.ky(p.rect, k[1]));
      if (d <= bd) { bd = d; best = j; }
    });
    return best;
  }
  hitSeg(p) {                                        // a line between two keys (not a vertical edge, not the floor)
    let best = -1, bd = R_SEG;
    for (let i = 0; i + 1 < this.K.length; i++) {
      const a = this.K[i], b = this.K[i + 1];
      if (b[0] - a[0] < 1e-4 || (a[1] <= 0 && b[1] <= 0)) continue;
      const x1 = this.tl.x(a[0]), y1 = this.ky(p.rect, a[1]), x2 = this.tl.x(b[0]), y2 = this.ky(p.rect, b[1]);
      if (p.px < x1 - 2 || p.px > x2 + 2) continue;
      const f = C.clamp(((p.px - x1) * (x2 - x1) + (p.py - y1) * (y2 - y1)) / Math.max(1e-6, (x2 - x1) ** 2 + (y2 - y1) ** 2), 0, 1);
      const d = Math.hypot(p.px - (x1 + f * (x2 - x1)), p.py - (y1 + f * (y2 - y1)));
      if (d <= bd) { bd = d; best = i; }
    }
    return best;
  }
  hitBody(p) {                                       // inside a plateau's fill
    return C.boxes(this.K).find((b) => p.px > this.tl.x(b.a) && p.px < this.tl.x(b.b) && p.py > this.ky(p.rect, b.u) + R_SEG) || null;
  }
  target(p) {
    const j = this.hitKey(p);
    if (j >= 0) return { kind: "key", j };
    const i = this.hitSeg(p);
    if (i >= 0) return { kind: "seg", i };
    const b = this.hitBody(p);
    if (b) return { kind: "body", b };
    return { kind: "empty" };
  }
  // ── gestures (timeline lane pointer hook) ──
  pointer(kind, e, p) {
    const free = e.shiftKey, h = p.rect.h;
    if (kind === "hover") {
      const tg = this.target(p);
      const was = JSON.stringify(this.hot);
      this.hot = tg.kind === "key" ? { j: tg.j } : tg.kind === "seg" ? { i: tg.i } : tg.kind === "body" ? { b: tg.b.a } : null;
      this.tl.cv.style.cursor = { key: "grab", seg: "ns-resize", body: "ew-resize", empty: "crosshair" }[tg.kind];
      this.tl.cv.title = {
        key: `${f2(this.K[tg.j] ? this.K[tg.j][1] : 0)} at ${fmt(this.K[tg.j] ? this.K[tg.j][0] : 0)}: drag to move · double-click or ⌫ deletes`,
        seg: "drag up / down: strength of this stretch",
        body: "drag: move this span sideways (⌥: duplicate)",
        empty: this.mode === "point" ? "click: add a point · drag: draw a span" : "drag: draw a span (height = strength) · double-click: add a point · click: move the playhead",
      }[tg.kind];
      if (JSON.stringify(this.hot) !== was) this.tl.redraw();
      return;
    }
    if (kind === "dbl") {
      const j = this.hitKey(p), before = this.ser();
      if (j >= 0) { this.K.splice(j, 1); this.sel = null; return this.commit(before); }
      if (this.mode !== "point") return this.addKey(this.snapT(p.t, [], free), this.snapU(p.v, free, h), before);
      return;
    }
    if (kind === "down") {
      if (this.o.focus) this.o.focus();
      const before = this.ser(), tg = this.target(p);
      const d0 = { x0: p.px, y0: p.py, v0: p.v, t0: p.t, moved: false, before };
      if (tg.kind === "key") { this.sel = { kind: "k", j: tg.j }; this.drag = { ...d0, mode: "key", j: tg.j }; }
      else if (tg.kind === "seg") {
        const a = this.K[tg.i], b = this.K[tg.i + 1];
        this.sel = a[1] === b[1] ? { kind: "p", a: C.r3(a[0]) } : { kind: "k", j: tg.i };
        this.drag = { ...d0, mode: "seg", i: tg.i, u0: a[1], u1: b[1] };
      } else if (tg.kind === "body") {
        this.sel = { kind: "p", a: tg.b.a };
        const base = e.altKey ? this.K.map((k) => k.slice()) : C.heal(this.K, tg.b.a, tg.b.b);
        this.drag = { ...d0, mode: "move", b: tg.b, base };
      } else if (this.mode === "point") {
        const u = this.snapU(p.v, free, h), t = this.snapT(p.t, [], free);
        this.K.push([t, u]); this.K.sort((a, b) => a[0] - b[0]);
        this.sel = { kind: "k", j: this.K.findIndex((k) => k[0] === t && k[1] === u) };
        this.drag = { ...d0, mode: "key", j: this.sel.j, moved: true };
      } else {
        const u = this.o.brushFromHeight ? Math.max(0.05, this.snapU(p.v, free, h)) : this.brush;
        this.drag = { ...d0, mode: "new", u, base: this.K.map((k) => k.slice()), ts: this.snapT(p.t, [], free) };
      }
      this.tl.redraw();
      return;
    }
    const d = this.drag;
    if (!d) return;
    if (kind === "move") {
      if (!d.moved && Math.abs(p.px - d.x0) < DRAG && Math.abs(p.py - d.y0) < DRAG) return;
      d.moved = true;
      if (d.mode === "new") {
        const t = this.snapT(p.t, [], free), a = Math.min(d.ts, t), b = Math.max(d.ts, t);
        this.K = b - a >= C.SPL ? C.setRange(d.base, a, b, d.u) : d.base.map((k) => k.slice());
        this.sel = b - a >= C.SPL ? { kind: "p", a: C.r3(a) } : null;
        this.label = `${f2(d.u)} · ${fmt(a)} – ${fmt(b)}`;
      } else if (d.mode === "key") {
        const k = this.K[d.j];
        if (!k) return;
        this.moveKey(d.j, this.snapT(p.t, [k[0]], free));
        k[1] = this.snapU(p.v, free, h);
        this.label = `${f2(k[1])} at ${fmt(k[0])}`;
      } else if (d.mode === "seg") {
        const dv = p.v - d.v0, a = this.K[d.i], b = this.K[d.i + 1];
        if (d.u0 === d.u1) a[1] = b[1] = this.snapU(d.u0 + dv, free, h);
        else { a[1] = C.clamp(d.u0 + dv, 0, 1); b[1] = C.clamp(d.u1 + dv, 0, 1); }
        this.label = d.u0 === d.u1 ? f2(a[1]) : `${f2(a[1])} → ${f2(b[1])}`;
      } else if (d.mode === "move") {
        const { b, base } = d, w = b.b - b.a;
        let s = C.clamp(b.a + (p.t - d.t0), 0, this.T - w);
        const sa = this.snapT(s, [b.a, b.b], free), sb = this.snapT(s + w, [b.a, b.b], free);
        s = Math.abs(sa - s) <= Math.abs(sb - (s + w)) ? sa : sb - w;
        const a = Math.max(0, s), z = Math.min(this.T, s + w);
        this.K = C.setRange(base, a, z, b.u);
        this.sel = { kind: "p", a: C.r3(a) };
        this.label = `${fmt(a)} – ${fmt(z)}`;
      }
      this.tl.redraw();
      return;
    }
    if (kind === "up") {
      this.drag = null; this.label = null;
      if (!d.moved) {
        if (d.mode === "new") { this.sel = null; if (this.o.onSeek) this.o.onSeek(p.t); }
        this.tl.redraw();
        return;
      }
      this.commit(this.ser() !== d.before ? d.before : null);
    }
  }
  // ── painting: the curve per latent frame (exactly what the sampler gets), the points, the hover ──
  paint(g, r, tl, { alpha = 0.22 } = {}) {
    if (!this.K.length) return;
    const x0 = tl.x(0), T = this.T, n = Math.ceil(T / C.SPL);
    const grad = g.createLinearGradient(0, r.y + r.h, 0, r.y);
    grad.addColorStop(0, C.ucol(0)); grad.addColorStop(0.5, C.ucol(0.5)); grad.addColorStop(1, C.ucol(1));
    const f0 = Math.max(0, Math.floor(tl.v0 / C.SPL) - 1), f1 = Math.min(n, Math.ceil(tl.v1 / C.SPL) + 1);
    g.beginPath(); g.moveTo(Math.max(x0, tl.x(f0 * C.SPL)), r.y + r.h);
    for (let f = f0; f < f1; f++) {
      const u = C.at(this.K, (f + 0.5) * C.SPL), y = this.ky(r, u);
      g.lineTo(tl.x(f * C.SPL), y); g.lineTo(tl.x(Math.min(T, (f + 1) * C.SPL)), y);
    }
    g.lineTo(tl.x(Math.min(T, f1 * C.SPL)), r.y + r.h); g.closePath();
    g.globalAlpha = alpha; g.fillStyle = grad; g.fill(); g.globalAlpha = 1;
    g.strokeStyle = grad; g.lineWidth = 1.5; g.stroke();
    const hot = this.drag ? null : this.hot, sb = this.selBox();
    const seg = (i, col, w) => {                     // highlight one segment
      const a = this.K[i], b = this.K[i + 1];
      if (!a || !b) return;
      g.strokeStyle = col; g.lineWidth = w;
      g.beginPath(); g.moveTo(tl.x(a[0]), this.ky(r, a[1])); g.lineTo(tl.x(b[0]), this.ky(r, b[1])); g.stroke();
    };
    if (sb) seg(sb.i, "#fff", 2.5);
    if (hot && hot.i != null) seg(hot.i, "rgba(255,255,255,0.75)", 3);
    if (hot && hot.b != null) {
      const b = C.boxes(this.K).find((x) => Math.abs(x.a - hot.b) < 1e-6);
      if (b) { g.fillStyle = "rgba(255,255,255,0.06)"; g.fillRect(tl.x(b.a), this.ky(r, b.u), tl.x(b.b) - tl.x(b.a), r.y + r.h - this.ky(r, b.u)); }
    }
    this.K.forEach((k, j) => {
      if (this.hidden(j)) return;
      const x = tl.x(k[0]), y = this.ky(r, k[1]);
      const on = this.sel && this.sel.kind === "k" && this.sel.j === j, hv = hot && hot.j === j;
      g.fillStyle = C.ucol(k[1]); g.strokeStyle = on ? "#fff" : hv ? "rgba(255,255,255,0.8)" : "rgba(9,9,13,0.9)"; g.lineWidth = on || hv ? 2 : 1;
      g.beginPath(); g.arc(x, y, on || hv ? 5.5 : 4, 0, 7); g.fill(); g.stroke();
    });
    if (this.label) {
      g.font = "11px JetBrains Mono, monospace"; g.textBaseline = "top";
      const w = g.measureText(this.label).width + 10;
      g.fillStyle = "rgba(9,9,13,0.85)"; g.fillRect(r.x + r.w - w - 4, r.y + 3, w, 16);
      g.fillStyle = "#e8e8f0"; g.fillText(this.label, r.x + r.w - w + 1, r.y + 5);
    }
  }
}
