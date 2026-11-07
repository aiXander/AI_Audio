// The Composer editor (design: docs/reference/sa3-composer.md; backend: ../server.py over
// ../compose.py and ../fit.py). Pieces cut from starred renders (or imports) sit on tracks and may
// overlap; each has a mix strip (monitored live in WebAudio with the server's own filters,
// /sa3/ui/mixer.js), a fix chain (the Fit panel's conform), mute / solo, and a conditioning anchor
// it proposes (overridable). Lanes: one per track, Rewrite (a strength curve drawn over the automatic
// one: drag = a span at strength 1, drag its top down to soften, click = a keypoint; ui/curvelane.js, the same editor as the
// studio's Inpaint timeline; project.json `rewrite`),
// Conditioning (what SA3 is conditioned on per frame; keypoints in Keypoints mode, resolved live in the
// page so drags redraw at once; no names on the lane: hover 0.7 s, or the right-click menu; a click
// only seeks: keypoints are added from the right-click menu, never by a click. Right-click any of it:
// edit its prompt, or swap in a library render's conditioning (pickAnchor: the Browser in a modal, ▶
// previews) or a typed prompt (promptAnchor), on the block, the clip or the one anchor it belongs to;
// a fade's presets, hard cut … 4 bars: condMenu. Each fade between two sources has a ⟷ handle: drag it
// to make the change longer / shorter around its centre (withFade);
// drag across it, or right-click a gap between clips: insert a new source over that span (insertCond:
// switches to keypoints, holds the new source over the span, fades in / out over a bar outside it);
// a dragged keypoint draws a guide through every lane and snaps to clip starts / ends), Reference (the
// built ref.wav: what SA3 keeps), then one lane per take under everything (takeLanes: every take, newest
// first, in the timeline's scrolling bottom region; ▶ / ■ in the gutter plays from the shared playhead, a click selects it into the
// inspector: score, star, Promote, splice; ↑ ↓ switch takes at the same moment). Takes = one studio
// render per seed; Promote makes a take the base (then layer more pieces over it, or draw edits on it).
// Drag across a take lane to select a stretch, then drag it onto a track (or ↵) to use it as a clip
// (liftTake); drag a take up / down (vertically) to carry the whole take. A carried take or stretch snaps
// to where it belongs (liftHome: lined up with the source clip it was rendered from, wherever that clip
// sits now; within 2 bars, ⇧ = free). Dropped on a track holding those source clips it replaces that stretch on every track (cutSpan: the take is
// the whole mix there); on any other track (an empty one, or the "+ Track" lane = a new track) it lands
// in time, layered, and the originals stay. The "+ Track" lane under the tracks adds a track (click its label; its body is a drop target only); right-click a track → Delete track removes it with its clips (confirm modal, deleteTrack). Clips cut from a take, and a promoted base, read the take's frozen copy in clips/
// (server.freeze), so trashing the take later never changes the timeline. Right-click a clip: Split here. Every edit autosaves
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
// Track gutter: M mutes the whole track, the fader sets its volume (−36…+6 dB, double-click = 0 dB);
// a track's volume curve (track_mix[k].env) starts from right-click → Add volume keypoint (envAdd);
// project.json `track_mix`, applied server-side too (compose.track_mix), so SA3's reference hears
// exactly what the live monitor plays.
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
import { revealItem } from "/sa3/ui/reveal.js";
import { openHighlight } from "/sa3/ui/highlight.js";
import { CurveLane } from "/sa3/ui/curvelane.js";
import * as C from "/sa3/ui/curve.js";
import { LoraStack } from "/sa3/ui/lora.js";
import { modal } from "/sa3/ui/modal.js";

const $ = (id) => document.getElementById(id);
const LS = "sa3-composer";
const MAX_LEN = 380;
const DEFAULT = () => ({ seeds: [], model: { dit: "medium", steps: 8, cfg: 1.0 }, seam: 2, seam_unit: "beats", cond: { gap: "crossfade" }, pieces: [], tracks: 2, master: { gain: 0, limit: true, ceiling: -1 } });
const S = { name: null, proj: null, layout: null, takes: [], jobs: [], take: null, sel: null, multi: new Set(), undo: [], redo: [],
  starred: [], imports: [], clips: [], lift: null, saved: null, empty: [], saveSeq: 0, saving: false, error: null, cursor: 0, src: "mixp", specs: {}, fit: null };
const mixer = new Mixer();
let prefs = {};
try { prefs = JSON.parse(localStorage.getItem(LS) || "{}"); } catch (e) { prefs = {}; }
const savePrefs = () => { try { localStorage.setItem(LS, JSON.stringify(prefs)); } catch (e) { /* private window */ } };
// lane heights (prefs.lh: track · rewrite · cond · take), dragged on a lane's bottom edge in the left
// column (the timeline's onResize; tracks and takes each resize as one group). Unset: the default
// (tracks flex to fill); a double-click on the edge resets it. Per browser, never in the project.
const laneH = (k, d) => ((prefs.lh || {})[k] ?? d);
const resizeLane = (k, min) => ({ rgroup: k, minH: min, onResize: (h, final) => {
  prefs.lh = { ...(prefs.lh || {}) };
  if (h == null) delete prefs.lh[k]; else prefs.lh[k] = h;
  if (final) { savePrefs(); paint(); }
} });

const fmt = (t) => (t == null ? "–" : t >= 60 ? `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}` : `${t.toFixed(1)} s`);
const short = (s, n = 46) => { s = (s || "").replace(/^anarchy:\s*@?/, "").replace(/\.cond\.npz$/, ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const base = () => !!(S.proj && S.proj.base);
const baseTake = () => S.proj && (S.proj.base_take || S.proj.base);   // the take lane the base came from (base = its frozen copy)
const bpmNow = () => (S.layout && S.layout.bpm) || null;          // the server's live estimate, never set by hand
const beat = () => (bpmNow() ? 60 / bpmNow() : null);
const rowOf = (id) => S.starred.find((r) => r.id === id) || S.imports.find((r) => r.id === id) || S.clips.find((r) => r.id === id);
const nameOf = (id) => { const r = rowOf(id); return r ? short(r.prompt || r.name || id) : short(String(id).split("/").pop()); };
const PCOL = ["#a99cff", "#5ed1b5", "#ffb86b", "#7ec8ff", "#ff6b81", "#e6e27a", "#c08cff", "#8cffd1"];
const condMode = () => ((S.proj && S.proj.cond) || {}).gap || "crossfade";
const pieces = () => (S.proj && S.proj.pieces) || [];
const nTracks = () => Math.max(S.proj.tracks || 1, ...pieces().map((p) => (p.track || 0) + 1), 1);
// per-track mix (project.json `track_mix[k]` = {gain: dB, mute}; compose.track_mix reads the same)
const trackMix = (k) => ((S.proj && S.proj.track_mix) || [])[k] || {};
const trackMuted = (k) => !!trackMix(k).mute;
const trackDb = (k) => +trackMix(k).gain || 0;
function setTrackMix(p, k, fn) {
  const tm = (p.track_mix || []).slice();
  while (tm.length <= k) tm.push(null);
  const x = { ...(tm[k] || {}) };
  fn(x);
  if (!x.gain) delete x.gain;
  if (!x.mute) delete x.mute;
  tm[k] = Object.keys(x).length ? x : null;
  while (tm.length && tm[tm.length - 1] == null) tm.pop();
  if (tm.length) p.track_mix = tm; else delete p.track_mix;
}
const stripMix = (it) => {                    // a clip's mix strip with its track's fader added (compose.strip does the same)
  const tg = trackDb(it.track || 0);
  return tg ? { ...(it.mix || {}), gain: +(((it.mix || {}).gain || 0) + tg).toFixed(4) } : it.mix;
};
const strip = (a) => { if (!a) return a; const { label, ...x } = a; return x; };
const srcLabel = (a) => (!a ? "–" : a.prompt != null ? (a.prompt || "(empty prompt)") : a.bank ? `${nameOf(a.bank)} #${(a.i || 0) + 1}` : a.id ? nameOf(a.id) : String(a.npz || "").split("/").pop());
// Muted conditioning (`mute: true` on any anchor; condsrc.muted): SA3's unconditional input over that
// stretch (all-zero tokens, as its CFG dropout trained it), so only the audio context steers. The rest of
// the anchor is kept so Unmute restores it; `{mute: true}` alone = a clip's / the project's own, muted.
const hasSrc = (a) => !!a && ["id", "npz", "prompt", "bank", "sha"].some((k) => k in a);
const isMuted = (a) => !!(a && a.mute);
const muteOf = (a) => ({ ...(strip(a) || {}), mute: true });
const unmuteOf = (a) => { if (!a) return null; const { mute, label, ...x } = a; return hasSrc(x) ? x : null; };   // null = its own again
const MUTE_COL = "#8b8b9a";
const anchorLabel = (a) => (isMuted(a) ? "muted · no conditioning" + (unmuteOf(a) ? ` (was ${srcLabel(unmuteOf(a))})` : "") : srcLabel(a));
const anchorCol = (a, i) => (isMuted(a) ? MUTE_COL : PCOL[i % PCOL.length]);
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
  const ps = pieces(), on = (p) => !trackMuted(p.track || 0), solo = ps.some((p) => p.solo && on(p));   // a muted track beats solo
  return ps.map((p, i) => [p, i]).filter(([p]) => on(p) && !(p.mute || (solo && !p.solo))).map(([, i]) => i);
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
    const ks = g.stretch || 1;                 // source s per timeline s, as pieceEdit converts a trim
    const span = [g.at - it.src[0] / ks, g.at + g.dur + Math.max(0, srcLen - it.src[1]) / ks];   // trims end where the audio does
    items.push({ id: "p" + pi, pi, kind: "piece", t0: g.at, t1: g.at + g.dur, span, move: !isBase, resize: isBase ? "" : "lr", stretch: !isBase, srcLen, g,
      tip: isBase ? "the promoted take: the timeline itself (layer clips over it, or rewrite parts of it on the Rewrite lane)"
        : "click: play the mix from here + settings on the right · drag: move (⇧ free, else snaps to beats; up / down onto another track) · butt it against another clip: a crossfade (⟷ on the join) · drag an edge: trim · ⌥-drag an edge: time-stretch · ⌘-click or drag on empty space: select several · ↑↓ track, ←→ one beat, M mute, S solo, ⌘C / ⌘V, ⌫ delete",
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
        const gain = ((it.mix || {}).gain || 0) + trackDb(k);
        if (gain < 0) { gx.fillStyle = `rgba(9,9,13,${Math.min(0.6, -gain / 30)})`; gx.fillRect(r.x, r.y + 2, r.w, r.h - 4); }
      } });
  });
  return items;
}
// ── crossfades: back-to-back clips on a track (compose.transitions resolves them: default 0.5 s,
// clamped to `max`, the source audio both clips have past the join). Each gets a ⟷ handle on the
// crossfade's right edge (the join itself for a hard cut): drag it, both edges move symmetrically;
// click / right-click: the options. Stored on the later clip as `xfade: {len, shape}`. Joins, lengths
// and curves are computed here (`joins`, mirroring compose.transitions), so they follow every edit at once.
const cutOf = (g) => g.src_cut || g.src;
function continues(ga, gb) {                 // compose.continues: b carries a's source on (a split clip)
  return ga.id === gb.id && Math.abs((ga.stretch || 1) - (gb.stretch || 1)) <= 1e-4 && Math.abs(cutOf(ga)[1] - cutOf(gb)[0]) < 5e-3;
}
// every back-to-back join on the tracks, worked out here from the clips (compose.transitions' rule, so
// the page never waits for a build): [{a, b, track, t, len, max, shape}]
function joins() {
  const out = [], act = activeIdx().map((pi) => ({ pi, g: geom(pi) }));
  for (const k of new Set(act.map((x) => x.g.track))) {
    const row = act.filter((x) => x.g.track === k).sort((x, y) => x.g.at - y.g.at);
    for (let i = 0; i + 1 < row.length; i++) {
      const A = row[i], B = row[i + 1], ga = A.g, gb = B.g;
      if (Math.abs(ga.at + ga.dur - gb.at) > XF_TOL || continues(ga, gb)) continue;
      const lenA = ga.src_len || (rowOf(ga.id) || {}).seconds || cutOf(ga)[1];
      const tail = (lenA - cutOf(ga)[1]) / (ga.stretch || 1), head = cutOf(gb)[0] / (gb.stretch || 1);
      const max = Math.max(0, Math.min(2 * tail, 2 * head, ga.dur, gb.dur));
      if (max < 0.02) continue;
      const set = pieces()[B.pi].xfade || {};
      out.push({ a: A.pi, b: B.pi, track: k, t: gb.at, max, shape: set.shape === "lin" ? "lin" : "eq",
        len: set.len != null ? xfClamp(+set.len, max) : Math.min(XF_DEFAULT, max) });
    }
  }
  return out;
}
function liveXf(k) {                         // a track's joins, minus any whose clip is being dragged
  const d = tl.drag, moving = (pi) => !!(d && d.it && (d.it.id === "p" + pi || (d.gids && d.gids.has("p" + pi))));
  return joins().filter((x) => x.track === k && !moving(x.a) && !moving(x.b));
}
const XF_TOL = 0.03;                         // compose.XF_TOL
const XF_DEFAULT = 0.5;                      // compose.XF_DEFAULT
const xfClamp = (w, mx) => (w < 0.02 ? 0 : +Math.min(mx, w).toFixed(3));
const xfTxt = (w) => (w < 0.02 ? "hard cut" : (w < 1 ? `${Math.round(w * 1000)} ms` : `${w.toFixed(2)} s`) + (beat() ? ` · ${(w / beat()).toFixed(2)} beats` : ""));
function xfLen(x) {                          // its length, with a handle drag in flight applied
  const d = tl.drag;
  return d && d.kind === "marker" && d.p && d.m.xf && d.m.xf.b === x.b ? xfClamp(2 * (d.p[0] - x.t), x.max) : x.len;
}
function xfMarkers(k) {
  return liveXf(k).map((x) => ({ id: "x" + x.b, xf: x, t: x.t + x.len / 2, shape: "fade", free: true, color: "#5ed1b5",
    tip: `${x.len ? "Crossfade " + xfTxt(x.len) + (x.shape === "lin" ? " (linear)" : "") : "Hard cut"}: ${nameOf(pieces()[x.a].id)} → ${nameOf(pieces()[x.b].id)}`
      + `\nlongest possible: ${xfTxt(x.max)} (the source audio both clips have past this edge)`
      + `\ndrag this edge → : longer · ← : shorter (onto the join: a hard cut, where SA3 re-renders the seam)\nclick: lengths + curve` }));
}
function drawXf(g, r, t, k) {
  const d = tl.drag;
  for (const x of liveXf(k)) {
    const w = xfLen(x), live = d && d.kind === "marker" && d.m.xf && d.m.xf.b === x.b;
    const a = t.x(x.t - w / 2), b = t.x(x.t + w / 2), top = r.y + 18, bot = r.y + r.h - 4;
    if (b < r.x || a > r.x + r.w) continue;
    if (w > 0 && b - a > 2) {
      g.fillStyle = "rgba(9,9,13,0.4)"; g.fillRect(a, r.y + 17, b - a, r.h - 19);   // below the ⟷ pill (over() paints after markers)
      const curve = (up, col) => {
        g.strokeStyle = col; g.lineWidth = 1.6; g.beginPath();
        for (let i = 0; i <= 32; i++) {
          const u = i / 32, v = x.shape === "lin" ? (up ? u : 1 - u) : up ? Math.sin(u * Math.PI / 2) : Math.cos(u * Math.PI / 2);
          const px = a + (b - a) * u, py = bot - (bot - top) * v;
          if (i) g.lineTo(px, py); else g.moveTo(px, py);
        }
        g.stroke();
      };
      curve(false, "rgba(255,255,255,0.8)");
      curve(true, "#5ed1b5");
    } else {
      g.strokeStyle = "rgba(94,209,181,0.8)"; g.lineWidth = 1.2; g.setLineDash([3, 3]);
      g.beginPath(); g.moveTo(t.x(x.t) + 0.5, r.y + 18); g.lineTo(t.x(x.t) + 0.5, r.y + r.h - 3); g.stroke(); g.setLineDash([]);
    }
    if (live) {
      const s = w ? `crossfade ${xfTxt(w)}${w >= x.max - 1e-3 ? " (longest)" : ""}` : "hard cut";
      g.font = "600 10.5px Inter"; g.textBaseline = "top";
      const tw = g.measureText(s).width + 8, lx = Math.min(r.x + r.w - tw - 2, Math.max(r.x + 2, t.x(x.t + w / 2) + 16));   // right of the handle
      g.fillStyle = "rgba(9,9,13,0.85)"; g.fillRect(lx, r.y + 2, tw, 15);
      g.fillStyle = "#fff"; g.fillText(s, lx + 4, r.y + 4.5);
    }
  }
}
function setXf(b, patch) {                   // patch {len} | {shape} | null (back to the default)
  edit((p) => {
    const pc = p.pieces[b];
    if (!pc) return;
    const x = patch ? { ...(pc.xfade || {}), ...patch } : {};
    if (x.shape === "eq") delete x.shape;
    if (Object.keys(x).length) pc.xfade = x; else delete pc.xfade;
  });
}
function xfMenu(e, x) {
  const set = pieces()[x.b].xfade || {}, B = beat(), on = (v) => Math.abs(x.len - Math.min(v, x.max)) < 0.005;
  const lens = [[0, "Hard cut"], [0.1], [0.25], [0.5, "", " (default)"], ...(B ? [[B, "1 beat"], [4 * B, "1 bar"]] : [[1], [2]]), [x.max, "Longest"]];
  menu(e.clientX, e.clientY, [
    { head: `${short(nameOf(pieces()[x.a].id), 24)} → ${short(nameOf(pieces()[x.b].id), 24)}` },
    ...lens.map(([v, l, tail]) => ({ label: (l ? (v ? `${l} · ${xfTxt(v)}` : l) : xfTxt(v)) + (tail || ""), hint: on(v) ? "✓" : "",
      disabled: v > x.max + 1e-3, fn: () => setXf(x.b, { len: +v.toFixed(3) }) })),
    "-",
    { label: "Equal-power (different material)", hint: x.shape !== "lin" ? "✓" : "", fn: () => setXf(x.b, { shape: "eq" }) },
    { label: "Linear (same material, in phase)", hint: x.shape === "lin" ? "✓" : "", fn: () => setXf(x.b, { shape: "lin" }) },
    set.len != null || set.shape ? "-" : null,
    set.len != null || set.shape ? { label: "Back to the default (500 ms, equal-power)", fn: () => setXf(x.b, null) } : null,
  ].filter(Boolean));
}

// ── Conditioning keypoints, resolved in the page with compose.key_curves' rule, so a dragged
// keypoint or fade handle redraws the bands live (the build's curves only arrive after a rebuild).
// A "source" = one distinct anchor; a run of consecutive keypoints with the same source is one block
// (it holds at full strength between them); between two blocks sits a fade (cosine, or a hard cut
// when the later keypoint has `cut`). Fades get a ⟷ handle on the lane: drag it to make the fade
// longer / shorter around its centre (withFade). Past the last keypoint its source holds, to any length.
const akey = (a) => JSON.stringify(strip(a) || {});
const condKeys = () => (condMode() === "keys" && ((S.proj.cond || {}).keys || [])) || [];
const condLen = () => Math.max((S.layout && S.layout.length) || 0, projLength());
function keyColors(ks) {                     // one colour per source, in keypoint order (diamonds, bands, menus agree)
  const col = new Map();
  let n = 0;
  for (const k of ks) { const j = akey(k.anchor); if (!col.has(j)) col.set(j, isMuted(k.anchor) ? MUTE_COL : PCOL[n++ % PCOL.length]); }
  return col;
}
function keyWeights(ks, t) {                 // [{a, w}] at t
  if (!ks.length) return [];
  let i = -1;
  ks.forEach((k, j) => { if (k.t <= t) i = j; });
  if (i < 0) return [{ a: ks[0].anchor, w: 1 }];
  const n = ks[i + 1];
  if (!n || akey(n.anchor) === akey(ks[i].anchor)) return [{ a: ks[i].anchor, w: 1 }];
  const span = n.t - ks[i].t, x = n.cut || span <= 0 ? 0 : 0.5 - 0.5 * Math.cos(Math.PI * (t - ks[i].t) / span);
  return [{ a: ks[i].anchor, w: 1 - x }, { a: n.anchor, w: x }];
}
function fades(ks) {                         // [{i (its left keypoint), a, b, c (centre), w (length; 0 = cut), cut}]
  const out = [];
  for (let i = 0; i + 1 < ks.length; i++) {
    const k = ks[i], n = ks[i + 1];
    if (akey(k.anchor) === akey(n.anchor)) continue;
    out.push({ i, a: k.t, b: n.t, c: n.cut ? n.t : (k.t + n.t) / 2, w: n.cut ? 0 : n.t - k.t, cut: !!n.cut });
  }
  return out;
}
// keys with the fade after keypoint i set to w seconds around its centre, clamped between the
// neighbouring keypoints; w < 0.05 = a hard cut (positions stay: the earlier source holds until the cut)
function withFade(ks, i, w) {
  const out = ks.map((k) => ({ ...k })), f = fades(ks).find((x) => x.i === i);
  if (!f) return out;
  if (w < 0.05) { out[i + 1].cut = true; return out; }
  delete out[i + 1].cut;
  const lo = i > 0 ? out[i - 1].t + 0.05 : 0, hi = i + 2 < out.length ? out[i + 2].t - 0.05 : Math.max(condLen(), f.b);
  out[i].t = +Math.max(lo, f.c - w / 2).toFixed(3);
  out[i + 1].t = +Math.min(hi, f.c + w / 2).toFixed(3);
  return out;
}
const fadeW = (m, t) => Math.max(0, m.w0 + 2 * (t - m.t));   // a fade handle dragged to t: its new length
const lenTxt = (w) => (w < 0.05 ? "a hard cut" : `${w.toFixed(1)} s` + (beat() ? ` (${(w / beat()).toFixed(1)} beats)` : ""));
function runOf(ks, i) {                      // the block keypoint i belongs to: [first, last] index
  let lo = i, hi = i;
  while (lo > 0 && akey(ks[lo - 1].anchor) === akey(ks[i].anchor)) lo--;
  while (hi + 1 < ks.length && akey(ks[hi + 1].anchor) === akey(ks[i].anchor)) hi++;
  return [lo, hi];
}
function liveKeys() {                        // the keypoints with an in-flight drag on the lane applied
  const ks = condKeys(), d = tl.drag;
  if (!d || d.kind !== "marker" || !d.p || !d.lane || d.lane.key !== "cond") return ks;
  if (d.m.shape === "fade") return withFade(ks, d.m.i, fadeW(d.m, d.p[0]));
  return ks.map((k, j) => (j === d.m.i ? { ...k, t: d.p[0] } : k)).sort((a, b) => a.t - b.t);
}
function condMarkers() {
  const ks = condKeys(), col = keyColors(ks);
  const out = ks.map((k, i) => ({ id: "k" + i, i, t: k.t, color: col.get(akey(k.anchor)), cut: !!k.cut,
    tip: `${short(anchorLabel(k.anchor), 200)}\nkeypoint ${i + 1} · ${fmt(k.t)}: full strength here\ndrag: move · click: settings · right-click: edit / replace` }));
  for (const f of fades(ks)) out.push({ id: "f" + f.i, i: f.i, t: f.c, w0: f.w, shape: "fade", free: true, color: col.get(akey(ks[f.i + 1].anchor)),
    tip: `${f.cut ? "Hard cut" : "Fade over " + lenTxt(f.w)}: ${short(anchorLabel(ks[f.i].anchor), 50)} → ${short(anchorLabel(ks[f.i + 1].anchor), 50)}\ndrag → : longer, more gradual · drag ← : shorter, sharper (all the way: a hard cut)\nright-click: fade presets` });
  return out;
}
function condTipAt(t) {                      // the lane's hover tooltip: what conditions this moment
  const ks = condKeys();
  let parts;
  if (ks.length) parts = keyWeights(ks, t).map((x) => ({ s: anchorLabel(x.a), w: x.w }));
  else {
    const c = S.layout && S.layout.cond;
    if (!c || !c.anchors.length) return "";
    parts = c.anchors.map((a, i) => ({ s: a.label || anchorLabel(a), w: sample(c.curves[i], t) }));
  }
  parts = parts.filter((x) => x.w > 0.01).sort((a, b) => b.w - a.w);
  return `at ${fmt(t)}:\n` + parts.map((x) => (parts.length > 1 ? `${Math.round(x.w * 100)}%  ` : "") + short(x.s, 160)).join("\n")
    + "\n\nright-click: edit / replace / add a keypoint · drag: a new source over a span";
}
function drawKeys(g, r, t) {
  const ks = liveKeys(), col = keyColors(condKeys()), L = condLen();
  const x0 = Math.max(r.x, t.x(0)), x1 = Math.min(r.x + r.w, t.x(L)), top = r.y + 3, h = r.h - 6;
  for (let x = x0; x < x1; x += 2) {
    let y = top + h;
    for (const { a, w } of keyWeights(ks, t.t(x))) {
      if (w <= 0) continue;
      g.fillStyle = (col.get(akey(a)) || PCOL[0]) + (isMuted(a) && x % 8 < 2 ? "99" : isMuted(a) ? "2e" : "55");
      g.fillRect(x, y - w * h, 2, w * h);
      y -= w * h;
    }
  }
  const d = t.drag;                           // a fade handle in flight: where its keypoints land + its length
  if (d && d.kind === "marker" && d.p && d.lane && d.lane.key === "cond" && d.m.shape === "fade") {
    const w = fadeW(d.m, d.p[0]), cy = r.y + r.h / 2;
    g.strokeStyle = "#fff"; g.lineWidth = 1.2;
    for (const j of [d.m.i, d.m.i + 1]) {
      const x = t.x(ks[j].t);
      g.beginPath(); g.moveTo(x, cy - 7); g.lineTo(x + 6, cy); g.lineTo(x, cy + 7); g.lineTo(x - 6, cy); g.closePath(); g.stroke();
    }
    const s = w < 0.05 ? "hard cut" : "fade " + lenTxt(w);
    g.font = "600 10.5px Inter"; g.textBaseline = "top";
    const tw = g.measureText(s).width + 8, x = Math.min(r.x + r.w - tw - 2, t.x(d.p[0]) + 14);
    g.fillStyle = "rgba(9,9,13,0.85)"; g.fillRect(x, r.y + 2, tw, 15);
    g.fillStyle = "#fff"; g.fillText(s, x + 4, r.y + 4.5);
  }
}
function drawCond(g, r, t) {
  if (condKeys().length) return drawKeys(g, r, t);
  const c = S.layout && S.layout.cond;
  if (!c || !c.anchors.length) {
    g.fillStyle = "#6d6d7c"; g.font = "11.5px Inter"; g.fillText(S.error ? "build error: see the status chip" : "built conditioning shows here", r.x + 12, r.y + r.h / 2);
    return;
  }
  const x0 = Math.max(r.x, t.x(0)), x1 = Math.min(r.x + r.w, t.x(S.layout.length));
  const top = r.y + 3, h = r.h - 6;
  for (let x = x0; x < x1; x += 2) {
    const tt = t.t(x);
    let y = top + h;
    c.curves.forEach((cv, i) => {
      const w = Math.max(0, sample(cv, tt));
      if (w <= 0) return;
      const m = isMuted(c.anchors[i]);
      g.fillStyle = anchorCol(c.anchors[i], i) + (m && x % 8 < 2 ? "99" : m ? "2e" : "55");
      g.fillRect(x, y - w * h, 2, w * h);
      y -= w * h;
    });
  }
}
const shown = (k) => !!((prefs.show || {})[k]) || (k === "cond" && condMode() === "keys");
// what a dragged conditioning keypoint snaps to: every clip's start and end, the timeline's ends
function clipEdges() {
  const out = [0, projLength()];
  pieces().forEach((_, pi) => { const g = geom(pi); out.push(g.at, g.at + g.dur); });
  return out;
}
// how much the render rewrites at t: the drawn Rewrite curve where it is above 0 (live while you drag),
// else the build's automatic strength (gaps between clips, seams)
function strengthAt(t) {
  const u = rw.K.length ? C.at(rw.K, t) : 0;
  return u > 0 ? u : sample(S.auto, t);
}
// the automatic strength (clips 0 minus seams at uncovered edges, uncovered time 1, legacy soft / gen
// regions), worked out here from the clips with compose.strength_keys' rule on every paint, so the
// overlay, the Rewrite lane's auto band and the noise preview follow an edit at once (the build's
// `layout.keys` lags by a whole build). Rewrite curve not included: strengthAt / liveStrength add it.
function autoStrength() {
  const L = projLength(), P = beat(), p = S.proj;
  if (!L) return [];
  const act = activeIdx().map((pi) => ({ pi, g: geom(pi) }));
  const xf = new Set(joins().filter((x) => x.len > 0).flatMap((x) => [x.b + ":s", x.a + ":e"]));
  const spanned = (t, me) => act.some((o) => o !== me && o.g.at < t - 1e-3 && o.g.at + o.g.dur > t + 1e-3);
  const contd = (me, start) => xf.has(me.pi + (start ? ":s" : ":e")) || act.some((o) => o !== me && Math.abs((start ? o.g.at + o.g.dur : o.g.at) - (start ? me.g.at : me.g.at + me.g.dur)) < 2e-3
    && (start ? continues(o.g, me.g) : continues(me.g, o.g)));
  const kept = [];
  for (const me of act) {
    const sm = +(p.seam || 0), s = P && me.g.beat && (p.seam_unit || "beats") === "beats" ? sm * P : sm;
    const a = me.g.at, b = me.g.at + me.g.dur;
    const a2 = a + (a > 1e-6 && !spanned(a, me) && !contd(me, true) ? s : 0), b2 = b - (b < L - 1e-6 && !spanned(b, me) && !contd(me, false) ? s : 0);
    if (b2 > a2) kept.push([a2, b2]);
  }
  const regs = (p.regions || []).filter((r) => r.type === "soft" || r.type === "gen");
  const pts = [...new Set([0, L, ...kept.flat(), ...regs.flatMap((r) => [+r.t0, +r.t1])])].filter((x) => x >= 0 && x <= L).sort((x, y) => x - y);
  const keys = [];
  for (let i = 0; i + 1 < pts.length; i++) {
    const a = pts[i], b = pts[i + 1], m = (a + b) / 2;
    let u = kept.some(([x, y]) => x <= m && m < y) ? 0 : 1;
    for (const r of regs) if (+r.t0 <= m && m < +r.t1) u = r.type === "gen" ? 1 : +(r.u ?? 0.4);
    if (keys.length && keys[keys.length - 1][1] === u) { keys[keys.length - 1] = [b, u]; continue; }
    keys.push([a, u], [b, u]);
  }
  return keys;
}
function liveStrength() {                    // auto + the Rewrite curve, per latent frame (compose.strength_keys' merge)
  if (!rw.K.length) return S.auto;
  const L = projLength(), out = [];
  for (let f = 0; f < Math.ceil(L / C.SPL - 1e-6); f++) out.push([(f + 0.5) * C.SPL, +strengthAt((f + 0.5) * C.SPL).toFixed(4)]);
  return out;
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
  const k = S.auto, runs = [];
  if (k && k.length) {
    const x0 = Math.max(r.x, t.x(0)), x1 = Math.min(r.x + r.w, t.x(projLength()));
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
    g.fillText("drag: draw a span (starts at 1 = regenerate; drag its top down to re-render softly)  ·  double-click: add a point", r.x + 12, r.y + r.h / 2 - 12);
  }
  rw.paint(g, r, t, { alpha: 0.3 });
}
function lanes() {
  const out = [];
  if (shown("ghost") && (S.proj.ghost || []).length) out.push({ key: "ghost", label: "Before", h: 26, tip: "the clips the promoted take was rendered from (greyed: history only)",
    items: S.proj.ghost.map((p, i) => ({ id: "ghost" + i, t0: p.at, t1: p.at + p.dur, label: nameOf(p.id), color: "rgba(255,255,255,0.06)" })) });
  for (let k = 0; k < nTracks(); k++) {
    out.push({ key: "t" + k, label: `Track ${k + 1}`, ...(laneH("track") ? { h: laneH("track") } : { flex: 1.1 }), ...resizeLane("track", 36), tip: "a track: clips on different tracks play together (summed); SA3 sees the mix.\nM: mute the whole track (its clips leave the mix and what SA3 keeps, like muting each clip).\nRight-click the track → Add volume keypoint: a volume curve over it (drag its points and lines on the track). Right-click → Delete track: the track and its clips (asks first; ⌘Z undoes it).\nFader: the track's volume (−36…+6 dB), heard live and baked into the reference SA3 renders from. Double-click it: 0 dB.\nDrag a track's bottom edge here in the label column: every track's height (double-click it: back to filling the space).",
      gutter: (g, r) => trackGutter(g, r, k), onGutter: (e, r) => trackGutterDown(e, r, k),
      items: trackItems(k), over: (g, r, t) => { strengthOver(g, r, t); drawXf(g, r, t, k); envOver(g, r, t, k); liftGhost(g, r, t, k); }, onEdit: pieceEdit, marquee: true, xdrag: "tracks",
      pointer: (kind, e, p) => envPointer(k, kind, e, p), claim: (p) => envClaim(k, p),
      markers: xfMarkers(k), onMarker: (m, t) => { if (m.xf) setXf(m.xf.b, { len: xfClamp(2 * (t - m.xf.t), m.xf.max) }); },
      onMarkerClick: (m, e) => { if (m.xf) xfMenu(e, m.xf); },
      onClick: (t) => playAt(t),
      onItemClick: (it, t, e) => { if (!(e.metaKey || e.ctrlKey || e.shiftKey)) playAt(t); },
      draw: (g, r) => { if (!pieces().length && k === 0) { g.fillStyle = "#8b8b9c"; g.font = "13px Inter"; g.fillText("Drag a clip here from the library on the left.", r.x + 16, r.y + r.h / 2); } } });
  }
  out.push({ key: "tadd", label: "+ Track", h: 22, items: [], tip: "add a track (click the label). Drop a clip or a take here: it lands on a new track",
    onGutter: () => addTrack(),
    draw: (g, r) => laneText(g, r, LD && LD.drop && LD.drop.track >= nTracks() ? "drop here: a new track" : "drop a clip / take here: a new track", LD && LD.drop && LD.drop.track >= nTracks() ? "#8ff0d6" : "#6d6d7c"),
    over: (g, r, t) => liftGhost(g, r, t, nTracks()) });
  out.push({ key: "rewrite", label: "Rewrite", sub: "strength\n0 keep · 1 new", h: laneH("rewrite", 74), ...resizeLane("rewrite", 40), items: [], draw: drawAuto, pointer: rwPointer,
    tip: "Rewrite: how strongly SA3 re-renders each moment (0 keeps, 1 regenerates, between = same arrangement, new sound). Drag on empty space: a span. Drag a point or a line to reshape. Double-click: add / delete a point. Gaps between clips and seams are generated automatically (the hatched band along the bottom: it follows the clips, you can't drag it)." });
  if (shown("cond")) out.push({ key: "cond", label: "Conditioning", sub: "right-click: edit\ndrag: insert", h: laneH("cond", 58), ...resizeLane("cond", 30),
    tip: "What SA3 is told the music is, at each moment (one colour per source; hover 0.7 s for its name).\nRight-click: edit a prompt, replace a source, add a keypoint, fade presets.\nDrag across it: a new prompt or render's conditioning over that span (e.g. a climax).\nKeypoints mode: drag a ◆ to move it; drag a fade's ⟷ handle to make the change more gradual (→) or sharper (←). Past the last keypoint its source holds.",
    items: [], draw: drawCond, markers: condMarkers(), tipAt: condTipAt,
    onClick: (t) => seek(t),
    onDraw: (a, b, e) => condSpanMenu(e, a, b),
    snapTo: clipEdges,
    onMarker: (m, t) => edit((p) => {
      if (m.shape === "fade") { p.cond.keys = withFade(p.cond.keys, m.i, fadeW(m, t)); S.sel = "k" + (m.i + 1); tl.sel = S.sel; return; }
      const k = p.cond.keys[m.i];
      k.t = +t.toFixed(3); p.cond.keys.sort((a, b) => a.t - b.t);
      if (S.sel === m.id) { S.sel = "k" + p.cond.keys.indexOf(k); tl.sel = S.sel; }
    }),
    onMarkerClick: (m) => { S.sel = m.shape === "fade" ? "k" + (m.i + 1) : m.id; tl.sel = S.sel; inspector(); } });
  if (shown("ref")) out.push({ key: "ref", label: "Reference", sub: "violet = generated", flex: 0.8, items: [], tip: "the built reference: the summed mix SA3 keeps where the strength is 0 (hatched = rewritten)",
    draw: (g, r, t) => {
      if (!S.layout) return;
      const s = spec(refUrl());
      if (s) drawCrop(g, s, S.layout.length, 0, S.layout.length, t.x(0), r.y + 2, t.x(S.layout.length) - t.x(0), r.h - 4);
      for (const [a, b, u] of genSpans()) hatch(g, t.x(a), r.y + 2, t.x(b) - t.x(a), r.h - 4, u >= 1 ? "rgba(155,140,255,0.35)" : "rgba(62,230,196,0.3)", "rgba(0,0,0,0)");
    } });
  return out.concat(takeLanes());
}

// ── track gutter: M (mute) + a volume fader under the label ──────────────────────────────────
const FADER = [-36, 6];
function gutGeo(y, h, w) {
  const fy = y + (h >= 58 ? 34 : Math.max(26, h - 12));
  return { m: { x: w - 26, y: y + 5, w: 20, h: 16 }, x0: 10, x1: w - 10, fy, text: h >= 58 };
}
function trackGutter(g, r, k) {
  const G = gutGeo(r.y, r.h, r.w), m = G.m, mu = trackMuted(k), db = trackDb(k);
  g.fillStyle = mu ? "#ffb86b" : "rgba(255,255,255,0.08)"; g.fillRect(m.x, m.y, m.w, m.h);
  g.fillStyle = mu ? "#0b0b10" : "#b9b9c8"; g.font = "700 10.5px Inter, sans-serif"; g.textBaseline = "middle"; g.textAlign = "center";
  g.fillText("M", m.x + m.w / 2, m.y + m.h / 2 + 0.5); g.textAlign = "left";
  const u = (db - FADER[0]) / (FADER[1] - FADER[0]), x = G.x0 + u * (G.x1 - G.x0), z = G.x0 + (-FADER[0] / (FADER[1] - FADER[0])) * (G.x1 - G.x0);
  g.fillStyle = "rgba(255,255,255,0.10)"; g.fillRect(G.x0, G.fy - 1.5, G.x1 - G.x0, 3);
  g.fillStyle = mu ? "#6d6d7c" : "#5ed1b5"; g.fillRect(G.x0, G.fy - 1.5, x - G.x0, 3);
  g.fillStyle = "rgba(255,255,255,0.35)"; g.fillRect(z - 0.5, G.fy - 4, 1, 8);            // 0 dB
  g.fillStyle = mu ? "#8b8b9c" : "#e8e8f0"; g.beginPath(); g.arc(x, G.fy, 5, 0, 7); g.fill();
  if (G.text) {
    g.fillStyle = mu ? "#ffb86b" : db ? "#b9b9c8" : "#6d6d7c"; g.font = "10px Inter, sans-serif"; g.textBaseline = "top";
    g.fillText(mu ? "muted" : `${db > 0 ? "+" : ""}${db.toFixed(1)} dB`, G.x0, G.fy + 7);
  }
}
let lastFader = { k: -1, t: 0 };
function trackGutterDown(e, r, k) {
  const [px, py] = tl.pos(e), G = gutGeo(r.y, r.h, tl.o.gutter - 6), m = G.m;
  if (px >= m.x - 2 && px <= m.x + m.w + 2 && py >= m.y - 2 && py <= m.y + m.h + 2) {
    edit((p) => setTrackMix(p, k, (x) => { x.mute = !x.mute; }));
    return toast(trackMuted(k) ? `Track ${k + 1} muted: its clips are out of the mix and of what SA3 keeps.` : `Track ${k + 1} unmuted.`);
  }
  if (Math.abs(py - G.fy) > 9 || px < G.x0 - 8 || px > G.x1 + 8) return;
  e.preventDefault();
  const now = performance.now();
  if (lastFader.k === k && now - lastFader.t < 350) { lastFader = { k: -1, t: 0 }; return edit((p) => setTrackMix(p, k, (x) => { delete x.gain; })); }
  lastFader = { k, t: now };
  const set = (ev, fin) => {
    const u = Math.max(0, Math.min(1, (tl.pos(ev)[0] - G.x0) / (G.x1 - G.x0)));
    let db = FADER[0] + u * (FADER[1] - FADER[0]);
    db = !ev.shiftKey && Math.abs(db) < 0.75 ? 0 : Math.round(db * 2) / 2;     // a detent at 0 dB (⇧: none)
    if (fin) { if (preDrag != null) edit((p) => setTrackMix(p, k, (x) => { x.gain = db; }), { before: dragEnd() }); return; }
    if (db === trackDb(k)) return;
    dragStart(); setTrackMix(S.proj, k, (x) => { x.gain = db; }); syncMixer(); tl.redraw();
  };
  set(e, false);
  const mv = (ev) => set(ev, false), up = (ev) => { removeEventListener("pointermove", mv); removeEventListener("pointerup", up); set(ev, true); };
  addEventListener("pointermove", mv); addEventListener("pointerup", up);
}

// ── take lanes: every render lands under the tracks, on the same time axis (newest first) ──────
// The gutter's ▶ / ■ plays that take from the playhead; a click on the take selects it (score, star,
// Promote in the inspector) and moves the playhead there, and while anything plays it switches to that
// take at that moment, so takes A/B at the same bar. ↑ / ↓ step through takes the same way.
const PEND_ROWS = 3;                          // queued takes shown as lanes; the last one counts the rest
// Every take is a lane (newest first), with no limit: take lanes carry `scroll: true`, so they share the
// timeline's bottom region, which scrolls up / down once they outgrow it (tl.revealLane on ↑ ↓).
const takeNo = (tk) => S.takes.length - S.takes.indexOf(tk);
const playingTake = () => (player.isPlaying("take") ? S.playingTake : null);
const laneText = (g, r, s, color = "#8b8b9c") => { g.fillStyle = color; g.font = "12px Inter"; g.textBaseline = "middle"; g.fillText(s, r.x + 14, r.y + r.h / 2); };
function takeLanes() {
  const out = [], slots = [];                 // one lane per take still owed, across every batch (oldest first)
  for (const j of S.jobs) if (j.running) for (let k = j.done; k < j.total; k++) slots.push({ j, k });
  const pend = slots.length, shown = slots.slice(0, PEND_ROWS);
  shown.forEach(({ j, k }, i) => {
    const more = i === shown.length - 1 && pend > shown.length ? pend - shown.length : 0;
    out.push({ key: `pend:${j.id}:${k}`, label: j.local ? "Queuing" : "Rendering", sub: `${k + 1} of ${j.total}`, h: laneH("take", 40), ...resizeLane("take", 26), items: [], scroll: true,
      tip: "a take queued in the studio's render queue (reorder / delete it there: the queue chip, top right): it lands here as a take when done",
      draw: (g, r, t) => {
        const [a, b] = j.section || [0, projLength()];
        hatch(g, t.x(a), r.y + 2, t.x(b) - t.x(a), r.h - 4, "rgba(155,140,255,0.22)", "rgba(155,140,255,0.04)");
        const what = j.local ? "building the reference…" : j.section ? `rendering ${fmt(a)} – ${fmt(b)} in the studio queue…` : "rendering in the studio queue…";
        laneText(g, { ...r, x: Math.max(r.x, t.x(a)) }, what + (more ? `  (+ ${more} more queued)` : ""));
      } });
  });
  if (!S.takes.length) {
    if (!pend && pieces().length) out.push({ key: "notakes", label: "Takes", h: 30, items: [], scroll: true, tip: "rendered takes land here, lined up with the tracks",
      draw: (g, r) => laneText(g, r, "No takes yet: press Render (top right). Each take lands here, lined up with the tracks.") });
    return out;
  }
  for (const tk of S.takes) out.push(takeLane(tk));
  return out;
}
const takeOff = (tk) => (tk && tk.section ? tk.section[0] : 0);   // a section take sits at its section's start
function setSection(r) {                      // the ruler's blue section: Render renders only that (null = the whole piece)
  const L = projLength();
  if (r && r[1] > L + 1e-3) {                   // past the last clip: an extension. SA3 has no preferred length
    const bar = beat() ? 4 * beat() : 1;        // (any whole latent frame), so the end rounds up to a whole bar
    r = [r[0], Math.min(r[0] + MAX_LEN, Math.ceil((r[1] - 1e-6) / bar) * bar)];   // on the grid (else a whole second)
  }
  S.range = r && r[1] - r[0] >= 0.25 ? r : null;
  tl.setRange(S.range);
  paint();
}
function takeLane(tk) {
  const isBase = baseTake() === tk.id;
  const sub = [isBase ? "base" : tk.section ? "section" : "", tk.score != null ? `${tk.score}/10` : "", tk.starred ? "★" : ""].filter(Boolean).join(" · ");
  return { key: "take:" + tk.id, takeId: tk.id, label: `Take ${takeNo(tk)}`, sub, h: laneH("take", 46), ...resizeLane("take", 26), items: [], scroll: true,
    tip: `seed ${tk.seed} · ${condName(tk.variant)}. ▶ / ■: play this take from the playhead. Click the take: select it (score, star, Promote on the right) and move the playhead; while playing, it switches to this take at that moment. Drag across it: select a stretch, then drag that onto a track (or ↵) to use it as a clip. Drag it up onto a track: the whole take, snapped back to where it was rendered (⇧: place it freely). Dropped on the clips it came from, it replaces that stretch on every track; on another track (or "+ Track": a new one) it is layered and the originals stay. ↑ ↓: the next take, same moment.`,
    draw: (g, r, t) => {
      const s = spec(api.specUrl(tk.id)), len = tk.length || projLength(), off = takeOff(tk), a = t.x(off), w = t.x(off + len) - a;
      if (s) drawCrop(g, s, len, 0, len, a, r.y + 2, w, r.h - 4);
      else { g.fillStyle = "rgba(255,255,255,0.04)"; g.fillRect(a, r.y + 2, w, r.h - 4); }
      if (S.take === tk.id) { g.strokeStyle = "#c4bbff"; g.lineWidth = 2; g.strokeRect(a + 1, r.y + 1, w - 2, r.h - 2); }
      drawLift(g, r, t, tk);
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
    pointer: takePointer(tk) };
}
// ── lift: cut a stretch of a take onto the tracks ────────────────────────────────────────────
// Drag across a take lane = select a stretch (S.lift {take, a, b}, timeline seconds, beat-snapped).
// Drag the selection onto a track = a clip there (sideways moves it; it lands snapped); ↵ or the
// inspector's Lift = the same time on the first track free there (a take sits on the timeline's
// grid, so the same time keeps it in place; `align: false`, it is never re-snapped). Every clip
// cut from a take reads the take's frozen copy (keepId → server.freeze: clips/<take>), so
// trashing or deleting the take later never touches the timeline. A click on a take lane still
// selects + plays it.
let LD = null;                                // the gesture: {tk, kind: "sel" | "carry", t0, x0, y0, moved, drop}
const pxT = (n) => (n * (tl.v1 - tl.v0)) / Math.max(1, tl.W - tl.o.gutter - 8);   // n pixels, in seconds
const inLift = (tk, t) => !!(S.lift && S.lift.take === tk.id && t >= S.lift.a - pxT(6) && t <= S.lift.b + pxT(6));   // a tiny selection stays grabbable
// Where a lifted stretch [a, b] of take tk belongs now. The take was rendered from the clips in its
// lineage (tk.pieces: where they sat then, server.takes); the one that covered most of the stretch is
// looked up among today's clips (same id, its source still covering that moment) and the stretch moves
// with it, so it lines up with that clip wherever it went since: another time, another track. No match
// (a gap SA3 generated, a clip deleted since): the time it was rendered at. `tracks`: the tracks that
// hold its sources over the aligned stretch now (a drop there replaces them; anywhere else layers).
function liftHome(tk, a, b) {
  const lin = (tk && tk.pieces) || [], P = pieces(), G = P.map((_, pi) => geom(pi));
  let best = null;
  for (const q of lin) {
    const ov = Math.min(b, q.at + q.dur) - Math.max(a, q.at);
    if (ov <= 1e-3 || !q.src) continue;
    const T = Math.max(a, q.at) + ov / 2, s = q.src[0] + (T - q.at) * (q.stretch || 1);   // the overlap's middle, in that clip's source
    for (let pi = 0; pi < P.length; pi++) {
      if (P[pi].id !== q.id) continue;
      const g = G[pi], cut = g.src_cut || g.src;
      if (s < cut[0] - 1e-3 || s > cut[1] + 1e-3) continue;
      const d = g.at + (s - cut[0]) / (g.stretch || 1) - T;
      if (!best || ov > best.ov + 1e-3 || (Math.abs(ov - best.ov) <= 1e-3 && Math.abs(d) < Math.abs(best.d))) best = { ov, d, pi };
    }
  }
  const dt = best ? best.d : 0, at = Math.max(0, a + dt), e = at + (b - a), tracks = new Set();
  if (lin.length) {
    const ids = new Set(lin.map((q) => q.id));
    P.forEach((it, pi) => { const g = G[pi]; if (ids.has(it.id) && !it.mute && g.at < e - 1e-3 && g.at + g.dur > at + 1e-3) tracks.add(it.track || 0); });
  } else for (let k = 0; k < nTracks(); k++) if (trackBusy(k, at, e)) tracks.add(k);   // no lineage: any clip there counts
  return { at: +at.toFixed(4), tracks, moved: Math.abs(dt) > 1e-3, pi: best ? best.pi : -1 };
}
const isTakeId = (id) => /^composer\/[^/]+\/takes\/[^/]+$/.test(String(id || ""));
function takePointer(tk) {
  return (kind, e, { t, px }) => {
    const off = takeOff(tk), len = tk.length || projLength();
    const clampT = (x) => Math.max(off, Math.min(off + len, x));
    if (kind === "hover") {
      const c = px < tl.o.gutter ? "pointer" : inLift(tk, t) ? "grab" : "text";
      tl.cursor = c; tl.cv.style.cursor = c;
      const tip = px < tl.o.gutter ? "▶ / ■: play this take from the playhead"
        : inLift(tk, t) ? "drag onto a track: place this stretch there · ↵: lift it to the same time · click: play from here"
        : "click: select + play this take from here · drag sideways: select a stretch · drag up onto a track: the whole take (snaps back in place)";
      if (tl.cv.title !== tip) tl.cv.title = tip;
      return;
    }
    if (kind === "down") { if (e.button) return; LD = { tk, kind: inLift(tk, t) ? "carry" : "sel", t0: t, x0: e.clientX, y0: e.clientY, moved: false, drop: null }; return; }
    if (!LD) return;
    if (kind === "move") {
      if (!LD.moved && Math.abs(e.clientX - LD.x0) < 4 && Math.abs(e.clientY - LD.y0) < 4) return;
      if (!LD.moved && LD.kind === "sel" && Math.abs(e.clientY - LD.y0) > Math.abs(e.clientX - LD.x0)) {
        S.lift = { take: tk.id, a: off, b: off + len, whole: true };   // a vertical drag carries the whole take
        LD.kind = "carry"; LD.whole = true;
      }
      LD.moved = true;
      if (LD.kind === "sel") {
        const a = clampT(tl.snap(LD.t0, e)), b = clampT(tl.snap(t, e));
        S.lift = { take: tk.id, a: Math.min(a, b), b: Math.max(a, b) };
      } else {
        const hit = tl.at(e), k = dropTrack(e);
        if (!LD.home) LD.home = liftHome(tk, S.lift.a, S.lift.b);   // once per gesture: where its source clips sit now
        let at = Math.max(0, tl.snap(S.lift.a + (hit.t - LD.t0), e));
        const H = LD.home, R = beat() ? 8 * beat() : 2;   // 2 bars of either the render time or its source's time now
        const near = !e.shiftKey && (Math.abs(at - H.at) <= R || Math.abs(at - S.lift.a) <= R);
        if (near) at = H.at;
        // replaces what was there only when dropped on the clips it was rendered from; on any other
        // track (an empty one, a new one, one with unrelated clips) it just lands in time, layered
        const inPlace = near && k != null && H.tracks.has(k);
        LD.drop = k == null ? null : { track: k, at, inPlace, near, src: H.tracks };
        const c = k == null ? "no-drop" : "grabbing";
        tl.cursor = c; tl.cv.style.cursor = c;
      }
      tl.redraw();
      return;
    }
    if (kind === "up") {
      const d = LD;
      LD = null;
      if (!d.moved) { if (d.kind === "sel") S.lift = null; return pickTake(tk.id, t); }
      if (d.kind === "sel") {
        if (S.lift && S.lift.b - S.lift.a < 0.1) S.lift = null;
        useTake(tk.id); inspKey = ""; paint();
        return;
      }
      if (d.drop) liftTake(d.drop.track, d.drop.at, d.drop.inPlace);
      else { if (d.whole) S.lift = null; tl.redraw(); }
    }
  };
}
function drawLift(g, r, t, tk) {              // the selected stretch on its take lane
  if (!S.lift || S.lift.take !== tk.id) return;
  const a = t.x(S.lift.a), b = t.x(S.lift.b);
  g.fillStyle = "rgba(94,209,181,0.22)"; g.fillRect(a, r.y + 1, b - a, r.h - 2);
  g.strokeStyle = "#5ed1b5"; g.lineWidth = 1.5; g.strokeRect(a + 0.5, r.y + 1.5, b - a - 1, r.h - 3);
  const s = `${fmt(S.lift.b - S.lift.a)} · drag onto a track · ↵ lift`;
  g.font = "10.5px Inter"; g.textBaseline = "top";
  const w = g.measureText(s).width + 8;
  if (b - a > w + 4) { g.fillStyle = "rgba(9,9,13,0.8)"; g.fillRect(a + 3, r.y + 4, w, 15); g.fillStyle = "#8ff0d6"; g.fillText(s, a + 7, r.y + 6); }
}
function liftGhost(g, r, t, k) {               // where a carried stretch lands, while dragging it
  if (!LD || LD.kind !== "carry" || !LD.drop || !S.lift) return;
  const D = LD.drop, a = t.x(D.at), b = t.x(D.at + S.lift.b - S.lift.a);
  if (D.track !== k) {                          // in place: what it replaces on the other tracks goes quiet
    if (D.inPlace) { g.fillStyle = "rgba(9,9,13,0.6)"; g.fillRect(a, r.y + 2, b - a, r.h - 4); }
    else if (D.near && D.src && D.src.has(k)) {   // layered: outline the source clips it lines up with
      g.setLineDash([3, 4]); g.strokeStyle = "rgba(94,209,181,0.55)"; g.lineWidth = 1; g.strokeRect(a + 0.5, r.y + 2.5, b - a - 1, r.h - 5); g.setLineDash([]);
    }
    return;
  }
  const col = D.inPlace ? "#ffd479" : "#5ed1b5";
  g.fillStyle = D.inPlace ? "rgba(255,212,121,0.16)" : "rgba(94,209,181,0.18)"; g.fillRect(a, r.y + 2, b - a, r.h - 4);
  g.setLineDash([5, 4]); g.strokeStyle = col; g.lineWidth = 1.5; g.strokeRect(a + 0.5, r.y + 2.5, b - a - 1, r.h - 5); g.setLineDash([]);
  const s = D.inPlace ? "in place · replaces this stretch on every track" : D.track >= nTracks() ? "on a new track · the originals stay"
    : D.near ? "in time · layered, the originals stay" : "layered here · ⇧ free";
  g.font = "10.5px Inter"; g.textBaseline = "top";
  const w = g.measureText(s).width + 8;
  if (b - a > w + 4) { g.fillStyle = "rgba(9,9,13,0.8)"; g.fillRect(a + 3, r.y + 5, w, 15); g.fillStyle = col; g.fillText(s, a + 7, r.y + 7); }
}
// cut [a, b] out of every clip (a take dropped in place is the whole mix there): clips inside go,
// clips over an edge are trimmed to it, a clip across the whole span splits around it (the base too).
// G = every clip's geom, taken before the edit. Short fades at the new edges (the take is not
// sample-identical to what it replaces, so a hard edge could click).
function cutSpan(p, a, b, G) {
  const E = 1e-3, F = 0.01;
  for (let pi = p.pieces.length - 1; pi >= 0; pi--) {
    const it = p.pieces[pi], g = G[pi], e = g.at + g.dur;
    if (e <= a + E || g.at >= b - E) continue;
    const k = g.stretch || 1, cut = g.src_cut || g.src || it.src, srcAt = (T) => +(cut[0] + (T - g.at) * k).toFixed(3);
    const left = g.at < a - E, right = e > b + E, fa = it.fade || [F, F];
    if (!left && !right) { p.pieces.splice(pi, 1); continue; }
    const r = right ? { ...JSON.parse(JSON.stringify(it)), src: [srcAt(b), it.src[1]], at: +b.toFixed(4), fade: [F, fa[1]] } : null;
    if (left) { it.src = [it.src[0], srcAt(a)]; it.fade = [fa[0], F]; if (r) p.pieces.splice(pi + 1, 0, r); }
    else p.pieces.splice(pi, 1, r);
  }
}
const trackBusy = (k, a, b) => pieces().some((p, pi) => (p.track || 0) === k && !p.mute && (() => { const g = geom(pi); return g.at < b - 1e-3 && g.at + g.dur > a + 1e-3; })());
function freeTrack(a, b) {                    // the first track with no unmuted clip over [a, b], else a new one
  for (let k = 0; k < nTracks(); k++) if (!trackBusy(k, a, b)) return k;
  return nTracks();
}
// the track a drop at this page event lands on: a track lane, the "+ Track" lane (a new track, index
// nTracks()), or, in the gaps between them, the nearest one; null anywhere else (the drop is refused)
function dropTrack(e) {
  const [, py] = tl.pos(e), rs = tl.laneRects().filter((r) => !r.clip && (/^t\d+$/.test(r.lane.key) || r.lane.key === "tadd"));
  if (!rs.length || py < rs[0].y - 4 || py > rs[rs.length - 1].y + rs[rs.length - 1].h + 4) return null;
  const r = rs.reduce((b, q) => (Math.abs(py - (q.y + q.h / 2)) < Math.abs(py - (b.y + b.h / 2)) ? q : b), rs[0]);
  return r.lane.key === "tadd" ? nTracks() : +r.lane.key.slice(1);
}
function addTrack() {
  edit((q) => { q.tracks = nTracks() + 1; });
  toast(`Track ${nTracks()} added: drag a clip or a take onto it.`, { ms: 2200 });
}
// delete track k (right-click a track): its clips go, the tracks below move up one, and its mix
// (fader, mute, volume curve: track_mix[k]) goes with it. Confirmed in a modal; one edit(), so ⌘Z undoes it.
function deleteTrack(k) {
  if (nTracks() <= 1) return toast("The last track can't be deleted (wipe the composition instead).");
  const pis = pieces().map((p, pi) => ((p.track || 0) === k ? pi : -1)).filter((pi) => pi >= 0);
  if (pis.some(isBasePi)) return toast(`Track ${k + 1} holds the base take (the timeline itself): promote another take first.`);
  const n = pis.length;
  const body = el(`<div class="note">Removes <b>Track ${k + 1}</b>${n ? ` and its <b>${n} clip${n === 1 ? "" : "s"}</b>` : " (it has no clips)"}, with its fader, mute and volume curve. The tracks below move up one. ⌘Z undoes it.</div>`);
  modal({ title: `Delete Track ${k + 1}?`, body, actions: [{ label: "Cancel", ghost: true }, { label: "Delete track", danger: true, fn: () => {
    const was = nTracks();
    edit((p) => {
      for (const pi of pis.slice().reverse()) p.pieces.splice(pi, 1);
      for (const pc of p.pieces) if ((pc.track || 0) > k) pc.track = pc.track - 1;
      if (p.track_mix) { p.track_mix = p.track_mix.slice(); p.track_mix.splice(k, 1); }
      p.tracks = was - 1;
    });
    envLanes.forEach((cl) => { if (cl) cl.sel = null; });
    S.sel = null; S.multi = new Set(); tl.multi = S.multi; tl.select(null);
    inspector(); paint();
    toast(`Track ${k + 1} deleted${n ? ` with its ${n} clip${n === 1 ? "" : "s"}` : ""} (⌘Z undoes it).`);
  } }] });
}
const takeLabel = (tk) => `Take ${takeNo(tk)} · seed ${tk.seed}`;
async function keepId(id, label = null) {     // a take → its frozen copy in this project's clips/ (anything else: itself)
  if (!isTakeId(id)) return id;
  const r = await api.post("/sa3/composer/freeze", { name: S.name, take: id, label });
  if (r.row && !S.clips.some((c) => c.id === r.row.id)) S.clips.push(r.row);
  return r.id;
}
async function addPieceKept(row, range, opts) {  // addPiece, a take kept first (a kept take dragged from the library, a link)
  try {
    const tk = S.takes.find((x) => x.id === row.id);
    const id = await keepId(row.id, tk ? takeLabel(tk) : null);
    addPiece(id === row.id ? row : { id }, range, id === row.id ? opts : { ...opts, extra: { align: false } });   // a take is on its own grid already
  } catch (e) { toast("Couldn't add it: " + e.message, { error: true }); }
}
async function liftTake(track = null, at = null, inPlace = false) {
  const L = S.lift, tk = L && S.takes.find((x) => x.id === L.take);
  if (!tk) return toast("Drag across a take lane first to select a stretch of it.");
  const off = takeOff(tk), s0 = L.a - off, s1 = L.b - off;
  const H = at == null ? liftHome(tk, L.a, L.b) : null;   // ↵ / Lift: lined up with its source clips, on a free track
  at = H ? H.at : at;
  if (track == null) track = freeTrack(at, at + s1 - s0);
  try {
    const id = await keepId(tk.id, takeLabel(tk));
    S.lift = null;
    const G = pieces().map((_, pi) => geom(pi));
    edit((p) => {
      if (inPlace) cutSpan(p, at, at + s1 - s0, G);
      p.pieces.push({ id, src: [+s0.toFixed(3), +s1.toFixed(3)], at: +at.toFixed(4), track, align: false });
      p.tracks = Math.max(p.tracks || 1, track + 1);
      S.sel = "p" + (p.pieces.length - 1);
    });
    loadImports();
    toast(inPlace ? `${takeLabel(tk)} now plays ${fmt(at)} – ${fmt(at + s1 - s0)} on Track ${track + 1}, in place of what was there on every track (⌘Z undoes it).`
      : `${fmt(s1 - s0)} of ${takeLabel(tk)} is on Track ${track + 1}${H && H.moved ? ", lined up with its source clip (moved since the render)" : ""}. It stays even if you trash the take.`);
  } catch (e) { toast("Lift: " + e.message, { error: true }); }
}

// ── split: one clip → two at a moment (right-click on a clip) ──────────────────────────────────
// Both halves keep every setting (mix, fix, anchor, speed); no fade at the cut, and compose's
// strength_keys sees the same source carrying on there, so the split adds no seam. Snapped to beats
// on a grid (⇧ held: exactly there).
function splitAt(pi, T, e) {
  const g = geom(pi), it = pieces()[pi];
  if (!it || isBasePi(pi)) return toast("The base is the timeline itself: it can't be split (layer clips over it, or Rewrite parts of it).");
  T = e && e.shiftKey ? T : tl.snap(T, e);
  if (T <= g.at + 0.05 || T >= g.at + g.dur - 0.05) return toast("Too close to the clip's edge to split there.");
  const k = g.stretch || 1, cut = g.src_cut || g.src || it.src, s = +(cut[0] + (T - g.at) * k).toFixed(3);
  edit((p) => {
    const a = p.pieces[pi], b = JSON.parse(JSON.stringify(a));
    const fa = a.fade || [0.01, 0.01];
    a.src = [a.src[0], s]; a.fade = [fa[0], 0];
    b.src = [s, b.src[1]]; b.fade = [0, fa[1]]; b.at = +T.toFixed(4);
    p.pieces.splice(pi + 1, 0, b);
    S.sel = "p" + (pi + 1);
  });
  S.multi = new Set([S.sel]);
}
function pieceAt(hit) {                       // the clip under a page event on a track lane (topmost), or -1
  if (!hit.lane || !/^t\d+$/.test(hit.lane.key)) return -1;
  const k = +hit.lane.key.slice(1);
  for (let pi = pieces().length - 1; pi >= 0; pi--) {
    if ((pieces()[pi].track || 0) !== k) continue;
    const g = geom(pi);
    if (hit.t >= g.at && hit.t <= g.at + g.dur) return pi;
  }
  return -1;
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
  tl.revealLane("take:" + nt.id);
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
// `before`: the undo snapshot, when a live drag already mutated S.proj (mix / master sliders)
function edit(fn, { save = true, before = null } = {}) {
  S.undo.push(before || JSON.stringify(S.proj));
  if (!before) preDrag = null;                 // a drag that never got its release (inspector redrawn) ends here
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
function pieceEdit(it, a, b, kind, toLane) {   // toLane: dragged onto another track lane
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
    } else {
      pc.at = +Math.max(0, a).toFixed(4);
      if (toLane && /^t\d+$/.test(toLane.key)) pc.track = +toLane.key.slice(1);
    }
  });
}
function curAnchorAt(t) {
  const c = S.layout && S.layout.cond;
  if (!c || !c.anchors.length) return pieces().length ? { id: pieces()[0].id } : { prompt: "" };
  let bi = 0, bw = -1;
  c.curves.forEach((cv, i) => { const w = sample(cv, t); if (w > bw) { bw = w; bi = i; } });
  return strip(c.anchors[bi]);
}
function toKeys() {                         // the auto curves → explicit keypoints (start / end of each plateau)
  const out = autoKeys();
  if (!out) return toast("Build first: the keypoints start from the built conditioning.");
  edit((p) => { p.cond = { ...(p.cond || {}), gap: "keys", keys: out.length ? out : [{ t: 0, anchor: curAnchorAt(0) }] }; });
  toast(`${out.length} keypoints from the automatic conditioning: drag a ◆ to move, a ⟷ to change a fade, right-click the lane to edit or add.`);
}
function autoKeys() {                       // null = nothing built yet
  const c = S.layout && S.layout.cond;
  if (!c || !c.anchors.length) return null;
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
  return ks.filter((k, i) => !(i && Math.abs(k.t - ks[i - 1].t) < 0.06 && JSON.stringify(k.anchor) === JSON.stringify(ks[i - 1].anchor)));
}
// the anchor that dominates keypoints `ks` at time t (compose.key_curves' rule: cosine between two
// keypoints, a `cut` holds the earlier one until it)
function keyAnchorAt(ks, t) {
  if (!ks.length) return null;
  let i = -1;
  ks.forEach((k, j) => { if (k.t <= t) i = j; });
  if (i < 0) return ks[0].anchor;
  const n = ks[i + 1];
  if (!n) return ks[i].anchor;
  return !n.cut && (t - ks[i].t) / Math.max(n.t - ks[i].t, 1e-6) > 0.5 ? n.anchor : ks[i].anchor;
}
// Insert `anchor` over [a, b]: it holds at full weight across the span; what played before / after
// comes back a fade (one bar, at most a quarter of the span either side) outside it. Keypoints that
// sat inside the span or its fades go. Not in Keypoints mode yet → the current conditioning becomes
// keypoints first (autoKeys), so nothing outside the span changes.
function insertCond(a, b, anchor) {
  const L = (S.layout && S.layout.length) || projLength();
  let ks = condMode() === "keys" ? ((S.proj.cond || {}).keys || []).slice() : autoKeys();
  if (!ks) return toast("Build first: inserting starts from the built conditioning.", { error: true });
  if (!ks.length) ks = [{ t: 0, anchor: curAnchorAt(0) }];
  ks = ks.map((k) => ({ ...k })).sort((x, y) => x.t - y.t);
  const f = Math.min(beat() ? 4 * beat() : 2, (b - a) / 4 + 0.5);
  const pa = a - f, pb = b + f;
  const before = pa > 0.05 && keyAnchorAt(ks, pa), after = pb < L - 0.05 && keyAnchorAt(ks, pb);
  const out = ks.filter((k) => k.t < pa - 1e-3 || k.t > pb + 1e-3);
  if (before) out.push({ t: +pa.toFixed(3), anchor: before });
  out.push({ t: +a.toFixed(3), anchor }, { t: +b.toFixed(3), anchor });
  if (after) out.push({ t: +pb.toFixed(3), anchor: after });
  out.sort((x, y) => x.t - y.t);
  edit((p) => {
    p.cond = { ...(p.cond || {}), gap: "keys", keys: out };
    S.sel = "k" + out.findIndex((k) => Math.abs(k.t - a) < 1e-3);
  });
  inspector();
  toast(`${short(anchorLabel(anchor), 40)} now conditions ${fmt(a)} – ${fmt(b)}, fading in / out over ${f.toFixed(1)} s. Drag its ⟷ handles for a more gradual / sharper change.`);
}
// after a drag across the Conditioning lane: what to put over that span
function condSpanMenu(e, a, b) {
  const go = (pick) => async () => { const an = await pick({ title: `New conditioning for ${fmt(a)} – ${fmt(b)}` }); if (an) insertCond(a, b, an); };
  menu(e.clientX, e.clientY, [{ head: `Insert new conditioning · ${fmt(a)} – ${fmt(b)}` },
    { label: "A typed prompt / anarchy spec…", fn: go(promptAnchor) },
    { label: "A library render's conditioning…", fn: go(pickAnchor) },
    { label: "No conditioning (muted: the audio alone steers)", fn: () => insertCond(a, b, { mute: true }) }]);
}
// the stretch with no active clip around t, or null when a clip covers t
function gapAt(t) {
  const L = (S.layout && S.layout.length) || projLength();
  let a = 0, b = L;
  for (const pi of activeIdx()) {
    const g = geom(pi), e = g.at + g.dur;
    if (t >= g.at && t < e) return null;
    if (e <= t) a = Math.max(a, e); else b = Math.min(b, g.at);
  }
  return b - a > 0.25 ? [a, b] : null;
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
  else if (id[0] === "v") { const cl = envLanes[+id.slice(1)]; if (cl) cl.del(); S.sel = cl && cl.sel ? id : null; return inspector(); }
  else if (id.startsWith("T:")) {               // a selected take: the studio's trash (recoverable there)
    const tk = S.takes.find((x) => x.id === id.slice(2));
    S.sel = null;
    return tk ? removeTake(tk, "trash") : paint();
  }
  else if (id[0] === "k") edit((p) => {
    p.cond.keys.splice(+id.slice(1), 1);
    if (!p.cond.keys.length) p.cond.gap = "crossfade";   // no keypoints left: the build is back on Auto, so say so
  });
  S.sel = null;
  paint();
}
function addPiece(row, range, { at = null, track = 0, extra = {} } = {}) {
  edit((p) => {
    const ends = p.pieces.filter((x) => (x.track || 0) === track).map((x, i) => { const g = geom(p.pieces.indexOf(x)); return g.at + g.dur; });
    const t = at != null ? at : ends.length ? Math.max(...ends) + (beat() ? 2 * beat() : 4) : 0;
    p.pieces.push({ id: row.id, src: [+range[0].toFixed(3), +range[1].toFixed(3)], at: +t.toFixed(4), track, ...extra });
    S.sel = "p" + (p.pieces.length - 1);
  });
}
function nudge(dir, e) {
  const id = S.sel;
  if (id === "w") return rw.nudge(dir * (e && e.shiftKey ? 1 : C.SPL));
  if ((id || "")[0] === "v") return envLanes[+id.slice(1)] && envLanes[+id.slice(1)].nudge(dir * (e && e.shiftKey ? 1 : 0.01));
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
function setPiece(pi, fn, { save = true, before = null } = {}) { edit((p) => fn(p.pieces[pi]), { save, before }); }
// a live slider drag mutates S.proj on every input (heard at once) and commits on release: the undo
// snapshot is the state from before the drag's first input, else ⌘Z would restore the dragged value
let preDrag = null;
const dragStart = () => { if (preDrag == null) preDrag = JSON.stringify(S.proj); };
const dragEnd = () => { const b = preDrag; preDrag = null; return b; };
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
  const pv = S.src === "mixp" && S.proj ? { keys: liveStrength(), length: projLength() } : null;   // live: no wait for the build
  if (pv && !pv.keys.length) pv.keys = null;
  mixer.setPreview(pv);
  const k = JSON.stringify(pv);
  if (k !== pvKey) { const restart = pvKey && mixer.playing; pvKey = k; if (restart) mixer.restart().then(playBtn); }   // continues seamlessly from where it is
  if (!S.layout || !fresh(S.layout)) return;
  mixer.setPieces(S.layout.pieces.filter((pc) => pc.audio_key && pieces()[pc.pi]).map((pc) => ({ key: "p" + pc.pi, url: pieceUrl(pc.audio_key), at: geom(pc.pi).at - (pc.pre || 0), dur: pc.dur, mix: stripMix(pieces()[pc.pi]),
    ...(() => { const K = trackMix(pieces()[pc.pi].track || 0).env; const sm = envSmooth(pc.pi);
      return K && K.length ? { env: (t) => envGain(K, t, sm), envBreaks: K.map((x) => x[0]), envSig: JSON.stringify(K) + sm } : {}; })() })));
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
  gutter: 92, free: true, rangePast: MAX_LEN,   // a section may extend the piece (setSection snaps its end to a bar)                    // an open canvas: zoom out as far as you like; the view never re-fits by itself
  onSeek: (t) => seek(t),
  onRange: (r) => setSection(r),             // a drag on the ruler: render only that section
  onSelect: (it, lane, e) => {
    if (it && it.marker && it.marker.xf) { tl.sel = S.sel && S.sel !== "w" ? S.sel : null; return; }   // a crossfade handle: the selection stays
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
  onGroupMove: (ids, dt, dl) => edit((p) => {
    for (const id of ids) {
      const pi = +id.slice(1);
      if (!p.pieces[pi]) continue;
      p.pieces[pi].at = +Math.max(0, geom(pi).at + dt).toFixed(4);
      if (dl) p.pieces[pi].track = Math.max(0, (p.pieces[pi].track || 0) + dl);   // across tracks: the timeline kept the set inside them
    }
  }),
});
const rw = new CurveLane(tl, {                // the Rewrite lane's strength curve (project.json `rewrite`)
  closeEnds: true,                            // new spans start at strength 1 (the kit's brush); lower them after
  end: () => Math.max(projLength(), 1),
  magnets: () => [S.cursor, ...pieces().flatMap((p, i) => { const g = geom(i); return [g.at, g.at + g.dur]; })],
  onSeek: (t) => seek(t),
  onCommit: (K) => edit((p) => { if (K.length) p.rewrite = K; else delete p.rewrite; }),
});
// ── track volume automation: a curve over each track (track_mix[k].env, compose.env_gain: gain = sin(π/2·u),
// equal-power, so two tracks' mirrored curves (u and 1 − u: a crossfade drawn as an X) sum to a constant level),
// edited with the same CurveLane as the Rewrite lane. Entry: right-click a track → "Add volume
// keypoint" (at the curve's value there, 0 dB on a track without one). After that the curve's points
// and lines are live on the track itself (the lane's `claim`: only gestures on them go to the curve,
// everything else still moves clips): drag a point (time + level), drag a line up / down, double-click
// the line to add a point, double-click a point / ⌫ deletes. It multiplies on top of the clip
// crossfades, in the reference and live in the monitor. Each clip reads the curve linearly or smoothly
// (`env_shape`, right-click a clip → Volume curve: Linear / Smooth); its stretch of the line draws that way.
const envGain = (K, t, smooth = false) => (K && K.length ? Math.sin(C.at(K, t, smooth) * Math.PI / 2) : 1);   // compose.env_gain
const envSmooth = (pi) => !!pieces()[pi] && pieces()[pi].env_shape === "smooth";   // a clip's own interpolation of its track's curve
const envDb = (u) => (u <= 0.001 ? "−∞ dB" : `${u >= 0.9995 ? "0.0" : (20 * Math.log10(Math.sin(u * Math.PI / 2))).toFixed(1)} dB`);
const ENV_COL = "#ffd479";
const envLanes = [];
function envLane(k) {
  if (!envLanes[k]) envLanes[k] = new CurveLane(tl, {
    mode: "span", stepped: false, bodies: false, keepZero: true, prune: false,   // prune off: two points at one level start a ramp
    color: ENV_COL, what: "volume", fmtU: envDb, grid: 0.01,
    edges: false,                                          // a jump's two points stay visible + draggable apart
    detents: [0, 0.1616, 0.3342, 0.5, 0.7003, 1],         // −∞, −12, −6, −3, −1, 0 dB (envGain's law)
    end: () => Math.max(projLength(), 1),
    magnets: () => [S.cursor, ...pieces().flatMap((p, i) => { const g = geom(i); return (g.track === k) ? [g.at, g.at + g.dur] : []; }),
      ...joins().filter((x) => x.track === k && x.len > 0).flatMap((x) => [x.t - x.len / 2, x.t + x.len / 2])],
    onSeek: (t) => seek(t),
    onCommit: (K) => edit((p) => setTrackMix(p, k, (x) => { if (K.length) x.env = K; else delete x.env; })),
  });
  return envLanes[k];
}
function envClaim(k, p) {                    // the curve takes a gesture only on its points and lines
  const cl = envLane(k);
  if (cl.drag) return true;
  if (!cl.K.length) return false;
  const tg = cl.target(p), yes = tg.kind === "key" || tg.kind === "seg";
  if (!yes && cl.hot) { cl.hot = null; tl.redraw(); }
  return yes;
}
function envAdd(k, t, smooth = false) {      // right-click → Add volume keypoint (on the curve as the clip there reads it)
  const cl = envLane(k), u = cl.K.length ? C.at(cl.K, t, smooth) : 1;
  cl.addKey(Math.max(0, +t.toFixed(3)), u, cl.ser());
  S.sel = "v" + k; inspector();
  if (cl.K.length === 1) toast(`Track ${k + 1} has a volume curve now: drag the point down to turn it down, right-click again for more points (or double-click the line). ⇧ while dragging: no snapping.`);
}
function envMenu(k, hit) {                    // the track's volume items for the right-click menu
  const cl = envLane(k), r = tl.laneRects().find((q) => q.lane === hit.lane), j = r ? cl.hitKey(tl.lanePos(r, hit.px, hit.py)) : -1;
  const cpi = pieceAt(hit), on = cpi >= 0 && (pieces()[cpi].track || 0) === k ? cpi : -1;
  const pis = on < 0 ? [] : S.multi.has("p" + on) ? selPis() : [on], sm = on >= 0 && envSmooth(on);
  const shape = (v) => edit((p) => { for (const pi of pis) { if (v) p.pieces[pi].env_shape = "smooth"; else delete p.pieces[pi].env_shape; } });
  const many = pis.length > 1 ? ` (${pis.length} clips)` : "";
  return [
    { head: "Volume" },
    { label: "Add volume keypoint", fn: () => envAdd(k, hit.t, on >= 0 && envSmooth(on)) },
    ...(on >= 0 ? [{ label: "Curve over this clip: Linear" + many, hint: sm ? "○" : "●", fn: () => shape(false) },
      { label: "Curve over this clip: Smooth" + many, hint: sm ? "●" : "○", fn: () => shape(true) }] : []),
    j >= 0 ? { label: "Delete this keypoint", fn: () => { const b = cl.ser(); cl.K.splice(j, 1); cl.sel = null; cl.commit(b); } } : null,
    cl.K.length ? { label: `Clear track ${k + 1}'s volume curve`, danger: true, fn: () => { const b = cl.ser(); cl.K = []; cl.sel = null; cl.commit(b); } } : null,
    nTracks() > 1 ? "-" : null,
    nTracks() > 1 ? { label: `Delete Track ${k + 1}…`, danger: true, fn: () => deleteTrack(k) } : null,
  ];
}
function envPointer(k, kind, e, p) {
  const cl = envLane(k);
  if (kind === "down") { tl.sel = null; rw.sel = null; envLanes.forEach((x, j) => { if (x && j !== k) x.sel = null; }); }
  cl.pointer(kind, e, p);
  if (kind === "down" || kind === "up") { S.sel = cl.sel ? "v" + k : null; S.multi = new Set(); tl.multi = S.multi; inspector(); }
}
function envOver(g, r, t, k) {                // the line only over this track's clips (it has no audio elsewhere); every point
  const cl = envLane(k);
  if (!cl.K.length) return;
  pieces().forEach((pc, pi) => {                // each clip's stretch, in that clip's interpolation
    if ((pc.track || 0) !== k) return;
    const gm = geom(pi);
    g.save(); g.beginPath(); g.rect(t.x(gm.at), r.y, t.x(gm.at + gm.dur) - t.x(gm.at), r.h); g.clip();
    cl.paint(g, r, t, { alpha: 0.06, points: false, smooth: envSmooth(pi) });
    g.restore();
  });
  cl.paint(g, r, t, { line: false });
}

function rwPointer(kind, e, p) {
  if (kind === "down") tl.sel = null;
  rw.pointer(kind, e, p);
  if (kind === "down" || kind === "up") { S.sel = rw.sel ? "w" : null; inspector(); }
}

function paint() {
  const p = S.proj;
  if (!p) return;
  const L = projLength();
  S.auto = autoStrength();
  for (let k = 0; k < nTracks(); k++) envLane(k).setKeys(trackMix(k).env || []);
  if ((S.sel || "")[0] === "v" && !(envLanes[+S.sel.slice(1)] || {}).sel) S.sel = null;
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
  $("cfg").value = (p.model && p.model.cfg) ?? 1;
  const lo = (p.model && p.model.loras) || [], ls = loraStack();
  if (JSON.stringify(lo) !== JSON.stringify(ls.value)) ls.set(lo);
  else if (ls.n !== +$("steps").value) ls.paint();     // the step window's "of N" follows Steps
  ls.n = +$("steps").value;
  $("clock").textContent = `${fmt(S.cursor)} / ${fmt(L)}`;
  $("clock").title = L > MAX_LEN ? "past 380 s: renders as chained windows (each keeps the last 60 s as context; no global coherence)" : "";
  $("undo").disabled = !S.undo.length;
  $("redo").disabled = !S.redo.length;
  $("render").disabled = !L;                   // batches stack: Render again while one runs queues another
  $("render").textContent = S.range ? `Render this section (${nSeeds()} take${nSeeds() === 1 ? "" : "s"})` : `Render ${nSeeds()} take${nSeeds() === 1 ? "" : "s"}`;
  $("render").title = S.range ? `render only ${fmt(S.range[0])} – ${fmt(S.range[1])} (the blue section; × on the ruler or Esc clears it)${
      S.range[1] > L + 1e-3 ? `. It runs ${fmt(S.range[1] - L)} past the last clip: SA3 continues the music there (rounded up to a whole bar)` : ""}`
    : "render takes of the whole timeline in the studio's queue (each lands as a lane under the tracks). Drag on the ruler to render only a section";
  status();
  saveChip();
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
  const id = (S.sel || "")[0] === "f" ? "k" + (+S.sel.slice(1) + 1) : S.sel || "";
  const tkSel = id.startsWith("T:") ? S.takes.find((x) => "T:" + x.id === id) : null;
  const k = id + "|" + JSON.stringify(tkSel ? [tkSel, S.proj.base, S.lift] : id[0] === "p" ? pieces()[+id.slice(1)] : id === "w" ? [rw.sel, rw.K] : id[0] === "k" ? ((S.proj.cond || {}).keys || []).slice(Math.max(0, +id.slice(1) - 1), +id.slice(1) + 2) : [S.proj.master, S.proj.cond, S.proj.anchor])
    + "|" + (S.fit ? S.fit.issues.map((x) => x.msg).join(";") + ":" + S.fit.pieces.length : "") + "|" + (S.layout ? S.layout.ref_sha256 : "") + "|" + S.starred.length + "|" + pieces().length + "|" + condMode() + "|" + ((S.proj.model || {}).steps || 8);
  if (k === inspKey && host.childElementCount) return;
  inspKey = k;
  host.innerHTML = "";
  if (tkSel) return takeInspector(host, tkSel);
  if (id[0] === "p" && pieces()[+id.slice(1)]) return pieceInspector(host, +id.slice(1));
  if (id === "w" && rw.sel) return rewriteInspector(host);
  if (id[0] === "k" && ((S.proj.cond || {}).keys || [])[+id.slice(1)]) return keyInspector(host, +id.slice(1));
  projectInspector(host);
}

// a library row as an anchor: a text render → its prompt; anything else by id (the server makes a
// take its dominant anchor: condsrc.expand). null = it has no conditioning (an import).
function anchorOfRow(r) {
  if (!r) return null;
  if (r.cond === false && r.prompt && r.kind !== "composite") return { prompt: r.prompt };
  return r.cond === false && !r.prompt ? null : { id: r.id };
}
// The library (starred renders) in a modal: ▶ / click a card's spectrogram to listen, Use picks its
// conditioning. Resolves the anchor, or null when closed.
function pickAnchor({ title = "Conditioning from the library", current = null } = {}) {
  return new Promise((resolve) => {
    const body = el(`<div style="display:flex;flex-direction:column;gap:6px"><div class="note">${current ? `Now: <b>${esc(short(anchorLabel(current), 70))}</b>. ` : ""}Listen with ▶ (or click a spectrogram), then <b>Use</b> to condition on that render instead (a take gives its main anchor).</div><div class="pick" style="height:min(64vh,620px)"></div></div>`);
    let done = false, br = null;
    const m = modal({ title, body, wide: true, onClose: () => {
      if (br && br.rows.some((r) => player.isPlaying("card:" + r.id))) player.stop();
      if (!done) resolve(null);
    } });
    br = new Browser(body.querySelector(".pick"), { addLabel: "Use", onAdd: (row) => {
      const a = anchorOfRow(row);
      if (!a) return toast("That render has no conditioning of its own (an import): pick another, or type a prompt.", { error: true });
      done = true; resolve(a); m.close();
    } });
    br.reload();
  });
}
// a typed prompt (or an "anarchy: …" spec) as an anchor; resolves null when cancelled
function promptAnchor({ title = "Condition on a prompt", current = null } = {}) {
  return new Promise((resolve) => {
    const body = el(`<div style="display:flex;flex-direction:column;gap:6px"><textarea class="ui-input" rows="4" placeholder="dark rolling techno, BPM: 140   ·   or   anarchy: <spec>"></textarea><div class="note">Encoded by T5Gemma, like a studio prompt; an "anarchy: …" spec works too. ⌘↵ applies.</div></div>`);
    const ta = body.querySelector("textarea");
    ta.value = current && current.prompt != null ? current.prompt : "";
    let done = false;
    const apply = () => { const v = ta.value.trim(); if (!v) { toast("Type a prompt first.", { error: true }); return false; } done = true; resolve({ prompt: v }); };
    const m = modal({ title, body, onClose: () => { if (!done) resolve(null); },
      actions: [{ label: "Cancel", ghost: true }, { label: "Apply", primary: true, fn: apply }] });
    ta.onkeydown = (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); if (apply() !== false) m.close(); } };
    setTimeout(() => ta.focus(), 0);
  });
}
// Right-click on the Conditioning lane: everything you can do to the conditioning under the cursor,
// on whatever owns it. Keypoints mode: the block (run of keypoints with one source) under the cursor:
// edit its prompt / replace it (every keypoint of the block, so it stays one colour), add a keypoint
// here, the fade's presets (hard cut … 4 bars), delete. Auto: each clip there, as its override.
// One anchor: the project's anchor. The menu's heads carry the full source names the lane no longer
// prints. A typed source edits in place: its prompt prefilled (a render: its own prompt, if it has one).
function condMenu(e, hit) {
  const t = hit.t, lib = (fn, cur, what) => async () => { const a = await pickAnchor({ current: cur, title: `Conditioning from the library: ${what}` }); if (a) fn(a); };
  const typed = (fn, cur, what) => async () => { const a = await promptAnchor({ current: cur, title: `Prompt: ${what}` }); if (a) fn(a); };
  const head = (label) => ({ head: label });
  const seed = (a) => (a && a.prompt != null ? a : a && a.id && rowOf(a.id) && rowOf(a.id).prompt ? { prompt: rowOf(a.id).prompt } : null);
  const sourceItems = (a, set, what) => (a && a.prompt != null
    ? [{ label: "Edit prompt…", fn: typed(set, a, what) }, { label: "Replace with a library render…", fn: lib(set, a, what) }]
    : [{ label: "Replace with a library render…", fn: lib(set, a, what) }, { label: seed(a) ? "Replace with a typed prompt (starts from its prompt)…" : "Replace with a typed prompt…", fn: typed(set, seed(a), what) }]);
  // Mute / Unmute: `set(null)` = back to its own conditioning (only where `own` exists: a clip, the project)
  const muteItem = (a, set, own) => (isMuted(a)
    ? (unmuteOf(a) ? { label: `Unmute: condition on ${short(srcLabel(unmuteOf(a)), 40)} again`, fn: () => set(unmuteOf(a)) }
      : own ? { label: "Unmute: back to its own conditioning", fn: () => set(null) } : null)
    : { label: "Mute conditioning (the audio alone steers here)", fn: () => set(a ? muteOf(a) : { mute: true }) });
  const srcOrMute = (a, set, what, own) => [muteItem(a, set, own), ...(isMuted(a) ? [] : sourceItems(a, set, what))].filter(Boolean);
  let items = [];
  const ks = condKeys();
  if (ks.length) {
    const onK = ks.findIndex((k) => Math.abs(tl.x(k.t) - hit.px) <= 8);
    let i = onK;
    if (i < 0) {                                // the keypoint whose source dominates t
      ks.forEach((k, j) => { if (k.t <= t) i = j; });
      if (i < 0) i = 0;
      else { const n = ks[i + 1]; if (n && !n.cut && (t - ks[i].t) / Math.max(n.t - ks[i].t, 1e-6) > 0.5) i++; }
    }
    const [lo, hi] = runOf(ks, i), A = ks[i].anchor, n = hi - lo + 1;
    const setRun = (a) => edit((p) => { for (let j = lo; j <= hi; j++) p.cond.keys[j].anchor = a; S.sel = "k" + i; });
    const addK = (a) => edit((p) => { p.cond.keys = (p.cond.keys || []).concat([{ t: +t.toFixed(3), anchor: a }]).sort((x, y) => x.t - y.t); S.sel = "k" + p.cond.keys.findIndex((x) => Math.abs(x.t - t) < 1e-3); });
    if (onK >= 0) { S.sel = "k" + onK; tl.select(S.sel); inspector(); }
    const what = n > 1 ? `${fmt(ks[lo].t)} – ${fmt(ks[hi].t)}` : `keypoint ${i + 1} (${fmt(ks[i].t)})`;
    items.push(head(short(anchorLabel(A), 90)), head(n > 1 ? `full strength ${what} · ${n} keypoints` : `keypoint ${i + 1} · ${fmt(ks[i].t)}`), ...srcOrMute(A, setRun, what, false));
    if (onK < 0) items.push("-", head(`Add a keypoint at ${fmt(t)}`),
      { label: "…pinning what plays here", fn: () => addK(keyAnchorAt(ks, t)) },
      { label: "…with a library render…", fn: lib(addK, null, `new keypoint at ${fmt(t)}`) },
      { label: "…with a typed prompt…", fn: typed(addK, null, `new keypoint at ${fmt(t)}`) });
    const fs = fades(ks);
    const fd = onK >= 0 ? fs.find((f) => f.i + 1 === onK) || fs.find((f) => f.i === onK)
      : fs.find((f) => Math.abs(tl.x(f.c) - hit.px) <= 13 || (!f.cut && t > f.a && t < f.b));
    if (fd) {
      const b = beat(), opts = b ? [[b, "1 beat"], [4 * b, "1 bar"], [8 * b, "2 bars"], [16 * b, "4 bars"]] : [[0.5, "0.5 s"], [2, "2 s"], [4, "4 s"], [8, "8 s"]];
      const setF = (w) => () => edit((p) => { p.cond.keys = withFade(p.cond.keys, fd.i, w); S.sel = "k" + (fd.i + 1); });
      items.push("-", head(`Fade ${short(anchorLabel(ks[fd.i].anchor), 22)} → ${short(anchorLabel(ks[fd.i + 1].anchor), 22)} · ${fd.cut ? "hard cut" : fd.w.toFixed(1) + " s"}`),
        { label: (fd.cut ? "✓ " : "") + "Hard cut", fn: setF(0) },
        ...opts.map(([w, lbl]) => ({ label: `${!fd.cut && Math.abs(fd.w - w) < 0.03 ? "✓ " : ""}Fade over ${lbl}`, fn: setF(w) })),
        { label: "(or drag its ⟷ handle on the lane)", disabled: true });
    }
    items.push("-");
    if (onK >= 0) items.push({ label: "Delete keypoint", hint: "⌫", danger: true, fn: () => { S.sel = "k" + onK; deleteSel(); } });
    if (n < ks.length) items.push({ label: n > 1 ? `Remove this source (its ${n} keypoints)` : "Remove this source", danger: true,
      fn: () => edit((p) => { p.cond.keys.splice(lo, n); S.sel = null; }) });
  } else if (condMode() === "global") {
    const setG = (a) => edit((p) => { if (a) p.anchor = a; else delete p.anchor; });
    items = [head(S.proj.anchor ? short(anchorLabel(S.proj.anchor), 90) : "One anchor: the longest clip's"), ...srcOrMute(S.proj.anchor, setG, "the one anchor", true)];
  } else {
    const under = activeIdx().filter((pi) => { const g = geom(pi); return t >= g.at && t < g.at + g.dur; });
    for (const pi of under) {
      const it = pieces()[pi], nm = short(nameOf(it.id), 30), cur = it.anchor || anchorOfRow(rowOf(it.id));
      const setP = (a) => edit((p) => { if (a) p.pieces[pi].anchor = a; else delete p.pieces[pi].anchor; });
      if (items.length) items.push("-");
      items.push(head(`Clip “${nm}” · ${isMuted(it.anchor) ? "conditioning muted" : it.anchor ? "overridden" : "its own conditioning"}`), head(short(anchorLabel(isMuted(it.anchor) ? it.anchor : cur), 90)),
        muteItem(it.anchor, setP, true), ...(isMuted(it.anchor) ? [] : sourceItems(cur, setP, `clip “${nm}”`)));
      if (it.anchor && (!isMuted(it.anchor) || unmuteOf(it.anchor))) items.push({ label: "Back to its own conditioning", fn: () => setP(null) });
    }
    if (!under.length) items.push(head("A gap: it crossfades between the clips around it"));
    items.push("-", { label: "Edit as keypoints (free control over time)", fn: toKeys });
  }
  const gap = gapAt(t);
  if (gap) {
    const go = (pick) => async () => { const an = await pick({ title: `New conditioning for the gap ${fmt(gap[0])} – ${fmt(gap[1])}` }); if (an) insertCond(gap[0], gap[1], an); };
    items.push("-", head(`Insert new conditioning over this gap · ${fmt(gap[0])} – ${fmt(gap[1])}`),
      { label: "…a typed prompt / anarchy spec", fn: go(promptAnchor) },
      { label: "…a library render's conditioning", fn: go(pickAnchor) },
      { label: "…no conditioning (muted: the audio alone steers)", fn: () => insertCond(gap[0], gap[1], { mute: true }) });
  }
  while (items.length && items[items.length - 1] === "-") items.pop();
  menu(e.clientX, e.clientY, items);
}

function anchorEditor(host, anchor0, { allowDefault = false, defaultLabel = "", onChange: emit }) {
  // a muted anchor edits what it mutes; every change keeps the mute until the box is unticked
  const muted = isMuted(anchor0), anchor = muted ? unmuteOf(anchor0) : anchor0;
  const onChange = (a) => emit(muted ? (a ? muteOf(a) : { mute: true }) : a);
  const kind = !anchor ? "default" : anchor.prompt != null ? "prompt" : "render";
  const box = el(`<div class="anch"><div class="row"><select class="ui-input kind">
      ${allowDefault ? `<option value="default">its own (${esc(short(defaultLabel, 28))})</option>` : ""}
      <option value="render">a render's conditioning</option><option value="prompt">text prompt / anarchy spec</option></select></div>
    <div class="row rsel"><select class="ui-input rid" style="flex:1;min-width:0"></select><button class="ui-btn small browse" title="pick any starred render from the library (▶ to listen first)">Browse…</button></div>
    <div class="psel"><textarea class="ui-input ptxt" rows="3" placeholder="dark rolling techno, BPM: 140   ·   or   anarchy: <spec>"></textarea>
      <div class="row"><button class="ui-btn small apply">Apply</button><span class="note">a prompt is encoded by T5Gemma; an "anarchy: …" spec works too</span></div></div></div>`);
  host.appendChild(box);
  const mrow = el(`<label class="row mute-cond" title="Condition this stretch on nothing (SA3's no-prompt input, as its training's prompt dropout saw it): only the surrounding audio and the noise steer it. Untick to condition on the source below again."><input type="checkbox"> <span>Mute conditioning: the audio alone steers</span></label>`);
  const mbox = mrow.querySelector("input");
  mbox.checked = muted;
  mbox.disabled = muted && !anchor && !allowDefault;              // nothing to go back to: remove the keypoints instead
  mbox.onchange = () => emit(mbox.checked ? (anchor ? muteOf(anchor) : { mute: true }) : anchor || null);
  box.prepend(mrow);
  box.classList.toggle("muted", muted);
  const ks = box.querySelector(".kind"), rid = box.querySelector(".rid"), txt = box.querySelector(".ptxt");
  ks.value = kind;
  const opts = [...new Map([...S.starred.filter((r) => r.cond || r.prompt).map((r) => [r.id, r]), ...pieces().map((p) => [p.id, rowOf(p.id) || { id: p.id, prompt: p.id }]),
    ...(anchor && anchor.id ? [[anchor.id, rowOf(anchor.id) || { id: anchor.id, prompt: anchor.id }]] : [])]).values()];   // the current one is always listed (else the select shows another render)
  rid.innerHTML = opts.map((r) => `<option value="${esc(r.id)}">${esc(short(r.prompt || r.id, 60))}${{ multi: ` (a take: its main anchor of ${srcCond(r.id).n})`, bag: "", prompt: " (prompt)" }[srcCond(r.id).kind] ?? " (no conditioning)"}</option>`).join("");
  if (anchor && anchor.id) rid.value = anchor.id;
  else if (anchor && anchor.prompt == null) {     // a resolved anchor (a take's bank row, an npz: from "Edit as keypoints"): show it, not another render
    rid.insertAdjacentHTML("afterbegin", `<option value="__cur">${esc(short(anchorLabel(anchor), 60))} (current)</option>`);
    rid.value = "__cur";
  }
  txt.value = anchor && anchor.prompt != null ? anchor.prompt : "";
  const show = () => { box.querySelector(".rsel").hidden = ks.value !== "render"; box.querySelector(".psel").hidden = ks.value !== "prompt"; box.querySelector(".rsel").style.display = ks.value === "render" ? "" : "none"; box.querySelector(".psel").style.display = ks.value === "prompt" ? "" : "none"; };
  show();
  ks.onchange = () => { show(); if (ks.value === "default") onChange(null); else if (ks.value === "render" && rid.value && rid.value !== "__cur") onChange(anchorOfRow(opts.find((r) => r.id === rid.value) || { id: rid.value })); };
  rid.onchange = () => { if (rid.value === "__cur") return; const a = anchorOfRow(opts.find((r) => r.id === rid.value) || { id: rid.value }); if (a) onChange(a); else toast("That render has no conditioning of its own (an import).", { error: true }); };
  box.querySelector(".browse").onclick = async () => { const a = await pickAnchor({ current: anchor }); if (a) onChange(a); };
  box.querySelector(".apply").onclick = () => (txt.value.trim() ? onChange({ prompt: txt.value.trim() }) : toast("Type a prompt first.", { error: true }));
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
    if (fin) setPiece(pi, (x) => { x.mix = mix; }, { before: dragEnd() });
    else { dragStart(); pieces()[pi].mix = mix; syncMixer(); drawEq(); }
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

  const unused = condMode() !== "crossfade";      // One anchor / Keypoints: the render never reads a clip's own anchor or pull
  host = section(top, "pcond", "Conditioning", unused ? (it.anchor ? "overridden · unused now" : "unused now") : it.anchor ? "overridden" : "", unused && !!it.anchor);
  if (unused) host.appendChild(el(`<div class="note warn">The project is on <b>${condMode() === "keys" ? "Keypoints" : "One anchor"}</b> (⚙ Render settings): ${condMode() === "keys" ? "the Conditioning lane's keypoints" : "the one anchor"} decide what SA3 is told, so this clip's source, override and pull below only apply in Auto.</div>`));
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
  const u = k ? k[1] : b ? b.u : 0, N = (S.proj.model && S.proj.model.steps) || 8;
  const free = (v) => Math.round(v * 0.625 * N);   // segments.threshold: u maps onto 0..5 of 8 free steps (V_SPAN), at any step count
  host.appendChild(el(`<h3>Rewrite ${k ? "keypoint" : "span"}</h3>`));
  host.appendChild(el(k ? `<div class="chips"><span class="ui-chip dim">at ${fmt(k[0])}</span></div>`
    : `<div class="chips"><span class="ui-chip dim">${fmt(b.a)} → ${fmt(b.b)}</span><span class="ui-chip dim">${(b.b - b.a).toFixed(2)} s</span></div>`));
  slider(host, "strength", u, 0, 1, 0.01, (v) => (v >= 1 ? "1 · regenerate" : v <= 0 ? "0 · keep" : `${v.toFixed(2)} · ~${free(v)} of ${N} steps free`), (v, fin) => fin && rw.setStrength(v),
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
  const ks = S.proj.cond.keys, [lo, hi] = runOf(ks, i);
  if (hi > lo) host.appendChild(el(`<div class="note">One block of ${hi - lo + 1} keypoints (${fmt(ks[lo].t)} – ${fmt(ks[hi].t)}): changing its source changes all of them.</div>`));
  anchorEditor(host, k.anchor, { onChange: (a) => edit((p) => { for (let j = lo; j <= hi; j++) p.cond.keys[j].anchor = a; }) });
  const f = fades(ks).find((x) => x.i + 1 === i);
  if (f) {
    const c = el(`<div class="row"><label>Fade in from <b>${esc(short(anchorLabel(ks[i - 1].anchor), 28))}</b></label><input class="ui-input num" type="number" min="0" step="0.1"><span class="note">s (0 = hard cut)</span></div>`);
    const inp = c.querySelector("input");
    inp.value = f.cut ? 0 : +f.w.toFixed(2);
    inp.onchange = () => edit((p) => { p.cond.keys = withFade(p.cond.keys, i - 1, Math.max(0, +inp.value || 0)); });
    host.appendChild(c);
    host.appendChild(el('<div class="note">The change is a cosine crossfade centred between the two keypoints; longer = more gradual. Same as dragging its ⟷ handle on the lane. A hard cut holds the previous source until this keypoint.</div>'));
  }
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
      <li><b>Rewrite</b> (optional). On the Rewrite lane under the tracks: drag over anything SA3 should re-render, or click to add keypoints. New spans start at full strength (new music); drag a span's top down for the same arrangement with a new sound.</li>
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
  if (c) h.appendChild(el(`<div class="note">${c.anchors.map((a, i) => `<div><i class="dot" style="display:inline-block;background:${anchorCol(a, i)}"></i> ${esc(short(a.label || anchorLabel(a), 60))}</div>`).join("")}</div>`));
  const ck = el(`<div class="row">${condMode() !== "keys" ? '<button class="ui-btn small tok" title="turn the automatic conditioning into keypoints you can edit on the lane">Edit as keypoints</button>' : '<button class="ui-btn small toa">Back to auto</button>'}</div>`);
  if (ck.querySelector(".tok")) ck.querySelector(".tok").onclick = toKeys;
  if (ck.querySelector(".toa")) ck.querySelector(".toa").onclick = () => edit((q) => { q.cond = { ...(q.cond || {}), gap: "crossfade" }; });
  h.appendChild(ck);
  if (condMode() === "global" || (condMode() === "crossfade" && pieces().some((x) => srcCond(x.id).kind === "none" && !x.anchor))) {
    h.appendChild(el(`<div class="note">${condMode() === "global" ? "The one anchor:" : "Fallback (imports and gaps with no conditioning of their own):"}</div>`));
    anchorEditor(h, p.anchor || null, { allowDefault: true, defaultLabel: "the longest clip's", onChange: (a) => edit((q) => { if (a) q.anchor = a; else delete q.anchor; }) });
  }

  const m = p.master || {};
  h = section(host, "master", "Master", `${mt.peak ?? "–"} dBFS peak`);
  slider(h, "gain", m.gain || 0, -18, 12, 0.5, (v) => `${v > 0 ? "+" : ""}${v.toFixed(1)} dB`, (v, fin) => {
    const nm = { ...(S.proj.master || {}), gain: v };
    if (fin) edit((q) => { q.master = nm; }, { before: dragEnd() }); else { dragStart(); S.proj.master = nm; mixer.setMaster(nm); }
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

// flush an edit still waiting on autosave: the server routes below read project.json from disk
async function flushSave() { clearTimeout(saveT); if (S.saving) await saveNow(); }
async function fitAll(pis) {
  try {
    await flushSave();
    const r = await api.post("/sa3/composer/fitall", { name: S.name, pis });
    S.undo.push(JSON.stringify(S.proj)); S.redo = [];
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
  const isBase = baseTake() === tk.id, stale = tk.ref_sha256 && S.layout && tk.ref_sha256 !== S.layout.ref_sha256;
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
    onRange: (rg) => { S.lift = rg ? { take: tk.id, a: rg[0] + takeOff(tk), b: rg[1] + takeOff(tk) } : null; inspKey = ""; paint(); } });
  const lf = S.lift && S.lift.take === tk.id ? S.lift : null;
  if (lf) {
    sv.setRange([lf.a - takeOff(tk), lf.b - takeOff(tk)]);
    const box = el(`<div class="liftbox"><div class="row"><b>Selection</b><span class="ui-chip accent">${fmt(lf.a)} – ${fmt(lf.b)}</span><span class="ui-chip dim">${fmt(lf.b - lf.a)}</span></div>
      <div class="row"><button class="ui-btn primary lift" title="a clip of this stretch, at the same time, on the first track that is free there (↵)">Lift onto the tracks ↵</button><button class="ui-btn small ghost lplay" title="play just the selection">▶</button><button class="ui-btn small ghost lhl" title="save the selection as a highlight on this take">★ Highlight</button></div>
      <div class="note">Or drag the selection from the take lane onto any track. The clip keeps its own copy of the take, so you can trash the take afterwards.</div></div>`);
    box.querySelector(".lift").onclick = () => liftTake();
    box.querySelector(".lplay").onclick = () => { useTake(tk.id); playFrom("take", lf.a, lf.b); };
    box.querySelector(".lhl").onclick = () => openHighlight({ id: tk.id, range: [lf.a - takeOff(tk), lf.b - takeOff(tk)], onSaved: () => refreshLib() });
    host.appendChild(box);
  }
  host.appendChild(el('<div class="note">Click the spectrogram (or the take lane) to play from there; drag across either to select a stretch to lift onto the tracks. ↑ ↓ switch to the next take at the same moment, while playing too.</div>'));
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
    clearTimeout(saveT);                       // the server repoints project.json on disk and the reply replaces
    if (S.saving) await saveNow();             // S.proj: an edit still waiting on autosave would be lost
    const i = S.takes.findIndex((x) => x.id === t.id), no = takeNo(t);
    const r = await api.post("/sa3/composer/take_remove", { name: S.name, take: t.id, mode });
    S.takes = r.takes || S.takes.filter((x) => x.id !== t.id);
    if (r.project) {                           // the tracks read this take: the server kept it in clips/ and repointed them
      for (const [o, n] of Object.entries(r.repointed || {})) {
        const sw = (j) => j.split(JSON.stringify(o)).join(JSON.stringify(n));
        S.undo = S.undo.map(sw); S.redo = S.redo.map(sw);
      }
      S.proj = Object.assign(DEFAULT(), r.project);
      migrate(S.proj);
      if (r.layout) S.layout = r.layout;
      S.clips = r.clips || S.clips;
      loadImports();
    }
    if (S.lift && S.lift.take === t.id) S.lift = null;
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
    await flushSave();                         // the server builds the new base from project.json on disk
    S.undo.push(JSON.stringify(S.proj)); S.redo = [];
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

// ── saved version (⌘S) ───────────────────────────────────────────────────────────────────────
// Every edit autosaves project.json (the working state, crash-safe). ⌘S on top of that writes the
// project's one *saved version* (server: saved.json, overwritten on each ⌘S); the chip next to the
// project menu says whether anything changed since, and its menu can revert to it (undoable).
// Dirty = the project JSON (keys sorted, `name` / `_x` left out) differs from the saved one.
const canon = (p) => {
  const o = (v) => (Array.isArray(v) ? v.map(o) : v && typeof v === "object" ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, o(v[k])])) : v);
  const { name, ...rest } = p || {};
  return JSON.stringify(o(Object.fromEntries(Object.entries(rest).filter(([k]) => k[0] !== "_"))));
};
function savedOf(sv) {
  if (!sv || !sv.project) return null;
  const p = Object.assign(DEFAULT(), JSON.parse(JSON.stringify(sv.project)));
  migrate(p);
  return { at: sv.saved_at, canon: canon(p) };
}
const dirty = () => !S.saved || canon(S.proj) !== S.saved.canon;
const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
function saveChip() {
  const b = $("savechip");
  if (!S.proj) return;
  const d = dirty();
  b.textContent = d ? (S.saved ? "● unsaved" : "● not saved") : `saved ${hhmm(S.saved.at)}`;
  b.classList.toggle("dirty", d);
  b.title = d ? (S.saved ? `changed since the last save (${hhmm(S.saved.at)}). ⌘S saves; click for Revert. Edits are autosaved as you go either way.` : "never saved with ⌘S yet (edits are autosaved as you go). ⌘S saves this version.")
    : "this is the saved version. ⌘S overwrites it with the current state.";
}
async function saveVersion() {
  if (!S.name || !S.proj) return;
  clearTimeout(saveT);
  S.saving = true; status();
  await saveNow();                            // the working state is on disk + built first
  try {
    const snap = JSON.parse(JSON.stringify(S.proj));
    const r = await api.post("/sa3/composer/version", { name: S.name, project: snap });
    S.saved = { at: r.saved_at, canon: canon(snap) };
    saveChip();
    toast(`Saved ${S.name}${S.error ? " (it has a build error: see the status chip)" : ""}.`, { ms: 1800 });
  } catch (e) { toast("Save failed: " + e.message, { error: true }); }
}
async function revertSaved() {
  if (!S.saved) return;
  clearTimeout(saveT);
  if (S.saving) await saveNow();
  player.stop(); mixer.stop();
  const before = JSON.stringify(S.proj);
  try {
    const r = await api.post("/sa3/composer/revert", { name: S.name });
    S.undo.push(before); S.redo = [];
    S.proj = Object.assign(DEFAULT(), r.project);
    migrate(S.proj);
    S.layout = r.layout || null; S.error = r.error || null;
    S.sel = null; S.multi = new Set(); S.lift = null; inspKey = "";
    paint(); syncMixer();
    if (S.layout) loadFit();
    toast(`Back to the version saved at ${hhmm(S.saved.at)} (⌘Z undoes this).`);
  } catch (e) { toast("Revert failed: " + e.message, { error: true }); }
}
$("savechip").onclick = (e) => {
  const r = e.currentTarget.getBoundingClientRect();
  menu(r.left, r.bottom + 4, [
    { label: "Save this version", hint: "⌘S", fn: saveVersion },
    { label: S.saved ? `Revert to the last save (${hhmm(S.saved.at)})` : "Revert to the last save", disabled: !S.saved || !dirty(), fn: revertSaved },
  ]);
};
// ⌘S anywhere on the page, text fields included (the kit's shortcuts skip those), and never the browser's "Save page as"
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "s") { e.preventDefault(); e.stopImmediatePropagation(); saveVersion(); }
}, true);

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
  if (S.clips.length) {                        // kept takes: the whole take behind every clip lifted from it, even once the take is trashed
    host.appendChild(el('<div class="muted" style="margin-top:12px" title="every take a clip was lifted from, kept whole in this project (clips/): drag one onto a track to cut another stretch, even after the take itself is trashed">Kept takes</div>'));
    for (const r of S.clips) new ClipCard(host, r, { compact: true });
  }
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
  const track = dropTrack(e) ?? 0;
  addPieceKept({ id: d.id }, [d.s0, d.s1], { at: tl.snap(hit.t, e), track });
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
  S.jobs = d.jobs || [];
  seenErr = new Set(S.jobs.filter((j) => j.error).map((j) => j.id));
  S.take = S.takes.length ? S.takes[0].id : null;
  S.undo = []; S.redo = []; S.sel = null; S.error = null; S.cursor = 0; S.fit = null; inspKey = ""; S.lift = null;
  S.clips = d.clips || [];
  S.saved = savedOf(d.saved);
  S.range = null; tl.setRange(null);
  prefs.last = name; savePrefs();
  await projectsMenu(name);
  paint();
  tl.fit();                                  // after paint: it fits the new project's length
  syncMixer();
  if (S.layout) loadFit();
  if (S.jobs.some((j) => j.running)) pollJob();
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
// Wipe composition: back to an empty timeline in the same project. Kept: the render settings
// (model / steps / CFG / LoRAs, seam, takes per render, the conditioning mode), master, windows,
// variants, version history. The takes (files under takes/) and the library are never touched.
// Gone: clips, tracks, Rewrite curve, conditioning keypoints + global anchor, base take, length.
// One edit(), so ⌘Z brings it all back.
const KEEP_ON_WIPE = ["name", "seeds", "model", "seam", "seam_unit", "master", "windows", "variants", "history"];
function wipe() {
  const n = pieces().length;
  const body = el(`<div class="note">Removes ${n ? `all <b>${n} clip${n === 1 ? "" : "s"}</b>, ` : ""}the tracks, the Rewrite curve and the conditioning keypoints / anchor from <b>${esc(S.name)}</b>. Render settings, the library and the takes stay. ⌘Z undoes it.</div>`);
  modal({ title: "Wipe this composition?", body, actions: [{ label: "Cancel", ghost: true }, { label: "Wipe composition", danger: true, fn: () => {
    player.stop();
    edit((p) => {
      const keep = Object.fromEntries(KEEP_ON_WIPE.filter((k) => k in p).map((k) => [k, p[k]]));
      const { keys, ...cond } = p.cond || {};          // the mode (Auto / One anchor / Keypoints) is a render setting: kept
      for (const k of Object.keys(p)) delete p[k];
      Object.assign(p, DEFAULT(), keep, { pieces: [], tracks: 2, cond: { ...DEFAULT().cond, ...cond } });
    });
    S.sel = null; S.multi = new Set(); S.lift = null; tl.multi = S.multi; tl.select(null); setSection(null);
    inspector(); paint();
    toast("Composition wiped (⌘Z undoes it).");
  } }] });
}
$("wipe").onclick = wipe;

async function create(name) {
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(name)) return toast("Use letters, digits, - and _ (no spaces).", { error: true });
  const ps = await api.get("/sa3/composer/projects");
  if (ps.some((p) => p.name === name)) return open(name);
  await api.post("/sa3/composer/save", { name, project: DEFAULT(), build: false });
  await open(name);
}

// ── render ──────────────────────────────────────────────────────────────────────────────────
// every moment of [a, b] conditions on muted anchors only (keypoints, else the last build's curves).
// Then CFG ≠ 1 is a no-op (cond = uncond = zeros) that still costs a second model pass per step.
function fullyMuted(a = 0, b = condLen()) {
  const ks = condKeys(), c = S.layout && S.layout.cond;
  if (!ks.length && (!c || !c.anchors.length)) return false;
  for (let t = a; t <= b + 1e-6; t += 0.25) {
    const parts = ks.length ? keyWeights(ks, t) : c.anchors.map((x, i) => ({ a: x, w: sample(c.curves[i], t) }));
    if (parts.some((x) => x.w > 1e-3 && !isMuted(x.a))) return false;
  }
  return true;
}
async function render() {
  const n = Math.max(1, Math.min(16, +$("nseeds").value || 2));
  const seeds = Array.from({ length: n }, () => Math.floor(Math.random() * 2 ** 31));
  const sec = S.range ? [S.range[0], S.range[1]] : null, name = S.name;
  // optimistic: the batch's lanes show at once ("Queuing"), the save + build + post run after;
  // the server's batch replaces this placeholder when the post returns
  const ph = { id: "local" + Date.now(), local: true, running: true, total: n, done: 0, section: sec };
  S.jobs.push(ph);
  paint();
  const drop = () => { S.jobs = S.jobs.filter((j) => j !== ph); paint(); };
  // the seeds go in the request (server.render → compose.submit), not into project.json: writing
  // them there cost an undo step and flagged the project "unsaved" after every render
  clearTimeout(saveT);
  await saveNow();                              // project.json on disk = what the page shows (the job reads it)
  if (name !== S.name) return drop();
  if (S.error) { drop(); return toast("Fix the build error first: " + S.error, { error: true }); }
  if (S.empty && S.empty.length) toast(`Heads-up: ${S.empty.length} empty stretch${S.empty.length === 1 ? " is" : "es are"} only partly rewritten (the ⚠ chip; usually you want strength 1 there). Rendering anyway.`, { ms: 7000 });
  const cfg = +(((S.proj || {}).model || {}).cfg ?? 1);
  if (Math.abs(cfg - 1) > 1e-6 && (sec ? fullyMuted(sec[0], sec[1]) : fullyMuted()))
    toast(`Heads-up: all of the conditioning ${sec ? "in this section " : ""}is muted, so CFG ${cfg} does nothing here (with no prompt, both CFG passes are the same) but still doubles the render time. Set CFG to 1 in Render settings. Rendering anyway.`, { ms: 9000 });
  try {
    const r = await api.post("/sa3/composer/render", { name, seeds, ...(sec ? { section: sec } : {}) });
    if (r.error) throw new Error(r.error);
    if (name !== S.name) return drop();
    const i = S.jobs.indexOf(ph);
    if (i >= 0) S.jobs[i] = r.job; else S.jobs.push(r.job);
    toast(`${n} take${n === 1 ? "" : "s"} queued in the studio (seeds ${seeds.join(", ")}).`);
    paint();
    pollJob();
  } catch (e) { drop(); toast(e.message, { error: true }); }
}
let pollT = 0, seenErr = new Set();
async function pollJob() {
  clearTimeout(pollT);
  try {
    const name = S.name;
    const d = await api.get("/sa3/composer/project", { name });
    if (name !== S.name) return;
    const had = new Set(S.takes.map((t) => t.id));
    S.takes = d.takes || [];
    S.jobs = (d.jobs || []).concat(S.jobs.filter((j) => j.local));   // keep placeholders still posting
    const nw = S.takes.find((t) => !had.has(t.id));
    if (nw) S.take = nw.id;
    for (const j of S.jobs) if (j.error && !j.running && !seenErr.has(j.id)) { seenErr.add(j.id); toast("Render: " + j.error, { error: true }); }
    paint();
    if (S.jobs.some((j) => j.running)) pollT = setTimeout(pollJob, 2000);
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
// LoRAs every take renders with: the kit's LoRA stack (the studio's LoRA panel is the same
// component), kept in project.json model.loras; compose.generate_args fills the studio's slots.
function loraStack() {
  return loraStack.el || (loraStack.el = new LoraStack($("loras"), {
    maxSteps: () => (S.proj && S.proj.model && S.proj.model.steps) || 8,
    onChange: (list) => edit((p) => { p.model = { ...(p.model || {}), loras: list }; }),
  }));
}
$("cfg").onchange = () => edit((p) => { p.model = { ...(p.model || {}), cfg: Math.max(0, Math.min(10, +$("cfg").value || 0)) }; });
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
  $(btn).addEventListener("pointerdown", (e) => e.stopPropagation());   // else the document closes it and the click reopens it
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
    { label: "Add a track", fn: addTrack },
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
bind("arrowup", "Selected clip one track up (a Rewrite point / span: strength +0.05; a take: the newer take, same moment)", () => (S.sel === "w" ? rw.nudgeU(0.05) : (S.sel || "")[0] === "v" ? envLanes[+S.sel.slice(1)].nudgeU(0.05) : (S.sel || "").startsWith("T:") ? stepTake(-1) : moveTrack(-1)));
bind("arrowdown", "Selected clip one track down (a Rewrite point / span: strength −0.05; a take: the older take, same moment)", () => (S.sel === "w" ? rw.nudgeU(-0.05) : (S.sel || "")[0] === "v" ? envLanes[+S.sel.slice(1)].nudgeU(-0.05) : (S.sel || "").startsWith("T:") ? stepTake(1) : moveTrack(1)));
bind("m", "Mute the selected clips", () => toggleSel("mute"));
bind("s", "Solo the selected clips", () => toggleSel("solo"));
bind("mod+s", "Save this version of the project (overwrites the last save; edits autosave anyway)", saveVersion);
bind("enter", "Lift the selected stretch of a take onto the tracks", () => (S.lift ? liftTake() : null));
bind("escape", "Clear the selection (and the ruler's render section)", () => { S.lift = null; S.sel = null; S.multi = new Set(); tl.multi = S.multi; rw.sel = null; tl.select(null); setSection(null); inspector(); });
$("tl").addEventListener("contextmenu", (e) => {
  const hit = tl.at(e), cpi = pieceAt(hit), tkid = hit.lane && hit.lane.takeId;
  const hx = hit.lane && /^t\d+$/.test(hit.lane.key) ? tl.hit(hit.px, hit.py).marker : null;
  if (hx && hx.xf) { e.preventDefault(); return xfMenu(e, hx.xf); }
  if (hit.lane && hit.lane.key === "cond" && S.layout) { e.preventDefault(); return condMenu(e, hit); }
  if (hit.lane && hit.lane.key === "rewrite") {   // the Rewrite stretch under the cursor: delete its two keys
    e.preventDefault();
    const sp = rw.spanAt(hit.t);
    if (!sp) return;
    return menu(e.clientX, e.clientY, [
      { head: `Rewrite · ${fmt(sp.a)} – ${fmt(sp.b)}` },
      { label: "Delete", danger: true, fn: () => { rw.delSpan(hit.t); S.sel = null; inspector(); } },
    ]);
  }
  if (cpi >= 0 && !S.multi.has("p" + cpi)) { S.sel = "p" + cpi; S.multi = new Set([S.sel]); paint(); }   // right-click picks the clip under it
  if (tkid) {                                   // a take lane: act on that take, never on an earlier selection
    const tk = S.takes.find((x) => x.id === tkid);
    if (!tk) return;
    e.preventDefault();
    S.take = tk.id; S.sel = "T:" + tk.id; paint();
    return menu(e.clientX, e.clientY, [
      { label: "Keep it (starred, off the lanes)", fn: () => removeTake(tk, "keep") },
      { label: "Trash this take", hint: "⌫", danger: true, fn: () => removeTake(tk, "trash") },
      "-",
      revealItem({ id: tk.id }),
    ]);
  }
  const sel = S.sel, trk = hit.lane && /^t\d+$/.test(hit.lane.key) ? +hit.lane.key.slice(1) : null;
  if (!sel && trk == null) return;
  e.preventDefault();
  if (trk != null && (!sel || (sel[0] !== "p" && sel[0] !== "v"))) return menu(e.clientX, e.clientY, envMenu(trk, hit).filter(Boolean));   // empty track space: its volume
  menu(e.clientX, e.clientY, [
    ...(trk != null ? [...envMenu(trk, hit), "-"] : []),
    cpi >= 0 && !isBasePi(cpi) ? { label: "Split here", hint: "⇧: exact, else on the beat", fn: () => splitAt(cpi, hit.t, e) } : null,
    sel[0] === "p" ? { label: "Mute / unmute", hint: "M", fn: () => toggleSel("mute") } : null,
    sel[0] === "p" ? { label: "Solo", hint: "S", fn: () => toggleSel("solo") } : null,
    sel[0] === "p" && selPis().some((pi) => (pieces()[pi].speed || 1) !== 1) ? { label: "Reset speed (undo the ⌥-stretch)", fn: () => edit((p) => { for (const pi of selPis()) delete p.pieces[pi].speed; }) } : null,
    sel[0] === "p" ? { label: "Play the mix from here", fn: () => playMix(hit.t) } : null,
    cpi >= 0 && pieces()[cpi] && pieces()[cpi].id ? revealItem({ id: pieces()[cpi].id }) : null,
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
  addPieceKept(rowOf(id) || { id }, [+q.get("s0") || 0, +q.get("s1") || 8]);
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
