() => {
  // Dadaspec timeline on the Inpaint and Audio → audio inputs (server side + the why: segments.py).
  // Each input's audio shows as its dadaspec (GET /sa3/inspec) with its own playback: click to
  // seek, space plays / pauses. Audio → audio is a viewer (one Noise level for the whole clip).
  // Inpaint adds spans to regenerate: drag on empty space draws one; a span drags to move, its
  // edges to resize, ⌥-drag duplicates, double-click auditions it, × / ⌫ deletes, ⌘Z / ⇧⌘Z undo /
  // redo, ←/→ nudge one latent frame (⇧: 1 s). Edges snap to SA3's latent grid (4096 samples,
  // ~93 ms, the mask's real resolution) and magnet to other edges, the playhead and the audio's
  // end (hold ⇧ while dragging: no snapping). The inpaint timeline spans max(audio, Length):
  // spans past the audio's end regenerate over zero padding (a continuation, as Extend does).
  // Length follows the input (segments.py locked_length enforces it per job): the inpaint reference's
  // length, or the last span's end when a span reaches past it (the dimmed zone after Length is
  // where a continuation is drawn), else the a2a guide's length. The Length slider is kept in sync.
  // Both inputs loaded = Generate disabled (one mode at a time; the server refuses it too).
  // Spans live as JSON in the CSS-hidden #sa3-seg-data box, written on every edit and read back
  // when the server changes it (restore, Extend, the saved session).
  const SPL = 4096 / 44100;                         // one latent frame, seconds
  const ROLES = { inp: { spans: true }, a2a: { spans: false } };
  const EMPTY = "Drag across the spectrogram to mark what to regenerate. Everything outside the spans stays bit-exact. Draw into the dimmed zone after the end to continue the clip.";
  const KEYS = "Drag a span to move, its edges to resize · ⌥-drag duplicates · double-click plays it · ⌫ deletes · ⌘Z undo · hold ⇧ for no snapping";
  let S = [];                                       // inpaint spans [s0, s1], seconds
  let written = null;                               // box value we last wrote / adopted
  const undo = [], redo = [];
  const eds = {};                                   // role → editor state

  const $box = () => document.querySelector("#sa3-seg-data textarea, #sa3-seg-data input");
  const r3 = (v) => Math.round(v * 1000) / 1000;
  const ser = () => JSON.stringify({ inpaint: S.map((s) => s.map(r3)) });
  const fmt = (t) => {
    const m = Math.floor(t / 60), s = t - m * 60;
    return m ? `${m}:${s.toFixed(1).padStart(4, "0")}` : `${s.toFixed(1)}s`;
  };

  function commit() {                               // after an edit: push to the box, redraw
    const v = ser(), b = $box();
    if (b && b.value !== v) {
      b.value = v;
      b.dispatchEvent(new Event("input", { bubbles: true }));
    }
    written = v;
    Object.values(eds).forEach(draw);
  }
  function snapshot() { undo.push(ser()); if (undo.length > 80) undo.shift(); redo.length = 0; }
  function load(v) {
    try { S = (JSON.parse(v || "{}").inpaint || []).map((s) => [+s[0], +s[1]]); } catch (e) { /* keep */ }
  }
  function history(from, to) {
    if (!from.length) return;
    to.push(ser());
    load(from.pop());
    Object.values(eds).forEach((e) => { e.sel = -1; });
    commit();
  }

  // ── DOM ──
  function build(role, blk) {
    const spans = ROLES[role].spans;
    const el = document.createElement("div");
    el.className = `sa3seg sa3seg-${role}`;
    el.tabIndex = 0;
    el.innerHTML = `
      <div class="sg-bar">
        <button class="sg-play" title="Play / pause (space)"><svg viewBox="0 0 16 16"><path d="M4 2.5v11l9-5.5z"/></svg></button>
        <span class="sg-time">0.0s</span>
        <span class="sg-sum"></span>
        <span class="sg-warn">Both inputs loaded: clear one (Audio → audio or Inpaint) to generate</span>
        <span class="sg-lock" title="Length follows the input audio (and any span past its end), so every render's length matches its input"></span>
        ${spans ? `<button class="sg-btn sg-inv" title="Invert: the gaps between spans become the spans">Invert</button>
        <button class="sg-btn sg-clr" title="Remove every span">Clear</button>` : ""}
      </div>
      <div class="sg-stage">
        <div class="sg-imgwrap"><img alt="spectrogram" draggable="false"></div>
        <div class="sg-pad" title="Past the audio's end: zero-padded"></div>
        <div class="sg-beyond"></div>
        <div class="sg-len"><span></span></div>
        <div class="sg-segs"></div>
        <div class="sg-hov"><span></span></div>
        <div class="sg-ph"></div>
        <div class="sg-load"></div>
      </div>
      <div class="sg-ruler"></div>
      ${spans ? `<div class="sg-hint"></div>` : ""}
      <audio preload="auto"></audio>`;
    blk.appendChild(el);
    const e = {
      role, spans, el, blk, url: null, dur: 0, len: 0, T: 1, sel: -1, drag: null, raf: 0, stopAt: null,
      stage: el.querySelector(".sg-stage"), img: el.querySelector("img"), segs: el.querySelector(".sg-segs"),
      au: el.querySelector("audio"), ph: el.querySelector(".sg-ph"), hov: el.querySelector(".sg-hov"),
    };
    wire(e);
    return e;
  }

  const x2t = (e, x) => {
    const r = e.stage.getBoundingClientRect();
    return Math.max(0, Math.min(e.T, ((x - r.left) / r.width) * e.T));
  };
  const pct = (e, t) => `${(t / e.T) * 100}%`;

  function snap(e, t, skip, free) {
    if (free) return t;
    const m = [0, e.dur, e.len, e.au.currentTime || 0];
    S.forEach((s, i) => { if (!skip.includes(i)) m.push(s[0], s[1]); });
    const px = e.T / Math.max(1, e.stage.clientWidth);   // seconds per pixel
    let best = null, bd = 7 * px;
    for (const v of m) { const d = Math.abs(v - t); if (v >= 0 && d < bd) { bd = d; best = v; } }
    return best != null ? best : Math.round(t / SPL) * SPL;
  }

  function draw(e) {
    if (!e.el.isConnected) return;
    e.el.querySelector(".sg-imgwrap").style.width = pct(e, Math.min(e.dur || e.T, e.T));
    const pad = e.el.querySelector(".sg-pad");
    pad.style.display = e.dur && e.dur < e.T ? "" : "none";
    pad.style.left = pct(e, e.dur);
    pad.style.width = pct(e, Math.max(0, e.T - e.dur));
    const by = e.el.querySelector(".sg-beyond"), ln = e.el.querySelector(".sg-len");
    const showLen = e.len && e.len < e.T - 1e-3;
    by.style.display = ln.style.display = showLen ? "" : "none";
    by.style.left = ln.style.left = pct(e, e.len);
    by.style.width = pct(e, e.T - e.len);
    ln.querySelector("span").textContent = `Length ${fmt(e.len)}`;
    const lock = e.el.querySelector(".sg-lock");
    lock.textContent = e.locked ? `Length ${fmt(e.len)}` : "";
    lock.style.display = e.locked ? "" : "none";
    const sum = e.el.querySelector(".sg-sum");
    if (e.spans) {
      e.el.classList.toggle("has", S.length > 0);
      const html = S.map((s, i) => `<div class="sg-s${i === e.sel ? " sel" : ""}${e.len && s[0] >= e.len ? " past" : ""}"
        data-i="${i}" style="left:${pct(e, s[0])};width:${pct(e, s[1] - s[0])}">
        <i class="h l"></i><i class="h r"></i><span class="lab">${fmt(s[0])} – ${fmt(s[1])}</span>
        <button class="x" title="Remove (⌫)">×</button></div>`).join("");
      if (e.segs._h !== html) { e.segs.innerHTML = html; e.segs._h = html; }
      const end = e.len || e.T;
      const tot = S.reduce((a, s) => a + Math.max(0, Math.min(s[1], end) - Math.min(s[0], end)), 0);
      sum.textContent = S.length ? `${S.length} ${S.length === 1 ? "span" : "spans"} · ${fmt(tot)} regenerated`
        : "No spans: nothing is regenerated";
      e.el.querySelector(".sg-hint").textContent = S.length ? KEYS : EMPTY;
    } else {
      sum.textContent = "Whole clip guided at the Noise level";
    }
    const r = e.el.querySelector(".sg-ruler"), key = `${e.T}|${e.stage.clientWidth}`;
    if (r._k !== key) {
      r._k = key;
      const want = Math.max(2, Math.floor(e.stage.clientWidth / 70));
      const step = [0.5, 1, 2, 5, 10, 15, 30, 60, 120].find((v) => e.T / v <= want) || 240;
      let h = "";
      for (let t = 0; t <= e.T + 1e-6; t += step) h += `<span style="left:${pct(e, t)}">${fmt(t).replace(/\.0(s?)$/, "$1")}</span>`;
      r.innerHTML = h;
    }
  }

  // ── playback ──
  function tick(e) {
    const t = e.au.currentTime || 0;
    e.ph.style.left = pct(e, t);
    e.el.querySelector(".sg-time").textContent = `${fmt(t)} / ${fmt(e.dur || 0)}`;
    if (e.stopAt != null && t >= e.stopAt) { e.au.pause(); e.stopAt = null; }
    if (!e.au.paused) e.raf = requestAnimationFrame(() => tick(e));
  }
  function play(e, from, to) {
    if (from != null) e.au.currentTime = Math.min(from, Math.max(0, (e.dur || 0) - 0.05));
    e.stopAt = to != null ? to : null;
    e.au.play().catch(() => {});
  }

  // ── interaction ──
  function wire(e) {
    const { el, stage, au } = e;
    au.addEventListener("play", () => {           // one player at a time (studio.js registry + page)
      (window._sa3All || []).forEach((o) => { if (o !== au) try { o.pause(); } catch (x) { /* gone */ } });
      document.querySelectorAll("audio").forEach((o) => { if (o !== au) o.pause(); });
      el.classList.add("playing");
      cancelAnimationFrame(e.raf);
      tick(e);
    });
    au.addEventListener("pause", () => { el.classList.remove("playing"); tick(e); });
    au.addEventListener("loadedmetadata", () => { if (!e.dur && isFinite(au.duration)) { e.dur = au.duration; layout(e); } });
    el.querySelector(".sg-play").addEventListener("click", () => (au.paused ? play(e) : au.pause()));
    if (e.spans) {
      el.querySelector(".sg-clr").addEventListener("click", () => {
        if (!S.length) return;
        snapshot(); S = []; e.sel = -1; commit();
      });
      el.querySelector(".sg-inv").addEventListener("click", () => {
        const end = e.len || e.T, out = [];
        let c = 0;
        for (const s of S.slice().sort((a, b) => a[0] - b[0])) {
          if (s[0] > c + 1e-3) out.push([c, Math.min(s[0], end)]);
          c = Math.max(c, s[1]);
        }
        if (c < end - 1e-3) out.push([c, end]);
        snapshot(); S = out.filter((s) => s[1] - s[0] > 1e-3); e.sel = -1; commit();
      });
    }

    stage.addEventListener("pointermove", (ev) => {
      if (e.drag) return;
      const t = x2t(e, ev.clientX);
      e.hov.style.left = pct(e, t);
      e.hov.querySelector("span").textContent = fmt(t);
    });
    stage.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0) return;
      el.focus({ preventScroll: true });
      const sd = e.spans && ev.target.closest(".sg-s");
      if (sd && ev.target.closest(".x")) {
        snapshot(); S.splice(+sd.dataset.i, 1); e.sel = -1; commit();
        ev.preventDefault(); return;
      }
      const t = x2t(e, ev.clientX), before = ser();
      if (sd) {
        let i = +sd.dataset.i;
        if (ev.altKey) { S.push(S[i].slice()); i = S.length - 1; }    // duplicate, drag the copy
        const h = ev.target.closest(".h");
        e.sel = i;
        e.drag = { mode: h ? (h.classList.contains("l") ? "l" : "r") : "move", i, t0: t, s0: S[i].slice(), x0: ev.clientX, moved: !!ev.altKey, before };
      } else {
        e.drag = { mode: "new", i: -1, t0: snap(e, t, [], ev.shiftKey), x0: ev.clientX, moved: false, before };
      }
      stage.setPointerCapture(ev.pointerId);
      el.classList.add("dragging");
      draw(e);
      ev.preventDefault();
    });
    stage.addEventListener("pointermove", (ev) => {
      const d = e.drag;
      if (!d || !e.spans) return;
      if (!d.moved && Math.abs(ev.clientX - d.x0) < 4) return;
      d.moved = true;
      const free = ev.shiftKey;
      let t = x2t(e, ev.clientX);
      if (d.mode === "new") {
        if (d.i < 0) { S.push([d.t0, d.t0]); d.i = e.sel = S.length - 1; }
        t = snap(e, t, [d.i], free);
        S[d.i] = [Math.min(d.t0, t), Math.max(d.t0, t)];
      } else if (d.mode === "move") {
        const w = d.s0[1] - d.s0[0];
        let a = Math.max(0, Math.min(e.T - w, d.s0[0] + (t - d.t0)));
        const sa = snap(e, a, [d.i], free), sb = snap(e, a + w, [d.i], free);   // whichever edge is closer
        a = Math.abs(sa - a) <= Math.abs(sb - (a + w)) ? sa : sb - w;
        S[d.i] = [Math.max(0, a), Math.min(e.T, a + w)];
      } else {
        t = snap(e, t, [d.i], free);
        const s = S[d.i];
        if (d.mode === "l") s[0] = Math.max(0, Math.min(t, s[1] - SPL));
        else s[1] = Math.min(e.T, Math.max(t, s[0] + SPL));
      }
      const s = S[d.i];
      e.hov.style.left = pct(e, d.mode === "l" ? s[0] : s[1]);
      e.hov.querySelector("span").textContent = `${fmt(s[0])} – ${fmt(s[1])}`;
      draw(e);
    });
    const end = (ev) => {
      const d = e.drag;
      if (!d) return;
      e.drag = null;
      el.classList.remove("dragging");
      e.hov.querySelector("span").textContent = fmt(x2t(e, ev.clientX));
      try { stage.releasePointerCapture(ev.pointerId); } catch (x) { /* released */ }
      if (!d.moved) {
        if (d.mode === "new") {                     // plain click on empty space: seek there
          e.sel = -1;
          au.currentTime = Math.min(x2t(e, ev.clientX), e.dur || 0);
          tick(e);
          draw(e);
        }
        return;
      }
      if (d.mode === "new" && d.i >= 0 && S[d.i][1] - S[d.i][0] < SPL) { S.splice(d.i, 1); e.sel = -1; }
      if (ser() !== d.before) { undo.push(d.before); redo.length = 0; }
      commit();
    };
    stage.addEventListener("pointerup", end);
    stage.addEventListener("pointercancel", end);
    stage.addEventListener("dblclick", (ev) => {     // audition the span under the cursor (by time:
      if (!e.spans || ev.target.closest(".x")) return;   // the first click's redraw replaced the node)
      const t = x2t(e, ev.clientX);
      const hit = (i) => S[i] && S[i][0] <= t && t <= S[i][1];
      const i = hit(e.sel) ? e.sel : S.findIndex((s, j) => hit(j));
      if (i >= 0) play(e, S[i][0], S[i][1]);
    });
    el.addEventListener("keydown", (ev) => {
      const s = e.spans ? S[e.sel] : null, k = ev.key;
      if (k === " ") { au.paused ? (s ? play(e, s[0], s[1]) : play(e)) : au.pause(); ev.preventDefault(); return; }
      if (!e.spans) return;
      if ((ev.metaKey || ev.ctrlKey) && k.toLowerCase() === "z") {
        history(ev.shiftKey ? redo : undo, ev.shiftKey ? undo : redo); ev.preventDefault(); return;
      }
      if (k === "Escape") { e.sel = -1; draw(e); return; }
      if (!s) return;
      if (k === "Backspace" || k === "Delete") { snapshot(); S.splice(e.sel, 1); e.sel = -1; commit(); ev.preventDefault(); }
      else if (k === "ArrowLeft" || k === "ArrowRight") {
        const dt = (k === "ArrowLeft" ? -1 : 1) * (ev.shiftKey ? 1 : SPL), w = s[1] - s[0];
        snapshot();
        s[0] = Math.max(0, Math.min(e.T - w, s[0] + dt));
        s[1] = s[0] + w;
        commit();
        ev.preventDefault();
      }
    });
    new ResizeObserver(() => draw(e)).observe(stage);
  }

  function layout(e) {
    // inpaint: the timeline reaches Length (spans past the audio = continuation); a2a: the guide is
    // cropped / zero-padded to Length too, so show the same frame
    let T = Math.max(e.dur || 0, e.len || 0, 1);
    if (e.spans && e.dur) T = Math.max(T, Math.min(maxLen(), T + Math.max(8, 0.25 * e.dur)));   // room to continue
    e.T = T;
    draw(e);
  }

  // ── audio source + spectrogram ──
  function srcOf(blk) {
    const a = blk.querySelector("a[download][href]");
    if (a && /file=/.test(a.href)) return a.href;
    const au = [...blk.querySelectorAll("audio")].find((x) => !x.closest(".sa3seg") && /^https?:/.test(x.currentSrc || x.src || ""));
    return au ? au.currentSrc || au.src : null;
  }
  async function setSrc(e, url) {
    e.url = url;
    e.dur = 0;
    e.au.pause();
    e.img.removeAttribute("src");
    e.el.classList.add("loading");
    e.au.src = url;
    layout(e);
    try {
      const r = await fetch(`/sa3/inspec?u=${encodeURIComponent(url)}`);
      if (e.url !== url) return;
      if (!r.ok) throw new Error(`${r.status}`);
      e.dur = +r.headers.get("X-Sa3-Duration") || e.dur;
      if (e.img._blob) URL.revokeObjectURL(e.img._blob);
      e.img._blob = URL.createObjectURL(await r.blob());
      e.img.src = e.img._blob;
    } catch (x) {
      console.warn("[sa3] input spectrogram failed", x);
    } finally {
      if (e.url === url) { e.el.classList.remove("loading"); layout(e); }
    }
  }
  function lenBlock() {
    return [...document.querySelectorAll("#sa3-ctl .block")].find((b) => {
      const l = b.querySelector("label span, [data-testid='block-info']");
      return l && l.textContent.trim() === "Length";
    });
  }
  function lengthNow() {
    const blk = lenBlock();
    const v = blk && +(blk.querySelector("input[type=number]") || {}).value;
    return v > 0 ? v : 0;
  }
  function maxLen() {
    const r = lenBlock()?.querySelector("input[type=range]");
    return r && +r.max > 0 ? +r.max : 380;
  }
  function setLength(v) {                           // the slider steps in whole seconds: display only,
    const blk = lenBlock();                         // the server uses the exact length
    const n = blk && blk.querySelector("input[type=number]");
    if (!n) return;
    n.value = v;
    n.dispatchEvent(new Event("input", { bubbles: true }));
    n.dispatchEvent(new Event("change", { bubbles: true }));
    const r = blk.querySelector("input[type=range]");
    if (r) setTimeout(() => {                       // gradio only refreshes the track fill on user input
      const lo = +r.min || 0, hi = +r.max || 100;
      if (hi > lo) r.style.setProperty("--range_progress", ((+r.value - lo) / (hi - lo)) * 100 + "%");
    }, 150);
  }
  function target() {                               // = segments.py locked_length, in the browser
    const i = eds.inp, a = eds.a2a;
    if (i && i.url) return i.dur ? Math.max(i.dur, ...S.map((s) => s[1])) : null;
    if (a && a.url) return a.dur || null;
    return null;
  }

  function gate(both) {                             // one input mode at a time (segments.py BOTH)
    const g = document.getElementById("sa3-generate");
    if (g && both !== !!g.dataset.sa3both) {
      g.disabled = both;
      if (both) { g.dataset.sa3both = "1"; g.title = "Both inputs loaded: clear Audio → audio or Inpaint to generate"; }
      else { delete g.dataset.sa3both; g.removeAttribute("title"); }
    } else if (g && both && !g.disabled) g.disabled = true;   // gradio re-rendered it
    Object.values(eds).forEach((e) => e.el.classList.toggle("both", both));
  }

  setInterval(() => {
    if (Object.values(eds).some((e) => e.drag)) return;   // never rescale the timeline mid-drag
    const b = $box();
    if (b && b.value !== written) {                  // the server changed it (restore / Extend / session)
      written = b.value;
      load(b.value);
      Object.values(eds).forEach((e) => { e.sel = -1; });
    }
    const tgt = target();
    if (tgt) {
      const want = Math.min(maxLen(), Math.max(1, Math.round(tgt)));
      if (Math.abs(lengthNow() - want) >= 0.5) setLength(want);
    }
    const len = tgt || lengthNow();
    for (const role of Object.keys(ROLES)) {
      const blk = document.querySelector(`.block.sa3-seg-${role}`);
      if (!blk) continue;
      let e = eds[role];
      if (!e || e.blk !== blk || !e.el.isConnected) {
        if (e) e.au.pause();
        e = eds[role] = build(role, blk);
        e.len = len;
      }
      const url = srcOf(blk);
      blk.classList.toggle("sa3seg-on", !!url);
      if (url !== e.url) {
        if (url) setSrc(e, url);
        else { e.url = null; e.au.pause(); e.au.removeAttribute("src"); }
      }
      e.locked = !!tgt;
      if (len !== e.len) { e.len = len; layout(e); }
      else draw(e);
    }
    gate(!!(eds.inp && eds.inp.url && eds.a2a && eds.a2a.url));
  }, 400);
}
