// SA3 UI kit: a canvas timeline: ruler (seconds, or bars + beats on a BPM grid), stacked lanes
// of regions, playhead, zoom / pan, snapping. Pages own the data; the timeline draws what they
// hand it and reports gestures back, so one edit path (the page's) stays the source of truth.
//
//   const tl = new Timeline(host, {gutter, onSeek(t), onSelect(item|null, lane, event), onKey(e), free,
//                                  onMarquee(ids, event), onGroupMove(ids, dt)})
//   free: true = an open canvas (the Composer): zoom out and pan as far as you like (to 2 h), and
//   the view never re-fits by itself when the length changes (only tl.fit() does); default false =
//   the view is clamped around the content and follows its length (the studio's strength editor).
//   tl.setLanes([{key, label, sub, flex | h, items: [...], draw(ctx, rect, tl), over(ctx, rect, tl),
//                 onDraw(t0, t1, event) (drag on empty space makes a region), onEdit(item, t0, t1, kind),
//                 onItemClick(item, t, event), marquee}])
//   tl.setLength(L)  tl.fit()  tl.setGrid(bpm | null)  tl.setPlayhead(t | null)  tl.redraw()
//
// Tooltips: a lane's `tip` shows on its gutter label, an item's `tip` (else its label) over the item,
// its `badgeTip` over its badge dots.
// An item is {id, t0, t1, label, tip, color, move, resize ("lr" | "l" | "r" | ""), draw(ctx, rect, tl),
// under, badges: ["ok" | "warn" | "bad", …]}: `under` marks a backdrop (a base clip): on a lane with
// onDraw, drags over it draw; `badges` draw as dots in the item's top-right corner (Fit panel).
// A lane may also carry `markers: [{id, t, label, tip, color, cut, shape, free}]` (keypoints: diamonds on
// the lane, drag to move → onMarker(marker, t), click → onMarkerClick(marker, event)); `shape: "fade"`
// draws a ⟷ pill along the lane's top instead (a handle, e.g. a crossfade's length: the page reads
// the dragged distance), `shape: "grip"` a ⠿ pill along the lane's bottom (a handle that moves a whole
// span, e.g. a conditioning block: again the page reads the distance),
// `free: true` drags without snapping or the guide line. `onClick(t, event)`
// While a marker is dragged a guide line runs through every lane (and its time shows in the ruler),
// so it lines up with what the other lanes show. `snapTo: [t, …] | () => [t, …]` on that lane are
// edges it snaps to first (clip starts / ends), from SNAP_PX away, ahead of the beat grid; the
// guide turns green while it sits on one. ⇧ drags freely.
// (a click on empty lane space; default: seek). Only the primary button starts a gesture: a right-click
// is the page's contextmenu, never a click / keypoint. `tipAt(t)` on a lane replaces its native body
// tooltip with the kit's own, shown after the pointer rests HOVER_MS (a marker's `tip` wins there).
// A lane with `pointer(kind, event, {t, v, rect})`
// owns its gestures: "down" / "move" / "up" / "hover" / "dbl" arrive with the time under the cursor
// and v = 0 (lane bottom) .. 1 (top); the timeline only draws (the strength curve editor, strength.js).
// The gutter (the lane's label column) is the page's too when it asks: `gutter(ctx, rect, tl)` draws
// over the label (a play button: the Composer's take lanes), `onGutter(event, rect)` gets its clicks.
// With `claim({t, v, rect, px, py}) → bool` as well, the lane's pointer gets only the gestures it claims
// (hover / down / dbl where claim says yes, e.g. on a volume curve's points and lines); everything else
// on that lane (its items, markers, empty space) behaves as on any lane. claim runs on every hover.
// While an item is dragged the timeline draws it at the preview position; onEdit fires once, on
// release, with the snapped times (the page applies it and calls setLanes again).
// onEdit's kind is "move" | "l" | "r" (an edge: the page trims), or "ls" | "rs" when the item has
// `stretch: true` and ⌥ is held at release (the page time-stretches); the item's draw gets the preview
// rect with `kind`, so a trim can draw a moving crop and a stretch a squeezed one. A click on an item
// that never moved goes to the lane's onItemClick with the time under the cursor. An item's `span:
// [t0, t1]` is how far a trim may reach (where its source audio runs out); a stretch ignores it.
// Multi-selection: the page owns it and hands it over as `tl.multi` (a Set of item ids, drawn selected
// like `tl.sel`). A lane with `marquee: true` turns a drag on its empty space into a rubber band:
// onMarquee(ids) with every item of a marquee lane the band touches. Dragging an item that is in
// tl.multi (with others) drags every movable item of the set by the same snapped Δt (none before 0),
// and onGroupMove(ids, dt, dl) fires instead of onEdit.
// Lanes that share an `xdrag` key form a group a moved item (or set) can be dragged across: it previews
// on the group lane nearest the pointer, onEdit gets that lane as a 5th argument (only when it changed)
// and onGroupMove's dl is the shift in group-lane index (a set keeps its shape inside the group).
// A purely vertical drag keeps the item's time (no snap).
// Snap: whole beats on a grid, else 0.05 s; hold ⇧ to drag freely.
// Lanes with `scroll: true` (last, each with `h`) stack in a region at the bottom that scrolls
// vertically once they outgrow it (a wheel over it scrolls up / down; a scrollbar on the right);
// tl.revealLane(key) scrolls one into view. (The Composer's take lanes: no limit on how many.)
// Resizable lanes: a lane with `onResize(h | null, final)` gets a grab edge along its bottom, in the
// gutter (row-resize cursor). Dragging it sets the lane's `h`, and that of every lane sharing its
// `rgroup` key (one height for the group: the Composer's tracks, its takes); onResize fires live
// (final = false) and once more on release (final = true), so the page keeps the height; a
// double-click on the edge sends null (the page's default). Clamped to the lane's `minH` (24) and to
// what still fits: flex lanes keep FLEX_MIN, the scroll region min(SCROLL_MIN, its content); scroll
// lanes grow up to `maxH` (320). Once every lane outside the scroll region has a fixed `h`, the scroll
// region takes all the height left under them (and the fixed lanes shrink proportionally when the
// canvas gets too short for them).
// Wheel: ⌘/ctrl (or pinch) zooms at the cursor, otherwise pans. Click the ruler to seek.
// Section: with `onRange(r | null)` a drag on the ruler marks a time range (snapped; drawn blue across
// every lane): drag its edges in the ruler to adjust it, its × clears it; onRange fires on release
// and on clear. tl.setRange([t0, t1] | null) sets it from the page (no callback). Without onRange a
// ruler drag scrubs the playhead. `rangePast: s` lets a section run up to s seconds past the end.

const RULER = 26;
const EDGE = 7;
const SNAP_PX = 12;                                               // a marker drag catches a lane's snapTo edges from this far
const HOVER_MS = 700;
const SCROLL_FRAC = 0.42;                                         // the scroll region's share of the canvas, at most
const SCROLL_MIN = 150;
const FLEX_MIN = 40;                                              // a flex lane's floor while a lane above / below is resized                                             // a tipAt lane's tooltip waits this long

export class Timeline {
  constructor(host, opts = {}) {
    this.o = { gutter: 96, ...opts };
    this.host = host;
    this.cv = document.createElement("canvas");
    this.cv.className = "ui-timeline";
    this.cv.style.cssText = "display:block;width:100%;height:100%;touch-action:none";
    host.appendChild(this.cv);
    this.lanes = [];
    this.length = 10;
    this.v0 = 0; this.v1 = 10;
    this.bpm = null;
    this.ph = null;
    this.sel = null;
    this.multi = new Set();
    this.range = null;
    this.drag = null;
    this.hoverT = null;
    this.cursor = "default";
    this.fitted = false;
    new ResizeObserver(() => this.redraw()).observe(host);
    this.cv.addEventListener("pointerdown", (e) => this.down(e));
    this.cv.addEventListener("pointermove", (e) => this.move(e));
    this.cv.addEventListener("pointerleave", () => { this.hoverT = null; this.hEdge = null; this.hoverTip(null); this.redraw(); });
    this.cv.addEventListener("pointerup", (e) => this.finish(e));
    this.cv.addEventListener("pointercancel", (e) => this.finish(e));
    this.cv.addEventListener("wheel", (e) => this.wheel(e), { passive: false });
    this.cv.addEventListener("dblclick", (e) => {
      const [px, py] = this.pos(e), er = this.edgeAt(px, py);
      if (er) { er.lane.onResize(null, true); return; }
      const h = this.hit(px, py);
      if (h.r && h.r.lane.pointer && this.claims(h.r, px, py)) h.r.lane.pointer("dbl", e, this.lanePos(h.r, px, py));
    });
  }

  // ── geometry ──
  get W() { return this.cv.clientWidth; }
  get H() { return this.cv.clientHeight; }
  x(t) { const g = this.o.gutter; return g + ((t - this.v0) / (this.v1 - this.v0)) * (this.W - g - 8); }
  t(x) { const g = this.o.gutter; return this.v0 + ((x - g) / (this.W - g - 8)) * (this.v1 - this.v0); }
  get beat() { return this.bpm ? 60 / this.bpm : null; }
  snap(t, e) {
    if (e && e.shiftKey) return Math.round(t * 1000) / 1000;
    if (this.beat) return Math.round(t / this.beat) * this.beat;
    return Math.round(t * 20) / 20;
  }
  edgeSnap(t, lane, e) {                                        // the nearest of lane.snapTo within SNAP_PX, else null
    if ((e && e.shiftKey) || !lane || !lane.snapTo) return null;
    const pts = typeof lane.snapTo === "function" ? lane.snapTo() : lane.snapTo;
    const x = this.x(t);
    let best = null, bd = SNAP_PX;
    for (const p of pts || []) { const d = Math.abs(this.x(p) - x); if (d <= bd) { bd = d; best = p; } }
    return best;
  }
  // Lanes with `scroll: true` (they must come last and carry `h`) share one region at the bottom that
  // scrolls vertically when they don't fit: at most SCROLL_FRAC of the canvas (at least SCROLL_MIN).
  // Their rects carry `clip: [y0, y1]` (the region) and only visible ones are returned.
  scrollGeo() {
    const scr = this.lanes.filter((l) => l.scroll), total = scr.reduce((s, l) => s + (l.h || 40) + 4, 0);
    const avail = this.H - RULER - 2, fl = this.lanes.filter((l) => !l.scroll);
    const fill = fl.length && fl.every((l) => l.h);               // no flex lane: the region takes what is left
    const vh = !scr.length ? 0 : fill ? Math.max(avail - fl.reduce((s, l) => s + l.h + 4, 0), Math.min(total, SCROLL_MIN))
      : Math.min(total, Math.max(SCROLL_MIN, Math.round(avail * (this.o.scrollFrac || SCROLL_FRAC))));
    const max = Math.max(0, total - vh);
    this.sy = Math.max(0, Math.min(max, this.sy || 0));
    return { scr, total, vh, max, y0: this.H - vh, y1: this.H };
  }
  laneRects() {
    const S = this.scrollGeo(), fl = this.lanes.filter((l) => !l.scroll);
    const fixed = fl.reduce((s, l) => s + (l.h || 0), 0);
    const flex = fl.reduce((s, l) => s + (l.h ? 0 : l.flex || 1), 0);
    const free = Math.max(0, this.H - RULER - S.vh - fixed - 4 * fl.length);
    const k = flex || !fixed ? 1 : Math.max(0.3, Math.min(1, (this.H - RULER - 2 - S.vh - 4 * fl.length) / fixed));   // too short: shrink
    let y = RULER + 2;
    const out = fl.map((l) => {
      const h = l.h ? Math.round(l.h * k) : Math.round((free * (l.flex || 1)) / Math.max(1, flex));
      const r = { lane: l, x: this.o.gutter, y, w: this.W - this.o.gutter - 8, h };
      y += h + 4;
      return r;
    });
    let sy = S.y0 - this.sy;
    for (const l of S.scr) {
      const h = l.h || 40, r = { lane: l, x: this.o.gutter, y: sy, w: this.W - this.o.gutter - 8, h, clip: [S.y0, S.y1] };
      sy += h + 4;
      if (r.y + r.h > S.y0 && r.y < S.y1) out.push(r);
    }
    return out;
  }
  edgeAt(px, py) {                                              // a resizable lane whose bottom edge is under the cursor (gutter only)
    if (px >= this.o.gutter || py < RULER) return null;
    for (const r of this.laneRects()) {
      const y = r.y + r.h;
      if (!r.lane.onResize || (r.clip && (y < r.clip[0] || y > r.clip[1]))) continue;
      if (py >= y - 3 && py <= y + 4) return r;
    }
    return null;
  }
  rgroupOf(l) { return l.rgroup ? this.lanes.filter((x) => x.rgroup === l.rgroup) : [l]; }
  growMax(r) {                                                  // the tallest the lane (and its group) may get
    const grp = this.rgroupOf(r.lane), l = r.lane;
    if (l.scroll) return Math.max(r.h, l.maxH || 320);
    const rects = this.laneRects().filter((q) => !q.clip), S = this.scrollGeo();
    const flex = rects.filter((q) => !q.lane.h && !grp.includes(q.lane));
    const slack = flex.length ? flex.reduce((s, q) => s + q.h - FLEX_MIN, 0)
      : S.scr.length ? S.vh - Math.min(S.total, SCROLL_MIN) : this.H - this.bottomOf(rects) - 2;
    const n = rects.filter((q) => grp.includes(q.lane)).length || 1;
    return r.h + Math.max(0, slack) / n;
  }
  inY(r, py) { return py >= (r.clip ? Math.max(r.y, r.clip[0]) : r.y) && py <= (r.clip ? Math.min(r.y + r.h, r.clip[1]) : r.y + r.h); }
  bottomOf(rects) { return rects.reduce((b, r) => Math.max(b, r.clip ? Math.min(r.y + r.h, r.clip[1]) : r.y + r.h), RULER); }
  revealLane(key) {                                             // scroll the bottom region so lane `key` shows
    const S = this.scrollGeo();
    let y = 0;
    for (const l of S.scr) {
      const h = l.h || 40;
      if (l.key === key) {
        if (y < this.sy) this.sy = y;
        else if (y + h > this.sy + S.vh) this.sy = y + h - S.vh;
        this.redraw();
        return;
      }
      y += h + 4;
    }
  }

  // ── state ──
  setLanes(lanes) { this.lanes = lanes; this.redraw(); }
  setLength(L) {
    const was = this.length;
    this.length = Math.max(0.5, L || 0.5);
    if (this.o.free ? !this.fitted : (this.v1 >= was - 1e-6 || this.v1 > this.length * 1.05 || this.v0 > this.length)) this.fit();
    else this.redraw();
  }
  fit() { this.fitted = true; this.v0 = 0; this.v1 = this.length * 1.02; this.redraw(); }
  setGrid(bpm) { this.bpm = bpm || null; this.redraw(); }
  setPlayhead(t) { this.ph = t; this.redraw(); }
  select(id) { this.sel = id; this.redraw(); }
  setRange(r) { this.range = r ? [r[0], r[1]] : null; this.redraw(); }
  rangeHit(px) {                                                // the section's × / edges, in the ruler
    if (!this.range) return null;
    const xa = this.x(this.range[0]), xb = this.x(this.range[1]);
    if (Math.abs(px - this.rangeX()) <= 8) return "x";
    if (Math.abs(px - xa) <= 5) return "a";
    if (Math.abs(px - xb) <= 5) return "b";
    return null;
  }
  rangeX() { const xb = this.x(this.range[1]); return xb + 22 < this.W ? xb + 11 : xb - 11; }
  redraw() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => { this._raf = 0; this.paint(); });
  }

  // ── hit testing ──
  hit(px, py) {
    for (const r of this.laneRects()) {
      if (!this.inY(r, py)) continue;
      const ms = r.lane.markers || [];
      for (const m of ms) {                                      // a fade pill (top strip) before the diamonds under it
        if (m.shape === "fade" && py <= r.y + 17 && Math.abs(px - this.x(m.t)) <= 13) return { r, it: null, marker: m };
        if (m.shape === "grip" && py >= r.y + r.h - 17 && Math.abs(px - this.x(m.t)) <= 16) return { r, it: null, marker: m };
      }
      for (const m of ms) {
        if (m.shape !== "fade" && m.shape !== "grip" && Math.abs(px - this.x(m.t)) <= 7) return { r, it: null, marker: m };
      }
      for (const it of (r.lane.items || []).slice().reverse()) {
        const a = this.x(it.t0), b = this.x(it.t1);
        if (px < a || px > b) continue;          // handles reach inward only: two items sharing an edge each keep theirs
        const e = Math.min(EDGE, (b - a) / 3);
        let mode = it.move ? "move" : "select";
        const rz = it.resize || "";
        if (rz.includes("l") && px - a <= e) mode = "l";
        else if (rz.includes("r") && b - px <= e) mode = "r";
        return { r, it, mode };
      }
      return { r, it: null };
    }
    return { r: null, it: null };
  }
  claims(r, px, py) { return !r.lane.claim || !!r.lane.claim(this.lanePos(r, px, py)); }
  pos(e) { const b = this.cv.getBoundingClientRect(); return [e.clientX - b.left, e.clientY - b.top]; }
  lanePos(r, px, py) {
    return { t: Math.max(0, Math.min(this.length, this.t(px))), v: Math.max(0, Math.min(1, 1 - (py - r.y) / r.h)), rect: r, px, py };
  }
  // where a page event (a drop) lands: {lane, t} (lane null outside every lane)
  at(e) {
    const [px, py] = this.pos(e);
    const r = this.laneRects().find((q) => this.inY(q, py));
    return { lane: r ? r.lane : null, t: Math.max(0, this.t(px)), px, py };
  }

  down(e) {
    this.hoverTip(null);
    if (e.button !== 0) return;                                  // right / middle button: the page's contextmenu, no gesture
    const [px, py] = this.pos(e);
    const er = this.edgeAt(px, py);
    if (er) {                                                    // a lane's bottom edge: resize it (and its group)
      this.cv.setPointerCapture(e.pointerId);
      this.drag = { kind: "lresize", lane: er.lane, y0: py, h0: er.h, min: er.lane.minH || 24, max: this.growMax(er) };
      return;
    }
    if (px < this.o.gutter) {
      const r = py >= RULER && this.laneRects().find((q) => this.inY(q, py));
      if (r && r.lane.onGutter) r.lane.onGutter(e, r);
      return;
    }
    this.cv.setPointerCapture(e.pointerId);
    if (py < RULER) {                                            // ruler: seek, then scrub (or mark a section)
      const seek = (ev) => { const t = Math.max(0, Math.min(this.length, this.t(this.pos(ev)[0]))); if (this.o.onSeek) this.o.onSeek(t); };
      const rh = this.o.onRange && this.rangeHit(px);
      if (rh === "x") { this.range = null; this.o.onRange(null); this.redraw(); return; }
      if (rh) { this.drag = { kind: "range", fix: this.range[rh === "a" ? 1 : 0], p: this.range.slice() }; return; }
      seek(e);
      this.drag = this.o.onRange ? { kind: "rsel", x0: px, a: this.t(px) } : { kind: "scrub", seek };
      return;
    }
    const h = this.hit(px, py);
    if (h.r && h.r.lane.pointer && this.claims(h.r, px, py)) {
      this.drag = { kind: "lane", lane: h.r.lane, r: h.r };
      h.r.lane.pointer("down", e, this.lanePos(h.r, px, py));
      this.redraw();
      return;
    }
    if (h.marker) {
      this.sel = h.marker.id;
      if (this.o.onSelect) this.o.onSelect({ id: h.marker.id, marker: h.marker }, h.r.lane);
      this.drag = { kind: "marker", m: h.marker, lane: h.r.lane, x0: px, moved: false, ev: e };
      this.redraw();
      return;
    }
    if (h.it && h.it.under && h.r.lane.onDraw) h.it = null;          // a backdrop item (a base clip): drags draw over it
    if (h.it) {
      this.sel = h.it.id;
      if (this.o.onSelect) this.o.onSelect(h.it, h.r.lane, e);       // the page may set tl.multi here
      const d = { kind: h.mode, it: h.it, lane: h.r.lane, x0: px, y0: py, t0: h.it.t0, t1: h.it.t1, moved: false, dl: 0 };
      if (h.mode === "move" && this.multi.size > 1 && this.multi.has(h.it.id)) {   // the set moves as one
        const group = this.lanes.flatMap((l) => (l.items || []).filter((x) => x.move && this.multi.has(x.id)));
        d.gids = new Set(group.map((x) => x.id));
        d.gmin = Math.min(...group.map((x) => x.t0));
      }
      if (h.mode === "move" && h.r.lane.xdrag) {                  // which group lanes the moving items sit on
        const grp = this.xgroup(h.r.lane.xdrag);
        const idx = grp.flatMap((l, i) => (l.items || []).filter((x) => (d.gids ? d.gids.has(x.id) : x.id === h.it.id)).map(() => i));
        d.lmin = Math.min(...idx); d.lmax = Math.max(...idx);
      }
      this.drag = d;
    } else if (h.r) {
      this.sel = null;
      if (this.o.onSelect) this.o.onSelect(null, h.r.lane, e);
      this.drag = { kind: h.r.lane.onDraw ? "draw" : "click", lane: h.r.lane, x0: px, y0: py, a: this.t(px), moved: false };
    }
    this.redraw();
  }

  tip(px, py) {                                                 // native tooltip: lane gutter labels + items
    let s = "";
    if (py < RULER) s = px < this.o.gutter ? "" : !this.o.onRange ? "click or drag: move the playhead"
      : { x: "clear the section", a: "drag: move the section's start", b: "drag: move the section's end" }[this.rangeHit(px)] || "click: move the playhead · drag: mark a section";
    else {
      const r = this.laneRects().find((q) => this.inY(q, py));
      if (r && px < this.o.gutter) s = r.lane.tip || "";
      else if (r && r.lane.tipAt) {                              // the kit's delayed tooltip, not the native one
        const h = this.hit(px, py);
        this.hoverTip(h.marker ? h.marker.tip || h.marker.label || "" : () => r.lane.tipAt(Math.max(0, this.t(px))), px, py);
        s = "";
      }
      else if (r && (!r.lane.pointer || !this.claims(r, px, py))) {   // a claim lane off its curve: its items' tips
        const h = this.hit(px, py), n = h.it ? (h.it.badges || []).length : 0;
        const onDots = n && h.it.badgeTip && py < r.y + 18 && px > this.x(h.it.t1) - 12 - n * 9;   // the badge dots, top-right
        s = onDots ? h.it.badgeTip : h.it ? (h.it.tip || h.it.label || "") : h.marker ? (h.marker.tip || h.marker.label || "") : r.lane.tip || "";
      }
      else { this.hoverTip(null); return; }                      // a pointer lane sets its own
    }
    if (this.cv.title !== s) this.cv.title = s;
    if (!(py >= RULER && px >= this.o.gutter && this.laneRects().some((q) => q.lane.tipAt && this.inY(q, py)))) this.hoverTip(null);
  }

  // the kit's hover tooltip: `text` (a string or () => string) after HOVER_MS at (px, py); null hides it
  hoverTip(text, px, py) {
    clearTimeout(this._tipT);
    if (this._tip) this._tip.hidden = true;
    if (text == null) return;
    this._tipT = setTimeout(() => {
      const s = typeof text === "function" ? text() : text;
      if (!s) return;
      if (!this._tip) {
        this._tip = document.createElement("div");
        this._tip.style.cssText = "position:fixed;z-index:60;max-width:420px;padding:5px 8px;border-radius:6px;background:rgba(12,12,17,0.96);"
          + "border:1px solid rgba(255,255,255,0.14);color:#e8e8f0;font:11.5px/1.4 Inter,sans-serif;white-space:pre-line;pointer-events:none;box-shadow:0 4px 14px rgba(0,0,0,0.5)";
        document.body.appendChild(this._tip);
      }
      this._tip.textContent = s;
      this._tip.hidden = false;
      const b = this.cv.getBoundingClientRect(), w = this._tip.offsetWidth, h = this._tip.offsetHeight;
      this._tip.style.left = Math.max(4, Math.min(innerWidth - w - 4, b.left + px + 12)) + "px";
      this._tip.style.top = Math.max(4, Math.min(innerHeight - h - 4, b.top + py + 16)) + "px";
    }, HOVER_MS);
  }

  move(e) {
    const [px, py] = this.pos(e);
    this.hoverT = px >= this.o.gutter ? this.t(px) : null;
    const d = this.drag;
    if (!d) this.tip(px, py);
    if (d && d.kind === "lane") { d.lane.pointer("move", e, this.lanePos(d.r, px, py)); return; }
    if (d && d.kind === "lresize") {
      d.h = Math.round(Math.max(d.min, Math.min(Math.max(d.min, d.max), d.h0 + py - d.y0)));
      for (const l of this.rgroupOf(d.lane)) l.h = d.h;
      d.lane.onResize(d.h, false);
      this.redraw();
      return;
    }
    if (!d) {
      const h0 = py >= RULER ? this.hit(px, py) : { it: null };
      if (h0.r && h0.r.lane.pointer && this.claims(h0.r, px, py)) { h0.r.lane.pointer("hover", e, this.lanePos(h0.r, px, py)); this.redraw(); return; }
    }
    if (!d) {
      const h = py >= RULER ? this.hit(px, py) : { it: null };
      this.hEdge = this.edgeAt(px, py);
      const c = this.hEdge ? "row-resize" : py < RULER && px >= this.o.gutter ? ({ x: "pointer", a: "ew-resize", b: "ew-resize" }[this.o.onRange && this.rangeHit(px)] || "col-resize") : px < this.o.gutter ? (h.r && h.r.lane.onGutter ? "pointer" : "default") : h.marker ? "grab" : h.it && !(h.it.under && h.r.lane.onDraw) ? ({ l: "ew-resize", r: "ew-resize", move: "grab" }[h.mode] || "pointer")
        : h.r && h.r.lane.onDraw ? "crosshair" : "default";
      if (c !== this.cursor) { this.cursor = c; this.cv.style.cursor = c; }
      this.redraw();
      return;
    }
    if (d.kind === "scrub") return d.seek(e);
    const rmax = this.length + (this.o.rangePast || 0);
    if (d.kind === "rsel") { if (Math.abs(px - d.x0) <= 3) return; d.kind = "range"; d.fix = Math.max(0, Math.min(rmax, this.snap(d.a, e))); }
    if (d.kind === "range") {
      const t = Math.max(0, Math.min(rmax, this.snap(this.t(px), e)));
      d.p = [Math.min(d.fix, t), Math.max(d.fix, t)];
      this.redraw();
      return;
    }
    if (d.kind === "click" && d.lane.marquee && Math.hypot(px - d.x0, py - d.y0) > 4) d.kind = "marquee";
    if (d.kind === "marquee") { d.box = [d.x0, d.y0, px, py]; this.redraw(); return; }
    const vert = d.kind === "move" && d.lane && d.lane.xdrag && Math.abs(py - d.y0) > 6;
    if (Math.abs(px - d.x0) > 3 || vert) d.moved = true;
    if (!d.moved) return;
    const dt = this.t(px) - this.t(d.x0);
    if (d.kind === "marker" && d.m.free) { d.p = [this.t(px)]; this.redraw(); return; }
    if (d.kind === "marker") {
      const t = this.t(px), edge = this.edgeSnap(t, d.lane, e);
      d.snapped = edge != null;
      d.p = [Math.max(0, Math.min(this.length, d.snapped ? edge : this.snap(t, e)))];
      this.redraw();
      return;
    }
    if (d.kind === "move") {
      const len = d.t1 - d.t0;
      let a = Math.abs(px - d.x0) <= 3 ? d.t0 : this.snap(d.t0 + dt, e);   // straight up / down: same time
      if (d.gids) a = Math.max(a, d.t0 - d.gmin);             // no item of the set moves before 0
      d.p = [a, a + len];
      if (d.lane.xdrag) {                                       // across the group: the lane nearest the pointer
        const grp = this.xgroup(d.lane.xdrag), rs = this.laneRects().filter((r) => grp.includes(r.lane));
        const near = rs.reduce((b, r) => (Math.abs(py - (r.y + r.h / 2)) < Math.abs(py - (b.y + b.h / 2)) ? r : b), rs[0]);
        const dl = grp.indexOf(near.lane) - grp.indexOf(d.lane);
        d.dl = Math.max(-d.lmin, Math.min(grp.length - 1 - d.lmax, dl));   // the set keeps its shape inside the group
      }
    } else if (d.kind === "l" || d.kind === "r") {
      d.stretch = !!(e.altKey && d.it.stretch);                 // ⌥ while dragging an edge: stretch
      const [lo, hi] = !d.stretch && d.it.span ? d.it.span : [-Infinity, Infinity];   // a trim stays inside the source
      if (d.kind === "l") d.p = [Math.max(lo, Math.min(this.snap(d.t0 + dt, e), d.t1 - 0.1)), d.t1];
      else d.p = [d.t0, Math.min(hi, Math.max(this.snap(d.t1 + dt, e), d.t0 + 0.1))];
    }
    if (d.kind === "draw") {
      const b = this.snap(this.t(px), e), a = this.snap(d.a, e);
      d.p = [Math.max(0, Math.min(a, b)), Math.min(this.length, Math.max(a, b))];
    }
    this.redraw();
  }

  finish(e) {
    const d = this.drag;
    this.drag = null;
    if (!d) return;
    if (d.kind === "lane") { const [px, py] = this.pos(e); d.lane.pointer("up", e, this.lanePos(d.r, px, py)); this.redraw(); return; }
    if (d.kind === "lresize") { if (d.h != null) d.lane.onResize(d.h, true); this.redraw(); return; }
    if (d.kind === "range") {
      if (d.p && d.p[1] - d.p[0] >= 0.25) { this.range = d.p; this.o.onRange(d.p.slice()); }
      this.redraw();
      return;
    }
    if (d.kind === "rsel") return;
    if (d.kind === "marker") {
      if (d.moved && d.p && d.lane.onMarker) d.lane.onMarker(d.m, d.p[0]);
      else if (!d.moved && d.lane.onMarkerClick) d.lane.onMarkerClick(d.m, e);
    } else if (d.kind === "click" || (d.kind === "draw" && !d.moved)) {
      const t = Math.max(0, Math.min(this.length, d.a));
      if (d.lane && d.lane.onClick) d.lane.onClick(t, e);
      else if (this.o.onSeek) this.o.onSeek(t);
    } else if (d.kind === "marquee") {
      if (d.box && this.o.onMarquee) this.o.onMarquee(this.inBox(d.box), e);
    } else if (d.kind === "draw" && d.p && d.p[1] - d.p[0] > 0.05) d.lane.onDraw(d.p[0], d.p[1], e);
    else if (d.it && !d.moved) { if (d.lane.onItemClick) d.lane.onItemClick(d.it, Math.max(0, Math.min(this.length, this.t(d.x0))), e); }
    else if (d.gids && d.p) { if (this.o.onGroupMove) this.o.onGroupMove([...d.gids], d.p[0] - d.t0, d.dl || 0); }
    else if (d.p && d.lane && d.lane.onEdit && d.it) {
      const grp = d.dl ? this.xgroup(d.lane.xdrag) : null;
      d.lane.onEdit(d.it, d.p[0], d.p[1], d.kind + (d.stretch ? "s" : ""), grp ? grp[grp.indexOf(d.lane) + d.dl] : undefined);
    }
    this.redraw();
  }

  xgroup(key) { return this.lanes.filter((l) => l.xdrag === key); }

  laneItems(l) {                                                // what a lane draws: a cross-lane drag moves items between lanes
    const d = this.drag;
    if (!d || !d.dl || !d.p || !l.xdrag || !d.lane || l.xdrag !== d.lane.xdrag) return l.items || [];
    const grp = this.xgroup(l.xdrag), src = grp[grp.indexOf(l) - d.dl];
    const moving = (it) => (d.gids ? d.gids.has(it.id) : it.id === d.it.id);
    return [...(l.items || []).filter((it) => !moving(it)), ...(src ? (src.items || []).filter(moving) : [])];
  }

  inBox([x0, y0, x1, y1]) {                                     // item ids of marquee lanes a rubber band touches
    const ta = this.t(Math.min(x0, x1)), tb = this.t(Math.max(x0, x1)), ya = Math.min(y0, y1), yb = Math.max(y0, y1);
    return this.laneRects().filter((r) => r.lane.marquee && r.y + r.h >= ya && r.y <= yb)
      .flatMap((r) => (r.lane.items || []).filter((it) => it.t1 > ta && it.t0 < tb).map((it) => it.id));
  }

  wheel(e) {
    e.preventDefault();
    const [px, py] = this.pos(e);
    const SG = this.scrollGeo();
    if (SG.max > 0 && py >= SG.y0 && !(e.ctrlKey || e.metaKey) && Math.abs(e.deltaY) > Math.abs(e.deltaX)) {   // the scroll region: up / down
      this.sy = Math.max(0, Math.min(SG.max, this.sy + e.deltaY));
      this.redraw();
      return;
    }
    if (e.ctrlKey || e.metaKey) {
      const k = Math.exp(e.deltaY * 0.01);
      const tc = this.t(Math.max(this.o.gutter, px));
      let span = Math.min(this.o.free ? 7200 : this.length * 1.5, Math.max(0.5, (this.v1 - this.v0) * k));
      this.v0 = tc - (tc - this.v0) * (span / (this.v1 - this.v0));
      this.v1 = this.v0 + span;
    } else {
      const dt = ((e.deltaX || e.deltaY) / this.W) * (this.v1 - this.v0);
      this.v0 += dt; this.v1 += dt;
    }
    const span = this.v1 - this.v0;
    if (this.v0 < -span * 0.05) { this.v0 = -span * 0.05; this.v1 = this.v0 + span; }
    const hi = this.o.free ? Math.max(7200, this.length + span) : this.length + span * 0.5;
    if (this.v1 > hi) { this.v1 = hi; this.v0 = this.v1 - span; }
    this.redraw();
  }

  // ── painting ──
  paint() {
    const cv = this.cv, dpr = devicePixelRatio || 1, W = this.W, H = this.H;
    if (!W || !H) return;
    if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
    const g = cv.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    const rects = this.laneRects(), dr = this.drag;
    this.mq = dr && dr.kind === "marquee" && dr.box ? new Set(this.inBox(dr.box)) : null;
    this.paintRuler(g, rects);
    for (const r of rects) {
      if (!r.clip) { this.paintLane(g, r); continue; }
      g.save(); g.beginPath(); g.rect(0, r.clip[0], W, r.clip[1] - r.clip[0]); g.clip();
      this.paintLane(g, r);
      g.restore();
    }
    this.paintScroll(g);
    this.paintRange(g, rects);
    if (this.mq) {                                                  // the rubber band
      const [x0, y0, x1, y1] = dr.box;
      g.fillStyle = "rgba(155,140,255,0.10)"; g.fillRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0));
      g.strokeStyle = "#c4bbff"; g.lineWidth = 1; g.setLineDash([4, 3]);
      g.strokeRect(Math.min(x0, x1) + 0.5, Math.min(y0, y1) + 0.5, Math.abs(x1 - x0), Math.abs(y1 - y0));
      g.setLineDash([]);
    }
    if (dr && dr.kind === "marker" && dr.p && !dr.m.free) this.paintGuide(g, rects, dr);
    if (this.ph != null) {
      const x = this.x(this.ph);
      if (x >= this.o.gutter) { g.fillStyle = "#fff"; g.fillRect(x - 0.75, 0, 1.5, H); }
    }
    if (this.hoverT != null && !this.drag) {
      const x = this.x(this.hoverT);
      g.fillStyle = "rgba(255,255,255,0.18)"; g.fillRect(x, RULER, 1, H - RULER);
    }
  }

  paintScroll(g) {                                              // the bottom region's scrollbar + its top edge
    const S = this.scrollGeo();
    if (!S.max) return;
    g.fillStyle = "rgba(255,255,255,0.10)"; g.fillRect(this.o.gutter, S.y0 - 2, this.W - this.o.gutter, 1);
    const th = Math.max(24, (S.vh * S.vh) / S.total), ty = S.y0 + (this.sy / S.max) * (S.vh - th);
    g.fillStyle = "rgba(255,255,255,0.05)"; g.fillRect(this.W - 6, S.y0, 4, S.vh);
    g.fillStyle = "rgba(196,187,255,0.55)"; g.fillRect(this.W - 6, ty, 4, th);
  }

  paintGuide(g, rects, d) {                                     // a dragged marker's line through every lane + its time
    const x = this.x(d.p[0]);
    if (x < this.o.gutter || x > this.W) return;
    const bottom = this.bottomOf(rects);
    g.fillStyle = d.snapped ? "rgba(126,226,160,0.95)" : "rgba(232,232,240,0.55)";
    if (d.snapped) g.fillRect(x - 1, RULER, 2, bottom - RULER);
    else for (let y = RULER; y < bottom; y += 7) g.fillRect(x - 0.5, y, 1, Math.min(4, bottom - y));
    const t = d.p[0], s = t >= 60 ? `${Math.floor(t / 60)}:${(t % 60).toFixed(2).padStart(5, "0")}` : `${t.toFixed(2)} s`;
    g.font = "600 10.5px Inter, sans-serif"; g.textBaseline = "middle";
    const w = g.measureText(s).width + 10, bx = Math.min(this.W - w - 2, Math.max(this.o.gutter, x - w / 2));
    g.fillStyle = d.snapped ? "#7ee2a0" : "#e8e8f0"; g.fillRect(bx, 4, w, RULER - 8);
    g.fillStyle = "#0c0c11"; g.fillText(s, bx + 5, RULER / 2);
  }

  paintRange(g, rects) {                                        // the marked section: blue over the ruler + every lane
    const d = this.drag, r = d && d.kind === "range" && d.p ? d.p : this.range;
    if (!r) return;
    const G = this.o.gutter, bottom = this.bottomOf(rects);
    const xa = this.x(r[0]), xb = this.x(r[1]), a = Math.max(G, xa), b = Math.min(this.W - 8, xb);
    if (b > a) {
      g.fillStyle = "rgba(124,196,255,0.07)"; g.fillRect(a, RULER, b - a, bottom - RULER);
      g.fillStyle = "rgba(124,196,255,0.30)"; g.fillRect(a, 0, b - a, RULER);
      const s = `${(r[1] - r[0]).toFixed(1)} s`;
      g.font = "600 10.5px Inter, sans-serif"; g.textBaseline = "middle";
      if (b - a > g.measureText(s).width + 12) { g.fillStyle = "#dff0ff"; g.fillText(s, a + 6, RULER - 9); }
    }
    g.fillStyle = "rgba(124,196,255,0.9)";
    for (const x of [xa, xb]) if (x >= G && x <= this.W) g.fillRect(x - 0.75, 0, 1.5, bottom);
    if (d && d.kind === "range") return;
    const cx = this.rangeX(), cy = RULER / 2;                     // × clears it
    if (cx < G) return;
    g.fillStyle = "#7cc4ff"; g.beginPath(); g.arc(cx, cy, 7.5, 0, 7); g.fill();
    g.strokeStyle = "#0b0b10"; g.lineWidth = 1.6;
    g.beginPath(); g.moveTo(cx - 3, cy - 3); g.lineTo(cx + 3, cy + 3); g.moveTo(cx + 3, cy - 3); g.lineTo(cx - 3, cy + 3); g.stroke();
  }

  ticks() {
    const span = this.v1 - this.v0, px = this.W - this.o.gutter;
    if (this.beat) {
      const beatPx = (this.beat / span) * px;
      const every = [1, 4, 16, 64, 256, 1024].find((n) => beatPx * n > 14) || 4096;
      return { step: this.beat * every, minor: beatPx > 6 ? this.beat : null, label: (t) => {
        const b = Math.round(t / this.beat);
        return b % 4 === 0 ? String(b / 4 + 1) : `${Math.floor(b / 4) + 1}.${(b % 4) + 1}`;
      }, major: (t) => Math.round(t / this.beat) % 4 === 0 };
    }
    const steps = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
    const step = steps.find((s) => (s / span) * px > 60) || 1200;
    return { step, minor: null, label: (t) => (t >= 60 ? `${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, "0")}` : `${+t.toFixed(2)}s`), major: () => true };
  }

  paintRuler(g, rects) {
    const W = this.W, G = this.o.gutter, bottom = this.bottomOf(rects);
    g.fillStyle = "#0c0c11"; g.fillRect(G, 0, W - G, RULER);
    const tk = this.ticks();
    g.font = "10.5px JetBrains Mono, monospace";
    g.textBaseline = "middle";
    if (tk.minor) {
      g.fillStyle = "rgba(255,255,255,0.035)";
      for (let t = Math.ceil(this.v0 / tk.minor) * tk.minor; t < this.v1; t += tk.minor) {
        const x = this.x(t);
        if (x >= G) g.fillRect(x, RULER, 1, bottom - RULER);
      }
    }
    for (let t = Math.max(0, Math.ceil(this.v0 / tk.step) * tk.step); t < this.v1; t += tk.step) {
      const x = this.x(t);
      if (x < G) continue;
      const maj = tk.major(t);
      g.fillStyle = maj ? "rgba(255,255,255,0.10)" : "rgba(255,255,255,0.05)";
      g.fillRect(x, RULER, 1, bottom - RULER);
      g.fillStyle = maj ? "#8b8b9c" : "#5d5d6c";
      g.fillRect(x, RULER - (maj ? 9 : 5), 1, maj ? 9 : 5);
      if (maj || (tk.step / (this.v1 - this.v0)) * (W - G) > 40) g.fillText(tk.label(t), x + 4, 10);
    }
    const xe = this.x(this.length);                               // project end
    if (xe > G && xe < W) { g.fillStyle = "rgba(255,184,107,0.6)"; g.fillRect(xe, 0, 1.5, bottom); }
    g.fillStyle = "#5d5d6c";
    g.fillText(this.bpm ? `${+this.bpm.toFixed(2)} BPM` : "free time", 10, 13);
  }

  paintLane(g, r) {
    const l = r.lane, G = this.o.gutter;
    g.fillStyle = "rgba(255,255,255,0.025)";
    g.fillRect(r.x, r.y, r.w, r.h);
    g.save();
    g.beginPath(); g.rect(r.x, r.y, r.w, r.h); g.clip();
    if (l.draw) l.draw(g, r, this);
    for (const it of this.laneItems(l)) {
      const dr = this.drag;
      let d = dr && dr.it && dr.it.id === it.id && dr.p ? dr.p : null;   // by id: the page may hand new item objects mid-drag
      if (!d && dr && dr.gids && dr.p && dr.gids.has(it.id)) { const dt = dr.p[0] - dr.t0; d = [it.t0 + dt, it.t1 + dt]; }   // the rest of a moving set
      const t0 = d ? d[0] : it.t0, t1 = d ? d[1] : it.t1;
      const a = this.x(t0), b = this.x(t1);
      if (b < r.x || a > r.x + r.w) continue;
      const kind = d ? (dr.it.id === it.id ? dr.kind + (dr.stretch ? "s" : "") : "move") : null;
      const rect = { x: a, y: r.y, w: b - a, h: r.h, t0, t1, kind };
      if (it.draw) it.draw(g, rect, this);
      else { g.fillStyle = it.color || "rgba(155,140,255,0.25)"; g.fillRect(a, r.y + 2, b - a, r.h - 4); }
      if (it.label && b - a > 30) {
        g.font = "11px Inter, sans-serif"; g.textBaseline = "top";
        const s = it.label, tw = Math.min(b - a - 8, g.measureText(s).width + 8);
        g.fillStyle = "rgba(9,9,13,0.72)"; g.fillRect(a + 3, r.y + 4, tw, 16);
        g.fillStyle = "#e8e8f0";
        g.save(); g.beginPath(); g.rect(a + 3, r.y + 4, tw, 16); g.clip();
        g.fillText(s, a + 7, r.y + 6.5); g.restore();
      }
      const on = this.sel === it.id || this.multi.has(it.id) || !!(this.mq && this.mq.has(it.id));
      g.strokeStyle = on ? "#c4bbff" : it.stroke || "rgba(255,255,255,0.16)";
      g.lineWidth = on ? 2 : 1;
      g.strokeRect(a + 0.5, r.y + 1.5, b - a - 1, r.h - 3);
      if (d) { g.fillStyle = "rgba(196,187,255,0.12)"; g.fillRect(a, r.y, b - a, r.h); }
      if (d && (dr.kind === "l" || dr.kind === "r") && dr.it.id === it.id) {   // what an edge drag does
        const s = dr.stretch ? `stretch ×${((t1 - t0) / Math.max(1e-6, dr.t1 - dr.t0)).toFixed(2)}` : it.stretch ? "trim · hold ⌥ to stretch" : "trim";
        g.font = "10.5px Inter, sans-serif"; g.textBaseline = "bottom";
        const tw = g.measureText(s).width + 8, lx = dr.kind === "l" ? a + 3 : Math.max(a + 3, b - tw - 3);
        g.fillStyle = "rgba(9,9,13,0.8)"; g.fillRect(lx, r.y + r.h - 19, tw, 15);
        g.fillStyle = dr.stretch ? "#ffd479" : "#e8e8f0"; g.fillText(s, lx + 4, r.y + r.h - 5.5);
      }
      (it.badges || []).forEach((st, k) => {
        g.fillStyle = { ok: "#7ee2a0", warn: "#ffb86b", bad: "#ff6b81" }[st] || "#5d5d6c";
        g.beginPath(); g.arc(b - 8 - k * 9, r.y + 10, 3.2, 0, 7); g.fill();
      });
    }
    for (const m of l.markers || []) {                               // keypoints
      const dm = this.drag && this.drag.kind === "marker" && this.drag.m === m && this.drag.p ? this.drag.p[0] : m.t;
      const x = this.x(dm), on = this.sel === m.id, cy = r.y + r.h / 2;
      if (m.shape === "fade") {                                      // a ⟷ pill along the top
        const y = r.y + 3, w = 22, h = 12;
        g.fillStyle = on ? "#fff" : "rgba(12,12,17,0.92)"; g.strokeStyle = m.color || "#c4bbff"; g.lineWidth = 1.2;
        g.beginPath(); g.roundRect(x - w / 2, y, w, h, 6); g.fill(); g.stroke();
        g.fillStyle = on ? "#0c0c11" : m.color || "#c4bbff";
        g.beginPath(); g.moveTo(x - 7, y + h / 2); g.lineTo(x - 3, y + 2.5); g.lineTo(x - 3, y + h - 2.5); g.closePath(); g.fill();
        g.beginPath(); g.moveTo(x + 7, y + h / 2); g.lineTo(x + 3, y + 2.5); g.lineTo(x + 3, y + h - 2.5); g.closePath(); g.fill();
        g.fillRect(x - 3, y + h / 2 - 0.6, 6, 1.2);
        continue;
      }
      if (m.shape === "grip") {                                      // a ⠿ pill along the bottom: move the span
        const w = 28, h = 12, y = r.y + r.h - h - 3;
        g.fillStyle = on ? "#fff" : "rgba(12,12,17,0.92)"; g.strokeStyle = m.color || "#c4bbff"; g.lineWidth = 1.2;
        g.beginPath(); g.roundRect(x - w / 2, y, w, h, 6); g.fill(); g.stroke();
        g.fillStyle = on ? "#0c0c11" : m.color || "#c4bbff";
        for (let i = -2; i <= 2; i++) for (const dy of [-2, 2]) { g.beginPath(); g.arc(x + i * 4, y + h / 2 + dy, 1.1, 0, 7); g.fill(); }
        continue;
      }
      g.fillStyle = m.color || "#c4bbff";
      g.strokeStyle = on ? "#fff" : "rgba(9,9,13,0.9)"; g.lineWidth = on ? 2 : 1.2;
      g.beginPath(); g.moveTo(x, cy - 7); g.lineTo(x + 6, cy); g.lineTo(x, cy + 7); g.lineTo(x - 6, cy); g.closePath(); g.fill(); g.stroke();
      if (m.cut) { g.fillStyle = "#fff"; g.fillRect(x - 0.5, r.y + 2, 1, r.h - 4); }
      if (m.label) {
        g.font = "10.5px Inter, sans-serif"; g.textBaseline = "top";
        const s = m.label.length > 34 ? m.label.slice(0, 33) + "…" : m.label, tw = g.measureText(s).width + 8;
        g.fillStyle = "rgba(9,9,13,0.8)"; g.fillRect(x + 8, r.y + 3, tw, 15);
        g.fillStyle = "#e8e8f0"; g.fillText(s, x + 12, r.y + 5.5);
      }
    }
    if (this.drag && this.drag.kind === "draw" && this.drag.lane === l && this.drag.p) {
      const a = this.x(this.drag.p[0]), b = this.x(this.drag.p[1]);
      g.fillStyle = "rgba(155,140,255,0.28)"; g.fillRect(a, r.y, b - a, r.h);
      g.strokeStyle = "#c4bbff"; g.strokeRect(a + 0.5, r.y + 0.5, b - a - 1, r.h - 1);
    }
    if (l.over) l.over(g, r, this);
    g.restore();
    g.fillStyle = "#0c0c11"; g.fillRect(0, r.y, G - 6, r.h);           // gutter label (clipped: a lane may be resized short)
    g.save(); g.beginPath(); g.rect(0, r.y, G - 6, r.h); g.clip();
    g.fillStyle = "#b9b9c8"; g.font = "600 11.5px Inter, sans-serif"; g.textBaseline = "top";
    g.fillText(l.label || "", 10, r.y + 8);
    if (l.sub) {
      g.fillStyle = "#6d6d7c"; g.font = "10.5px Inter, sans-serif";
      String(l.sub).split("\n").forEach((s, i) => g.fillText(s, 10, r.y + 25 + i * 14));
    }
    g.restore();
    if (l.gutter) l.gutter(g, { x: 0, y: r.y, w: G - 6, h: r.h }, this);
    if (l.onResize) {                                                 // the bottom edge's grip: a short line, the whole edge when hot
      const dr = this.drag, hot = (dr && dr.kind === "lresize" && this.rgroupOf(dr.lane).includes(l)) || (!dr && this.hEdge && this.hEdge.lane === l);
      g.fillStyle = hot ? "#c4bbff" : "rgba(255,255,255,0.14)";
      if (hot) g.fillRect(0, r.y + r.h + 1, G - 6, 2);
      else g.fillRect((G - 6) / 2 - 9, r.y + r.h + 1, 18, 2);
    }
  }
}
