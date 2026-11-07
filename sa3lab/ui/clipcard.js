// SA3 UI kit: a render card: a header (title + play / stop on the right), dadaspec with highlight
// bands, and an action strip (range, ★ Highlight, Add) shown only while it has something in it. One card = one render, or one moment of it (`range`: the card shows that stretch, e.g. a
// highlight). The play button reads ■ while this card plays (player.subscribe).
//
//   new ClipCard(host, row, {range, onAdd(row, [s0, s1]), addLabel, onChanged(), known})
// addLabel renames the Add button (the Composer's conditioning picker: "Use").
//
// Gestures on the dadaspec (spec 3.1): click seeks + plays, drag selects a range (→ "Add" /
// "Highlight" buttons appear), click a highlight band → its menu (loop, edit, add, remove). The card is draggable (MIME application/x-sa3-piece: {id, s0, s1}) onto the composer.
// Right-click anywhere on the card → "Show in Finder" (reveal.js; the dadaspec's highlight bands keep their own menu).
// `row` = a /sa3/composer/starred row: {id, prompt, seconds, bpm, conf, score, starred?, hl, hl_an, folder}.

import * as api from "./api.js";
import * as player from "./player.js";
import { SpecView, load as loadSpec, drawCrop } from "./dadaspec.js";
import { menu } from "./menu.js";
import { openHighlight } from "./highlight.js";
import { toast } from "./shell.js";
import { install as installReveal } from "./reveal.js";

export const MIME = "application/x-sa3-piece";
const fmt = (t) => (t == null ? "–" : t >= 60 ? `${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, "0")}` : `${t.toFixed(1)} s`);
export const short = (s, n = 60) => { s = (s || "").replace(/^anarchy:\s*@?/, "").replace(/\.cond\.npz$/, ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; };
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

export class ClipCard {
  constructor(host, row, { range = null, onAdd = null, addLabel = "Add", onChanged = null, known = [], compact = false } = {}) {
    this.row = row; this.o = { range, onAdd, onChanged, known, compact };
    const el = (this.el = document.createElement("div"));
    el.className = "ui-card" + (compact ? " compact" : "");
    el.draggable = true;
    el.dataset.sa3Id = row.id;                     // right-click → Show in Finder (reveal.js)
    installReveal();
    const bpm = row.bpm ? `<span class="ui-chip${row.conf >= 0.3 ? "" : " dim"}" title="beat confidence ${row.conf}">${(+row.bpm).toFixed(0)} BPM${row.conf >= 0.3 ? "" : " ?"}</span>` : "";
    const key = this.key();
    el.innerHTML = `<div class="hd"><div class="t" title="${esc(row.prompt || row.id)}">${esc(short(row.prompt || row.id, compact ? 46 : 70))}</div><button class="ui-btn small ghost pl" title="play">▶</button></div>
      <div class="meta">${range ? `<span class="ui-chip accent">${fmt(range[0])}–${fmt(range[1])}</span>` : ""}<span class="ui-chip dim">${esc(row.folder || "")}</span><span class="ui-chip dim">${fmt(row.seconds)}</span>${bpm}${key ? `<span class="ui-chip">${key}</span>` : ""}${row.score != null ? `<span class="ui-chip">${row.score}/10</span>` : ""}${(row.hl || []).length ? `<span class="ui-chip hl">${row.hl.length} ★</span>` : ""}${(row.loras || []).length ? '<span class="ui-chip dim">LoRA</span>' : ""}</div>
      <div class="sv"></div>
      <div class="acts"><span class="r mono"></span><button class="ui-btn small ghost hlb" title="save the range as a highlight" hidden>★ Highlight</button>${onAdd ? `<button class="ui-btn small primary add" title="${addLabel === "Add" ? "add to the timeline" : esc(addLabel)}">${esc(addLabel)}</button>` : ""}</div>`;
    host.appendChild(el);
    this.sel = range ? [...range] : null;
    const key2 = "card:" + row.id;
    const play = (t0, t1 = null, loop = false) => player.play({ key: key2, url: api.audioUrl(row.id), t0, t1, loop, onTime: (x) => this.sv.setPlayhead(x) })
      .catch((e) => toast("can't play: " + e.message, { error: true }));
    this.sv = new SpecView(el.querySelector(".sv"), {
      url: api.specUrl(row.id), duration: row.seconds, height: compact ? 84 : 64, bands: row.hl || [],
      onRange: (rg) => { this.sel = rg; this.acts(); },
      onSeek: (t) => play(t, this.sel && t < this.sel[1] ? this.sel[1] : null),
      onBand: (b, e) => this.bandMenu(b, e, play),
    });
    if (range) this.sv.setRange(range);
    this.acts();
    const pl = el.querySelector(".pl");
    pl.onclick = () => (player.isPlaying(key2) ? player.stop() : play(this.sel ? this.sel[0] : 0, this.sel ? this.sel[1] : null));
    // ▶ ↔ ■ follows the one shared player: another card starting stops this one, and its icon flips back.
    const paintPl = () => { const on = player.isPlaying(key2); pl.textContent = on ? "■" : "▶"; pl.title = on ? "stop" : "play"; pl.classList.toggle("on", on); };
    const unsub = player.subscribe(() => { if (!el.isConnected) return unsub(); paintPl(); });
    paintPl();
    const add = el.querySelector(".add");
    if (add) add.onclick = () => onAdd(row, this.sel || [0, row.seconds]);
    el.querySelector(".hlb").onclick = () => this.highlight(null, this.sel);
    el.addEventListener("pointerdown", (e) => { this._onSpec = !!e.target.closest(".sv, button, select"); }, true);
    el.addEventListener("dragstart", (e) => {
      if (this._onSpec) { e.preventDefault(); return; }               // drags on the dadaspec select a range
      const r = this.sel || [0, row.seconds];
      e.dataTransfer.setData(MIME, JSON.stringify({ id: row.id, s0: r[0], s1: r[1] }));
      e.dataTransfer.effectAllowed = "copy";
    });
  }
  key() {
    const an = Object.values(this.row.hl_an || {}).find((a) => a && a.key);
    return an ? an.key : "";
  }
  acts() {
    const r = this.el.querySelector(".r");
    r.textContent = this.sel ? `${this.sel[0].toFixed(1)}–${this.sel[1].toFixed(1)} s` : "";
    this.el.querySelector(".hlb").hidden = !this.sel;
    this.el.querySelector(".acts").hidden = !this.sel && !this.o.onAdd;   // an empty strip gives its height back to the dadaspec
    this.el.classList.toggle("picked", !!this.sel);
  }
  bandMenu(b, e, play) {
    menu(e.clientX, e.clientY, [
      { label: "Loop this highlight", fn: () => play(b.s0, b.s1, true) },
      { label: "Edit…", fn: () => this.highlight(b) },
      this.o.onAdd ? { label: "Add to the timeline", fn: () => this.o.onAdd(this.row, [b.s0, b.s1]) } : null,
      "-",
      { label: "Remove highlight", danger: true, fn: async () => { const v = await api.post("/sa3/highlights", { id: this.row.id, op: "remove", h: { id: b.id } }); this.update(v); } },
    ].filter(Boolean));
  }
  highlight(hl, range = null) {
    openHighlight({ id: this.row.id, hl, range: range || [hl.s0, hl.s1], known: this.o.known,
      onSaved: (v) => this.update(v),
      onComposer: (id, a, b) => window.open(`/sa3/page/composer?add=${encodeURIComponent(id)}&s0=${a.toFixed(3)}&s1=${b.toFixed(3)}`, "sa3-composer") });
  }
  update(v) {
    this.row.hl = v.highlights || [];
    this.row.hl_an = v.analysis || {};
    this.sv.setBands(this.row.hl);
    this.sv.setRange(null); this.sel = null; this.acts();
    if (this.o.onChanged) this.o.onChanged(this.row);
  }
}

// A small static crop of a render's dadaspec (for moment cards and lists).
export function cropCanvas(id, dur, s0, s1, w = 240, h = 46) {
  const cv = document.createElement("canvas");
  cv.style.cssText = `width:100%;height:${h}px;display:block;border-radius:6px;background:#0b0b10`;
  loadSpec(api.specUrl(id)).then((src) => {
    const dpr = devicePixelRatio || 1; cv.width = (cv.clientWidth || w) * dpr; cv.height = h * dpr;
    drawCrop(cv.getContext("2d"), src, dur, s0, s1, 0, 0, cv.width, cv.height);
  }).catch(() => {});
  return cv;
}
