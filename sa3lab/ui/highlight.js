// SA3 UI kit: the highlight modal (spec 3.1). Opened from a range drag on any dadaspec, or from a
// highlight band's menu. A zoomed dadaspec of the range (± 25 %), looped playback, in / out
// handles to fine-tune; with a confident beat (render tempo conf ≥ 0.3) the handles snap to the
// render's beat grid, ⇧ drags freely; without one they move freely. Shows the range's BPM / key once its analysis is in (only when
// confident). Tags (autocomplete), note, score. Save stars the render and adds (or updates) the
// highlight in its sidecar (POST /sa3/highlights, sa3lab/highlights.py).
//
//   openHighlight({id, range: [s0, s1], hl (an existing highlight, to edit), known: [tags],
//                  onSaved(view), onComposer(id, s0, s1)})

import * as api from "./api.js";
import * as player from "./player.js";
import { load as loadSpec, drawCrop } from "./dadaspec.js";
import { modal } from "./modal.js";
import { TagInput } from "./taginput.js";
import { toast } from "./shell.js";

const fmt = (t) => (t >= 60 ? `${Math.floor(t / 60)}:${(t % 60).toFixed(2).padStart(5, "0")}` : t.toFixed(2) + " s");

export async function openHighlight({ id, range, hl = null, known = [], onSaved = null, onComposer = null }) {
  let v;
  try { v = await api.get("/sa3/highlights", { id }); } catch (e) { toast(e.message, { error: true }); return; }
  const dur = v.duration || range[1];
  let [a, b] = hl ? [hl.s0, hl.s1] : range;
  const beat = v.conf >= 0.3 && v.bpm ? 60 / v.bpm : null;
  const phase = v.phase || 0;
  const body = document.createElement("div");
  body.className = "ui-hl";
  const an = hl && v.analysis[hl.id];
  body.innerHTML = `<canvas class="zoom"></canvas>
    <div class="row"><button class="ui-btn small play">▶ Loop</button><span class="mono io"></span><span class="grow"></span>
      <span class="ui-chip dim snap">${beat ? `beat snap · ${(+v.bpm).toFixed(1)} BPM · ⇧ = free` : "no confident beat: free"}</span></div>
    <div class="row meta"></div>
    <label class="ui-label">Tags</label><div class="tags"></div>
    <label class="ui-label">Note</label><textarea class="ui-input note" rows="2" placeholder="what makes this moment good"></textarea>
    <div class="row"><label class="ui-label">Score</label><select class="ui-input score"></select></div>`;
  const cv = body.querySelector("canvas");
  const sel = body.querySelector(".score");
  sel.innerHTML = '<option value="">–</option>' + Array.from({ length: 11 }, (_, k) => `<option value="${k}">${k}</option>`).join("");
  sel.value = hl && hl.score != null ? String(hl.score) : "";
  body.querySelector(".note").value = (hl && hl.note) || "";
  const ti = new TagInput(body.querySelector(".tags"), { tags: (hl && hl.tags) || [], known });
  const meta = body.querySelector(".meta");
  const showMeta = (x) => {
    if (!x || x.pending) { meta.innerHTML = '<span class="muted">analysis: measured after saving</span>'; return; }
    const chips = [];
    if (x.bpm && x.bpm_conf >= 0.3) chips.push(`${(+x.bpm).toFixed(1)} BPM`);
    chips.push(x.key ? x.key : `no key (${x.key_guess}?)`);
    if (x.lufs != null) chips.push(`${x.lufs} LUFS`);
    if (x.width != null) chips.push(`width ${x.width}`);
    meta.innerHTML = chips.map((c) => `<span class="ui-chip">${c}</span>`).join("");
  };
  showMeta(an);
  const win = () => { const pad = Math.max(1, (b - a) * 0.25); return [Math.max(0, a - pad), Math.min(dur, b + pad)]; };
  let W = win();
  let src = null;
  loadSpec(api.specUrl(id)).then((s) => { src = s; draw(); }).catch(() => {});
  const snap = (t, e) => {
    if (!beat || (e && e.shiftKey)) return Math.round(t * 100) / 100;
    return Math.max(0, phase + Math.round((t - phase) / beat) * beat);
  };
  function draw() {
    const dpr = devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
    if (!w) return;
    cv.width = w * dpr; cv.height = h * dpr;
    const g = cv.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = "#0b0b10"; g.fillRect(0, 0, w, h);
    if (src) drawCrop(g, src, dur, W[0], W[1], 0, 0, w, h);
    const X = (t) => ((t - W[0]) / (W[1] - W[0])) * w;
    if (beat) {
      g.fillStyle = "rgba(255,255,255,0.10)";
      for (let t = phase + Math.ceil((W[0] - phase) / beat) * beat; t < W[1]; t += beat) g.fillRect(X(t), 0, 1, h);
    }
    g.fillStyle = "rgba(0,0,0,0.55)";
    g.fillRect(0, 0, X(a), h); g.fillRect(X(b), 0, w - X(b), h);
    g.fillStyle = "#ffd479";
    g.fillRect(X(a) - 1.5, 0, 3, h); g.fillRect(X(b) - 1.5, 0, 3, h);
    if (ph != null) { g.fillStyle = "#fff"; g.fillRect(X(ph), 0, 1.5, h); }
    body.querySelector(".io").textContent = `${fmt(a)} → ${fmt(b)} · ${(b - a).toFixed(2)} s${beat ? ` · ${Math.round((b - a) / beat)} beats` : ""}`;
  }
  let ph = null;
  cv.addEventListener("pointerdown", (e) => {
    const r = cv.getBoundingClientRect();
    const T = (ev) => W[0] + ((ev.clientX - r.left) / r.width) * (W[1] - W[0]);
    const t = T(e), xa = Math.abs(t - a), xb = Math.abs(t - b), tol = (W[1] - W[0]) * 0.03;
    const which = xa < tol && xa <= xb ? "a" : xb < tol ? "b" : null;
    if (!which) { loop(t); return; }
    cv.setPointerCapture(e.pointerId);
    const mv = (ev) => {
      const x = snap(T(ev), ev);
      if (which === "a") a = Math.max(0, Math.min(x, b - 0.25)); else b = Math.min(dur, Math.max(x, a + 0.25));
      draw();
    };
    const up = () => { cv.removeEventListener("pointermove", mv); cv.removeEventListener("pointerup", up); W = win(); draw(); if (player.isPlaying("hl")) loop(a); };
    cv.addEventListener("pointermove", mv);
    cv.addEventListener("pointerup", up);
  });
  function loop(t0 = a) {
    player.play({ key: "hl", url: api.audioUrl(id), t0: Math.max(a, Math.min(t0, b - 0.05)), t1: b, loop: true,
      onTime: (t) => { ph = t; draw(); } }).catch((e) => toast(e.message, { error: true }));
  }
  body.querySelector(".play").onclick = () => (player.isPlaying("hl") ? player.stop() : loop(a));
  const fields = () => ({ s0: +a.toFixed(3), s1: +b.toFixed(3), tags: ti.tags, note: body.querySelector(".note").value,
    score: sel.value === "" ? null : +sel.value });
  const acts = [];
  if (hl) acts.push({ label: "Remove", danger: true, left: true, fn: async () => {
    try { const r = await api.post("/sa3/highlights", { id, op: "remove", h: { id: hl.id } }); if (onSaved) onSaved(r); }
    catch (e) { toast(e.message, { error: true }); return false; } } });
  if (onComposer) acts.push({ label: "Open in composer", ghost: true, fn: () => onComposer(id, a, b) });
  acts.push({ label: hl ? "Save" : "Save highlight", primary: true, fn: async () => {
    try {
      const r = await api.post("/sa3/highlights", hl ? { id, op: "update", h: { id: hl.id, ...fields() } } : { id, op: "add", h: fields() });
      toast(hl ? "Highlight saved." : "Highlight saved (render starred).");
      if (onSaved) onSaved(r);
    } catch (e) { toast(e.message, { error: true }); return false; }
  } });
  const m = modal({ title: hl ? "Highlight" : "New highlight", body, actions: acts, wide: true, onClose: () => { if (player.isPlaying("hl")) player.stop(); } });
  requestAnimationFrame(draw);
  return m;
}
