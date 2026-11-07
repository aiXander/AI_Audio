// SA3 UI kit: the library browser (spec 3.2): the starred set as a grid of render / highlight cards
// or a 2-D map, with filters and sorts. The Library page shows it full width; the composer shows it
// compact in its side panel. ("More like this" was taken out of the UI for now; the server's
// /sa3/embed/similar and `embed.py` still exist, unused here.)
//
//   const br = new Browser(host, {compact, onAdd(row, [s0, s1]), addLabel, view: "grid" | "map"})
//   br.reload()
//
// Data: GET /sa3/composer/starred (rows incl. highlights + their analysis), /sa3/embed/map (PCA +
// k-means clusters of the SAME-latent fingerprints).
// Filters: text, highlighted only, tag, BPM range (confident beats only), key compatible with X
// (from highlight analysis), score ≥, folder, source type (text / anarchy / a2a / inpaint /
// composite), LoRA. Sorts: newest, score. Map: hover auditions 8 s from the render's middle, click opens it,
// shift-drag lassoes a set (the grid then shows only those).

import * as api from "./api.js";
import * as player from "./player.js";
import { ClipCard, cropCanvas, short } from "./clipcard.js";
import { toast } from "./shell.js";

const KEYS = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];
const keyIdx = (k) => { if (!k) return null; const [n, m] = k.split(" "); const i = KEYS.indexOf(n); return i < 0 ? null : i + (m === "minor" ? 12 : 0); };
const rel = (i) => (i < 12 ? ((i + 9) % 12) + 12 : (i % 12 + 3) % 12);
function compatible(a, b) {
  const x = keyIdx(a), y = keyIdx(b);
  if (x == null || y == null) return false;
  return x === y || rel(x) === y || ((x < 12) === (y < 12) && [5, 7].includes(((y - x) % 12 + 12) % 12));
}
const COLORS = ["#a99cff", "#5ed1b5", "#ffb86b", "#ff6b81", "#7ec8ff", "#e6e27a"];

export class Browser {
  constructor(host, { compact = false, onAdd = null, addLabel = "Add", view = "grid" } = {}) {
    this.o = { compact, onAdd, addLabel };
    this.view = view;
    this.rows = [];
    this.lasso = null;        // Set of ids
    this.mode = "sound";      // the map's space: SAME-latent fingerprints (E2)
    this.el = document.createElement("div");
    this.el.className = "ui-browser" + (compact ? " compact" : "");
    this.el.innerHTML = `<div class="bar">
        <input class="ui-input q" placeholder="filter: prompt, tag, folder">
        <div class="ui-seg vw"><button data-v="grid" title="grid">Grid</button><button data-v="map" title="2-D map of the starred set">Map</button></div>
        <button class="ui-btn small ghost more" title="more filters">Filters ▾</button>
      </div>
      <div class="filters" hidden>
        <label><input type="checkbox" class="f-hl"> highlighted</label>
        <select class="ui-input f-tag"><option value="">any tag</option></select>
        <span class="lbl">BPM</span><input class="ui-input num f-b0" type="number" placeholder="min"><input class="ui-input num f-b1" type="number" placeholder="max">
        <select class="ui-input f-key"><option value="">any key</option></select>
        <span class="lbl">score ≥</span><input class="ui-input num f-sc" type="number" min="0" max="10">
        <select class="ui-input f-folder"><option value="">any folder</option></select>
        <select class="ui-input f-kind"><option value="">any source</option><option>text</option><option>anarchy</option><option>a2a</option><option>inpaint</option><option>composite</option></select>
        <select class="ui-input f-lora"><option value="">LoRA: any</option><option value="1">with LoRA</option><option value="0">no LoRA</option></select>
      </div>
      <div class="sortbar"><span class="lbl">sort</span><select class="ui-input sort"><option value="new">newest starred</option><option value="score">score</option></select>
        <span class="grow"></span><span class="count muted"></span></div>
      <div class="body"></div>`;
    host.appendChild(this.el);
    const $ = (c) => this.el.querySelector(c);
    this.$ = $;
    $(".q").oninput = () => this.paint();
    $(".more").onclick = () => { $(".filters").hidden = !$(".filters").hidden; };
    for (const c of [".f-hl", ".f-tag", ".f-b0", ".f-b1", ".f-key", ".f-sc", ".f-folder", ".f-kind", ".f-lora"]) $(c).oninput = () => this.paint();
    $(".sort").onchange = () => this.paint();
    $(".vw").onclick = (e) => { const v = e.target.dataset.v; if (v) { this.view = v; this.paint(); } };
    $(".f-key").innerHTML = '<option value="">any key</option>' + Array.from({ length: 24 }, (_, i) => `<option value="${KEYS[i % 12]} ${i < 12 ? "major" : "minor"}">compatible with ${KEYS[i % 12]} ${i < 12 ? "major" : "minor"}</option>`).join("");
  }
  async reload() {
    try { this.rows = await api.get("/sa3/composer/starred"); } catch (e) { toast(e.message, { error: true }); }
    const tags = [...new Set(this.rows.flatMap((r) => [...(r.tags || []), ...(r.hl || []).flatMap((h) => h.tags || [])]))].sort();
    this.known = tags;
    this.$(".f-tag").innerHTML = '<option value="">any tag</option>' + tags.map((t) => `<option>${t}</option>`).join("");
    const fs = [...new Set(this.rows.map((r) => r.folder))].sort();
    this.$(".f-folder").innerHTML = '<option value="">any folder</option>' + fs.map((t) => `<option>${t}</option>`).join("");
    this.paint();
  }
  filtered() {
    const $ = this.$, q = $(".q").value.trim().toLowerCase();
    const b0 = +$(".f-b0").value || null, b1 = +$(".f-b1").value || null, sc = $(".f-sc").value === "" ? null : +$(".f-sc").value;
    const tag = $(".f-tag").value, key = $(".f-key").value, fo = $(".f-folder").value, kind = $(".f-kind").value, lo = $(".f-lora").value;
    return this.rows.filter((r) => {
      if (q && !(r.prompt + " " + r.id + " " + (r.tags || []).join(" ") + " " + (r.hl || []).map((h) => (h.tags || []).join(" ") + " " + (h.note || "")).join(" ")).toLowerCase().includes(q)) return false;
      if ($(".f-hl").checked && !(r.hl || []).length) return false;
      if (tag && !(r.tags || []).includes(tag) && !(r.hl || []).some((h) => (h.tags || []).includes(tag))) return false;
      if ((b0 || b1) && !(r.bpm && r.conf >= 0.3 && (!b0 || r.bpm >= b0) && (!b1 || r.bpm <= b1))) return false;
      if (key && !Object.values(r.hl_an || {}).some((a) => a && compatible(a.key, key))) return false;
      if (sc != null && !(r.score != null && r.score >= sc)) return false;
      if (fo && r.folder !== fo) return false;
      if (kind && r.kind !== kind) return false;
      if (lo !== "" && !!(r.loras || []).length !== (lo === "1")) return false;
      if (this.lasso && !this.lasso.has(r.id)) return false;
      return true;
    });
  }
  paint() {
    const $ = this.$, body = $(".body");
    for (const b of $(".vw").querySelectorAll("button")) b.classList.toggle("on", b.dataset.v === this.view);
    const rows = this.filtered();
    const sort = $(".sort").value;
    body.innerHTML = "";
    if (this.lasso) {
      const c = document.createElement("div"); c.className = "lassoed";
      c.innerHTML = `${this.lasso.size} picked on the map <button class="ui-btn small ghost">clear</button>`;
      c.querySelector("button").onclick = () => { this.lasso = null; this.paint(); };
      body.appendChild(c);
    }
    if (this.view === "map") return this.paintMap(body, rows);
    const opts = { onAdd: this.o.onAdd, addLabel: this.o.addLabel, compact: this.o.compact, known: this.known,
      onChanged: () => {} };
    let n = 0;
    const list = rows.slice().sort((a, b) => (sort === "score" ? (b.score ?? -1) - (a.score ?? -1) : String(b.ts).localeCompare(String(a.ts))));
    for (const r of list) { new ClipCard(body, r, opts); n++; }
    $(".count").textContent = `${n} of ${this.rows.length}`;
    if (!n) body.insertAdjacentHTML("beforeend", `<div class="muted empty">${this.rows.length ? "Nothing matches these filters." : "No starred renders yet. Star renders in the studio (★) to add them here."}</div>`);
  }
  async paintMap(body, rows) {
    const wrap = document.createElement("div");
    wrap.className = "map";
    wrap.innerHTML = '<canvas></canvas><div class="tip"></div><div class="legend muted">hover auditions · click opens · ⇧-drag lassoes</div>';
    body.appendChild(wrap);
    const cv = wrap.querySelector("canvas"), tip = wrap.querySelector(".tip");
    if (!this.mapData) {
      try { this.mapData = await api.get("/sa3/embed/map", { mode: this.mode }); } catch (e) { this.mapData = { points: [] }; }
    }
    const ok = new Set(rows.map((r) => r.id));
    const pts = (this.mapData.points || []).filter((p) => ok.has(p.id));
    this.$(".count").textContent = `${pts.length} mapped · ${this.rows.length - (this.mapData.points || []).length} not embedded yet`;
    if (!pts.length) { wrap.insertAdjacentHTML("beforeend", '<div class="muted empty">No embeddings yet: the studio encodes starred renders while its queue is idle (GET /sa3/embed/status).</div>'); }
    const P = (p, w, h) => [w / 2 + p.x * (w / 2 - 18), h / 2 - p.y * (h / 2 - 18)];
    let hover = null, lasso = null;
    const draw = () => {
      const dpr = devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
      cv.width = w * dpr; cv.height = h * dpr;
      const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.fillStyle = "#0b0b10"; g.fillRect(0, 0, w, h);
      for (const p of pts) {
        const [x, y] = P(p, w, h);
        g.fillStyle = COLORS[p.cluster % COLORS.length];
        g.globalAlpha = hover === p ? 1 : 0.8;
        g.beginPath(); g.arc(x, y, hover === p ? 7 : 5, 0, 7); g.fill();
      }
      g.globalAlpha = 1;
      if (lasso && lasso.length > 1) {
        g.strokeStyle = "#c4bbff"; g.beginPath(); g.moveTo(...lasso[0]); for (const q of lasso) g.lineTo(...q); g.closePath(); g.stroke();
      }
    };
    requestAnimationFrame(draw);
    const at = (e) => { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    const near = (x, y) => { let best = null, d = 12; for (const p of pts) { const [a, b] = P(p, cv.clientWidth, cv.clientHeight); const q = Math.hypot(a - x, b - y); if (q < d) { d = q; best = p; } } return best; };
    cv.onpointermove = (e) => {
      const [x, y] = at(e);
      if (lasso) { lasso.push([x, y]); return draw(); }
      const p = near(x, y);
      if (p !== hover) {
        hover = p;
        draw();
        if (p) {
          const r = this.rows.find((z) => z.id === p.id) || {};
          tip.textContent = `${short(r.prompt || p.id, 60)} · cluster ${p.cluster + 1}`;
          tip.style.left = x + 12 + "px"; tip.style.top = y + 8 + "px"; tip.hidden = false;
          const mid = Math.max(0, (r.seconds || 16) / 2 - 4);
          player.play({ key: "map", url: api.audioUrl(p.id), t0: mid, t1: mid + 8 }).catch(() => {});
        } else { tip.hidden = true; if (player.isPlaying("map")) player.stop(); }
      }
    };
    cv.onpointerleave = () => { hover = null; tip.hidden = true; if (player.isPlaying("map")) player.stop(); draw(); };
    cv.onpointerdown = (e) => { if (e.shiftKey) { cv.setPointerCapture(e.pointerId); lasso = [at(e)]; } };
    cv.onpointerup = (e) => {
      if (lasso) {
        const poly = lasso; lasso = null;
        const inside = (x, y) => { let c = false; for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) { const [xi, yi] = poly[i], [xj, yj] = poly[j]; if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c; } return c; };
        const ids = pts.filter((p) => inside(...P(p, cv.clientWidth, cv.clientHeight))).map((p) => p.id);
        if (ids.length) { this.lasso = new Set(ids); this.view = "grid"; this.paint(); } else draw();
        return;
      }
      const p = near(...at(e));
      if (p) { this.lasso = new Set([p.id]); this.view = "grid"; this.paint(); }
    };
  }
}
export { cropCanvas };
