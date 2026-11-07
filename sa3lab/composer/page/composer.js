// The Composer editor (design: docs/reference/sa3-composer.md; backend: ../server.py over
// ../compose.py and ../fit.py). Pieces cut from starred renders (or imports) sit on tracks and may
// overlap; each has a mix strip (monitored live in WebAudio with the server's own filters,
// /sa3/ui/mixer.js), a fix chain (the Fit panel's conform), mute / solo, and a conditioning anchor
// it proposes (overridable). Lanes: one per track, Rewrite (a strength curve drawn over the automatic
// one: drag = a span, click = a keypoint, height = strength; ui/curvelane.js, the same editor as the
// studio's Inpaint timeline; project.json `rewrite`),
// Conditioning (what SA3 is conditioned on per frame; keypoints in Keypoints mode), Reference (the
// built ref.wav: what SA3 keeps), then one lane per take under everything (takeLanes: newest 3 + the
// selected one; ▶ / ■ in the gutter plays from the shared playhead, a click selects it into the
// inspector: score, star, Promote, splice; ↑ ↓ switch takes at the same moment). Takes = one studio
// render per seed; Promote makes a take the base (then layer more pieces over it, or draw edits on it). Every edit autosaves
// project.json and rebuilds the reference; the build's layout comes back and the page redraws.
//
// The BPM is never set by hand: the server's live consensus over every clip on the tracks
// (compose.tempo_consensus); the BPM popover lists each clip's reading against it, furthest first.
// Clips are drawn where the page asked for them (`at`) as soon as you drop them; the server's build
// only adds measured lengths / stretch (geom), so nothing jumps back while it rebuilds.
//
// Over the tracks, a violet tint + grain (alpha = strength) shows how strongly each moment is
// rewritten (strengthAt: the drawn curve live, else the build's automatic strength). The ⚠ chip flags
// time with no audio (no active clip, or the build's `silent` spans) rendered at 0 < u < 1, and fixes it.
//
// Layout is deliberately guided (Xander found the all-visible version unreadable): by default only
// the tracks, the Rewrite lane and the take lanes show; expert settings (takes per render,
// model, steps, seams, conditioning mode) live in the ⚙ Render settings popover, the diagnostic lanes
// (Conditioning, Reference, Before) behind View ▾ (prefs.show; Keypoints mode forces the
// Conditioning lane on), and inspector sections start collapsed (prefs.open). An empty project shows
// the four steps. Add a control to an existing section or popover, not to the header or toolbar.
//
// Track gestures: a click (on a clip or empty space) plays the mix from there at once; drag a clip to
// move it; drag an edge to trim (the picture stays put, the crop window moves); ⌥-drag an edge to
// time-stretch (the piece's `speed`, applied server-side with rubberband: compose._speed). ⌘-click
// adds / removes a clip from the selection, a drag on empty track space rubber-bands one (⇧ / ⌘ adds);
// dragging any selected clip moves the set, and ⌘C / ⌘X / ⌘V, ⌫, ←→, ↑↓, M, S act on all of it
// (S.multi: ids "p<pi>", always holding S.sel when that is a clip; paint() keeps it so).
//
// The project JSON is the only state: edits go through edit(fn) (undo snapshot → mutate →
// autosave → the server's layout comes back and the timeline redraws from it).

import { mountShell, toast } from "/sa3/ui/shell.js";
import * as api from "/sa3/ui/api.js";
import * as player from "/sa3/ui/player.js";
import { load as loadSpec, drawCrop, SpecView } from "/sa3/ui/dadaspec.js";
import { Timeline } from "/sa3/ui/timeline.js";
import { Mixer } from "/sa3/ui/mixer.js";
import { Browser } from "/sa3/ui/browser.js";
import { ClipCard, MIME } from "/sa3/ui/clipcard.js";
import { bind, help } from "/sa3/ui/shortcuts.js";
import { menu } from "/sa3/ui/menu.js";
import { openHighlight } from "/sa3/ui/highlight.js";
import { CurveLane } from "/sa3/ui/curvelane.js";
import * as C from "/sa3/ui/curve.js";

const $ = (id) => document.getElementById(id);
const LS = "sa3-composer";
const MAX_LEN = 380;
const DEFAULT = () => ({ seeds: [], model: { dit: "medium", steps: 8, cfg: 1.0 }, seam: 2, seam_unit: "beats", cond: { gap: "crossfade" }, pieces: [], tracks: 2, master: { gain: 0, limit: true, ceiling: -1 } });
const S = { name: null, proj: null, layout: null, takes: [], job: null, take: null, sel: null, multi: new Set(), undo: [], redo: [],
  starred: [], imports: [], empty: [], saveSeq: 0, saving: false, error: null, cursor: 0, src: "mixp", specs: {}, fit: null };
const mixer = new Mixer();
let prefs = {};
try { prefs = JSON.parse(localStorage.getItem(LS) || "{}"); } catch (e) { prefs = {}; }
const savePrefs = () => { try { localStorage.setItem(LS, JSON.stringify(prefs)); } catch (e) { /* private window */ } };

const fmt = (t) => (t == null ? "–" : t >= 60 ? `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}` : `${t.toFixed(1)} s`);
const short = (s, n = 46) => { s = (s || "").replace(/^anarchy:\s*@?/, "").replace(/\.cond\.npz$/, ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const base = () => !!(S.proj && S.proj.base);
const bpmNow = () => (S.layout && S.layout.bpm) || null;          // the server's live estimate, never set by hand
const beat = () => (bpmNow() ? 60 / bpmNow() : null);
const rowOf = (id) => S.starred.find((r) => r.id === id) || S.imports.find((r) => r.id === id);
const nameOf = (id) => { const r = rowOf(id); return r ? short(r.prompt || r.name || id) : short(String(id).split("/").pop()); };
const PCOL = ["#a99cff", "#5ed1b5", "#ffb86b", "#7ec8ff", "#ff6b81", "#e6e27a", "#c08cff", "#8cffd1"];
const condMode = () => ((S.proj && S.proj.cond) || {}).gap || "crossfade";
const pieces = () => (S.proj && S.proj.pieces) || [];
const nTracks = () => Math.max(S.proj.tracks || 1, ...pieces().map((p) => (p.track || 0) + 1), 1);
const strip = (a) => { if (!a) return a; const { label, ...x } = a; return x; };
const anchorLabel = (a) => (!a ? "–" : a.prompt != null ? (a.prompt || "(empty prompt)") : a.bank ? `${nameOf(a.bank)} #${(a.i || 0) + 1}` : a.id ? nameOf(a.id) : String(a.npz || "").split("/").pop());
// what a piece source was conditioned on (server: condsrc.summary): bag = anarchy, multi = a take's anchors
const srcCond = (id) => {
  const lp = ((S.layout && S.layout.pieces) || []).find((x) => x.id === id && x.cond_kind);
  if (lp) return { kind: lp.cond_kind, n: lp.cond_n || 0 };
  const r = rowOf(id);
  if (!r) return { kind: "none", n: 0 };
  if (r.cond) return { kind: r.cond_n > 1 || r.kind === "composite" ? "multi" : "bag", n: r.cond_n || 1 };
  return r.prompt && r.kind !== "composite" ? { kind: "prompt", n: 1 } : { kind: "none", n: 0 };
};
const condWord = (c) => ({ multi: `its ${c.n} anchors, as rendered`, bag: "anarchy bag", prompt: "its prompt" }[c.kind] || "none (follows the project anchor)");

// ── spec images, drawn on the timeline once loaded ──────────────────────────────────────────
function spec(url) {
  if (!(url in S.specs)) {
    S.specs[url] = null;
    loadSpec(url).then((s) => { S.specs[url] = s; tl.redraw(); })
      .catch(() => { S.specs[url] = false; setTimeout(() => { delete S.specs[url]; tl.redraw(); }, 2000); });
  }
  return S.specs[url];
}
const refQ = () => `name=${encodeURIComponent(S.name)}&v=${(S.layout && S.layout.ref_sha256 || "").slice(0, 12)}`;
const refUrl = () => `/sa3/composer/ref?${refQ()}&spec=1`;
const refAudio = () => `/sa3/composer/ref?${refQ()}`;
const pieceUrl = (k) => `/sa3/composer/piece?name=${encodeURIComponent(S.name)}&k=${k}`;

function hatch(g, x, y, w, h, color = "rgba(155,140,255,0.55)", bg = "rgba(155,140,255,0.10)") {
  g.save();
  g.beginPath(); g.rect(x, y, w, h); g.clip();
  g.fillStyle = bg; g.fillRect(x, y, w, h);
  g.strokeStyle = color; g.lineWidth = 1;
  for (let k = x - h; k < x + w; k += 7) { g.beginPath(); g.moveTo(k, y + h); g.lineTo(k + h, y); g.stroke(); }
  g.restore();
}
function genSpans() {                       // spans the take regenerates (u > 0), from the strength keys
  const k = S.layout && S.layout.keys, out = [];
  if (!k) return out;
  for (let i = 0; i + 1 < k.length; i++) if (k[i][1] > 0 && k[i + 1][1] > 0 && k[i + 1][0] > k[i][0]) out.push([k[i][0], k[i + 1][0], k[i][1]]);
  return out;
}
function sample(keys, t) {                  // compose._sample / segments.frame_strength's rule
  if (!keys || !keys.length) return 0;
  let i = 0;
  while (i < keys.length && keys[i][0] <= t) i++;
  if (i === 0) return keys[0][1];
  if (i >= keys.length) return keys[keys.length - 1][1];
  const a = keys[i - 1], b = keys[i];
  return b[0] > a[0] ? a[1] + ((t - a[0]) / (b[0] - a[0])) * (b[1] - a[1]) : b[1];
}

// ── layout: the server's build, or a naive guess while it runs ──────────────────────────────
function fresh(L) {
  if (!L) return false;
  const act = activeIdx();
  return L.pieces.length === act.length && L.pieces.every((pc) => act.includes(pc.pi) && pieces()[pc.pi] && pieces()[pc.pi].id === pc.id);
}
function activeIdx() {
  const ps = pieces(), solo = ps.some((p) => p.solo);
  return ps.map((p, i) => [p, i]).filter(([p]) => !(p.mute || (solo && !p.solo))).map(([, i]) => i);
}
function geom(pi) {                         // where piece pi sits: its own at / track, at once; the build adds measured length + stretch
  const it = pieces()[pi], L = S.layout, sp = it.speed || 1;   // speed: the piece's own time-stretch (⌥-drag an edge)
  const own = { pi, id: it.id, at: it.at || 0, dur: (it.src[1] - it.src[0]) / sp, src: it.src, stretch: sp, speed: sp, beat: false, track: it.track || 0 };
  const pc = L && L.pieces.find((x) => x.pi === pi && x.id === it.id);
  if (!pc) return own;
  const same = Math.abs(pc.src[0] - it.src[0]) < 1e-3 && Math.abs(pc.src[1] - it.src[1]) < 1e-3 && Math.abs((pc.speed || 1) - sp) < 1e-4;
  const auto = (pc.stretch || 1) / (pc.speed || 1);            // the beat alignment's own factor (stretch = auto × speed)
  const asked = pc.at_req != null && Math.abs(pc.at_req - (it.at || 0)) < 1e-3;   // built for this position: keep its grid snap
  return { ...pc, at: asked ? pc.at : own.at, track: own.track, speed: sp, stretch: same ? pc.stretch : auto * sp,
    dur: same ? pc.dur : (it.src[1] - it.src[0]) / (auto * sp), src: same ? pc.src : it.src, src_cut: same ? pc.src_cut : undefined };
}
function projLength() {
  const ends = pieces().map((p, i) => { const g = geom(i); return g.at + g.dur; });
  return Math.max(S.proj.length || 0, ...ends, 0) || (S.layout && S.layout.length) || 0;
}

// ── lanes ───────────────────────────────────────────────────────────────────────────────────
function badges(pi) {
  const r = S.fit && S.fit.pieces.find((x) => x.pi === pi);
  if (!r) return [];
  const order = ["tempo", "key", "loudness", "bands", "width", "lora"];
  const worst = order.map((k) => r.dims[k] && r.dims[k].state).filter((s) => s === "warn" || s === "bad");
  return worst.length ? worst.slice(0, 3) : ["ok"];
}
const DIM = { tempo: "tempo", key: "key", loudness: "loudness", bands: "tone (bass / mids / highs balance)", width: "stereo width", lora: "LoRA" };
function badgeTip(pi) {                      // the dots' tooltip: what each one says about this clip
  const r = S.fit && S.fit.pieces.find((x) => x.pi === pi);
  if (!r) return "";
  const bad = Object.entries(r.dims).filter(([k, v]) => DIM[k] && (v.state === "warn" || v.state === "bad"));
  if (!bad.length) return "Fit check: this clip matches the others (tempo, key, loudness, tone, width).";
  return "Fit check, how this clip compares with the others (orange = a little off, red = far off):\n"
    + bad.map(([k, v]) => `${v.state === "bad" ? "●" : "○"} ${DIM[k]}: ${v.msg}`).join("\n")
    + "\nClick the clip → \"Fit to the others\" to fix it, or ignore: they only matter if the clips should blend.";
}
function trackItems(k) {
  const items = [];
  pieces().forEach((it, pi) => {
    if ((it.track || 0) !== k) return;
    const g = geom(pi), act = activeIdx().includes(pi);
    const cut = g.src_cut || g.src || it.src;
    const srcLen = g.src_len || (rowOf(it.id) || {}).seconds || cut[1];
    const tag = g.beat ? `${(+g.bpm).toFixed(1)} BPM${Math.abs((g.stretch || 1) - 1) > 0.002 ? ` ×${g.stretch.toFixed(3)}` : ""}` : g.bpm != null && g.conf >= 0.3 ? `${(+g.bpm).toFixed(1)} BPM` : g.bpm != null ? "free time" : "";
    const isBase = S.proj.base && it.id === S.proj.base;
    const fx = (it.fix && Object.keys(it.fix).length ? " · fixed" : "") + ((it.speed || 1) !== 1 ? ` · speed ×${(+it.speed).toFixed(2)}` : "");
    items.push({ id: "p" + pi, pi, kind: "piece", t0: g.at, t1: g.at + g.dur, move: !isBase, resize: isBase ? "" : "lr", stretch: !isBase, srcLen, g,
      tip: isBase ? "the promoted take: the timeline itself (layer clips over it, or rewrite parts of it on the Rewrite lane)"
        : "click: play the mix from here + settings on the right · drag: move (⇧ free, else snaps to beats) · drag an edge: trim · ⌥-drag an edge: time-stretch · ⌘-click or drag on empty space: select several · ↑↓ track, ←→ one beat, M mute, S solo, ⌘C / ⌘V, ⌫ delete",
      label: `${isBase ? "base · " : ""}${nameOf(it.id)}${tag ? "  ·  " + tag : ""}${fx}${it.mute ? " · muted" : ""}${it.solo ? " · solo" : ""}`,
      stroke: it.anchor ? "rgba(255,212,121,0.7)" : "rgba(94,209,181,0.55)", badges: act ? badges(pi) : [], badgeTip: act ? badgeTip(pi) : "",
      draw: (gx, r) => {
        const s = spec(api.specUrl(it.id));
        gx.fillStyle = "#0b0b10"; gx.fillRect(r.x, r.y + 2, r.w, r.h - 4);
        // the crop: a trim (an edge drag) slides the window over the source, so the picture stays where it
        // is and only the edge moves; a stretch ("ls" / "rs") and a move draw the whole clip into the rect
        let c0 = cut[0], c1 = cut[1], x0 = r.x, x1 = r.x + r.w;
        if ((r.kind === "l" || r.kind === "r") && r.t1 > r.t0) {
          const K = (cut[1] - cut[0]) / Math.max(1e-6, g.dur), pps = r.w / (r.t1 - r.t0);   // source s per timeline s
          c0 = Math.max(0, cut[0] + (r.t0 - g.at) * K); c1 = Math.min(srcLen, cut[0] + (r.t1 - g.at) * K);
          x0 = r.x + (g.at + (c0 - cut[0]) / K - r.t0) * pps; x1 = r.x + (g.at + (c1 - cut[0]) / K - r.t0) * pps;
        }
        if (s && c1 > c0) drawCrop(gx, s, srcLen, c0, c1, x0, r.y + 2, x1 - x0, r.h - 4);
        if (!act) { gx.fillStyle = "rgba(9,9,13,0.7)"; gx.fillRect(r.x, r.y + 2, r.w, r.h - 4); }
        const gain = ((it.mix || {}).gain || 0);
        if (gain < 0) { gx.fillStyle = `rgba(9,9,13,${Math.min(0.6, -gain / 30)})`; gx.fillRect(r.x, r.y + 2, r.w, r.h - 4); }
      } });
  });
  return items;
}
function condMarkers() {
  if (condMode() !== "keys") return [];
  const ks = (S.proj.cond && S.proj.cond.keys) || [];
  const col = new Map();
  return ks.map((k, i) => {
    const j = JSON.stringify(k.anchor || {});
    if (!col.has(j)) col.set(j, PCOL[col.size % PCOL.length]);
    return { id: "k" + i, i, t: k.t, label: anchorLabel(k.anchor), color: col.get(j), cut: !!k.cut };
  });
}
function drawCond(g, r, t) {
  const c = S.layout && S.layout.cond;
  if (!c || !c.anchors.length) {
    g.fillStyle = "#6d6d7c"; g.font = "11.5px Inter"; g.fillText(S.error ? "build error: see the status chip" : "built conditioning shows here", r.x + 12, r.y + r.h / 2);
    return;
  }
  const x0 = Math.max(r.x, t.x(0)), x1 = Math.min(r.x + r.w, t.x(S.layout.length));
  const top = r.y + 3, h = r.h - 6, best = c.anchors.map(() => [0, 0]);
  for (let x = x0; x < x1; x += 2) {
    const tt = t.t(x);
    let y = top + h;
    c.curves.forEach((cv, i) => {
      const w = Math.max(0, sample(cv, tt));
      if (w > best[i][0]) best[i] = [w, x];
      if (w <= 0) return;
      g.fillStyle = PCOL[i % PCOL.length] + "55";
      g.fillRect(x, y - w * h, 2, w * h);
      y -= w * h;
    });
  }
  g.font = "10.5px Inter"; g.textBaseline = "top";
  if (condMode() !== "keys") c.anchors.forEach((a, i) => {
    if (best[i][0] < 0.5) return;
    const s = short(a.label || anchorLabel(a), 40), tw = g.measureText(s).width + 8, x = Math.min(best[i][1], x1 - tw - 4);
    g.fillStyle = "rgba(9,9,13,0.78)"; g.fillRect(x, r.y + 3 + (i % 2) * 17, tw, 15);
    g.fillStyle = PCOL[i % PCOL.length]; g.fillText(s, x + 4, r.y + 5.5 + (i % 2) * 17);
  });
}
const shown = (k) => !!((prefs.show || {})[k]) || (k === "cond" && condMode() === "keys");
// how much the render rewrites at t: the drawn Rewrite curve where it is above 0 (live while you drag),
// else the build's automatic strength (gaps between clips, seams)
function strengthAt(t) {
  const u = rw.K.length ? C.at(rw.K, t) : 0;
  return u > 0 ? u : (S.layout && S.layout.keys ? sample(S.layout.keys, t) : 0);
}
let noisePat = null;
function noise(g) {                          // a static grain pattern, one grain per screen pixel
  if (!noisePat) {
    const c = document.createElement("canvas"), n = 192, dpr = devicePixelRatio || 1;
    c.width = c.height = n;
    const cx = c.getContext("2d"), im = cx.createImageData(n, n);
    for (let i = 0; i < n * n; i++) {          // sparse light grain: TV static over the audio, not a wall
      const v = 150 + Math.random() * 105, on = Math.random();
      im.data.set([v * 0.92, v * 0.9, v, on < 0.55 ? Math.round(Math.random() * 255) : 0], i * 4);
    }
    cx.putImageData(im, 0, 0);
    noisePat = g.createPattern(c, "repeat");
    if (noisePat.setTransform) noisePat.setTransform(new DOMMatrix().scale(1 / dpr));
  }
  return noisePat;
}
// time with no audio at all (no active clip covers it, or the built reference is silent there) that the
// render only partly rewrites (0 < u < 1): denoising silence halfway is almost never intended
function emptyIssues() {
  const L = projLength();
  if (!L) return [];
  const cov = activeIdx().map((pi) => { const g = geom(pi); return [g.at, g.at + g.dur]; });
  const sil = (S.layout && S.layout.silent) || [];
  const inAny = (sp, t) => sp.some(([a, b]) => t >= a && t < b);
  const out = [];
  let run = null;
  for (let t = C.SPL / 2; t < L + C.SPL; t += C.SPL) {
    const u = t < L ? strengthAt(t) : 0;
    const bad = t < L && (!inAny(cov, t) || inAny(sil, t)) && u > 0.001 && u < 0.99;
    if (bad && run) { run.b = t + C.SPL / 2; run.u = Math.min(run.u, u); }
    else if (bad) run = { a: Math.max(0, t - C.SPL / 2), b: t + C.SPL / 2, u };
    else if (run) { if (run.b - run.a >= 0.3) out.push(run); run = null; }
  }
  return out;
}
function emptyChip() {
  const el = $("emptywarn"), n = S.empty.length;
  el.hidden = !n;
  if (!n) return;
  el.textContent = `⚠ ${n} empty stretch${n === 1 ? "" : "es"} only partly rewritten`;
  el.title = "No audio here (no clip, or silent audio), but the strength is below 1: SA3 starts from near-silence and only partly re-renders it, which usually comes out as quiet mush. Click to jump there or fix it.\n"
    + S.empty.map((q) => `${fmt(q.a)} – ${fmt(q.b)} · strength down to ${q.u.toFixed(2)}`).join("\n");
}
function fixEmpty() {
  edit((p) => {
    let K = (p.rewrite || []).map((k) => k.slice());
    const near = (t) => { const k = K.map((x) => x[0]).filter((x) => Math.abs(x - t) <= C.SPL * 1.01); return k.length ? k.reduce((m, x) => (Math.abs(x - t) < Math.abs(m - t) ? x : m)) : t; };
    for (const q of S.empty) K = C.setRange(K, +near(q.a).toFixed(3), +near(q.b).toFixed(3), 1);   // onto the curve's own edges: no slivers
    K = C.norm(K);
    if (K.length) p.rewrite = K; else delete p.rewrite;
  });
  toast("Those stretches are now fully regenerated (strength 1 on the Rewrite lane).");
}
function strengthOver(g, r, t) {             // over every track: a violet tint + grain as strong as the rewrite there
  const L = projLength();
  if (!L) return;
  const a = Math.max(r.x, t.x(0)), b = Math.min(r.x + r.w, t.x(L));
  for (let x = a; x < b; x += 3) {
    const u = strengthAt(t.t(x + 1.5));
    if (u <= 0) continue;
    g.fillStyle = `rgba(155,140,255,${(0.04 + 0.1 * u).toFixed(3)})`; g.fillRect(x, r.y, 3, r.h);
    g.globalAlpha = Math.min(1, u * 0.7); g.fillStyle = noise(g); g.fillRect(x, r.y, 3, r.h); g.globalAlpha = 1;
  }
}
// the Rewrite lane: the drawn strength curve (CurveLane) over the automatic one (gaps, seams: from the build)
function drawAuto(g, r, t) {
  // the automatic part (no clip there → generated; seams around clip edges): a thin hatched band
  // along the lane bottom, labelled, so it never reads as a span you drew (it can't be grabbed)
  const k = S.layout && S.layout.keys, runs = [];
  if (k && k.length) {
    const x0 = Math.max(r.x, t.x(0)), x1 = Math.min(r.x + r.w, t.x(S.layout.length));
    let run = null;
    for (let x = x0; x < x1; x += 2) {
      const tt = t.t(x + 1), u = sample(k, tt) > 0 && !(rw.K.length && C.at(rw.K, tt) > 0) ? sample(k, tt) : 0;
      if (u > 0 && run) run.b = x + 2;
      else if (u > 0) run = { a: x, b: x + 2, u };
      else if (run) { runs.push(run); run = null; }
    }
    if (run) runs.push(run);
  }
  const bh = 16, by = r.y + r.h - bh;
  for (const q of runs) {
    hatch(g, q.a, by, q.b - q.a, bh, q.u >= 1 ? "rgba(155,140,255,0.45)" : "rgba(62,230,196,0.45)", "rgba(155,140,255,0.06)");
    const s = q.u >= 1 ? "auto: no clip here, generated" : "auto: seam";
    g.font = "10.5px Inter"; g.textBaseline = "middle";
    const w = g.measureText(s).width + 8;
    if (q.b - q.a > w + 4) { g.fillStyle = "rgba(9,9,13,0.8)"; g.fillRect(q.a + 3, by + 1, w, bh - 2); g.fillStyle = "#b9b0ff"; g.fillText(s, q.a + 7, by + bh / 2); }
  }
  for (const q of S.empty || []) {             // empty but only partly rewritten: amber, along the top
    const a = Math.max(r.x, t.x(q.a)), b = Math.min(r.x + r.w, t.x(q.b));
    if (b <= a) continue;
    hatch(g, a, r.y, b - a, 12, "rgba(255,184,107,0.8)", "rgba(255,184,107,0.12)");
    if (b - a > 26) { g.fillStyle = "#ffb86b"; g.font = "600 11px Inter"; g.textBaseline = "top"; g.fillText("⚠", a + 4, r.y + 14); }
  }
  g.fillStyle = "rgba(255,255,255,0.06)"; g.fillRect(r.x, r.y + r.h / 2, r.w, 1);   // u = 0.5
  if (!rw.K.length && pieces().length) {
    g.fillStyle = "#8b8b9c"; g.font = "11.5px Inter"; g.textBaseline = "middle";
    g.fillText("drag: draw a span (height = strength: top regenerates, low re-renders softly)  ·  double-click: add a point", r.x + 12, r.y + r.h / 2 - 12);
  }
  rw.paint(g, r, t, { alpha: 0.3 });
}
function lanes() {
  const out = [];
  if (shown("ghost") && (S.proj.ghost || []).length) out.push({ key: "ghost", label: "Before", h: 26, tip: "the clips the promoted take was rendered from (greyed: history only)",
    items: S.proj.ghost.map((p, i) => ({ id: "ghost" + i, t0: p.at, t1: p.at + p.dur, label: nameOf(p.id), color: "rgba(255,255,255,0.06)" })) });
  for (let k = 0; k < nTracks(); k++) {
    out.push({ key: "t" + k, label: `Track ${k + 1}`, flex: 1.1, tip: "a track: clips on different tracks play together (summed); SA3 sees the mix",
      items: trackItems(k), over: strengthOver, onEdit: pieceEdit, marquee: true,
      onClick: (t) => playAt(t),
      onItemClick: (it, t, e) => { if (!(e.metaKey || e.ctrlKey || e.shiftKey)) playAt(t); },
      draw: (g, r) => { if (!pieces().length && k === 0) { g.fillStyle = "#8b8b9c"; g.font = "13px Inter"; g.fillText("Drag a clip here from the library on the left.", r.x + 16, r.y + r.h / 2); } } });
  }
  out.push({ key: "rewrite", label: "Rewrite", sub: "strength\n0 keep · 1 new", h: 74, items: [], draw: drawAuto, pointer: rwPointer,
    tip: "Rewrite: how strongly SA3 re-renders each moment (0 keeps, 1 regenerates, between = same arrangement, new sound). Drag on empty space: a span. Drag a point or a line to reshape. Double-click: add / delete a point. Gaps between clips and seams are generated automatically (the hatched band along the bottom: it follows the clips, you can't drag it)." });
  if (shown("cond")) out.push({ key: "cond", label: "Conditioning", sub: condMode() === "keys" ? "click: add" : "", h: 58,
    tip: "what SA3 is told the music is, at each moment (one color per source). In Keypoints mode: click to add a keypoint, drag to move, click one to edit",
    items: [], draw: drawCond, markers: condMarkers(),
    onClick: (t, e) => (condMode() === "keys" ? addKey(t) : seek(t)),
    onMarker: (m, t) => edit((p) => { p.cond.keys[m.i].t = +t.toFixed(3); p.cond.keys.sort((a, b) => a.t - b.t); }),
    onMarkerClick: (m) => { S.sel = m.id; inspector(); } });
  if (shown("ref")) out.push({ key: "ref", label: "Reference", sub: "violet = generated", flex: 0.8, items: [], tip: "the built reference: the summed mix SA3 keeps where the strength is 0 (hatched = rewritten)",
    draw: (g, r, t) => {
      if (!S.layout) return;
      const s = spec(refUrl());
      if (s) drawCrop(g, s, S.layout.length, 0, S.layout.length, t.x(0), r.y + 2, t.x(S.layout.length) - t.x(0), r.h - 4);
      for (const [a, b, u] of genSpans()) hatch(g, t.x(a), r.y + 2, t.x(b) - t.x(a), r.h - 4, u >= 1 ? "rgba(155,140,255,0.35)" : "rgba(62,230,196,0.3)", "rgba(0,0,0,0)");
    } });
  return out.concat(takeLanes());
}

// ── take lanes: every render lands under the tracks, on the same time axis (newest first) ──────
// The gutter's ▶ / ■ plays that take from the playhead; a click on the take selects it (score, star,
// Promote in the inspector) and moves the playhead there, and while anything plays it switches to that
// take at that moment, so takes A/B at the same bar. ↑ / ↓ step through takes the same way.
const TAKE_ROWS = 3;                          // newest takes shown as lanes (+ the selected one); older ones behind "+N older"
const takeNo = (tk) => S.takes.length - S.takes.indexOf(tk);
const playingTake = () => (player.isPlaying("take") ? S.playingTake : null);
const laneText = (g, r, s, color = "#8b8b9c") => { g.fillStyle = color; g.font = "12px Inter"; g.textBaseline = "middle"; g.fillText(s, r.x + 14, r.y + r.h / 2); };
function takeLanes() {
  const out = [], j = S.job, pend = j && j.running ? j.total - j.done : 0;
  for (let k = 0; k < pend; k++) out.push({ key: "pend" + k, label: "Rendering", sub: `${j.done + k + 1} of ${j.total}`, h: 40, items: [],
    tip: "a take being rendered in the studio's queue: it lands here when done",
    draw: (g, r, t) => {
      const [a, b] = j.section || [0, projLength()];
      hatch(g, t.x(a), r.y + 2, t.x(b) - t.x(a), r.h - 4, "rgba(155,140,255,0.22)", "rgba(155,140,255,0.04)");
      laneText(g, { ...r, x: Math.max(r.x, t.x(a)) }, j.section ? `rendering ${fmt(a)} – ${fmt(b)} in the studio queue…` : "rendering in the studio queue…");
    } });
  if (!S.takes.length) {
    if (!pend && pieces().length) out.push({ key: "notakes", label: "Takes", h: 30, items: [], tip: "rendered takes land here, lined up with the tracks",
      draw: (g, r) => laneText(g, r, "No takes yet: press Render (top right). Each take lands here, lined up with the tracks.") });
    return out;
  }
  const vis = S.takes.slice(0, TAKE_ROWS), cur = S.takes.find((x) => x.id === S.take);
  if (cur && !vis.includes(cur)) vis.push(cur);
  for (const tk of vis) out.push(takeLane(tk));
  const rest = S.takes.length - vis.length;
  if (rest > 0) out.push({ key: "older", label: "", h: 22, items: [], tip: "the older takes of this project: click to pick one",
    draw: (g, r) => laneText(g, r, `+ ${rest} older take${rest === 1 ? "" : "s"} ▾`, "#b9b0ff"), onClick: (t, e) => olderMenu(e) });
  return out;
}
const takeOff = (tk) => (tk && tk.section ? tk.section[0] : 0);   // a section take sits at its section's start
function setSection(r) {                      // the ruler's blue section: Render renders only that (null = the whole piece)
  S.range = r && r[1] - r[0] >= 0.25 ? r : null;
  tl.setRange(S.range);
  paint();
}
function takeLane(tk) {
  const isBase = S.proj.base === tk.id;
  const sub = [isBase ? "base" : tk.section ? "section" : "", tk.score != null ? `${tk.score}/10` : "", tk.starred ? "★" : ""].filter(Boolean).join(" · ");
  return { key: "take:" + tk.id, takeId: tk.id, label: `Take ${takeNo(tk)}`, sub, h: 46, items: [],
    tip: `seed ${tk.seed} · ${condName(tk.variant)}. ▶ / ■: play this take from the playhead. Click the take: select it (score, star, Promote on the right) and move the playhead; while playing, it switches to this take at that moment. ↑ ↓: the next take, same moment.`,
    draw: (g, r, t) => {
      const s = spec(api.specUrl(tk.id)), len = tk.length || projLength(), off = takeOff(tk), a = t.x(off), w = t.x(off + len) - a;
      if (s) drawCrop(g, s, len, 0, len, a, r.y + 2, w, r.h - 4);
      else { g.fillStyle = "rgba(255,255,255,0.04)"; g.fillRect(a, r.y + 2, w, r.h - 4); }
      if (S.take === tk.id) { g.strokeStyle = "#c4bbff"; g.lineWidth = 2; g.strokeRect(a + 1, r.y + 1, w - 2, r.h - 2); }
      if (tk.ref_sha256 && S.layout && tk.ref_sha256 !== S.layout.ref_sha256) {
        g.font = "11px Inter"; g.textBaseline = "middle";
        const s2 = "older layout", tw = g.measureText(s2).width + 10;
        g.fillStyle = "rgba(9,9,13,0.75)"; g.fillRect(r.x + r.w - tw - 6, r.y + 4, tw, 16);
        g.fillStyle = "#ffb86b"; g.fillText(s2, r.x + r.w - tw - 1, r.y + 12);
      }
    },
    gutter: (g, r) => {                         // ▶ / ■ on the right of the label; a teal dot = not heard yet
      const on = playingTake() === tk.id, cx = r.x + r.w - 14, cy = r.y + r.h / 2;
      g.fillStyle = on ? "#9b8cff" : "rgba(255,255,255,0.10)";
      g.beginPath(); g.arc(cx, cy, 11, 0, 7); g.fill();
      g.fillStyle = on ? "#0b0b10" : "#e8e8f0";
      if (on) g.fillRect(cx - 4, cy - 4, 8, 8);
      else { g.beginPath(); g.moveTo(cx - 3.5, cy - 5.5); g.lineTo(cx - 3.5, cy + 5.5); g.lineTo(cx + 5.5, cy); g.closePath(); g.fill(); }
      if (!tk.played) { g.fillStyle = "#3fc6c0"; g.beginPath(); g.arc(r.x + 4.5, r.y + 14, 2.5, 0, 7); g.fill(); }
    },
    onGutter: () => (playingTake() === tk.id ? player.stop() : playTake(tk.id, S.cursor)),
    onClick: (t) => pickTake(tk.id, t) };
}
function olderMenu(e) {
  menu(e.clientX, e.clientY, S.takes.slice(TAKE_ROWS).map((tk) => ({
    label: `Take ${takeNo(tk)} · seed ${tk.seed}${tk.score != null ? ` · ${tk.score}/10` : ""}${tk.starred ? " ★" : ""}${S.proj.base === tk.id ? " · base" : ""}`,
    fn: () => pickTake(tk.id, S.cursor) })));
}
function useTake(id) {                        // make `id` the selected take and ▶ / space play it
  if (S.src !== "take") S.mixSrc = S.src;
  S.src = "take"; S.take = id; S.sel = "T:" + id; rw.sel = null;
}
function playTake(id, t) {
  const tk = S.takes.find((x) => x.id === id);
  if (!tk) return;
  useTake(id);
  const a = takeOff(tk), len = tk.length || projLength();
  playFrom("take", t >= a && t < a + len - 0.25 ? t : a);   // outside the take (a section): from its start
}
function pickTake(id, t) {
  const was = mixer.playing || player.isPlaying();
  S.cursor = t;
  if (was) return playTake(id, t);
  useTake(id);
  tl.setPlayhead(t); clock(t); playBtn(); paint();
}
function stepTake(d) {                        // ↑ = the newer take, ↓ = the older one, at the same moment
  const i = S.takes.findIndex((x) => x.id === S.take), nt = S.takes[Math.max(0, Math.min(S.takes.length - 1, i + d))];
  if (!nt || nt.id === S.take) return;
  if (player.isPlaying("take")) playTake(nt.id, player.now() != null ? player.now() + takeOff(S.takes[i]) : S.cursor);
  else { useTake(nt.id); playBtn(); paint(); }
}
const COND_NOTE = {
  crossfade: "Auto: each clip's own source (its prompt or anarchy conditioning) over that clip, crossfaded across the gaps between clips.",
  global: "One anchor: one source for the whole timeline (set it in the Project panel's Conditioning section).",
  keys: "Keypoints: your own sources at chosen times, crossfaded between them, edited on the Conditioning lane.",
};
const foldBpm = (b, ref) => [0.25, 0.5, 1, 2, 4].map((f) => b * f).reduce((x, y) => (Math.abs(Math.log2(y / ref)) < Math.abs(Math.log2(x / ref)) ? y : x));
let bpmKey = "";
function bpmPop() {                           // every clip's tempo reading against the live estimate, furthest first
  const b = bpmNow();
  const rows = activeIdx().map((pi) => {
    const g = geom(pi), ok = g.bpm != null && g.conf >= 0.3;
    const dev = ok && b ? Math.abs(foldBpm(g.bpm, b) / b - 1) : null;
    return { pi, g, ok, dev };
  }).sort((x, y) => (y.dev ?? -1) - (x.dev ?? -1));
  const k = b + "|" + JSON.stringify(rows.map((r) => [r.pi, r.g.bpm, r.g.conf, r.dev]));
  if (k === bpmKey) return;
  bpmKey = k;
  const host = $("bpmlist");
  host.innerHTML = "";
  if (!rows.length) { host.innerHTML = '<div class="note">No clips on the tracks yet.</div>'; return; }
  for (const r of rows) {
    const st = r.dev == null ? "na" : r.dev < 0.02 ? "ok" : r.dev < 0.08 ? "warn" : "bad";
    const read = r.ok ? `${(+r.g.bpm).toFixed(1)}${b && Math.abs(foldBpm(r.g.bpm, b) - r.g.bpm) > 0.5 ? ` (→ ${foldBpm(r.g.bpm, b).toFixed(1)})` : ""}` : r.g.bpm == null ? "measuring…" : "no clear beat";
    const d = el(`<div class="brow" title="click: select this clip"><i class="dot ${st}"></i><span class="nm">${esc(nameOf(pieces()[r.pi].id))}</span><span class="mono">${read}</span><span class="mono dv">${r.dev == null ? "" : (r.dev * 100).toFixed(1) + " %"}</span></div>`);
    d.onclick = () => { S.sel = "p" + r.pi; rw.sel = null; tl.select(S.sel); inspector(); $("bpmpop").hidden = true; };
    host.appendChild(d);
  }
}
const condName = (v) => ({ m1: "auto conditioning", global: "one anchor", keys: "keypoints" }[v] || v || "");
const nSeeds = () => Math.max(1, Math.min(16, +$("nseeds").value || 2));

// ── edits ────────────────────────────────────────────────────────────────────────────────────
function edit(fn, { save = true } = {}) {
  S.undo.push(JSON.stringify(S.proj));
  if (S.undo.length > 200) S.undo.shift();
  S.redo = [];
  fn(S.proj);
  paint();
  if (save) scheduleSave();
}
function undo(dir) {
  const from = dir < 0 ? S.undo : S.redo, to = dir < 0 ? S.redo : S.undo;
  if (!from.length) return;
  to.push(JSON.stringify(S.proj));
  S.proj = JSON.parse(from.pop());
  S.sel = null;
  paint();
  scheduleSave();
}
function pieceEdit(it, a, b, kind) {
  if (it.kind !== "piece") return;
  const k = it.g.stretch || 1;
  edit((p) => {
    const pc = p.pieces[it.pi];
    if (kind === "ls" || kind === "rs") {           // ⌥ + edge: time-stretch to the new length (the source range stays)
      const auto = k / (it.g.speed || 1);           // the beat alignment's factor, without the old speed
      const v = Math.min(4, Math.max(0.25, (pc.src[1] - pc.src[0]) / auto / Math.max(0.1, b - a)));
      if (Math.abs(v - 1) < 0.005) delete pc.speed; else pc.speed = +v.toFixed(4);
      if (kind === "ls") pc.at = +Math.max(0, a).toFixed(4);
    } else if (kind === "l") {
      const src0 = Math.max(0, Math.min(pc.src[1] - 0.5, pc.src[0] + (a - it.t0) * k));
      pc.at = +Math.max(0, (pc.at || 0) + (src0 - pc.src[0]) / k).toFixed(4);
      pc.src = [+src0.toFixed(3), pc.src[1]];
    } else if (kind === "r") {
      pc.src = [pc.src[0], +Math.min(it.srcLen || 1e9, Math.max(pc.src[0] + 0.5, pc.src[1] + (b - it.t1) * k)).toFixed(3)];
    } else pc.at = +Math.max(0, a).toFixed(4);
  });
}
function curAnchorAt(t) {
  const c = S.layout && S.layout.cond;
  if (!c || !c.anchors.length) return pieces().length ? { id: pieces()[0].id } : { prompt: "" };
  let bi = 0, bw = -1;
  c.curves.forEach((cv, i) => { const w = sample(cv, t); if (w > bw) { bw = w; bi = i; } });
  return strip(c.anchors[bi]);
}
function addKey(t) {
  edit((p) => {
    p.cond = p.cond || {};
    p.cond.keys = (p.cond.keys || []).concat([{ t: +t.toFixed(3), anchor: curAnchorAt(t) }]).sort((a, b) => a.t - b.t);
    const i = p.cond.keys.findIndex((k) => Math.abs(k.t - t) < 1e-3);
    S.sel = "k" + i;
  });
  inspector();
}
function toKeys() {                         // the auto curves → explicit keypoints (start / end of each plateau)
  const c = S.layout && S.layout.cond;
  if (!c || !c.anchors.length) return toast("Build first: the keypoints start from the built conditioning.");
  const L = S.layout.length, ks = [];
  let cur = -1, runStart = 0;
  for (let t = 0; t <= L + 1e-6; t += 0.05) {
    let bi = -1;
    c.curves.forEach((cv, i) => { if (sample(cv, t) >= 0.98) bi = i; });
    if (bi !== cur) {
      if (cur >= 0) ks.push({ t: +(t - 0.05).toFixed(3), anchor: strip(c.anchors[cur]) });
      if (bi >= 0) ks.push({ t: +t.toFixed(3), anchor: strip(c.anchors[bi]) });
      cur = bi; runStart = t;
    }
  }
  if (cur >= 0 && L - runStart > 0.1) ks.push({ t: +L.toFixed(3), anchor: strip(c.anchors[cur]) });
  const out = ks.filter((k, i) => !(i && Math.abs(k.t - ks[i - 1].t) < 0.06 && JSON.stringify(k.anchor) === JSON.stringify(ks[i - 1].anchor)));
  edit((p) => { p.cond = { ...(p.cond || {}), gap: "keys", keys: out.length ? out : [{ t: 0, anchor: curAnchorAt(0) }] }; });
  toast(`${out.length} keypoints from the automatic conditioning: drag, click to edit, click the lane to add.`);
}
// the selected clips (indices, ascending): the multi-selection, else the one selected clip
function selPis() {
  const ids = S.multi.size ? [...S.multi] : S.sel && S.sel[0] === "p" ? [S.sel] : [];
  return ids.map((id) => +id.slice(1)).filter((pi) => pieces()[pi]).sort((a, b) => a - b);
}
const isBasePi = (pi) => !!(S.proj.base && pieces()[pi] && pieces()[pi].id === S.proj.base);
function setMulti(ids, primary = null) {        // replace the multi-selection; S.sel = the clip the inspector shows
  S.multi = new Set(ids);
  S.sel = primary && S.multi.has(primary) ? primary : [...S.multi][0] || null;
  tl.multi = S.multi; tl.sel = S.sel;
}
function playAt(t) {                            // a click on a track: the mix from there, now (a take playing switches to it)
  S.cursor = t; tl.setPlayhead(t); clock(t);
  if (pieces().length) playMix(t);
}
function deleteSel() {
  const id = S.sel;
  if (!id) return;
  if (id[0] === "p") {
    const pis = selPis(), del = pis.filter((pi) => !isBasePi(pi));
    if (!del.length) return toast("The base is the take itself: promote another take, or mute it.");
    if (del.length < pis.length) toast("The base take stays (it is the timeline itself); the other clips are deleted.");
    edit((p) => { for (const pi of del.reverse()) p.pieces.splice(pi, 1); });
    S.multi = new Set();
  }
  else if (id === "w") { rw.del(); S.sel = rw.sel ? "w" : null; return inspector(); }
  else if (id[0] === "k") edit((p) => { p.cond.keys.splice(+id.slice(1), 1); });
  S.sel = null;
  paint();
}
function addPiece(row, range, { at = null, track = 0 } = {}) {
  edit((p) => {
    const ends = p.pieces.filter((x) => (x.track || 0) === track).map((x, i) => { const g = geom(p.pieces.indexOf(x)); return g.at + g.dur; });
    const t = at != null ? at : ends.length ? Math.max(...ends) + (beat() ? 2 * beat() : 4) : 0;
    p.pieces.push({ id: row.id, src: [+range[0].toFixed(3), +range[1].toFixed(3)], at: +t.toFixed(4), track });
    S.sel = "p" + (p.pieces.length - 1);
  });
}
function nudge(dir, e) {
  const id = S.sel;
  if (id === "w") return rw.nudge(dir * (e && e.shiftKey ? 1 : C.SPL));
  if (!id || id[0] !== "p") return;
  const pis = selPis().filter((pi) => !isBasePi(pi)), b = beat() || 0.1;
  if (!pis.length) return;
  const dt = Math.max(dir * b, -Math.min(...pis.map((pi) => pieces()[pi].at || 0)));   // the set keeps its shape: nobody before 0
  if (Math.abs(dt) < 1e-6) return;
  edit((p) => { for (const pi of pis) p.pieces[pi].at = +((p.pieces[pi].at || 0) + dt).toFixed(4); });
}
function moveTrack(dir) {
  const id = S.sel;
  if (!id || id[0] !== "p") return;
  const pis = selPis();
  if (!pis.length || (dir < 0 && pis.some((pi) => !(pieces()[pi].track || 0)))) return;   // the set keeps its shape
  edit((p) => { for (const pi of pis) { const t = (p.pieces[pi].track || 0) + dir; p.pieces[pi].track = t; p.tracks = Math.max(p.tracks || 1, t + 1); } });
}
// clipboard: ⌘C / ⌘X / ⌘V for clips (one or a selected set). Kept in localStorage, so clips copied in
// one project paste into another (pieces point at render ids, which are global). Shape:
// {kind: "pieces", items: [{data, dt, dk}], dur, track}: each clip's offset from the set's first start
// (dt) and its top track (dk), so a set pastes with its layout; an older single {kind: "piece", data} still reads.
const CLIP = "sa3-composer-clip";
function copySel(cut = false) {
  const id = S.sel;
  if (!id || id[0] !== "p") return toast("Select a clip to copy.");
  const pis = selPis().filter((pi) => !isBasePi(pi));
  if (!pis.length) return toast("The base take can't be copied: it is the timeline itself.");
  const gs = pis.map((pi) => ({ g: geom(pi), pc: pieces()[pi] }));
  const t0 = Math.min(...gs.map((x) => x.g.at)), k0 = Math.min(...gs.map((x) => x.pc.track || 0));
  const item = { kind: "pieces", track: k0, dur: +(Math.max(...gs.map((x) => x.g.at + x.g.dur)) - t0).toFixed(4),
    items: gs.map(({ g, pc }) => ({ data: { ...JSON.parse(JSON.stringify(pc)), solo: false }, dt: +(g.at - t0).toFixed(4), dk: (pc.track || 0) - k0 })) };
  try { localStorage.setItem(CLIP, JSON.stringify(item)); } catch (e) { /* private window */ }
  S.clipMem = item;
  if (cut) deleteSel();
  const what = pis.length === 1 ? "the clip" : `${pis.length} clips`;
  toast(`${cut ? "Cut" : "Copied"} ${what}: ⌘V pastes after the selection, or at the playhead.`, { ms: 2500 });
}
function pasteClip() {
  let item = S.clipMem;
  try { item = JSON.parse(localStorage.getItem(CLIP) || "null") || item; } catch (e) { /* keep the in-memory one */ }
  if (item && item.kind === "piece") item = { kind: "pieces", track: item.data.track || 0, dur: item.dur, items: [{ data: item.data, dt: 0, dk: 0 }] };
  if (!item || item.kind !== "pieces" || !(item.items || []).length) return toast("Nothing copied yet: select a clip and press ⌘C.");
  const snapT = (t) => { const b = beat(); return +(b ? Math.round(t / b) * b : t).toFixed(4); };
  const pis = selPis();
  const at = pis.length ? +Math.max(...pis.map((pi) => { const g = geom(pi); return g.at + g.dur; })).toFixed(4) : snapT(S.cursor);
  const track = pis.length ? Math.min(...pis.map((pi) => pieces()[pi].track || 0)) : (item.track || 0);
  edit((p) => {
    const n0 = p.pieces.length;
    for (const x of item.items) {
      const k = track + (x.dk || 0);
      p.pieces.push({ ...JSON.parse(JSON.stringify(x.data)), at: +(at + (x.dt || 0)).toFixed(4), track: k });
      p.tracks = Math.max(p.tracks || 1, k + 1);
    }
    setMulti(item.items.map((_, i) => "p" + (n0 + i)), "p" + n0);
  });
  inspector();
}
function toggleSel(key) {                        // M / S on every selected clip: all on, unless all already are
  const pis = selPis();
  if (!pis.length) return;
  const on = !pis.every((pi) => pieces()[pi][key]);
  edit((p) => { for (const pi of pis) p.pieces[pi][key] = on; });
}
function setPiece(pi, fn, { save = true } = {}) { edit((p) => fn(p.pieces[pi]), { save }); }
function extend(n) {
  edit((p) => { p.length = +(projLength() + n).toFixed(4); });
  toast(`${n} s added at the end: it is generated (from the last clip's conditioning) on the next render.`);
}

// ── save / build / fit ──────────────────────────────────────────────────────────────────────
let saveT = 0, fitT = 0;
function scheduleSave() {
  clearTimeout(saveT);
  S.saving = true; status();
  saveT = setTimeout(saveNow, 350);
  syncMixer();
}
async function saveNow() {
  const seq = ++S.saveSeq;
  try {
    const r = await api.post("/sa3/composer/save", { name: S.name, project: S.proj });
    if (seq !== S.saveSeq) return;
    S.error = r.error || null;
    if (r.layout) S.layout = r.layout;
    else if (!r.error) S.layout = null;
  } catch (e) {
    if (seq !== S.saveSeq) return;
    S.error = e.message;
  }
  S.saving = false;
  paint();
  syncMixer();
  clearTimeout(fitT);
  if (S.layout && !S.error) fitT = setTimeout(loadFit, 500);
}
async function loadFit() {
  const name = S.name;
  try { const f = await api.get("/sa3/composer/fit", { name }); if (name === S.name) { S.fit = f; paint(); } } catch (e) { S.fit = null; }
}
function status() {
  const el = $("status");
  el.hidden = false; el.title = "";
  if (S.saving) { el.textContent = "building…"; el.className = "ui-chip"; return; }
  if (S.error) { el.textContent = "error: " + S.error.slice(0, 60); el.className = "ui-chip warn"; el.title = S.error; return; }
  el.hidden = true;                          // saved + built is the normal state: nothing to say
}

// ── live mix monitor ────────────────────────────────────────────────────────────────────────
const isMix = () => S.src === "mix" || S.src === "mixp";
let pvKey = "";
function syncMixer() {
  if (!S.layout || !fresh(S.layout)) return;
  const pv = S.src === "mixp" && S.layout.keys ? { keys: S.layout.keys, length: S.layout.length } : null;
  mixer.setPreview(pv);
  const k = JSON.stringify(pv);
  if (k !== pvKey) { const restart = pvKey && mixer.playing; pvKey = k; if (restart) mixer.play(mixer.time(), { onEnd: () => tl.setPlayhead(S.cursor) }).then(playBtn); }
  mixer.setPieces(S.layout.pieces.filter((pc) => pc.audio_key && pieces()[pc.pi]).map((pc) => ({ key: "p" + pc.pi, url: pieceUrl(pc.audio_key), at: geom(pc.pi).at, dur: pc.dur, mix: pieces()[pc.pi].mix })));
  mixer.setMaster(S.proj.master);
}
let raf = 0;
function tick() {
  const t = mixer.time();
  if (t == null) { raf = 0; playBtn(); $("peak").textContent = ""; return; }
  tl.setPlayhead(t); clock(t);
  const pk = mixer.peak();
  $("peak").textContent = isFinite(pk) ? `${pk.toFixed(1)} dBFS` : "";
  $("peak").classList.toggle("hot", pk > ((S.proj.master || {}).ceiling ?? -1) - 0.1);
  raf = requestAnimationFrame(tick);
}
async function playMix(t0) {
  player.stop();
  if (!S.layout || !fresh(S.layout)) return toast("Nothing built yet (or still building).");
  if (!isMix()) S.src = "mixp";
  syncMixer();
  await mixer.play(t0, { onEnd: () => { tl.setPlayhead(S.cursor); } });
  playBtn();
  if (!raf) raf = requestAnimationFrame(tick);
}

// ── painting ────────────────────────────────────────────────────────────────────────────────
const tl = new Timeline($("tl"), {
  gutter: 92, free: true,                    // an open canvas: zoom out as far as you like; the view never re-fits by itself
  onSeek: (t) => seek(t),
  onRange: (r) => setSection(r),             // a drag on the ruler: render only that section
  onSelect: (it, lane, e) => {
    if (!it && lane && lane.takeId) return;   // a take lane: its onClick selects it
    if (lane && /^t\d+$/.test(lane.key) && S.src === "take") { S.src = S.mixSrc || "mixp"; playBtn(); }   // back on the tracks: ▶ plays the mix again
    const mod = e && (e.metaKey || e.ctrlKey);
    rw.sel = null;
    if (it && it.kind === "piece") {
      if (mod) {                              // ⌘-click: in / out of the set
        const ids = new Set(S.multi.size ? S.multi : S.sel && S.sel[0] === "p" ? [S.sel] : []);
        if (ids.has(it.id)) ids.delete(it.id); else ids.add(it.id);
        setMulti(ids, ids.has(it.id) ? it.id : null);
      } else if (S.multi.has(it.id)) setMulti(S.multi, it.id);   // already in the set: keep it (a drag moves them all)
      else setMulti([it.id]);
    } else if (!it && lane && lane.marquee && e && (mod || e.shiftKey)) return;   // an additive rubber band keeps the set
    else { S.multi = new Set(); tl.multi = S.multi; S.sel = it ? it.id : null; }
    inspector();
  },
  onMarquee: (ids, e) => {
    const add = e && (e.shiftKey || e.metaKey || e.ctrlKey);
    setMulti([...(add ? S.multi : []), ...ids.filter((id) => id[0] === "p")], S.sel);
    paint(); inspector();
  },
  onGroupMove: (ids, dt) => edit((p) => {
    for (const id of ids) { const pi = +id.slice(1); if (p.pieces[pi]) p.pieces[pi].at = +Math.max(0, geom(pi).at + dt).toFixed(4); }
  }),
});
const rw = new CurveLane(tl, {                // the Rewrite lane's strength curve (project.json `rewrite`)
  brushFromHeight: true, closeEnds: true,
  end: () => Math.max(projLength(), 1),
  magnets: () => [S.cursor, ...pieces().flatMap((p, i) => { const g = geom(i); return [g.at, g.at + g.dur]; })],
  onSeek: (t) => seek(t),
  onCommit: (K) => edit((p) => { if (K.length) p.rewrite = K; else delete p.rewrite; }),
});
function rwPointer(kind, e, p) {
  if (kind === "down") tl.sel = null;
  rw.pointer(kind, e, p);
  if (kind === "down" || kind === "up") { S.sel = rw.sel ? "w" : null; inspector(); }
}

function paint() {
  const p = S.proj;
  if (!p) return;
  const L = projLength();
  tl.setGrid(bpmNow());
  tl.setLength(L || 1);
  tl.sel = S.sel && S.sel !== "w" ? S.sel : null;
  if (!S.sel || S.sel[0] !== "p") S.multi = new Set();          // the set always holds the selected clip
  else if (!S.multi.has(S.sel)) S.multi = new Set([S.sel]);
  S.multi = new Set([...S.multi].filter((id) => pieces()[+id.slice(1)]));
  tl.multi = S.multi;
  rw.setKeys(p.rewrite || []);
  if (S.sel === "w" && !rw.sel) S.sel = null;
  S.empty = emptyIssues();
  emptyChip();
  tl.setLanes(lanes());
  $("bpmbtn").textContent = bpmNow() ? `≈ ${(+bpmNow()).toFixed(1)} BPM ▾` : "no tempo ▾";
  bpmPop();
  $("seam").value = String(p.seam ?? 0);
  for (const b of $("cond").querySelectorAll("button")) b.classList.toggle("on", b.dataset.v === condMode());
  $("condnote").textContent = COND_NOTE[condMode()] || "";
  $("dit").value = (p.model && p.model.dit) || "medium";
  $("steps").value = (p.model && p.model.steps) || 8;
  $("clock").textContent = `${fmt(S.cursor)} / ${fmt(L)}`;
  $("clock").title = L > MAX_LEN ? "past 380 s: renders as chained windows (each keeps the last 60 s as context; no global coherence)" : "";
  $("undo").disabled = !S.undo.length;
  $("redo").disabled = !S.redo.length;
  $("render").disabled = !!(S.job && S.job.running) || !L;
  $("render").textContent = S.job && S.job.running ? `Rendering ${S.job.done}/${S.job.total}…`
    : S.range ? `Render this section (${nSeeds()} take${nSeeds() === 1 ? "" : "s"})` : `Render ${nSeeds()} take${nSeeds() === 1 ? "" : "s"}`;
  $("render").title = S.range ? `render only ${fmt(S.range[0])} – ${fmt(S.range[1])} (the blue section; × on the ruler or Esc clears it)`
    : "render takes of the whole timeline in the studio's queue (each lands as a lane under the tracks). Drag on the ruler to render only a section";
  status();
  inspector();
}

// ── inspector ───────────────────────────────────────────────────────────────────────────────
function el(html) { const d = document.createElement("div"); d.innerHTML = html.trim(); return d.firstChild; }
function section(host, key, title, badge = "", warn = false) {   // collapsed by default; open state remembered
  const open = !!(prefs.open || {})[key];
  const d = el(`<details class="sec"${open ? " open" : ""}><summary><span>${title}</span>${badge ? `<span class="badge${warn ? " warn" : ""}">${esc(badge)}</span>` : ""}</summary><div class="body"></div></details>`);
  d.ontoggle = () => { prefs.open = { ...(prefs.open || {}), [key]: d.open }; savePrefs(); if (d.open) d.querySelectorAll("canvas.eq").forEach(() => requestAnimationFrame(() => d.dispatchEvent(new Event("eqdraw")))); };
  host.appendChild(d);
  return d.querySelector(".body");
}
function slider(host, label, v, min, max, step, fmtv, on, { log = false, tip = "" } = {}) {
  const row = el(`<div class="row"${tip ? ` title="${esc(tip)}"` : ""}><label>${label}</label><input type="range"><span class="v"></span></div>`);
  const r = row.querySelector("input"), out = row.querySelector(".v");
  const to = (x) => (log ? Math.log(x) : x), from = (x) => (log ? Math.exp(x) : x);
  r.min = to(min); r.max = to(max); r.step = log ? 0.001 : step; r.value = to(v);
  out.textContent = fmtv(v);
  r.oninput = () => { const x = +(+from(+r.value)).toFixed(log ? 0 : 3); out.textContent = fmtv(x); on(x, false); };
  r.onchange = () => on(+(+from(+r.value)).toFixed(log ? 0 : 3), true);
  host.appendChild(row);
  return row;
}
let inspKey = "";
function inspector() {
  const host = $("insp");
  const id = S.sel || "";
  const tkSel = id.startsWith("T:") ? S.takes.find((x) => "T:" + x.id === id) : null;
  const k = id + "|" + JSON.stringify(tkSel ? [tkSel, S.proj.base] : id[0] === "p" ? pieces()[+id.slice(1)] : id === "w" ? [rw.sel, rw.K] : id[0] === "k" ? ((S.proj.cond || {}).keys || [])[+id.slice(1)] : [S.proj.master, S.proj.cond, S.proj.anchor])
    + "|" + (S.fit ? S.fit.issues.map((x) => x.msg).join(";") + ":" + S.fit.pieces.length : "") + "|" + (S.layout ? S.layout.ref_sha256 : "") + "|" + S.starred.length + "|" + pieces().length;
  if (k === inspKey && host.childElementCount) return;
  inspKey = k;
  host.innerHTML = "";
  if (tkSel) return takeInspector(host, tkSel);
  if (id[0] === "p" && pieces()[+id.slice(1)]) return pieceInspector(host, +id.slice(1));
  if (id === "w" && rw.sel) return rewriteInspector(host);
  if (id[0] === "k" && ((S.proj.cond || {}).keys || [])[+id.slice(1)]) return keyInspector(host, +id.slice(1));
  projectInspector(host);
}

function anchorEditor(host, anchor, { allowDefault = false, defaultLabel = "", onChange }) {
  const kind = !anchor ? "default" : anchor.prompt != null ? "prompt" : "render";
  const box = el(`<div class="anch"><div class="row"><select class="ui-input kind">
      ${allowDefault ? `<option value="default">its own (${esc(short(defaultLabel, 28))})</option>` : ""}
      <option value="render">a render's conditioning</option><option value="prompt">text prompt / anarchy spec</option></select></div>
    <div class="row rsel"><select class="ui-input rid" style="flex:1;max-width:100%"></select></div>
    <div class="psel"><textarea class="ui-input ptxt" rows="3" placeholder="dark rolling techno, BPM: 140   ·   or   anarchy: <spec>"></textarea>
      <div class="row"><button class="ui-btn small apply">Apply</button><span class="note">a prompt is encoded by T5Gemma; an "anarchy: …" spec works too</span></div></div></div>`);
  host.appendChild(box);
  const ks = box.querySelector(".kind"), rid = box.querySelector(".rid"), txt = box.querySelector(".ptxt");
  ks.value = kind;
  const opts = [...new Map([...S.starred.filter((r) => r.cond || r.prompt).map((r) => [r.id, r]), ...pieces().map((p) => [p.id, rowOf(p.id) || { id: p.id, prompt: p.id }])]).values()];
  rid.innerHTML = opts.map((r) => `<option value="${esc(r.id)}">${esc(short(r.prompt || r.id, 60))}${{ multi: ` (a take: its main anchor of ${srcCond(r.id).n})`, bag: "", prompt: " (prompt)" }[srcCond(r.id).kind] ?? " (no conditioning)"}</option>`).join("");
  if (anchor && anchor.id) rid.value = anchor.id;
  txt.value = anchor && anchor.prompt != null ? anchor.prompt : "";
  const show = () => { box.querySelector(".rsel").hidden = ks.value !== "render"; box.querySelector(".psel").hidden = ks.value !== "prompt"; box.querySelector(".rsel").style.display = ks.value === "render" ? "" : "none"; box.querySelector(".psel").style.display = ks.value === "prompt" ? "" : "none"; };
  show();
  // a text render → its prompt; anything else by id (the server makes a take its dominant anchor: condsrc.expand)
  const anchorOfRow = (r) => (r && r.cond === false && r.prompt && r.kind !== "composite" ? { prompt: r.prompt } : { id: rid.value });
  ks.onchange = () => { show(); if (ks.value === "default") onChange(null); else if (ks.value === "render" && rid.value) onChange(anchorOfRow(opts.find((r) => r.id === rid.value))); };
  rid.onchange = () => onChange(anchorOfRow(opts.find((r) => r.id === rid.value)));
  box.querySelector(".apply").onclick = () => onChange({ prompt: txt.value.trim() });
  return box;
}

function fitDims(host, pi) {
  const r = S.fit && S.fit.pieces.find((x) => x.pi === pi);
  if (!r) return;
  const d = el('<div class="dims"></div>');
  for (const [k, v] of Object.entries(r.dims)) d.insertAdjacentHTML("beforeend", `<span><i class="dot ${v.state}"></i>${k}</span><span>${esc(v.msg)}</span>`);
  host.appendChild(d);
}

function pieceInspector(host, pi) {
  const it = pieces()[pi], g = geom(pi), isBase = S.proj.base && it.id === S.proj.base;
  const row = rowOf(it.id) || {};
  const top = host;
  host.appendChild(el(`<h3 title="${esc(it.id)}">${isBase ? "Base take" : esc(short(row.prompt || row.name || it.id, 60))}</h3>`));
  host.appendChild(el(`<div class="chips"><span class="ui-chip dim">at ${fmt(g.at)}</span><span class="ui-chip dim">${fmt(g.dur)} long</span>${g.beat ? `<span class="ui-chip">${(+g.bpm).toFixed(1)} BPM${Math.abs((g.stretch || 1) - 1) > 0.002 ? ` ×${(g.stretch || 1).toFixed(3)}` : ""}</span>` : ""}</div>`));
  const acts = el(`<div class="row"><select class="ui-input trk" title="track"></select><button class="ui-btn small mute" title="M">Mute</button><button class="ui-btn small solo" title="S">Solo</button>${isBase ? "" : '<button class="ui-btn small danger del" title="⌫">Delete</button>'}</div>`);
  const trk = acts.querySelector(".trk");
  trk.innerHTML = Array.from({ length: nTracks() + 1 }, (_, k) => `<option value="${k}">${k < nTracks() ? "Track " + (k + 1) : "+ new track"}</option>`).join("");
  trk.value = String(it.track || 0);
  trk.onchange = () => edit((p) => { p.pieces[pi].track = +trk.value; p.tracks = Math.max(p.tracks || 1, +trk.value + 1); });
  acts.querySelector(".mute").classList.toggle("on", !!it.mute);
  acts.querySelector(".solo").classList.toggle("on", !!it.solo);
  acts.querySelector(".mute").onclick = () => setPiece(pi, (x) => { x.mute = !x.mute; });
  acts.querySelector(".solo").onclick = () => setPiece(pi, (x) => { x.solo = !x.solo; });
  if (acts.querySelector(".del")) acts.querySelector(".del").onclick = deleteSel;
  host.appendChild(acts);

  host = section(top, "time", "Timing & fades", Array.isArray(it.fade) && (it.fade[0] > 0.02 || it.fade[1] > 0.02) ? "fades" : "");
  const acts2 = el(`<div class="row"><button class="ui-btn small pl">▶ from here</button><button class="ui-btn small nl" title="one beat earlier (←)">−1 beat</button><button class="ui-btn small nr" title="one beat later (→)">+1 beat</button><button class="ui-btn small dup">Duplicate</button><button class="ui-btn small hl">★ Highlight</button></div>`);
  acts2.querySelector(".pl").onclick = () => playMix(g.at);
  acts2.querySelector(".nl").onclick = () => nudge(-1);
  acts2.querySelector(".nr").onclick = () => nudge(1);
  acts2.querySelector(".dup").onclick = () => edit((p) => { const c = JSON.parse(JSON.stringify(p.pieces[pi])); c.at = +(g.at + g.dur).toFixed(4); c.solo = false; p.pieces.push(c); S.sel = "p" + (p.pieces.length - 1); });
  acts2.querySelector(".hl").onclick = () => openHighlight({ id: it.id, range: it.src, onSaved: () => refreshLib() });
  host.appendChild(acts2);
  if (!isBase) {
    const al = el(`<div class="row"><label title="tempo-stretch + beat-cut this clip onto the project grid (confident beats only)"><input type="checkbox" class="al"> snap to the beat grid</label></div>`);
    al.querySelector(".al").checked = it.align !== false;
    al.querySelector(".al").onchange = (e) => setPiece(pi, (x) => { if (e.target.checked) delete x.align; else x.align = false; });
    host.appendChild(al);
  }
  const fd = Array.isArray(it.fade) ? it.fade : [0.01, 0.01];
  slider(host, "fade in", fd[0], 0, 4, 0.01, (v) => v.toFixed(2) + " s", (v, fin) => fin && setPiece(pi, (x) => { x.fade = [v, (Array.isArray(x.fade) ? x.fade : [0.01, 0.01])[1]]; }), { tip: "fade-in at the clip’s start (before SA3 sees it)" });
  slider(host, "fade out", fd[1], 0, 4, 0.01, (v) => v.toFixed(2) + " s", (v, fin) => fin && setPiece(pi, (x) => { x.fade = [(Array.isArray(x.fade) ? x.fade : [0.01, 0.01])[0], v]; }), { tip: "fade-out at the clip’s end (before SA3 sees it)" });

  // mix strip: live in the monitor while dragging, saved (+ rebuilt) on release
  const m = it.mix || {};
  const mixSum = [m.gain ? `${m.gain > 0 ? "+" : ""}${m.gain} dB` : "", m.hp ? "HP" : "", m.lp ? "LP" : "", ["low", "mid", "high"].some((k) => m[k] && m[k].g) ? "EQ" : "", Math.abs(m.pan || 0) > 0.01 ? "pan" : ""].filter(Boolean).join(" · ");
  host = section(top, "mix", "Mix", mixSum);
  host.appendChild(el('<div class="note">Heard live while you drag (▶ with Mix selected).</div>'));
  const live = (fn, fin) => {
    const mix = JSON.parse(JSON.stringify(pieces()[pi].mix || {}));
    fn(mix);
    if (fin) setPiece(pi, (x) => { x.mix = mix; });
    else { pieces()[pi].mix = mix; syncMixer(); drawEq(); }
  };
  slider(host, "gain", m.gain || 0, -30, 12, 0.5, (v) => `${v > 0 ? "+" : ""}${v.toFixed(1)} dB`, (v, f) => live((x) => { x.gain = v; }, f), { tip: "this clip’s level in the mix (heard live; also how strongly it pulls the conditioning over an overlap)" });
  slider(host, "pan", m.pan || 0, -1, 1, 0.01, (v) => (Math.abs(v) < 0.01 ? "C" : v < 0 ? `L ${(-v * 100).toFixed(0)}` : `R ${(v * 100).toFixed(0)}`), (v, f) => live((x) => { x.pan = v; }, f), { tip: "left / right placement in the mix" });
  const filt = (key, label, dflt, min, max) => {
    const on = !!(m[key] && m[key].f);
    const r = el(`<div class="row" title="${key === "hp" ? "cut the lows below the cutoff (e.g. make room for another clip’s bass)" : "cut the highs above the cutoff"}"><label><input type="checkbox"> ${label}</label><select class="ui-input sl"><option value="12">12 dB/oct</option><option value="24">24 dB/oct</option></select></div>`);
    r.querySelector("input").checked = on;
    r.querySelector(".sl").value = String((m[key] && m[key].slope) || 12);
    r.querySelector("input").onchange = (e) => live((x) => { x[key] = e.target.checked ? { f: (m[key] && m[key].f) || dflt, slope: +r.querySelector(".sl").value } : null; }, true);
    r.querySelector(".sl").onchange = () => live((x) => { if (x[key]) x[key].slope = +r.querySelector(".sl").value; }, true);
    host.appendChild(r);
    if (on) slider(host, "", m[key].f, min, max, 1, (v) => `${Math.round(v)} Hz`, (v, f) => live((x) => { x[key] = { ...(x[key] || {}), f: v }; }, f), { log: true });
  };
  filt("hp", "high-pass", 120, 20, 2000);
  filt("lp", "low-pass", 8000, 400, 20000);
  for (const [key, label, f0, fmin, fmax] of [["low", "low shelf", 150, 40, 600], ["mid", "mid peak", 900, 150, 6000], ["high", "high shelf", 6000, 1500, 16000]]) {
    const b = m[key] || { f: f0, g: 0, q: 1 };
    slider(host, label, b.g || 0, -15, 15, 0.5, (v) => `${v > 0 ? "+" : ""}${v.toFixed(1)} dB`, (v, f) => live((x) => { x[key] = { f: (x[key] || b).f || f0, g: v, ...(key === "mid" ? { q: (x[key] || b).q || 1 } : {}) }; }, f));
    slider(host, "", b.f || f0, fmin, fmax, 1, (v) => `${Math.round(v)} Hz`, (v, f) => live((x) => { x[key] = { ...(x[key] || b), f: v }; }, f), { log: true });
  }
  const eq = el('<canvas class="eq"></canvas>');
  host.appendChild(eq);
  function drawEq() {
    const mix = pieces()[pi] && pieces()[pi].mix;
    const w = eq.clientWidth, h = eq.clientHeight, dpr = devicePixelRatio || 1;
    if (!w) return;
    eq.width = w * dpr; eq.height = h * dpr;
    const gx = eq.getContext("2d"); gx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const fs = Array.from({ length: 160 }, (_, i) => 20 * Math.pow(1000, i / 159));
    const db = mixer.response(mix, fs);
    gx.strokeStyle = "rgba(255,255,255,0.08)";
    for (const d of [-12, 0, 12]) { const y = h / 2 - (d / 24) * (h / 2); gx.beginPath(); gx.moveTo(0, y); gx.lineTo(w, y); gx.stroke(); }
    gx.strokeStyle = "#a99cff"; gx.lineWidth = 1.5; gx.beginPath();
    db.forEach((d, i) => { const x = (i / 159) * w, y = h / 2 - (Math.max(-24, Math.min(24, d)) / 24) * (h / 2); i ? gx.lineTo(x, y) : gx.moveTo(x, y); });
    gx.stroke();
    gx.fillStyle = "#5d5d6c"; gx.font = "10px JetBrains Mono"; gx.fillText("20 Hz", 3, h - 3); gx.fillText("20 kHz", w - 40, h - 3);
  }
  requestAnimationFrame(drawEq);
  eq.closest("details").addEventListener("eqdraw", drawEq);

  // fix chain (spec 4.3): conform settings, kept apart from the mix strip
  const fr0 = S.fit && S.fit.pieces.find((x) => x.pi === pi);
  const nIss = fr0 ? Object.values(fr0.dims).filter((v) => v.state === "warn" || v.state === "bad").length : 0;
  host = section(top, "fix", "Fit to the others", nIss ? `${nIss} mismatch${nIss === 1 ? "" : "es"}` : it.fix ? "fixed" : "", nIss > 0);
  host.appendChild(el('<div class="note">Tempo, key, loudness, tone and width measured against the longest clip. "Apply the recommended fix" sets the sliders below.</div>'));
  host.appendChild(el('<div class="row"><span class="ui-label">compare</span><button class="ui-btn small ghost ab0" title="loudness-matched: without the fix">▶ before</button><button class="ui-btn small ghost ab1" title="with the fix">▶ after</button></div>'));
  fitDims(host, pi);
  host.querySelector(".ab0").onclick = () => abPlay(pi, 0);
  host.querySelector(".ab1").onclick = () => abPlay(pi, 1);
  const fx = it.fix || {};
  const setFix = (fn) => setPiece(pi, (x) => { const f = { ...(x.fix || {}) }; fn(f); x.fix = Object.keys(f).length ? f : null; });
  slider(host, "transpose", fx.transpose || 0, -7, 7, 1, (v) => `${v > 0 ? "+" : ""}${v} st`, (v, f) => f && setFix((x) => { if (v) x.transpose = v; else delete x.transpose; }), { tip: "pitch-shift this clip by semitones (rubberband) to match the others’ key" });
  const fm = el('<div class="row"><label><input type="checkbox"> preserve formants</label></div>');
  fm.querySelector("input").checked = !!fx.formant;
  fm.querySelector("input").onchange = (e) => setFix((x) => { if (e.target.checked) x.formant = true; else delete x.formant; });
  host.appendChild(fm);
  slider(host, "gain", fx.gain || 0, -18, 18, 0.5, (v) => `${v > 0 ? "+" : ""}${v.toFixed(1)} dB`, (v, f) => f && setFix((x) => { if (v) x.gain = v; else delete x.gain; }));
  ["low", "mid", "high"].forEach((n, j) => slider(host, "eq " + n, (fx.eq || [0, 0, 0])[j] || 0, -9, 9, 0.5, (v) => `${v > 0 ? "+" : ""}${v.toFixed(1)} dB`,
    (v, f) => f && setFix((x) => { const e = (x.eq || [0, 0, 0]).slice(); e[j] = v; if (e.some((q) => q)) x.eq = e; else delete x.eq; })));
  slider(host, "width", fx.width ?? 1, 0, 2.5, 0.05, (v) => `×${v.toFixed(2)}`, (v, f) => f && setFix((x) => { if (Math.abs(v - 1) > 1e-3) x.width = v; else delete x.width; }), { tip: "stereo width: 0 = mono, 1 = as rendered, above 1 = wider" });
  const fa = el('<div class="row"><button class="ui-btn small rec">Apply the recommended fix</button><button class="ui-btn small ghost clr">Clear fix</button></div>');
  fa.querySelector(".rec").onclick = () => fitAll([pi]);
  fa.querySelector(".clr").onclick = () => setPiece(pi, (x) => { x.fix = null; });
  host.appendChild(fa);
  const fr = S.fit && S.fit.pieces.find((x) => x.pi === pi);
  if (fr && (fr.swaps || []).length) {
    host.appendChild(el('<h4>Swap suggestions <span class="grow"></span><span class="note">closest by sound</span></h4>'));
    for (const m of fr.swaps) {
      const d = el(`<div class="issue"><span style="flex:1">${esc(short(m.prompt || m.id, 48))} · ${m.t0.toFixed(0)}–${m.t1.toFixed(0)} s · ≈ ${m.score.toFixed(2)}</span><button class="ui-btn small ghost pl">▶</button><button class="ui-btn small sw">Swap</button></div>`);
      d.querySelector(".pl").onclick = (e) => { e.stopPropagation(); mixer.stop(); player.toggle({ key: "swap", url: api.audioUrl(m.id), t0: m.t0, t1: m.t1 }); };
      d.querySelector(".sw").onclick = (e) => { e.stopPropagation(); setPiece(pi, (x) => { const len = x.src[1] - x.src[0]; x.id = m.id; x.src = [m.t0, +(m.t0 + Math.min(len, m.t1 - m.t0 + 8)).toFixed(3)]; x.fix = null; delete x.anchor; }); };
      host.appendChild(d);
    }
  }

  host = section(top, "pcond", "Conditioning", it.anchor ? "overridden" : "");
  const own = condWord(srcCond(it.id));
  anchorEditor(host, it.anchor || null, { allowDefault: true, defaultLabel: own, onChange: (a) => setPiece(pi, (x) => { if (a) x.anchor = a; else delete x.anchor; }) });
  slider(host, "pull", it.pull ?? 1, 0, 4, 0.05, (v) => `×${v.toFixed(2)}`, (v, f) => f && setPiece(pi, (x) => { if (Math.abs(v - 1) > 1e-3) x.pull = v; else delete x.pull; }), { tip: "how strongly this clip’s conditioning wins where it overlaps other clips (multiplies its loudness share)" });
  host.appendChild(el('<div class="note">Over an overlap, layers share the conditioning by loudness × pull. In Keypoints mode the lane decides instead.</div>'));
}

function abPlay(pi, f) {
  mixer.stop();
  const url = `/sa3/composer/ab?name=${encodeURIComponent(S.name)}&pi=${pi}&fix=${f}&v=${Date.now() >> 12}`;
  player.toggle({ key: "ab" + f, url, t0: 0 }).catch((e) => toast("A/B: " + e.message, { error: true }));
}

function rewriteInspector(host) {
  const k = rw.selKey(), b = rw.selBox();
  const u = k ? k[1] : b ? b.u : 0;
  host.appendChild(el(`<h3>Rewrite ${k ? "keypoint" : "span"}</h3>`));
  host.appendChild(el(k ? `<div class="chips"><span class="ui-chip dim">at ${fmt(k[0])}</span></div>`
    : `<div class="chips"><span class="ui-chip dim">${fmt(b.a)} → ${fmt(b.b)}</span><span class="ui-chip dim">${(b.b - b.a).toFixed(2)} s</span></div>`));
  slider(host, "strength", u, 0, 1, 0.01, (v) => (v >= 1 ? "1 · regenerate" : v <= 0 ? "0 · keep" : `${v.toFixed(2)} · ~${Math.round(v * 5)} of 8 steps`), (v, fin) => fin && rw.setStrength(v),
    { tip: "0 keeps the reference, 1 regenerates from scratch; in between SA3 starts from the reference partway through denoising: low values keep the arrangement and re-render the sound" });
  host.appendChild(el(`<div class="note">${u >= 1 ? "Regenerated: new music here, bridging what's around it."
    : "Soft: held toward the reference, then released. Around 0.2-0.4 keeps the arrangement and re-renders the timbre; over overlapping clips this is the glue (the layers come back as one performance)."}</div>`));
  const a = el('<div class="row"><button class="ui-btn small pl" title="play the mix from just before here">▶ mix here</button><button class="ui-btn small danger del" title="remove it (⌫): the curve heals">Delete</button></div>');
  a.querySelector(".pl").onclick = () => playMix(Math.max(0, (k ? k[0] : b.a) - 2));
  a.querySelector(".del").onclick = deleteSel;
  host.appendChild(a);
  host.appendChild(el('<div class="note">On the lane: drag a point (time + strength) or a line (up / down); drag inside a span to move it (⌥ duplicates); double-click adds or deletes a point. Keys: ⌫ delete, ←→ move, ↑↓ strength. ⇧ while dragging = no snapping.</div>'));
}

function keyInspector(host, i) {
  const k = S.proj.cond.keys[i];
  host.appendChild(el(`<h3>Conditioning keypoint ${i + 1}</h3>`));
  host.appendChild(el(`<div class="row"><span class="ui-chip dim">at ${fmt(k.t)}</span></div>`));
  anchorEditor(host, k.anchor, { onChange: (a) => edit((p) => { p.cond.keys[i].anchor = a; }) });
  const c = el('<div class="row"><label><input type="checkbox"> hard cut into this keypoint</label></div>');
  c.querySelector("input").checked = !!k.cut;
  c.querySelector("input").onchange = (e) => edit((p) => { if (e.target.checked) p.cond.keys[i].cut = true; else delete p.cond.keys[i].cut; });
  host.appendChild(c);
  host.appendChild(el('<div class="note">Between two keypoints the conditioning crossfades (cosine) from one anchor to the next. With a hard cut, the previous anchor holds until this keypoint and switches at once.</div>'));
  const a = el('<div class="row"><button class="ui-btn small danger del">Delete keypoint</button></div>');
  a.querySelector(".del").onclick = deleteSel;
  host.appendChild(a);
}

function projectInspector(host) {
  const p = S.proj, mt = (S.layout && S.layout.meter) || {};
  if (!pieces().length) {
    host.appendChild(el("<h3>Make a piece in 4 steps</h3>"));
    host.appendChild(el(`<ol class="steps">
      <li><b>Arrange.</b> Drag clips from the library on the left onto the tracks. Leave gaps between them: SA3 fills those.</li>
      <li><b>Rewrite</b> (optional). On the Rewrite lane under the tracks: drag over anything SA3 should re-render, or click to add keypoints. Height = strength: top = new music, low = same arrangement with a new sound.</li>
      <li><b>Render.</b> Top right. Each take is a different seed, so render a few.</li>
      <li><b>Pick.</b> Each take lands as a lane under the tracks: ▶ plays it from the playhead. Click one for its score and <i>Promote</i>, to keep building on top of it.</li></ol>`));
    host.appendChild(el('<div class="note">Click a clip for its settings (mix, fit, conditioning). Expert settings sit under ⚙ Render settings; extra lanes under View.</div>'));
    return;
  }
  host.appendChild(el("<h3>Project</h3>"));
  host.appendChild(el(`<div class="chips"><span class="ui-chip dim">${pieces().length} clip${pieces().length === 1 ? "" : "s"}</span><span class="ui-chip dim">${fmt(projLength())}</span>${base() ? '<span class="ui-chip accent">built on a promoted take</span>' : ""}</div>`));
  host.appendChild(el('<div class="note">Click a clip to edit it. On the Rewrite lane, drag a span or click keypoints to mark what SA3 re-renders, and how strongly.</div>'));

  const f = S.fit, nIss = f ? f.issues.length : 0;
  let h = section(host, "pfit", "Fit check", f ? (nIss ? `${nIss} issue${nIss === 1 ? "" : "s"}` : "all good") : "", nIss > 0);
  const fh = el('<div class="row"><button class="ui-btn small fa" title="apply the recommended fix to every clip except the reference (the longest)">Fix all clips</button><button class="ui-btn small ghost rf">Re-measure</button></div>');
  fh.querySelector(".rf").onclick = loadFit;
  fh.querySelector(".fa").onclick = () => fitAll(null);
  h.appendChild(fh);
  if (!f) h.appendChild(el('<div class="note">Measured after each build: tempo, grid, key, loudness, tone, width; overlaps: beat phase, masking, headroom.</div>'));
  else {
    const t = f.target;
    h.appendChild(el(`<div class="note">Measured against the longest clip: ${t.bpm ? (+t.bpm).toFixed(1) + " BPM" : "free time"} · ${t.key || "no clear key"} · ${t.lufs ?? "–"} LUFS.</div>`));
    if (!f.issues.length) h.appendChild(el('<div class="note">No issues.</div>'));
    for (const is of f.issues) {
      const d = el(`<div class="issue"><i class="dot ${is.state}"></i><span>${esc(is.kind === "piece" ? `clip ${is.i + 1}` : is.kind === "master" ? "master" : `clips ${is.a + 1}+${is.b + 1}`)} · <b>${esc(is.dim)}</b>: ${esc(is.msg)}</span></div>`);
      d.onclick = () => { const r = f.pieces[is.i ?? is.a]; if (r) { S.sel = "p" + r.pi; tl.select(S.sel); inspector(); } };
      h.appendChild(d);
    }
  }

  const c = S.layout && S.layout.cond;
  h = section(host, "pcondall", "Conditioning", { crossfade: "auto", global: "one anchor", keys: "keypoints" }[condMode()]);
  h.appendChild(el('<div class="note">What SA3 is told the music is, at each moment. Mode is under ⚙ Render settings; View ▾ shows it as a lane.</div>'));
  if (c) h.appendChild(el(`<div class="note">${c.anchors.map((a, i) => `<div><i class="dot" style="display:inline-block;background:${PCOL[i % PCOL.length]}"></i> ${esc(short(a.label || anchorLabel(a), 60))}</div>`).join("")}</div>`));
  const ck = el(`<div class="row">${condMode() !== "keys" ? '<button class="ui-btn small tok" title="turn the automatic conditioning into keypoints you can edit on the lane">Edit as keypoints</button>' : '<button class="ui-btn small toa">Back to auto</button>'}</div>`);
  if (ck.querySelector(".tok")) ck.querySelector(".tok").onclick = toKeys;
  if (ck.querySelector(".toa")) ck.querySelector(".toa").onclick = () => edit((q) => { q.cond = { ...(q.cond || {}), gap: "crossfade" }; });
  h.appendChild(ck);
  if (condMode() === "global" || pieces().every((x) => srcCond(x.id).kind === "none")) {
    h.appendChild(el(`<div class="note">${condMode() === "global" ? "The one anchor:" : "Fallback (imports and gaps with no conditioning of their own):"}</div>`));
    anchorEditor(h, p.anchor || null, { allowDefault: true, defaultLabel: "the longest clip's", onChange: (a) => edit((q) => { if (a) q.anchor = a; else delete q.anchor; }) });
  }

  const m = p.master || {};
  h = section(host, "master", "Master", `${mt.peak ?? "–"} dBFS peak`);
  slider(h, "gain", m.gain || 0, -18, 12, 0.5, (v) => `${v > 0 ? "+" : ""}${v.toFixed(1)} dB`, (v, fin) => {
    const nm = { ...(S.proj.master || {}), gain: v };
    if (fin) edit((q) => { q.master = nm; }); else { S.proj.master = nm; mixer.setMaster(nm); }
  }, { tip: "level of the whole mix before the limiter (what SA3 receives)" });
  const lim = el('<div class="row"><label><input type="checkbox"> limiter</label><span class="ui-label">ceiling</span><input class="ui-input num ceil" type="number" step="0.5" max="0" min="-12"></div>');
  lim.querySelector("input").checked = m.limit !== false;
  lim.querySelector(".ceil").value = m.ceiling ?? -1;
  lim.querySelector("input").onchange = (e) => edit((q) => { q.master = { ...(q.master || {}), limit: e.target.checked }; });
  lim.querySelector(".ceil").onchange = (e) => edit((q) => { q.master = { ...(q.master || {}), ceiling: Math.min(0, +e.target.value || -1) }; });
  h.appendChild(lim);
  h.appendChild(el(`<div class="meter">reference: peak ${mt.peak ?? "–"} dBFS · ${mt.lufs ?? "–"} LUFS</div>`));
  h.appendChild(el('<div class="note">The live Mix monitor approximates the limiter; the Reference has the real one.</div>'));
}

async function fitAll(pis) {
  try {
    const r = await api.post("/sa3/composer/fitall", { name: S.name, pis });
    S.undo.push(JSON.stringify(S.proj));
    S.proj = Object.assign(DEFAULT(), r.project);
    S.layout = r.layout || S.layout;
    S.error = r.error || null;
    const n = Object.values(r.applied || {}).filter((x) => x && Object.keys(x).length).length;
    toast(n ? `Fix chain applied to ${n} piece${n === 1 ? "" : "s"}. A/B it in the piece's Fix chain (▶ before / after).` : "Nothing to fix.");
    paint(); syncMixer(); loadFit();
  } catch (e) { toast(e.message, { error: true }); }
}

// ── take inspector (a take lane selected) ──────────────────────────────────────────────────
let TK = null;                                // {id, btn, sv}: the open take inspector, kept in step with playback
function syncTakeUI() {
  if (!TK || !TK.btn.isConnected) { TK = null; return; }
  const on = playingTake() === TK.id;
  TK.btn.textContent = on ? "■ Stop" : "▶ Play";
  TK.btn.classList.toggle("on", on);
  if (!on) TK.sv.setPlayhead(null);
}
function takeInspector(host, tk) {
  const isBase = S.proj.base === tk.id, stale = tk.ref_sha256 && S.layout && tk.ref_sha256 !== S.layout.ref_sha256;
  host.appendChild(el(`<h3>Take ${takeNo(tk)}</h3>`));
  host.appendChild(el(`<div class="chips"><span class="ui-chip dim">seed ${tk.seed}</span><span class="ui-chip dim">${fmt(tk.length)}</span>${tk.section ? `<span class="ui-chip accent" title="rendered from the ruler's section only">section ${fmt(tk.section[0])} – ${fmt(tk.section[1])}</span>` : ""}${tk.variant ? `<span class="ui-chip dim">${esc(condName(tk.variant))}</span>` : ""}${isBase ? '<span class="ui-chip base" title="the timeline is built on this take">base</span>' : ""}</div>`));
  if (stale) host.appendChild(el('<div class="note warn">Rendered from an earlier layout: the tracks have changed since.</div>'));
  const row = el(`<div class="row"><button class="ui-btn play" title="play this take from the playhead (space)">▶ Play</button><select class="ui-input sc" title="score 0-10 (written to the render's sidecar)"></select><button class="ui-btn ghost star${tk.starred ? " on" : ""}" title="star: keep it in the library">★</button></div>`);
  const sc = row.querySelector(".sc");
  sc.innerHTML = '<option value="">score</option>' + Array.from({ length: 11 }, (_, k) => `<option value="${k}">${k}</option>`).join("");
  sc.value = tk.score == null ? "" : String(tk.score);
  sc.onchange = async () => { try { await api.setLabel(tk.id, { score: sc.value === "" ? null : +sc.value }); tk.score = sc.value === "" ? null : +sc.value; paint(); } catch (e) { toast(e.message, { error: true }); } };
  row.querySelector(".star").onclick = async () => { try { await api.setStar(tk.id, !tk.starred); tk.starred = !tk.starred; await refreshLib(); paint(); } catch (er) { toast(er.message, { error: true }); } };
  const btn = row.querySelector(".play");
  btn.onclick = () => (playingTake() === tk.id ? player.stop() : playTake(tk.id, S.cursor));
  host.appendChild(row);
  const svHost = el('<div class="takesv"></div>');
  host.appendChild(svHost);
  const sv = new SpecView(svHost, { url: api.specUrl(tk.id), duration: tk.length, height: 70,
    onSeek: (x) => { S.cursor = x + takeOff(tk); playTake(tk.id, S.cursor); },
    onRange: (rg) => { if (rg) openHighlight({ id: tk.id, range: rg, onSaved: () => refreshLib() }); } });
  host.appendChild(el('<div class="note">Click the spectrogram to play from there; drag across it to save a highlight. ↑ ↓ switch to the next take at the same moment, while playing too.</div>'));
  if (isBase) host.appendChild(el('<div class="note">The timeline is built on this take (Track 1): layer clips over it, or rewrite parts of it on the Rewrite lane.</div>'));
  else if (tk.section) host.appendChild(el('<div class="note">A section take covers only part of the timeline, so it can\'t be promoted: once a section works, render the whole piece (clear the section) and promote that.</div>'));
  else {
    const pr = el('<div class="row"><button class="ui-btn primary promote">Promote: build on this take</button></div>');
    pr.querySelector(".promote").onclick = () => promote(tk);
    host.appendChild(pr);
    host.appendChild(el('<div class="note">Makes this take the timeline itself, so you can layer clips over it or rewrite parts of it.</div>'));
  }
  const ex = el('<div class="row"><button class="ui-btn small ghost" title="kept parts from the original audio, generated parts from this take">Download as splice</button></div>');
  ex.querySelector("button").onclick = () => exportSplice(tk);
  host.appendChild(ex);
  const rm = el(`<div class="row"><button class="ui-btn small ghost keep" title="worth keeping: star it (it joins the Library and the clips panel) and take it off these lanes; the file stays on disk. Unstar it to bring it back here">★ Keep &amp; remove</button><button class="ui-btn small danger trash" title="not good: off the lanes and into the trash (score 0, moved to takes/deleted/, like the studio's trash button)"${isBase ? " disabled" : ""}>Trash</button></div>`);
  rm.querySelector(".keep").onclick = () => removeTake(tk, "keep");
  rm.querySelector(".trash").onclick = () => removeTake(tk, "trash");
  host.appendChild(rm);
  TK = { id: tk.id, btn, sv };
  syncTakeUI();
}
async function removeTake(t, mode) {        // "keep": starred + off the lanes; "trash": the studio's trash
  try {
    if (playingTake() === t.id) player.stop();
    const i = S.takes.findIndex((x) => x.id === t.id), no = takeNo(t);
    const r = await api.post("/sa3/composer/take_remove", { name: S.name, take: t.id, mode });
    S.takes = r.takes || S.takes.filter((x) => x.id !== t.id);
    if (S.take === t.id) {
      const nt = S.takes[Math.min(i, S.takes.length - 1)];
      S.take = nt ? nt.id : null;
      S.sel = nt ? "T:" + nt.id : null;
      if (!nt && S.src === "take") S.src = S.mixSrc || "mixp";
    }
    inspKey = "";
    if (mode === "keep") await refreshLib();
    paint();
    toast(mode === "keep" ? `Take ${no} kept: starred, in the Library, off the lanes.` : `Take ${no} trashed (takes/deleted/).`);
  } catch (e) { toast(e.message, { error: true }); }
}
async function exportSplice(t) {
  try {
    toast("Splicing…");
    const r = await api.post("/sa3/composer/export", { name: S.name, take: t.id });
    const a = document.createElement("a"); a.href = r.url; a.download = r.path.split("/").pop(); a.click();
    toast(`Splice export written to outputs/composer/${S.name}/${r.path}`);
  } catch (e) { toast("Splice: " + e.message, { error: true }); }
}
async function promote(t) {
  try {
    S.undo.push(JSON.stringify(S.proj));
    const r = await api.post("/sa3/composer/promote", { name: S.name, take: t.id });
    S.proj = Object.assign(DEFAULT(), r.project);
    migrate(S.proj);
    S.layout = r.layout;
    S.error = r.error || null;
    S.sel = null;
    paint(); syncMixer();
    if (S.layout) loadFit();
    toast(`Take seed ${t.seed} is the new base. Draw edits on it, layer pieces on Track 2, or Extend.`);
  } catch (e) { S.undo.pop(); toast(e.message, { error: true }); }
}

// ── playback ────────────────────────────────────────────────────────────────────────────────
function seek(t) {
  S.cursor = t;
  tl.setPlayhead(t);
  clock(t);
  if (mixer.playing) playMix(t);
  else if (player.isPlaying("ref") || player.isPlaying("take")) playFrom(S.src, t);
}
function playFrom(src, t0, t1 = null) {
  mixer.stop();
  S.src = src;
  const tk = S.takes.find((x) => x.id === S.take);
  const url = src === "ref" ? refAudio() : tk ? api.audioUrl(tk.id) : null;
  if (!url || (src === "ref" && !S.layout)) return toast(src === "ref" ? "Nothing built yet." : "Pick a take first.");
  if (src === "take" && tk && !tk.played) { tk.played = true; api.setLabel(tk.id, { played: true }).catch(() => {}); }
  S.playingTake = src === "take" && tk ? tk.id : null;
  const off = src === "take" ? takeOff(tk) : 0;         // a section take's audio starts at its section
  player.play({ key: src, url, t0: Math.max(0, (t0 ?? S.cursor) - off), t1: t1 == null ? null : t1 - off, onTime: (t) => {
    const T = t == null ? S.cursor : t + off;
    tl.setPlayhead(T); clock(T);
    if (TK && TK.id === S.playingTake) TK.sv.setPlayhead(t);      // the inspector's spectrogram follows too
  } }).catch((e) => toast("can't play: " + e.message, { error: true }));
  paint();
}
player.subscribe(({ playing }) => { if (playing) mixer.stop(); playBtn(); syncTakeUI(); tl.redraw(); });
function playBtn() { $("play").textContent = mixer.playing || player.isPlaying() ? "■" : "▶"; $("src").value = S.src; }
function clock(t) { $("clock").textContent = `${fmt(t)} / ${fmt(projLength())}`; }
function togglePlay() {
  if (mixer.playing) { mixer.stop(); playBtn(); return; }
  if (player.isPlaying()) { player.stop(); return; }
  if (isMix()) playMix(S.cursor); else playFrom(S.src, S.cursor);
}

// ── side panel: library browser + imports ───────────────────────────────────────────────────
const lib = new Browser($("sidelib"), { compact: true });          // no Add button: cards are dragged onto a track
async function refreshLib() { await lib.reload(); S.starred = lib.rows; inspKey = ""; }
async function loadImports() {
  if (!S.name) return;
  try { S.imports = (await api.get("/sa3/composer/imports", { name: S.name })).map((x) => ({ ...x, prompt: "import: " + x.name, folder: "import" })); } catch (e) { S.imports = []; }
  const host = $("implist");
  host.innerHTML = "";
  if (!S.imports.length) host.innerHTML = '<div class="muted">No imports in this project yet.</div>';
  for (const r of S.imports) new ClipCard(host, r, { compact: true });
}
$("upl").querySelector("input").onchange = async (e) => {
  for (const f of e.target.files) {
    const fd = new FormData(); fd.append("file", f);
    try {
      const r = await fetch(`/sa3/composer/import?name=${encodeURIComponent(S.name)}`, { method: "POST", body: fd });
      const d = await r.json();
      if (!r.ok || d.error) throw new Error(d.error || r.statusText);
      toast(`Imported ${f.name} (${fmt(d.seconds)}).`);
    } catch (er) { toast("Import failed: " + er.message, { error: true }); }
  }
  e.target.value = "";
  loadImports();
};
function sidebar(show) {
  $("side").hidden = !show;
  $("rail").classList.toggle("closed", !show);
  $("sidetog").textContent = show ? "⟨" : "⟩";
  prefs.side = show; savePrefs();
}
$("rail").onclick = () => sidebar($("side").hidden);
sidebar(prefs.side !== false);
bind("l", "Show / hide the library", () => sidebar($("side").hidden));
function inspPanel(show) {                    // the right panel (settings of the selection) collapses like the library
  $("insp").hidden = !show;
  $("irail").classList.toggle("closed", !show);
  $("insptog").textContent = show ? "⟩" : "⟨";
  prefs.insp = show; savePrefs();
  tl.redraw();
}
$("irail").onclick = () => inspPanel($("insp").hidden);
inspPanel(prefs.insp !== false);
bind("i", "Show / hide the settings panel", () => inspPanel($("insp").hidden));
document.querySelector(".side-tabs").onclick = (e) => {
  const v = e.target.dataset.v;
  if (!v) return;
  document.querySelectorAll(".side-tabs button").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
  $("sidelib").hidden = v !== "lib"; $("sideimp").hidden = v !== "imp";
  if (v === "imp") loadImports();
};
// drag a card from the side panel onto a track lane
const tlHost = $("tl");
tlHost.addEventListener("dragover", (e) => { if (e.dataTransfer.types.includes(MIME)) { e.preventDefault(); tlHost.classList.add("drop"); } });
tlHost.addEventListener("dragleave", () => tlHost.classList.remove("drop"));
tlHost.addEventListener("drop", (e) => {
  tlHost.classList.remove("drop");
  const raw = e.dataTransfer.getData(MIME);
  if (!raw) return;
  e.preventDefault();
  const d = JSON.parse(raw), hit = tl.at(e);
  const track = hit.lane && /^t\d+$/.test(hit.lane.key) ? +hit.lane.key.slice(1) : 0;
  addPiece({ id: d.id }, [d.s0, d.s1], { at: tl.snap(hit.t, e), track });
});

// ── projects ────────────────────────────────────────────────────────────────────────────────
async function projectsMenu(selected) {
  const ps = await api.get("/sa3/composer/projects");
  const sel = $("proj");
  sel.innerHTML = ps.map((p) => `<option value="${p.name}">${p.name}${p.takes ? ` (${p.takes})` : ""}</option>`).join("");
  sel.value = selected;
  return ps;
}
async function open(name) {
  player.stop(); mixer.stop();
  let d = await api.get("/sa3/composer/project", { name });
  if ((d.project || {}).sequence || (d.project || {}).cond_fixed) {      // phase 0 / 1 shapes → explicit pieces
    try { await api.post("/sa3/composer/convert", { name }); d = await api.get("/sa3/composer/project", { name }); }
    catch (e) { toast("Couldn't convert this project to tracks: " + e.message, { error: true }); }
  }
  S.name = name;
  S.proj = Object.assign(DEFAULT(), d.project || {});
  const legacy = migrate(S.proj);
  S.layout = d.layout;
  S.takes = d.takes || [];
  S.job = d.job;
  S.take = S.takes.length ? S.takes[0].id : null;
  S.undo = []; S.redo = []; S.sel = null; S.error = null; S.cursor = 0; S.fit = null; inspKey = "";
  S.range = null; tl.setRange(null);
  prefs.last = name; savePrefs();
  await projectsMenu(name);
  paint();
  tl.fit();                                  // after paint: it fits the new project's length
  syncMixer();
  if (S.layout) loadFit();
  if (S.job && S.job.running) pollJob();
  if ((!S.layout && pieces().length) || legacy) scheduleSave();
  if (!$("sideimp").hidden) loadImports();
}
function migrate(p) {                         // older projects: rewrite regions → the Rewrite curve; no hand-set BPM
  let changed = false;
  if ((p.regions || []).length) {
    let K = (p.rewrite || []).map((k) => k.slice());
    for (const r of p.regions) if (r.type === "gen" || r.type === "soft") K = C.setRange(K, +r.t0, +r.t1, r.type === "gen" ? 1 : +(r.u ?? 0.4));
    K = C.norm(K);
    if (K.length) p.rewrite = K;
    changed = true;
  }
  if ("regions" in p) { delete p.regions; changed = true; }
  if (p.bpm != null) { delete p.bpm; changed = true; }
  return changed;
}
async function create(name) {
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(name)) return toast("Use letters, digits, - and _ (no spaces).", { error: true });
  const ps = await api.get("/sa3/composer/projects");
  if (ps.some((p) => p.name === name)) return open(name);
  await api.post("/sa3/composer/save", { name, project: DEFAULT(), build: false });
  await open(name);
}

// ── render ──────────────────────────────────────────────────────────────────────────────────
async function render() {
  const n = Math.max(1, Math.min(16, +$("nseeds").value || 2));
  const seeds = Array.from({ length: n }, () => Math.floor(Math.random() * 2 ** 31));
  clearTimeout(saveT);
  if (S.saving) await saveNow();
  edit((p) => { p.seeds = seeds; }, { save: false });
  await saveNow();
  if (S.error) return toast("Fix the build error first: " + S.error, { error: true });
  if (S.empty && S.empty.length) toast(`Heads-up: ${S.empty.length} empty stretch${S.empty.length === 1 ? " is" : "es are"} only partly rewritten (the ⚠ chip; usually you want strength 1 there). Rendering anyway.`, { ms: 7000 });
  try {
    const sec = S.range ? [S.range[0], Math.min(S.range[1], projLength())] : null;
    const r = await api.post("/sa3/composer/render", { name: S.name, seeds, ...(sec ? { section: sec } : {}) });
    if (r.error) throw new Error(r.error);
    S.job = r.job;
    toast(`${n} take${n === 1 ? "" : "s"} queued in the studio (seeds ${seeds.join(", ")}).`);
    paint();
    pollJob();
  } catch (e) { toast(e.message, { error: true }); }
}
let pollT = 0;
async function pollJob() {
  clearTimeout(pollT);
  try {
    const name = S.name;
    const d = await api.get("/sa3/composer/project", { name });
    if (name !== S.name) return;
    const had = new Set(S.takes.map((t) => t.id));
    S.takes = d.takes || [];
    S.job = d.job;
    const nw = S.takes.find((t) => !had.has(t.id));
    if (nw) S.take = nw.id;
    if (S.job && S.job.error && !S.job.running) toast("Render: " + S.job.error, { error: true });
    paint();
    if (S.job && S.job.running) pollT = setTimeout(pollJob, 2000);
  } catch (e) { pollT = setTimeout(pollJob, 4000); }
}

// ── wiring ──────────────────────────────────────────────────────────────────────────────────
const shell = mountShell(document.body, { active: "composer" });
// one header row instead of two: project + BPM and Render settings + Render all live in the shell's nav row
shell.bar.querySelector("nav").after($("projgrp"));
shell.right.prepend($("rendergrp"));
shell.right.prepend($("status"));          // left of the render group: its "building…" never shifts the header
$("proj").onchange = () => open($("proj").value);
$("new").onclick = () => { const i = $("newname"); i.hidden = !i.hidden; if (!i.hidden) { i.value = ""; i.focus(); } };
$("newname").onkeydown = (e) => { if (e.key === "Enter") { create(e.target.value.trim()); e.target.hidden = true; } if (e.key === "Escape") e.target.hidden = true; };
$("seam").onchange = () => edit((p) => { p.seam = +$("seam").value; p.seam_unit = "beats"; });
$("cond").onclick = (e) => {
  const v = e.target.dataset && e.target.dataset.v;
  if (!v) return;
  if (v === "keys" && !((S.proj.cond || {}).keys || []).length) return toKeys();
  edit((p) => { p.cond = { ...(p.cond || {}), gap: v }; });
};
$("dit").onchange = () => edit((p) => { p.model = { ...(p.model || {}), dit: $("dit").value }; });
$("steps").onchange = () => edit((p) => { p.model = { ...(p.model || {}), steps: Math.max(4, Math.min(16, +$("steps").value || 8)) }; });
$("nseeds").value = prefs.nseeds || 2;
$("nseeds").oninput = () => { prefs.nseeds = +$("nseeds").value; savePrefs(); paint(); };
$("render").onclick = render;
$("undo").onclick = () => undo(-1);
$("redo").onclick = () => undo(1);
$("keys").onclick = help;
$("play").onclick = togglePlay;
$("emptywarn").onclick = (e) => {
  const r = e.target.getBoundingClientRect();
  menu(r.left, r.bottom + 4, [
    { label: "Regenerate them fully (strength 1 there)", fn: fixEmpty },
    "-",
    ...S.empty.map((q) => ({ label: `Jump to ${fmt(q.a)} – ${fmt(q.b)} (strength ${q.u.toFixed(2)})`, fn: () => seek(q.a) })),
  ]);
};
$("src").onchange = () => { $("src").blur(); const was = mixer.playing || player.isPlaying(); mixer.stop(); player.stop(); S.src = $("src").value; prefs.src = S.src; savePrefs(); if (was) togglePlay(); else playBtn(); };
if (["mixp", "mix", "ref", "take"].includes(prefs.src)) S.src = prefs.src;
// popovers: BPM and Render settings
function pop(btn, id) {
  $(btn).onclick = (e) => { e.stopPropagation(); const p = $(id), was = !p.hidden; document.querySelectorAll(".pop").forEach((x) => { x.hidden = true; }); p.hidden = was; };
  $(id).addEventListener("pointerdown", (e) => e.stopPropagation());
}
pop("bpmbtn", "bpmpop"); pop("setbtn", "setpop");
document.addEventListener("pointerdown", () => document.querySelectorAll(".pop").forEach((x) => { x.hidden = true; }));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") document.querySelectorAll(".pop").forEach((x) => { x.hidden = true; }); });
$("view").onclick = (e) => {
  const r = e.target.getBoundingClientRect(), tick = (k) => (shown(k) ? "✓ " : "   ");
  const tog = (k) => () => { prefs.show = { ...(prefs.show || {}), [k]: !shown(k) }; savePrefs(); paint(); };
  menu(r.left, r.bottom + 4, [
    { label: tick("cond") + "Conditioning lane (what SA3 is told, over time)", fn: tog("cond"), disabled: condMode() === "keys" },
    { label: tick("ref") + "Reference lane (what SA3 keeps)", fn: tog("ref") },
    (S.proj.ghost || []).length ? { label: tick("ghost") + "Before lane (the layout a promoted take came from)", fn: tog("ghost") } : null,
    "-",
    { label: "Zoom to fit", fn: () => tl.fit() },
    { label: "Add 8 s at the end", fn: () => extend(8) },
    { label: "Add 30 s at the end", fn: () => extend(30) },
    { label: "Add a track", fn: () => edit((q) => { q.tracks = nTracks() + 1; }) },
  ].filter(Boolean));
};
bind("space", "Play / stop (the last source: mix, reference or take)", togglePlay);
bind("mod+z", "Undo", () => undo(-1));
bind("shift+mod+z", "Redo", () => undo(1));
bind("backspace", "Delete the selection", deleteSel);
bind("mod+backspace", "Delete the selection", deleteSel);
bind("mod+c", "Copy the selected clip(s) / region", () => copySel(false));
bind("mod+x", "Cut the selected clip(s) / region", () => copySel(true));
bind("mod+v", "Paste: after the selected clip(s), else at the playhead", pasteClip);
bind("delete", "Delete the selection", deleteSel);
bind("arrowleft", "Selected clip one beat earlier (a Rewrite key / span: one latent frame)", (e) => nudge(-1, e));
bind("arrowright", "Selected clip one beat later (a Rewrite key / span: one latent frame)", (e) => nudge(1, e));
bind("arrowup", "Selected clip one track up (a Rewrite point / span: strength +0.05; a take: the newer take, same moment)", () => (S.sel === "w" ? rw.nudgeU(0.05) : (S.sel || "").startsWith("T:") ? stepTake(-1) : moveTrack(-1)));
bind("arrowdown", "Selected clip one track down (a Rewrite point / span: strength −0.05; a take: the older take, same moment)", () => (S.sel === "w" ? rw.nudgeU(-0.05) : (S.sel || "").startsWith("T:") ? stepTake(1) : moveTrack(1)));
bind("m", "Mute the selected clips", () => toggleSel("mute"));
bind("s", "Solo the selected clips", () => toggleSel("solo"));
bind("escape", "Clear the selection (and the ruler's render section)", () => { S.sel = null; S.multi = new Set(); tl.multi = S.multi; rw.sel = null; tl.select(null); setSection(null); inspector(); });
$("tl").addEventListener("contextmenu", (e) => {
  const hit = tl.at(e);
  const sel = S.sel;
  if (!sel) return;
  e.preventDefault();
  menu(e.clientX, e.clientY, [
    sel[0] === "p" ? { label: "Mute / unmute", hint: "M", fn: () => toggleSel("mute") } : null,
    sel[0] === "p" ? { label: "Solo", hint: "S", fn: () => toggleSel("solo") } : null,
    sel[0] === "p" && selPis().some((pi) => (pieces()[pi].speed || 1) !== 1) ? { label: "Reset speed (undo the ⌥-stretch)", fn: () => edit((p) => { for (const pi of selPis()) delete p.pieces[pi].speed; }) } : null,
    sel[0] === "p" ? { label: "Play the mix from here", fn: () => playMix(hit.t) } : null,
    { label: "Delete", hint: "⌫", danger: true, fn: deleteSel },
  ].filter(Boolean));
});

// "Open in composer" from another page: ?add=<id>&s0&s1 on load, or __sa3nav(search) from the tab host
// (ui/app.js) when this page is already open in its kept-alive tab
let ready = false;
function navAdd(search) {
  const q = new URLSearchParams(search);
  const id = q.get("add");
  if (!id) return;
  addPiece(rowOf(id) || { id }, [+q.get("s0") || 0, +q.get("s1") || 8]);
  toast(`Added to ${S.name} (track 1, after the last clip).`);
}
window.__sa3nav = (search) => { if (ready) navAdd(search); else setTimeout(() => window.__sa3nav(search), 300); };

(async () => {
  try {
    await refreshLib();
    const ps = await api.get("/sa3/composer/projects");
    const want = prefs.last && ps.some((p) => p.name === prefs.last) ? prefs.last : ps.length ? ps[0].name : null;
    if (want) await open(want);
    else await create("sketch-1");
    navAdd(location.search);
    if (location.search) history.replaceState(null, "", location.pathname);
    ready = true;
  } catch (e) { toast("Composer backend not reachable: " + e.message + " (run make start)", { error: true, ms: 15000 }); }
})();
