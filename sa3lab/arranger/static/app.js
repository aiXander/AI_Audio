// SA3 Arranger page. The template JSON (S.t) is the only state that matters: every edit goes
// through commit() (undo snapshot → autosave → re-resolve the events for the canvas → auto
// render). The server (server.py) owns music + audio: /api/resolve turns the template into
// events (compose.py), /api/render into a WAV (render.py). Bars are 0-based in the JSON and
// shown 1-based here.
"use strict";
const $ = (id) => document.getElementById(id);
const LANE_COLORS = { kick: "#ff6b6b", snare: "#ffa94d", hats: "#ffd43b", bass: "#69db7c", chords: "#4dabf7", lead: "#b197fc", fx: "#f783ac" };
const LANE_LABEL = { kick: "Kick", snare: "Snare / clap", hats: "Hats / perc", bass: "Bass", chords: "Chords / pad", lead: "Lead", fx: "Risers / FX" };
const DRUMS = ["kick", "snare", "hats"], MUSIC = ["bass", "chords", "lead"];
const G = { head: 118, ruler: 22, sec: 30, chord: 20, lane: 54, gap: 3, pad: 10 };
const S = { t: null, meta: null, res: null, resStarts: null, sel: null, undo: [], redo: [], lastKey: null, lastT: 0,
            drag: null, hover: null, audioHash: null, renderSeq: 0 };
const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
                set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } } };

// ── helpers ───────────────────────────────────────────────────────────────
const clone = (x) => JSON.parse(JSON.stringify(x));
const rnd = () => Math.floor(Math.random() * 1e6);
const barS = () => 240 / S.t.bpm;
const totalBars = () => S.t.sections.reduce((a, s) => a + (+s.bars), 0);
const secBounds = () => { let b = 0; return S.t.sections.map((s) => { const r = [b, b + +s.bars, s]; b += +s.bars; return r; }); };
const fmtT = (s) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
function toast(msg, ms = 2600) {
  let el = $("toast");
  if (!el) { el = document.createElement("div"); el.id = "toast"; document.body.appendChild(el); }
  el.textContent = msg; el.classList.add("on");
  clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove("on"), ms);
}
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`);
  return r.json();
}
function curveAt(spec, x) {
  if (spec === null || spec === undefined) return 1;
  if (typeof spec === "number") return spec;
  if (spec.length === 1) return spec[0];
  x = Math.min(Math.max(x, 0), 1) * (spec.length - 1);
  const i = Math.min(Math.floor(x), spec.length - 2);
  return spec[i] + (spec[i + 1] - spec[i]) * (x - i);
}
function subCurve(spec, a, b) {          // the part of a curve between clip fractions a..b
  if (spec === null || spec === undefined || typeof spec === "number") return spec;
  const n = Math.max(2, Math.round((spec.length - 1) * (b - a)) + 1);
  const out = [];
  for (let k = 0; k < n; k++) out.push(+curveAt(spec, a + (b - a) * k / (n - 1)).toFixed(3));
  return out.every((v) => v === out[0]) ? out[0] : out;
}

// ── commit / undo / persistence ───────────────────────────────────────────
function commit(fn, key) {
  const now = Date.now();
  const snap = JSON.stringify(S.t);
  if (!(key && key === S.lastKey && now - S.lastT < 1500)) { S.undo.push(snap); if (S.undo.length > 200) S.undo.shift(); S.redo = []; }
  S.lastKey = key; S.lastT = now;
  fn(S.t);
  changed();
}
function changed(opts = {}) {
  syncHeader();
  if (!opts.keepInsp) renderInsp();
  draw();
  clearTimeout(changed._r); changed._r = setTimeout(resolve, 90);
  clearTimeout(changed._s); changed._s = setTimeout(save, 700);
  if ($("auto").checked) { clearTimeout(changed._a); changed._a = setTimeout(doRender, 900); }
}
function undo(redo = false) {
  const from = redo ? S.redo : S.undo, to = redo ? S.undo : S.redo;
  if (!from.length) return;
  to.push(JSON.stringify(S.t));
  S.t = JSON.parse(from.pop());
  S.lastKey = null;
  validateSel();
  changed();
}
async function save() {
  try { const r = await api("/api/template", S.t); store.set("arranger-slug", r.slug); } catch (e) { status(e.message, true); }
}
async function resolve() {
  const snap = clone(S.t);
  try {
    const r = await api("/api/resolve", snap);
    S.res = r;
    S.resStarts = Object.fromEntries(Object.entries(snap.lanes).map(([l, c]) => [l, c.clips.map((x) => x.start)]));
    syncHeader(); draw();
  } catch (e) { status(e.message, true); }
}
function status(msg, err = false) { const el = $("status"); el.textContent = msg; el.classList.toggle("err", err); }

// ── rendering + playback ──────────────────────────────────────────────────
async function doRender() {
  const seq = ++S.renderSeq;
  status("rendering…");
  try {
    const r = await api("/api/render", { template: S.t, stems: $("stems").checked });
    if (seq !== S.renderSeq) return;               // a newer edit is already rendering
    status(`${r.cached ? "cached" : "rendered in " + (r.render_ms / 1000).toFixed(1) + " s"} · ${r.seconds} s · ${r.hash}`);
    if (r.hash !== S.audioHash) {
      const a = $("audio"), pos = a.currentTime, playing = !a.paused;
      S.audioHash = r.hash;
      a.src = r.url;
      a.addEventListener("loadedmetadata", () => { a.currentTime = Math.min(pos, a.duration || pos); if (playing) a.play(); }, { once: true });
    }
  } catch (e) { if (seq === S.renderSeq) status(e.message, true); }
}
function togglePlay() {
  const a = $("audio");
  if (!a.src) { doRender().then(() => a.play()); return; }
  if (a.paused) a.play(); else a.pause();
}
function tick() {
  const a = $("audio");
  $("time").textContent = fmtT(a.currentTime || 0);
  $("play").textContent = a.paused ? "▶" : "❚❚";
  draw();
  if (!a.paused) requestAnimationFrame(tick);
}

// ── header / chord bar ────────────────────────────────────────────────────
function bindHeader() {
  const m = S.meta;
  $("key").innerHTML = m.keys.map((k) => `<option>${k}</option>`).join("");
  $("scale").innerHTML = m.scales.map((k) => `<option value="${k}">${k.replace("_", " ")}</option>`).join("");
  const on = (id, ev, f) => $(id).addEventListener(ev, f);
  on("name", "change", (e) => commit((t) => { t.name = e.target.value.trim() || "untitled"; }));
  on("bpm", "change", (e) => commit((t) => { t.bpm = Math.max(40, Math.min(220, +e.target.value || 120)); }));
  on("key", "change", (e) => commit((t) => { t.key = e.target.value; }));
  on("scale", "change", (e) => commit((t) => { t.scale = e.target.value; }));
  on("swing", "input", (e) => commit((t) => { t.swing = +e.target.value; }, "swing"));
  on("humanize", "input", (e) => commit((t) => { t.humanize = +e.target.value; }, "humanize"));
  on("seed", "change", (e) => commit((t) => { t.seed = +e.target.value || 0; }));
  on("seed-dice", "click", () => commit((t) => { t.seed = rnd(); }));
  on("degrees", "change", (e) => {
    const d = e.target.value.split(/[\s,-]+/).map((x) => parseInt(x, 10)).filter((x) => x >= 1 && x <= 7);
    if (d.length) commit((t) => { t.chords.degrees = d; }); else syncHeader();
  });
  on("chordbars", "change", (e) => commit((t) => { t.chords.bars = Math.max(1, +e.target.value || 1); }));
  on("sevenths", "change", (e) => commit((t) => { t.chords.sevenths = e.target.checked; }));
  on("prog-dice", "click", async () => {
    const r = await api(`/api/progression?seed=${rnd()}&scale=${S.t.scale}`);
    commit((t) => { t.chords.degrees = r.degrees; });
  });
  on("undo", "click", () => undo());
  on("redo", "click", () => undo(true));
  on("play", "click", togglePlay);
  on("render", "click", doRender);
  on("prompt", "input", (e) => commit((t) => { t.prompt = e.target.value; }, "prompt"));
  on("sigma", "input", (e) => commit((t) => { t.sa3.init_noise = +e.target.value; }, "sigma"));
  on("ladder", "change", (e) => {
    const v = e.target.value.split(/[\s,]+/).map(Number).filter((x) => x > 0 && x <= 1);
    if (v.length) commit((t) => { t.sa3.ladder = v; }); else syncHeader();
  });
  on("hints", "click", () => {
    const h = S.t.sections.filter((s) => s.hint).map((s) => `${s.name.toLowerCase()}: ${s.hint}`);
    if (!h.length) return toast("No section hints yet (set them in a section's inspector)");
    commit((t) => { t.prompt = (t.prompt ? t.prompt.replace(/\s*Structure:.*$/s, "") + ". " : "") + "Structure: " + h.join(", then "); });
  });
  on("send-load", "click", () => send([S.t.sa3.init_noise], false));
  on("send-gen", "click", () => send([S.t.sa3.init_noise], true));
  on("send-ladder", "click", () => send(S.t.sa3.ladder, true));
  const a = $("audio");
  a.addEventListener("play", () => requestAnimationFrame(tick));
  a.addEventListener("pause", tick);
  a.addEventListener("seeked", tick);
  // Open menu: saved templates + presets
  on("open-btn", "click", async (e) => {
    e.stopPropagation();
    const pop = $("open-menu");
    if (pop.classList.toggle("on")) {
      const ts = await api("/api/templates").catch(() => []);
      pop.innerHTML = `<div class="h">Presets</div>` + m.presets.map((p) =>
        `<button data-preset="${p.id}">${p.name}<small>${p.doc.split("\n")[0]}</small></button>`).join("") +
        `<div class="h">Saved templates</div>` + (ts.length ? ts.map((x) =>
        `<button data-slug="${x.slug}">${x.name}<small>${new Date(x.mtime * 1000).toLocaleString()}</small></button>`).join("")
        : `<div class="muted" style="padding:4px 8px">none yet</div>`);
    }
  });
  $("open-menu").addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    $("open-menu").classList.remove("on");
    const t = b.dataset.preset ? await api(`/api/preset/${b.dataset.preset}`) : await api(`/api/template/${b.dataset.slug}`);
    load(t, true);
  });
  addEventListener("mousedown", (e) => { if (!e.target.closest(".menu")) $("open-menu").classList.remove("on"); });
}
function syncHeader() {
  const t = S.t, set = (id, v) => { const el = $(id); if (document.activeElement !== el) el.value = v; };
  set("name", t.name); set("bpm", t.bpm); set("key", t.key); set("scale", t.scale);
  set("swing", t.swing); set("humanize", t.humanize); set("seed", t.seed);
  set("degrees", t.chords.degrees.join(" ")); set("chordbars", t.chords.bars);
  $("sevenths").checked = !!t.chords.sevenths;
  set("prompt", t.prompt || ""); set("sigma", t.sa3.init_noise); set("ladder", (t.sa3.ladder || []).join(" "));
  $("sigma-v").textContent = (+t.sa3.init_noise).toFixed(2);
  const bars = totalBars(), sec = Math.ceil(bars * barS() - 1e-6);
  const len = $("len");
  len.textContent = `${bars} bars · ${sec} s`;
  len.classList.toggle("warn", sec > 120);
  len.title = sec > 120 ? "Longer than 120 s: check the SA3 model's maximum Length" : "Length sent to SA3";
  if (S.res && S.res.chords) {
    const per = t.chords.degrees.length, names = S.res.chords.slice(0, per).map((c) => c.name);
    $("chordnames").textContent = names.join(" · ");
  }
  $("undo").disabled = !S.undo.length; $("redo").disabled = !S.redo.length;
}

// ── canvas geometry + drawing ─────────────────────────────────────────────
const cv = $("tl"), ctx = cv.getContext("2d");
const laneY = (k) => G.ruler + G.sec + G.chord + G.pad + k * (G.lane + G.gap);
const H = () => laneY(S.meta.lanes.length) + 8;
const xOf = (bar) => G.head + bar / totalBars() * (cv.clientWidth - G.head - 12);
const barOf = (x) => (x - G.head) / (cv.clientWidth - G.head - 12) * totalBars();
function resize() {
  const dpr = devicePixelRatio || 1;
  cv.style.height = H() + "px";
  cv.width = Math.round(cv.clientWidth * dpr); cv.height = Math.round(H() * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw();
}
const SEC_COLORS = ["#2a3150", "#3a2a4a", "#24403a", "#4a3a24", "#2a3f4f", "#46283a"];
function draw() {
  if (!S.t || !S.meta) return;
  const W = cv.clientWidth, h = H(), tb = totalBars();
  ctx.clearRect(0, 0, W, h);
  ctx.font = "11px -apple-system, sans-serif";
  ctx.textBaseline = "middle";
  // ruler
  ctx.fillStyle = "#13151b"; ctx.fillRect(0, 0, W, G.ruler);
  const every = tb > 96 ? 8 : tb > 48 ? 4 : 2;
  for (let b = 0; b <= tb; b++) {
    const x = xOf(b);
    ctx.fillStyle = b % every === 0 ? "#3a3f4f" : "#22252e";
    ctx.fillRect(x, G.ruler - (b % every === 0 ? 8 : 4), 1, b % every === 0 ? 8 : 4);
    if (b % every === 0 && b < tb) { ctx.fillStyle = "#8a90a2"; ctx.fillText(String(b + 1), x + 3, 8); }
  }
  ctx.fillStyle = "#5c6275"; ctx.textAlign = "right";
  ctx.fillText(`${S.t.bpm} BPM`, G.head - 8, 11); ctx.textAlign = "left";
  // sections
  const sb = secBounds();
  sb.forEach(([b0, b1, s], i) => {
    const x0 = xOf(b0), x1 = xOf(b1), y = G.ruler;
    const selS = S.sel && S.sel.type === "section" && S.sel.i === i;
    ctx.fillStyle = SEC_COLORS[i % SEC_COLORS.length];
    ctx.fillRect(x0 + 1, y + 2, x1 - x0 - 2, G.sec - 4);
    if (selS) { ctx.strokeStyle = "#e6e8ee"; ctx.lineWidth = 1.5; ctx.strokeRect(x0 + 1.5, y + 2.5, x1 - x0 - 3, G.sec - 5); }
    ctx.save(); ctx.beginPath(); ctx.rect(x0 + 2, y, x1 - x0 - 4, G.sec); ctx.clip();
    ctx.fillStyle = "#e6e8ee"; ctx.font = "600 12px -apple-system, sans-serif";
    ctx.fillText(s.name, x0 + 7, y + G.sec / 2);
    const nw = ctx.measureText(s.name).width;
    ctx.font = "11px -apple-system, sans-serif"; ctx.fillStyle = "#9aa0b3";
    ctx.fillText(`${s.bars} bars · ${fmtT(b0 * barS()).replace(/\.\d$/, "")}${s.mute && s.mute.length ? " · mute " + s.mute.join(",") : ""}`, x0 + 13 + nw, y + G.sec / 2);
    ctx.restore();
  });
  // chords
  if (S.res && S.res.chords) {
    const y = G.ruler + G.sec;
    S.res.chords.forEach((c) => {
      const x0 = xOf(c.bar), x1 = xOf(c.bar + c.bars);
      ctx.fillStyle = "#1a1d25"; ctx.fillRect(x0 + 1, y + 2, x1 - x0 - 2, G.chord - 3);
      if (x1 - x0 > 18) { ctx.fillStyle = "#aab0c3"; ctx.fillText(x1 - x0 > 40 ? `${c.name} ${c.roman}` : c.name, x0 + 4, y + G.chord / 2 + 1); }
    });
  }
  ctx.fillStyle = "#5c6275"; ctx.textAlign = "right"; ctx.fillText("chords", G.head - 8, G.ruler + G.sec + G.chord / 2 + 1); ctx.textAlign = "left";
  // lanes
  S.meta.lanes.forEach((lane, k) => {
    const y = laneY(k), cfg = S.t.lanes[lane], col = LANE_COLORS[lane];
    const selL = S.sel && S.sel.type === "lane" && S.sel.lane === lane;
    ctx.fillStyle = selL ? "#242835" : "#15171d"; ctx.fillRect(0, y, G.head - 6, G.lane);
    ctx.fillStyle = col; ctx.fillRect(0, y, 3, G.lane);
    ctx.fillStyle = "#e6e8ee"; ctx.font = "600 12px -apple-system, sans-serif"; ctx.fillText(LANE_LABEL[lane], 10, y + 16);
    ctx.font = "11px -apple-system, sans-serif"; ctx.fillStyle = "#8a90a2";
    ctx.fillText(`${cfg.sound} · ${cfg.gain_db > 0 ? "+" : ""}${(+cfg.gain_db).toFixed(0)} dB`, 10, y + 34);
    ctx.fillStyle = "#121318"; ctx.fillRect(G.head, y, W - G.head - 12, G.lane);
    // muted spans
    sb.forEach(([b0, b1, s]) => {
      const m = s.mute || [];
      if (m.includes("all") || m.includes(lane) || (m.includes("drums") && DRUMS.includes(lane)) || (m.includes("music") && MUSIC.includes(lane))) {
        const x0 = xOf(b0), x1 = xOf(b1);
        ctx.save(); ctx.beginPath(); ctx.rect(x0, y, x1 - x0, G.lane); ctx.clip();
        ctx.strokeStyle = "#3a1f24"; ctx.lineWidth = 1;
        for (let xx = x0 - G.lane; xx < x1; xx += 7) { ctx.beginPath(); ctx.moveTo(xx, y + G.lane); ctx.lineTo(xx + G.lane, y); ctx.stroke(); }
        ctx.restore();
      }
    });
    for (let b = 0; b < tb; b++) if (b % 4 === 0) { ctx.fillStyle = "#1b1e26"; ctx.fillRect(xOf(b), y, 1, G.lane); }
    sb.forEach(([b0]) => { ctx.fillStyle = "#2c3140"; ctx.fillRect(xOf(b0), y, 1, G.lane); });
    const pk = pack(lane);
    cfg.clips.forEach((c, i) => drawClip(lane, k, c, i, pk));
  });
  // playhead
  const a = $("audio");
  if (a.src && !isNaN(a.duration)) {
    const x = xOf(a.currentTime / barS());
    ctx.fillStyle = "#ffffffcc"; ctx.fillRect(x, 0, 1.5, h);
  }
}
function pack(lane) {                       // overlapping clips of a lane → stacked sub-rows
  const cl = S.t.lanes[lane].clips, order = cl.map((c, i) => i).sort((a, b) => cl[a].start - cl[b].start || a - b);
  const ends = [], row = [];
  for (const i of order) {
    let r = ends.findIndex((e) => e <= cl[i].start);
    if (r < 0) { r = ends.length; ends.push(0); }
    ends[r] = cl[i].start + cl[i].bars; row[i] = r;
  }
  return { row, n: Math.max(1, ends.length) };
}
function clipBox(lane, k, i, pk) {
  const n = pk.n, hh = (G.lane - 4) / n;
  return [laneY(k) + 2 + pk.row[i] * hh, hh - (n > 1 ? 1 : 0)];
}
function drawClip(lane, k, c, i, pk) {
  const [y, hh] = clipBox(lane, k, i, pk), x0 = xOf(c.start), x1 = xOf(c.start + c.bars), w = Math.max(3, x1 - x0 - 1);
  const col = LANE_COLORS[lane], ti = hh > 34 ? 14 : 11;
  const sel = S.sel && S.sel.type === "clip" && S.sel.lane === lane && S.sel.i === i;
  ctx.save();
  ctx.beginPath(); ctx.roundRect(x0 + 0.5, y, w, hh, 4); ctx.clip();
  ctx.fillStyle = col + (sel ? "55" : "2e"); ctx.fillRect(x0, y, w + 1, hh);
  // events
  const ev = S.res && S.res.lanes[lane] ? S.res.lanes[lane].filter((e) => e.clip === i) : [];
  const shift = S.resStarts && S.resStarts[lane] && S.resStarts[lane][i] !== undefined ? c.start - S.resStarts[lane][i] : 0;
  const bs = barS();
  ctx.fillStyle = col;
  if (DRUMS.includes(lane)) {
    ev.forEach((e) => { const x = xOf(e.t / bs + shift); ctx.globalAlpha = 0.35 + 0.65 * e.v; ctx.fillRect(x, y + hh - 4 - (hh - ti - 2) * e.v, 1.2, (hh - ti - 2) * e.v + 2); });
  } else if (lane === "fx") {
    const kind = c.gen;
    ctx.globalAlpha = 0.6; ctx.beginPath();
    const yb = y + hh - 3, yt = y + ti;
    if (kind === "riser" || kind === "reverse") { ctx.moveTo(x0, yb); ctx.lineTo(x1, yt); ctx.lineTo(x1, yb); }
    else if (kind === "downlifter") { ctx.moveTo(x0, yt); ctx.lineTo(x1, yb); ctx.lineTo(x0, yb); }
    else if (kind === "impact") { ctx.moveTo(x0, yt); ctx.quadraticCurveTo(x0 + (x1 - x0) * 0.15, yb, x1, yb); ctx.lineTo(x0, yb); }
    else { ctx.moveTo(x0, yb); ctx.quadraticCurveTo((x0 + x1) / 2, yt - 20, x1, yb); }
    ctx.fill();
  } else if (ev.length) {
    let lo = Infinity, hi = -Infinity;
    ev.forEach((e) => { lo = Math.min(lo, e.p); hi = Math.max(hi, e.p); });
    lo -= 1; hi += 1;
    const span = Math.max(hi - lo, 12), mid = (lo + hi) / 2, top = y + ti, bot = y + hh - 3;
    ev.forEach((e) => {
      const x = xOf(e.t / bs + shift), xe = xOf((e.t + e.d) / bs + shift);
      const yy = bot - ((e.p - (mid - span / 2)) / span) * (bot - top);
      ctx.globalAlpha = 0.5 + 0.5 * e.v; ctx.fillRect(x, yy - 1, Math.max(1.5, xe - x - 0.5), 2.4);
    });
  }
  ctx.globalAlpha = 1;
  // level (solid) and cutoff (dashed) curves
  const curveLine = (spec, style, dash) => {
    if (spec === undefined || spec === null) return;
    ctx.strokeStyle = style; ctx.setLineDash(dash); ctx.lineWidth = 1.2; ctx.beginPath();
    for (let s = 0; s <= 24; s++) { const u = s / 24, x = x0 + u * (x1 - x0), yy = y + hh - 2 - curveAt(spec, u) * (hh - ti - 2); s ? ctx.lineTo(x, yy) : ctx.moveTo(x, yy); }
    ctx.stroke(); ctx.setLineDash([]);
  };
  curveLine(c.level, "#ffffffaa", []);
  if (lane !== "fx") curveLine(c.cutoff, "#9fd3ffaa", [3, 3]);
  if (c.density !== undefined && c.density !== 1) curveLine(c.density, "#ffd43b88", [1, 3]);
  ctx.fillStyle = "#e6e8ee"; ctx.font = `600 ${hh > 34 ? 11 : 10}px -apple-system, sans-serif`;
  const lbl = c.gen + (c.params && c.params.sound ? `·${c.params.sound}` : "") + (c.params && c.params.kind ? `·${c.params.kind}` : "");
  ctx.fillText(lbl, x0 + 5, y + (hh > 34 ? 8 : 6));
  ctx.restore();
  ctx.strokeStyle = sel ? "#ffffff" : col + "99"; ctx.lineWidth = sel ? 1.5 : 1;
  ctx.beginPath(); ctx.roundRect(x0 + 0.5, y, w, hh, 4); ctx.stroke();
}

// ── hit testing + mouse ───────────────────────────────────────────────────
function hit(x, y) {
  if (y < G.ruler) return { zone: "ruler" };
  if (y < G.ruler + G.sec) {
    for (const [i, [b0, b1]] of secBounds().entries()) {
      if (Math.abs(x - xOf(b1)) < 5 && x > G.head) return { zone: "secEdge", i };
      if (x >= xOf(b0) && x < xOf(b1)) return { zone: "sec", i };
    }
    return { zone: "none" };
  }
  if (y < G.ruler + G.sec + G.chord + G.pad) return { zone: "chord" };
  const k = Math.floor((y - laneY(0)) / (G.lane + G.gap));
  const lane = S.meta.lanes[k];
  if (!lane) return { zone: "none" };
  if (x < G.head) return { zone: "laneHead", lane };
  const clips = S.t.lanes[lane].clips, pk = pack(lane);
  for (let i = clips.length - 1; i >= 0; i--) {
    const c = clips[i], x0 = xOf(c.start), x1 = xOf(c.start + c.bars), [cy, ch] = clipBox(lane, k, i, pk);
    if (x >= x0 && x <= x1 && y >= cy && y <= cy + ch) {
      if (x1 - x < 7) return { zone: "clipR", lane, i };
      if (x - x0 < 7 && x1 - x0 > 20) return { zone: "clipL", lane, i };
      return { zone: "clip", lane, i };
    }
  }
  return { zone: "lane", lane, bar: barOf(x) };
}
function secAtBar(bar) { return secBounds().findIndex(([b0, b1]) => bar >= b0 && bar < b1); }
function pos(e) { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
cv.addEventListener("mousedown", (e) => {
  const [x, y] = pos(e), h = hit(x, y);
  if (h.zone === "ruler") { seek(x); S.drag = { kind: "seek" }; return; }
  if (h.zone === "sec") { select({ type: "section", i: h.i }); return; }
  if (h.zone === "laneHead") { select({ type: "lane", lane: h.lane }); return; }
  if (h.zone === "lane") { const i = secAtBar(h.bar); if (i >= 0) select({ type: "section", i, lane: h.lane }); return; }
  if (h.zone === "secEdge") {
    S.undo.push(JSON.stringify(S.t)); S.redo = []; S.lastKey = null;
    S.drag = { kind: "secEdge", i: h.i, x0: x, base: JSON.stringify(S.t), bars0: +S.t.sections[h.i].bars };
    return;
  }
  if (h.zone.startsWith("clip")) {
    S.undo.push(JSON.stringify(S.t)); S.redo = []; S.lastKey = null;
    let i = h.i;
    if (e.altKey && h.zone === "clip") { S.t.lanes[h.lane].clips.push(clone(S.t.lanes[h.lane].clips[i])); i = S.t.lanes[h.lane].clips.length - 1; }
    select({ type: "clip", lane: h.lane, i });
    const c = S.t.lanes[h.lane].clips[i];
    S.drag = { kind: h.zone, lane: h.lane, i, x0: x, start: c.start, bars: c.bars, moved: false };
  }
});
cv.addEventListener("dblclick", (e) => {
  const [x, y] = pos(e), h = hit(x, y);
  if (h.zone !== "lane") return;
  const si = secAtBar(h.bar);
  if (si < 0) return;
  const [b0, b1] = secBounds()[si];
  commit((t) => {
    const lane = h.lane, gen = S.meta.gens[lane][0];
    t.lanes[lane].clips.push({ start: b0, bars: b1 - b0, gen, level: 1 });
    S.sel = { type: "clip", lane, i: t.lanes[lane].clips.length - 1 };
  });
});
addEventListener("mousemove", (e) => {
  const [x, y] = pos(e);
  const d = S.drag;
  if (!d) {
    if (e.target !== cv) return;
    const h = hit(x, y);
    cv.style.cursor = h.zone === "clipR" || h.zone === "clipL" || h.zone === "secEdge" ? "ew-resize" : h.zone === "clip" ? "grab"
      : h.zone === "ruler" ? "text" : "default";
    return;
  }
  if (d.kind === "seek") { seek(x); return; }
  const dBar = Math.round(barOf(x) - barOf(d.x0));
  if (d.kind === "secEdge") {
    const nb = Math.max(1, d.bars0 + dBar);
    S.t = JSON.parse(d.base);
    resizeSection(S.t, d.i, nb);
    draw(); syncHeader();
    return;
  }
  const c = S.t.lanes[d.lane].clips[d.i], tb = totalBars();
  if (d.kind === "clip") { c.start = Math.max(0, Math.min(tb - c.bars, d.start + dBar)); }
  else if (d.kind === "clipR") { c.bars = Math.max(1, Math.min(tb - c.start, d.bars + dBar)); }
  else if (d.kind === "clipL") { const s = Math.max(0, Math.min(d.start + d.bars - 1, d.start + dBar)); c.bars = d.start + d.bars - s; c.start = s; }
  d.moved = d.moved || dBar !== 0;
  draw();
  clearTimeout(S._dragRes); S._dragRes = setTimeout(resolve, 60);
});
addEventListener("mouseup", () => {
  const d = S.drag;
  S.drag = null;
  if (!d || d.kind === "seek") return;
  if ((d.kind.startsWith("clip") && !d.moved) || (d.kind === "secEdge" && S.t.sections[d.i].bars === d.bars0)) {
    S.t = JSON.parse(S.undo.pop());                 // a plain click: back to the pre-drag state (drops an unmoved ⌥-copy)
    validateSel(); renderInsp(); draw();
    return;
  }
  changed();
});
function seek(x) {
  const a = $("audio");
  const t = Math.max(0, barOf(x) * barS());
  if (!a.src) { doRender().then(() => { a.currentTime = t; }); return; }
  a.currentTime = Math.min(t, (a.duration || t) - 0.05);
  tick();
}

// ── template operations ───────────────────────────────────────────────────
function splitAt(t, lane, i, bar) {        // split clip i at bar → [left, right] (curves cut to match)
  const c = t.lanes[lane].clips[i];
  if (bar <= c.start || bar >= c.start + c.bars) return;
  const f = (bar - c.start) / c.bars;
  const right = clone(c);
  right.start = bar; right.bars = c.start + c.bars - bar;
  for (const k of ["level", "density", "cutoff"]) { if (k in c) { right[k] = subCurve(c[k], f, 1); c[k] = subCurve(c[k], 0, f); } }
  c.bars = bar - c.start;
  t.lanes[lane].clips.push(right);
}
function cutRange(t, lane, b0, b1) {       // remove [b0, b1) from a lane (clips split at the edges)
  const cl = t.lanes[lane].clips;
  for (let i = cl.length - 1; i >= 0; i--) { splitAt(t, lane, i, b0); }
  for (let i = cl.length - 1; i >= 0; i--) { splitAt(t, lane, i, b1); }
  t.lanes[lane].clips = cl.filter((c) => c.start + c.bars <= b0 || c.start >= b1);
}
function pieceIn(t, lane, b0, b1) {        // split at edges; indices of clips inside [b0, b1)
  const cl = t.lanes[lane].clips;
  for (let i = cl.length - 1; i >= 0; i--) splitAt(t, lane, i, b0);
  for (let i = cl.length - 1; i >= 0; i--) splitAt(t, lane, i, b1);
  return cl.map((c, i) => [c, i]).filter(([c]) => c.start >= b0 && c.start + c.bars <= b1).map(([, i]) => i);
}
function resizeSection(t, si, nb) {
  const sb = (() => { let b = 0; return t.sections.map((s) => { const r = [b, b + +s.bars]; b += +s.bars; return r; }); })();
  const [, b1] = sb[si], delta = nb - t.sections[si].bars;
  if (!delta) return;
  for (const lane of Object.keys(t.lanes)) {
    if (delta < 0) cutRange(t, lane, b1 + delta, b1);
    for (const c of t.lanes[lane].clips) {
      if (c.start >= b1) c.start += delta;
      else if (delta > 0 && c.start + c.bars >= b1) c.bars += delta;
    }
  }
  t.sections[si].bars = nb;
}
function insertSection(t, at, n, sec) {    // insert n bars at bar `at` (clips spanning it are split)
  for (const lane of Object.keys(t.lanes)) {
    const cl = t.lanes[lane].clips;
    for (let i = cl.length - 1; i >= 0; i--) splitAt(t, lane, i, at);
    for (const c of t.lanes[lane].clips) if (c.start >= at) c.start += n;
  }
  let idx = t.sections.length;
  for (let i = 0, b = 0; i < t.sections.length; b += +t.sections[i].bars, i++) if (b >= at) { idx = i; break; }
  t.sections.splice(idx, 0, sec);
}
function fill(lane, si, clip, replace = true) {
  commit((t) => {
    const [b0, b1] = secBounds()[si];
    if (replace) cutRange(t, lane, b0, b1);
    t.lanes[lane].clips.push(Object.assign({ start: b0, bars: b1 - b0, level: 1 }, clone(clip)));
  });
}
function lastBars(si, n, lane, clip) {
  commit((t) => {
    const [b0, b1] = secBounds()[si], s = Math.max(b0, b1 - n);
    cutRange(t, lane, s, b1);
    t.lanes[lane].clips.push(Object.assign({ start: s, bars: b1 - s }, clone(clip)));
  });
}
function shape(lanes, si, f) {
  commit((t) => {
    const [b0, b1] = secBounds()[si];
    let n = 0;
    for (const lane of lanes) for (const i of pieceIn(t, lane, b0, b1)) { f(t.lanes[lane].clips[i], lane); n++; }
    if (!n) toast("No clips in this section on those lanes");
  });
}
function toggleMute(si, what) {
  commit((t) => {
    const m = t.sections[si].mute = t.sections[si].mute || [];
    const k = m.indexOf(what);
    if (k >= 0) m.splice(k, 1); else m.push(what);
  });
}
const HELPERS = [
  ["Drums", [
    ["4-on-the-floor", (s) => fill("kick", s, { gen: "four_floor" })],
    ["Half-time kick", (s) => fill("kick", s, { gen: "half" })],
    ["Backbeat clap", (s) => fill("snare", s, { gen: "backbeat" })],
    ["Hats 8ths", (s) => fill("hats", s, { gen: "eighths" })],
    ["Hats 16ths", (s) => fill("hats", s, { gen: "sixteenths", density: 0.8 })],
    ["+ Open hats", (s) => fill("hats", s, { gen: "offbeat", level: 0.7, params: { kind: "open" } }, false)],
    ["Snare roll ↗ (last 4)", (s) => lastBars(s, 4, "snare", { gen: "roll", level: [0.3, 1], params: { from: 4, to: 32 } })],
    ["Snare roll ↗ (last 8)", (s) => lastBars(s, 8, "snare", { gen: "roll", level: [0.25, 1], params: { from: 2, to: 32 } })],
    ["Kick roll ↗ (last 2)", (s) => lastBars(s, 2, "kick", { gen: "roll", level: [0.5, 1], params: { from: 4, to: 16 } })],
    ["Density build ↗", (s) => shape(DRUMS, s, (c) => { c.density = [0.15, 1]; })],
  ]],
  ["Music", [
    ["Pad chords", (s) => fill("chords", s, { gen: "pad" })],
    ["+ Chord stabs", (s) => fill("chords", s, { gen: "stabs", level: 0.8, params: { sound: "saw" } }, false)],
    ["+ Arp", (s) => fill("chords", s, { gen: "arp", level: 0.6, params: { sound: "pluck", rate: 16, octaves: 2 } }, false)],
    ["Bass offbeat", (s) => fill("bass", s, { gen: "offbeat" })],
    ["Bass sustain", (s) => fill("bass", s, { gen: "sustain" })],
    ["Rolling bass", (s) => fill("bass", s, { gen: "pulse" })],
    ["Melody (lane hook)", (s) => fill("lead", s, { gen: "melody" })],
    ["Melody (new seed)", (s) => fill("lead", s, { gen: "melody", seed: rnd() })],
  ]],
  ["FX", [
    ["Riser into next", (s) => { const n = Math.min(8, S.t.sections[s].bars); lastBars(s, n, "fx", { gen: "riser" }); }],
    ["Impact at start", (s) => commit((t) => { const [b0] = secBounds()[s]; t.lanes.fx.clips.push({ start: b0, bars: Math.min(2, t.sections[s].bars), gen: "impact" }); })],
    ["Reverse cymbal into next", (s) => lastBars(s, 1, "fx", { gen: "reverse" })],
    ["Sweep", (s) => fill("fx", s, { gen: "sweep", level: 0.7 }, false)],
    ["Downlifter", (s) => commit((t) => { const [b0] = secBounds()[s]; t.lanes.fx.clips.push({ start: b0, bars: Math.min(4, t.sections[s].bars), gen: "downlifter" }); })],
  ]],
  ["Shape", [
    ["Filter sweep ↗", (s) => shape(MUSIC, s, (c) => { c.cutoff = [0.12, 1]; })],
    ["Filter close ↘", (s) => shape(MUSIC, s, (c) => { c.cutoff = [1, 0.15]; })],
    ["Fade in", (s) => shape([...DRUMS, ...MUSIC], s, (c) => { c.level = [0.05, curveAt(c.level, 1)]; })],
    ["Fade out", (s) => shape([...DRUMS, ...MUSIC], s, (c) => { c.level = [curveAt(c.level, 0), 0.05]; })],
    ["Humanize clips", (s) => shape([...DRUMS, ...MUSIC], s, (c) => { c.humanize = 0.5; })],
    ["Drop out everything", (s) => toggleMute(s, "all")],
    ["Drop drums", (s) => toggleMute(s, "drums")],
    ["Clear section", (s) => commit((t) => { const [b0, b1] = secBounds()[s]; for (const l of S.meta.lanes) cutRange(t, l, b0, b1); })],
  ]],
];

// ── selection + inspector ─────────────────────────────────────────────────
function select(sel) { S.sel = sel; renderInsp(); draw(); }
function validateSel() {
  const s = S.sel;
  if (!s) return;
  if (s.type === "clip" && !(S.t.lanes[s.lane] && S.t.lanes[s.lane].clips[s.i])) S.sel = null;
  if (s.type === "section" && !S.t.sections[s.i]) S.sel = null;
}
function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else if (k === "class") e.className = v; else if (v !== undefined && v !== null) e.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined) e.append(k.nodeType ? k : document.createTextNode(String(k)));
  return e;
}
function row(f, label, input, title) { f.append(el("span", { title }, label), input); }
function numIn(val, set, o = {}) {
  const i = el("input", { type: "number", step: o.step ?? 1, min: o.min, max: o.max, placeholder: o.ph });
  i.value = val ?? "";
  i.addEventListener("change", () => set(i.value === "" ? null : +i.value));
  return i;
}
function selIn(opts, val, set) {
  const s = el("select", {}, opts.map(([v, l]) => { const o = el("option", { value: v }, l); if (String(v) === String(val ?? "")) o.selected = true; return o; }));
  s.addEventListener("change", () => set(s.value));
  return s;
}
const fmtCurve = (c) => (c === undefined || c === null ? "" : typeof c === "number" ? String(c) : c.join(" "));
function curveIn(val, set, dflt) {
  const box = el("div", { class: "curve" });
  const i = el("input", { class: "mono", placeholder: dflt === undefined ? "1" : `lane default (${dflt})`, title: "A number, or breakpoints spread evenly over the clip: '0.2 1' ramps up, '1 0.3 1' dips" });
  i.value = fmtCurve(val);
  const parse = (s) => { const v = s.trim().split(/[\s,]+/).filter(Boolean).map(Number).filter((x) => !isNaN(x)); return !v.length ? null : v.length === 1 ? v[0] : v; };
  i.addEventListener("change", () => set(parse(i.value)));
  const b = (lbl, v, title) => el("button", { title, onclick: () => set(v) }, lbl);
  box.append(i, b("—", 1, "flat 1"), b("↗", [0.15, 1], "ramp up"), b("↘", [1, 0.15], "ramp down"));
  return box;
}
function renderInsp() {
  const box = $("insp");
  box.innerHTML = "";
  const s = S.sel;
  if (s && s.type === "clip") return inspClip(box, s.lane, s.i);
  if (s && s.type === "lane") return inspLane(box, s.lane);
  if (s && s.type === "section") return inspSection(box, s.i);
  box.append(el("h3", {}, "Arranger"), el("div", { class: "help" }));
  box.lastChild.innerHTML = `
    Build the song's skeleton here; SA3 supplies the sound.<br><br>
    <b>Click a section</b> (top strip, or empty lane space) for its helpers: fill with 4-on-the-floor, snare roll, riser, filter sweep, drop out…<br>
    <b>Double-click</b> empty lane space: a clip filling that section.<br>
    <b>Drag</b> clips (snap to bars), their edges to resize, <kbd>⌥</kbd>-drag to copy; drag a section's right edge to resize it (later clips follow).<br>
    <b>Click a lane name</b> for its sound, level, pan, reverb, sidechain duck.<br><br>
    <kbd>space</kbd> play · <kbd>R</kbd> render · <kbd>⌫</kbd> delete clip · <kbd>⌘D</kbd> duplicate · <kbd>⌘Z</kbd> undo · click the ruler to seek.<br><br>
    Curves on clips: <span style="color:#fff">white = level</span>, <span style="color:#9fd3ff">dashed = filter cutoff</span>, <span style="color:#ffd43b">dotted = density</span>.<br><br>
    <b>Send to SA3</b> (bottom) renders the WAV and loads it into the running studio as an audio-to-audio job (a2a audio, Length, σ, prompt); LoRA, model and seed stay as set there.`;
}
function inspClip(box, lane, i) {
  const c = S.t.lanes[lane].clips[i];
  const set = (f, key) => commit(() => f(c), key);
  const sect = secAtBar(c.start);
  box.append(el("h3", {}, `${LANE_LABEL[lane]} clip `, el("b", {}, c.gen)));
  const f = el("div", { class: "f" });
  row(f, "Generator", selIn(S.meta.gens[lane].map((g) => [g, g]), c.gen, (v) => set(() => { c.gen = v; })));
  row(f, "Start bar", numIn(c.start + 1, (v) => set(() => { c.start = Math.max(0, Math.min(totalBars() - 1, (v || 1) - 1)); }), { min: 1 }));
  row(f, "Bars", numIn(c.bars, (v) => set(() => { c.bars = Math.max(1, v || 1); }), { min: 1 }));
  const seedBox = el("div", { class: "curve" });
  const effSeed = S.res && S.res.seeds && S.res.seeds[lane] ? S.res.seeds[lane][i] : "";
  seedBox.append(numIn(c.seed, (v) => set(() => { if (v === null) delete c.seed; else c.seed = v; }), { ph: `lane (${effSeed})` }),
    el("button", { title: "New seed for this clip", onclick: () => set(() => { c.seed = rnd(); }) }, "🎲"),
    el("button", { title: "Back to the lane's seed (all clips of a lane share it, so a melody repeats)", onclick: () => set(() => { delete c.seed; }) }, "lane"));
  row(f, "Seed", seedBox, "Randomness of generators + humanize. Blank = the lane's seed (derived from the song seed)");
  row(f, "Level", curveIn(c.level, (v) => set(() => { if (v === null) delete c.level; else c.level = v; })), "0..1 gain across the clip");
  if (lane !== "fx") {
    row(f, "Density", curveIn(c.density, (v) => set(() => { if (v === null) delete c.density; else c.density = v; })), "0..1: pattern generators keep the strongest hits first; melody thins out");
    row(f, "Cutoff", curveIn(c.cutoff, (v) => set(() => { if (v === null) delete c.cutoff; else c.cutoff = v; }), S.t.lanes[lane].cutoff ?? 1), "Low-pass 0..1 (80 Hz…18 kHz)");
    row(f, "Humanize", numIn(c.humanize, (v) => set(() => { if (v === null) delete c.humanize; else c.humanize = v; }), { step: 0.05, min: 0, max: 1, ph: `song (${S.t.humanize})` }));
  } else {
    row(f, "Shape", curveIn(c.cutoff, (v) => set(() => { if (v === null) delete c.cutoff; else c.cutoff = v; })), "Riser: where the noise band sits over time (0 low … 1 high)");
  }
  const p = c.params || {};
  const setP = (k) => (v) => set(() => {
    c.params = c.params || {};
    if (v === null || v === "") delete c.params[k]; else c.params[k] = v;
    if (!Object.keys(c.params).length) delete c.params;
  });
  if (["steps", "stabs"].includes(c.gen)) {
    const i2 = el("input", { class: "mono", placeholder: c.gen === "stabs" ? "x..x..x...x..x.." : "x...x...x...x...", title: "16 steps per bar: x = accent, o = soft, . = rest" });
    i2.value = p.steps || "";
    i2.addEventListener("change", () => setP("steps")(i2.value.trim()));
    row(f, "Steps", i2);
  }
  if (c.gen === "roll") {
    row(f, "Hits/bar from", numIn(p.from ?? 4, setP("from"), { min: 1 }));
    row(f, "Hits/bar to", numIn(p.to ?? 32, setP("to"), { min: 1 }));
  }
  if (c.gen === "arp") {
    row(f, "Rate", selIn([[8, "8ths"], [16, "16ths"], [4, "quarters"]], p.rate ?? 16, (v) => setP("rate")(+v)));
    row(f, "Octaves", numIn(p.octaves ?? 2, setP("octaves"), { min: 1, max: 4 }));
    row(f, "Direction", selIn([["up", "up"], ["down", "down"], ["updown", "up-down"]], p.dir ?? "up", setP("dir")));
  }
  if (c.gen === "melody") row(f, "Vary", numIn(p.vary ?? 0.2, setP("vary"), { step: 0.05, min: 0, max: 1 }), "How many weak-beat notes change on each repeat of the 8-bar hook");
  if (MUSIC.includes(lane)) row(f, "Sound", selIn([["", `lane (${S.t.lanes[lane].sound})`], ...S.meta.sounds[lane].map((x) => [x, x])], p.sound, setP("sound")));
  if (DRUMS.includes(lane)) row(f, "Sound", selIn([["", `lane (${S.t.lanes[lane].sound})`], ...S.meta.sounds[lane].map((x) => [x, x])], p.kind, setP("kind")));
  box.append(f);
  const a = $("audio");
  box.append(el("div", { class: "btns" },
    el("button", { onclick: () => dupClip() }, "Duplicate after"),
    el("button", { title: "Split at the playhead", onclick: () => commit((t) => splitAt(t, lane, i, Math.round((a.currentTime || 0) / barS()))) }, "Split at playhead"),
    el("button", { onclick: () => delClip() }, "Delete"),
    sect >= 0 ? el("button", { onclick: () => select({ type: "section", i: sect }) }, "Section ↑") : null));
}
function inspLane(box, lane) {
  const cfg = S.t.lanes[lane];
  const set = (k, key) => (v) => commit(() => { cfg[k] = v; }, key);
  box.append(el("h3", {}, "Lane ", el("b", {}, LANE_LABEL[lane])));
  const f = el("div", { class: "f" });
  row(f, "Sound", selIn(S.meta.sounds[lane].map((x) => [x, x]), cfg.sound, set("sound")));
  row(f, "Gain dB", numIn(cfg.gain_db, set("gain_db"), { step: 0.5 }));
  row(f, "Pan", numIn(cfg.pan, set("pan"), { step: 0.05, min: -1, max: 1 }), "-1 left … 1 right");
  row(f, "Reverb", numIn(cfg.reverb, set("reverb"), { step: 0.05, min: 0, max: 1 }), "Send to the shared reverb");
  row(f, "Duck", numIn(cfg.duck, set("duck"), { step: 0.05, min: 0, max: 1 }), "Sidechain dip under every kick (EDM pump)");
  if (MUSIC.includes(lane)) {
    row(f, "Octave", numIn(cfg.octave, set("octave"), { min: 0, max: 7 }));
    row(f, "Cutoff", numIn(cfg.cutoff, set("cutoff"), { step: 0.05, min: 0, max: 1 }), "Default low-pass for clips without their own");
    row(f, "Resonance", numIn(cfg.res ?? 0, set("res"), { step: 0.05, min: 0, max: 1.5 }));
  }
  box.append(f, el("div", { class: "help" }, `${cfg.clips.length} clips. Generators: ${S.meta.gens[lane].join(", ")}.`));
}
function inspSection(box, si) {
  const s = S.t.sections[si], [b0, b1] = secBounds()[si];
  box.append(el("h3", {}, "Section ", el("b", {}, s.name)));
  const f = el("div", { class: "f" });
  const nm = el("input", {}); nm.value = s.name;
  nm.addEventListener("change", () => commit(() => { s.name = nm.value.trim() || "Section"; }));
  row(f, "Name", nm);
  row(f, "Bars", numIn(s.bars, (v) => commit((t) => resizeSection(t, si, Math.max(1, v || 1)))), "Later sections and clips move with it");
  row(f, "Starts", el("span", { class: "mono" }, `bar ${b0 + 1} · ${fmtT(b0 * barS())} → ${fmtT(b1 * barS())}`));
  const hint = el("textarea", { rows: 2, placeholder: "what happens here, in words (offered as part of the prompt)" });
  hint.value = s.hint || "";
  hint.addEventListener("input", () => commit(() => { s.hint = hint.value; }, "hint" + si));
  row(f, "Hint", hint);
  const mutes = el("div", { class: "mutes" });
  for (const m of ["all", "drums", "music", ...S.meta.lanes]) {
    const cb = el("input", { type: "checkbox" }); cb.checked = (s.mute || []).includes(m);
    cb.addEventListener("change", () => toggleMute(si, m));
    mutes.append(el("label", {}, cb, " " + m));
  }
  row(f, "Mute", mutes, "Silences those lanes in this section (reverb tails ring on)");
  box.append(f);
  box.append(el("div", { class: "btns" },
    el("button", { onclick: () => commit((t) => insertSection(t, b1, 8, { name: "New", bars: 8, hint: "", mute: [] })) }, "+ 8 bars after"),
    el("button", { title: "Copy this section and its clips right after it", onclick: () => dupSection(si) }, "Duplicate"),
    el("button", { onclick: () => commit((t) => { for (const l of S.meta.lanes) cutRange(t, l, b0, b1); resizeSectionDelete(t, si); }) }, "Delete")));
  for (const [grp, hs] of HELPERS) {
    box.append(el("div", { class: "grp" }, grp));
    box.append(el("div", { class: "btns" }, hs.map(([lbl, fn]) => el("button", { onclick: () => fn(si) }, lbl))));
  }
}
function resizeSectionDelete(t, si) {
  let b0 = 0;
  for (let i = 0; i < si; i++) b0 += +t.sections[i].bars;
  const n = +t.sections[si].bars;
  for (const lane of Object.keys(t.lanes)) for (const c of t.lanes[lane].clips) if (c.start >= b0 + n) c.start -= n;
  t.sections.splice(si, 1);
  if (!t.sections.length) t.sections.push({ name: "A", bars: 8, hint: "", mute: [] });
  S.sel = null;
}
function dupSection(si) {
  commit((t) => {
    const [b0, b1] = secBounds()[si], n = b1 - b0;
    const copies = {};
    for (const lane of S.meta.lanes) {
      copies[lane] = pieceIn(t, lane, b0, b1).map((i) => clone(t.lanes[lane].clips[i]));
    }
    insertSection(t, b1, n, clone(t.sections[si]));
    for (const lane of S.meta.lanes) for (const c of copies[lane]) { c.start += n; t.lanes[lane].clips.push(c); }
    S.sel = { type: "section", i: si + 1 };
  });
}
function dupClip() {
  const s = S.sel;
  if (!s || s.type !== "clip") return;
  commit((t) => {
    const c = clone(t.lanes[s.lane].clips[s.i]);
    c.start = Math.min(totalBars() - c.bars, c.start + c.bars);
    t.lanes[s.lane].clips.push(c);
    S.sel = { type: "clip", lane: s.lane, i: t.lanes[s.lane].clips.length - 1 };
  });
}
function delClip() {
  const s = S.sel;
  if (!s || s.type !== "clip") return;
  commit((t) => { t.lanes[s.lane].clips.splice(s.i, 1); S.sel = null; });
}

// ── send to SA3 ───────────────────────────────────────────────────────────
async function send(sigmas, generate) {
  const seedV = $("send-seed").value;
  status("rendering + sending…");
  try {
    const r = await api("/api/send", { template: S.t, prompt: S.t.prompt, sigmas, generate, seed: seedV === "" ? null : +seedV });
    status(`sent ${r.id}`);
    const up = (await api("/api/studio").catch(() => ({}))).up;
    toast(up ? (sigmas.length > 1 ? `σ ladder (${sigmas.join(", ")}, seed ${r.seed}) handed to the studio` : generate ? "Sent: the studio loads it and presses Generate" : "Loaded into the studio's a2a input: press Generate there")
      : "The studio isn't running: start it (make start); an open studio page picks this up within 2 minutes", 4500);
  } catch (e) { status(e.message, true); }
}
async function pollStudio() {
  try {
    const r = await api("/api/studio");
    $("studio-dot").className = "dot " + (r.up ? "up" : "down");
    $("studio-txt").textContent = r.up ? (r.inbox_pending ? "studio up · waiting for its page to pick up" : "studio up") : "studio not running";
  } catch (e) { $("studio-dot").className = "dot down"; }
}

// ── boot ──────────────────────────────────────────────────────────────────
function load(t, fresh) {
  S.t = t;
  if (fresh) { S.undo = []; S.redo = []; S.sel = null; S.audioHash = null; $("audio").removeAttribute("src"); }
  resize();
  changed();
}
addEventListener("keydown", (e) => {
  const tag = (e.target.tagName || "").toLowerCase();
  if (["input", "textarea", "select"].includes(tag)) return;
  const mod = e.metaKey || e.ctrlKey;
  if (e.key === " ") { e.preventDefault(); togglePlay(); }
  else if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); undo(e.shiftKey); }
  else if (mod && e.key.toLowerCase() === "d") { e.preventDefault(); dupClip(); }
  else if (e.key === "Backspace" || e.key === "Delete") { e.preventDefault(); delClip(); }
  else if (!mod && e.key.toLowerCase() === "r") doRender();
  else if (e.key === "Escape") select(null);
});
addEventListener("resize", resize);
(async () => {
  S.meta = await api("/api/meta");
  bindHeader();
  const slug = store.get("arranger-slug");
  let t = null;
  if (slug) t = await api(`/api/template/${slug}`).catch(() => null);
  if (!t) t = await api("/api/preset/melodic-build-drop");
  load(t, true);
  pollStudio(); setInterval(pollStudio, 4000);
})();
