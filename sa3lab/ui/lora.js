// SA3 UI kit: the LoRA stack, the one LoRA editor every page uses (the studio's "LoRA adapters" panel
// via ../lora_link.js, the Composer's ⚙ Render settings). Up to 3 cards, each: a pick from
// stable-audio-3/loras/ (or a dropped / browsed .safetensors, uploaded into loras/uploads/), strength,
// and the sampler steps it acts on (From / To, 1-based, inclusive; the full range = every step).
// Value = [{path, strength, steps: null | [from, to]}] (sa3lab/loras.py documents the format and how
// the studio maps it onto upstream's slots).
//   const ls = new LoraStack(host, {value, maxSteps: () => 8, onChange(list)})  ·  ls.value  ·  ls.set(list)
import * as api from "/sa3/ui/api.js";

const MAX = 3;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const base = (p) => String(p || "").split("/").pop().replace(/\.safetensors$/, "");
let LIB = null;                                         // shared by every stack on the page
const library = (fresh) => (!LIB || fresh ? (LIB = api.get("/sa3/loras").then((d) => d.items || []).catch(() => [])) : LIB);

export class LoraStack {
  constructor(host, { value = [], maxSteps = () => 8, onChange = null } = {}) {
    this.maxSteps = maxSteps;
    this.onChange = onChange;
    this.items = [];
    this.lib = [];
    this.el = document.createElement("div");
    this.el.className = "ui-lora";
    host.appendChild(this.el);
    this.file = document.createElement("input");
    this.file.type = "file";
    this.file.accept = ".safetensors";
    this.file.hidden = true;
    this.file.onchange = () => { if (this.file.files[0]) this.upload(this.file.files[0], this.fileSlot); this.file.value = ""; };
    host.appendChild(this.file);
    this.el.addEventListener("mouseenter", () => this.refresh(true));   // files copied into loras/ show up without a reload
    this.el.addEventListener("dragover", (e) => { if ([...(e.dataTransfer.items || [])].some((i) => i.kind === "file")) { e.preventDefault(); this.el.classList.add("drop"); } });
    this.el.addEventListener("dragleave", () => this.el.classList.remove("drop"));
    this.el.addEventListener("drop", (e) => {
      const f = [...(e.dataTransfer.files || [])].find((x) => x.name.endsWith(".safetensors"));
      this.el.classList.remove("drop");
      if (!f) return;
      e.preventDefault();
      const card = e.target.closest(".ui-lora-card");
      this.upload(f, card ? +card.dataset.i : this.items.length);
    });
    this.set(value);
    this.refresh();
  }
  get value() { return this.items.map((x) => ({ ...x, steps: x.steps ? [...x.steps] : null })); }
  set(list) {
    this.items = (Array.isArray(list) ? list : []).filter((x) => x && x.path).slice(0, MAX)
      .map((x) => ({ path: x.path, strength: +(x.strength ?? 1), steps: Array.isArray(x.steps) ? [+x.steps[0], +x.steps[1]] : null }));
    this.paint();
  }
  async refresh(fresh) {                                // repaints only when loras/ changed (never under an open select)
    const lib = await library(fresh);
    if (JSON.stringify(lib) !== JSON.stringify(this.lib)) { this.lib = lib; this.paint(); }
  }
  changed() { this.paint(); if (this.onChange) this.onChange(this.value); }
  async upload(f, i) {
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await fetch("/sa3/loras/upload", { method: "POST", body: fd });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || r.statusText);
      await this.refresh(true);
      if (i >= this.items.length) { if (this.items.length >= MAX) return; this.items.push({ path: d.path, strength: 1, steps: null }); }
      else this.items[i].path = d.path;
      this.changed();
    } catch (e) {
      this.el.dataset.err = `upload failed: ${e.message}`;
      this.paint();
    }
  }
  steps(x) {
    const n = Math.max(1, +this.maxSteps() || 8);
    return { n, lo: Math.min(n, x.steps ? x.steps[0] : 1), hi: Math.min(n, x.steps ? x.steps[1] : n) };
  }
  paint() {
    const known = new Set(this.lib.map((l) => l.path));
    const cards = this.items.map((x, i) => {
      const s = this.steps(x);
      const opts = (known.has(x.path) ? "" : `<option value="${esc(x.path)}" selected>${esc(base(x.path))} (outside loras/)</option>`)
        + this.lib.map((l) => `<option value="${esc(l.path)}"${l.path === x.path ? " selected" : ""}>${esc(l.name)}</option>`).join("")
        + `<option value="__browse">Browse for a .safetensors…</option>`;
      return `<div class="ui-lora-card" data-i="${i}">
        <div class="r1"><i></i><select class="ui-input pick" title="${esc(x.path)}">${opts}</select>
          <button class="ui-btn small ghost rm" title="remove this LoRA">✕</button></div>
        <div class="r2"><span class="ui-label">Strength</span><input class="str" type="range" min="0" max="2" step="0.05" value="${Math.min(2, x.strength)}">
          <input class="ui-input num strn" type="number" min="0" max="10" step="0.05" value="${x.strength}"></div>
        <div class="r3" title="the sampler steps this LoRA acts on (of ${s.n})"><span class="ui-label">Steps</span>
          <input class="ui-input num lo" type="number" min="1" max="${s.n}" value="${s.lo}"><span class="muted">to</span>
          <input class="ui-input num hi" type="number" min="1" max="${s.n}" value="${s.hi}"><span class="muted">of ${s.n}${x.steps ? "" : " · all"}</span></div>
      </div>`;
    }).join("");
    const empty = !this.items.length ? `<div class="empty muted">No LoRA: the base model. Drop a .safetensors here or add one from loras/.</div>` : "";
    const err = this.el.dataset.err ? `<div class="err">${esc(this.el.dataset.err)}</div>` : "";
    delete this.el.dataset.err;
    this.el.innerHTML = cards + empty + err + (this.items.length < MAX ? `<button class="ui-btn small add">+ Add LoRA</button>` : "");
    this.wire();
  }
  wire() {
    const add = this.el.querySelector(".add");
    if (add) add.onclick = () => {
      const used = new Set(this.items.map((x) => x.path));
      const first = this.lib.find((l) => !used.has(l.path));
      if (!first) { this.fileSlot = this.items.length; this.file.click(); return; }
      this.items.push({ path: first.path, strength: 1, steps: null });
      this.changed();
    };
    for (const card of this.el.querySelectorAll(".ui-lora-card")) {
      const i = +card.dataset.i, x = this.items[i];
      const q = (c) => card.querySelector(c);
      q(".rm").onclick = () => { this.items.splice(i, 1); this.changed(); };
      q(".pick").onchange = (e) => {
        if (e.target.value === "__browse") { this.fileSlot = i; this.file.click(); this.paint(); return; }
        x.path = e.target.value;
        this.changed();
      };
      const str = (v) => { x.strength = Math.max(0, Math.min(10, +v || 0)); this.changed(); };
      q(".str").oninput = (e) => { q(".strn").value = e.target.value; };      // live readout; saved on release
      q(".str").onchange = (e) => str(e.target.value);
      q(".strn").onchange = (e) => str(e.target.value);
      const win = () => {
        const n = this.steps(x).n;
        let lo = Math.max(1, Math.min(n, Math.round(+q(".lo").value || 1)));
        let hi = Math.max(1, Math.min(n, Math.round(+q(".hi").value || n)));
        if (hi < lo) [lo, hi] = [hi, lo];
        x.steps = lo <= 1 && hi >= n ? null : [lo, hi];
        this.changed();
      };
      q(".lo").onchange = win;
      q(".hi").onchange = win;
    }
  }
}
