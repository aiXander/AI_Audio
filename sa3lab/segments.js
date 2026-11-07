() => {
  // Dadaspec timeline on the Inpaint and Audio → audio inputs (server side + the why: segments.py).
  // Each input's audio shows as its dadaspec (GET /sa3/inspec) with its own playback: click to
  // seek, space plays / pauses. Audio → audio is a viewer (one Noise level for the whole clip).
  //
  // Inpaint draws ONE denoising-strength curve over the reference: 0 = keep the frame bit-exact,
  // 1 = regenerate it from noise, in between = soft re-synthesis (more freedom as it rises). The
  // curve is keyframes [t, u], linear between, flat past the ends; two keys at the same t are a
  // hard edge. Gestures write keys:
  //   Span mode   drag on empty space → a plateau at the brush strength (1 = the old inpaint span)
  //   Points mode click → a key at the cursor's height; drag keys to move them (time + strength)
  // A plateau (any flat stretch above 0) is a box: drag to move, edges to resize, top edge to set
  // its strength, ⌥-drag duplicates, double-click auditions, × / ⌫ deletes (the curve heals across
  // it). Keys: drag, double-click or ⌫ deletes; pulling one key of a hard edge sideways feathers it.
  // ⌘Z / ⇧⌘Z undo / redo, ←/→ nudge one latent frame (⇧: 1 s). Times snap to SA3's latent grid
  // (4096 samples, ~93 ms, the curve's real resolution) and magnet to other keys, the playhead and
  // the audio's end; strengths magnet to quarters (hold ⇧: no snapping). The timeline spans
  // max(audio, Length): strength past the audio's end regenerates over zero padding (a continuation).
  // Length follows the input (segments.py locked_length enforces it per job): the reference's
  // length, or the curve's last non-zero stretch when it reaches past it, else the a2a guide's
  // length. The Length slider is kept in sync. Both inputs loaded = Generate disabled.
  // The curve lives as JSON {"strength": [[t, u], …]} in the CSS-hidden #sa3-seg-data box, written
  // on every edit and read back when the server changes it (restore, Extend, the saved session).
  // No keys = the reference is unused (today's behaviour with no spans).
  const SPL = 4096 / 44100;                         // one latent frame, seconds
  const ROLES = { inp: { spans: true }, a2a: { spans: false } };
  const EMPTY = "Drag across the spectrogram to regenerate a stretch (strength 1), or lower the strength first for a soft re-synthesis. Points mode: click to place strength keys, linear between. No curve = the reference is unused.";
  const KEYS = "Box: drag to move, edges resize, top edge sets strength · ⌥-drag duplicates · double-click plays · ⌫ deletes · keys: drag, double-click deletes · ⌘Z undo · ⇧ no snapping";
  // both hints live in the foot's "?" tooltip (title attribute), so they must stay free of double quotes
  let K = [];                                       // strength keys [t, u], sorted by t (stable)
  let brush = 1;                                    // strength for new plateaus
  let written = null;                               // box value we last wrote / adopted
  const undo = [], redo = [];
  const eds = {};                                   // role → editor state

  const $box = () => document.querySelector("#sa3-seg-data textarea, #sa3-seg-data input");
  const r3 = (v) => Math.round(v * 1000) / 1000;
  const r4 = (v) => Math.round(v * 10000) / 10000;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  let extra = {};                                   // the adopted box's other keys (cond, mask, kept): an edit keeps them,
  const ser = () => JSON.stringify({ ...extra, strength: K.map((k) => [r3(k[0]), r4(k[1])]) });   // so a restored take stays on its anchors
  const fmt = (t) => {
    const m = Math.floor(t / 60), s = t - m * 60;
    return m ? `${m}:${s.toFixed(1).padStart(4, "0")}` : `${s.toFixed(1)}s`;
  };
  const f2 = (u) => u.toFixed(2);
  function ucol(u) {                                // teal (soft) → violet → pink (regenerate)
    const st = [[62, 230, 196], [155, 140, 255], [255, 111, 181]];
    const x = clamp(u, 0, 1) * 2, i = Math.min(1, Math.floor(x)), f = x - i;
    const c = st[i].map((v, j) => Math.round(v + (st[i + 1][j] - v) * f));
    return `rgb(${c.join(",")})`;
  }

  // ── the curve (same rules as segments.py parse_plan / frame_strength / plan_end) ──
  function at(t) {                                  // value at t, from the right (post-jump)
    if (!K.length) return 0;
    let j = K.findIndex((k) => k[0] > t);
    if (j < 0) return K[K.length - 1][1];
    if (j === 0) return K[0][1];
    const a = K[j - 1], b = K[j];
    return a[1] + ((t - a[0]) / (b[0] - a[0])) * (b[1] - a[1]);
  }
  function before(L, t) {                           // value just before t (pre-jump)
    if (!L.length) return 0;
    let i = -1;
    for (let n = 0; n < L.length; n++) if (L[n][0] < t) i = n;
    if (i < 0) return L[0][1];
    const a = L[i], b = L[i + 1];
    if (!b) return a[1];
    return a[1] + ((t - a[0]) / (b[0] - a[0])) * (b[1] - a[1]);
  }
  function after(L, t) {                            // value just after t (post-jump)
    if (!L.length) return 0;
    const j = L.findIndex((k) => k[0] > t);
    if (j < 0) return L[L.length - 1][1];
    if (j === 0) return L[0][1];
    const a = L[j - 1], b = L[j];
    return a[1] + ((t - a[0]) / (b[0] - a[0])) * (b[1] - a[1]);
  }
  function setRange(L, a, b, u) {                   // [a, b] flat at u, hard edges, outside unchanged
    const fl = before(L, a), fr = after(L, b);
    return [...L.filter((k) => k[0] < a), [a, fl], [a, u], [b, u], [b, fr], ...L.filter((k) => k[0] > b)];
  }
  function heal(L, a, b) {                          // drop [a, b]; the curve rejoins linearly
    const fl = before(L, a), fr = after(L, b);
    return [...L.filter((k) => k[0] < a), [a, fl], [b, fr], ...L.filter((k) => k[0] > b)];
  }
  function norm(L) {                                // drop redundant keys; all-zero = no curve
    L = L.map((k) => [r3(k[0]), r4(clamp(k[1], 0, 1))]);
    let out = [];
    for (const k of L) {                            // a run of keys at one t: keep first + last
      const n = out.length;
      if (n >= 2 && out[n - 1][0] === k[0] && out[n - 2][0] === k[0]) out[n - 1] = k;
      else out.push(k);
    }
    out = out.filter((k, i) => !(i > 0 && k[0] === out[i - 1][0] && k[1] === out[i - 1][1]));
    let changed = true;
    while (changed) {                               // flat runs: drop the middle / redundant ends
      changed = false;
      for (let i = 0; i < out.length; i++) {
        const p = out[i - 1], k = out[i], n = out[i + 1];
        const flatL = !p || (p[1] === k[1] && p[0] !== k[0]), flatR = !n || (n[1] === k[1] && n[0] !== k[0]);
        if ((p || n) && flatL && flatR && !(p && p[0] === k[0]) && !(n && n[0] === k[0])) {
          out.splice(i, 1); changed = true; break;
        }
      }
    }
    return out.every((k) => k[1] <= 0) ? [] : out;
  }
  function end() {                                  // segments.py plan_end
    let e = 0, prev = 0;
    for (const [t, u] of K) { if (u > 0 || prev > 0) e = Math.max(e, t); prev = u; }
    return e;
  }
  function boxes() {                                // flat stretches above 0: the draggable plateaus
    const out = [];
    for (let i = 0; i + 1 < K.length; i++) {
      const a = K[i], b = K[i + 1];
      if (a[1] === b[1] && a[1] > 0 && b[0] - a[0] > 1e-4) out.push({ i, a: a[0], b: b[0], u: a[1] });
    }
    return out;
  }
  function spansToKeys(spans) {                     // the old {"inpaint": [[s0, s1]]} contract
    let L = [];
    for (const s of spans.slice().sort((x, y) => x[0] - y[0])) L = setRange(L, +s[0], +s[1], 1);
    return norm(L);
  }

  function commit() {                               // after an edit: push to the box, redraw
    K = norm(K);
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
    try {
      const d = JSON.parse(v || "{}");
      const { strength, inpaint, ...rest } = d;
      extra = rest;
      if (Array.isArray(d.strength)) K = d.strength.map((k) => [+k[0], +k[1]]);
      else K = spansToKeys(d.inpaint || []);
      K.sort((a, b) => a[0] - b[0]);
    } catch (e) { /* keep */ }
  }
  function history(from, to) {
    if (!from.length) return;
    to.push(ser());
    load(from.pop());
    Object.values(eds).forEach((e) => { e.sel = null; });
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
        ${spans ? "" : `<span class="sg-time">0.0s</span><span class="sg-sum"></span>`}
        <span class="sg-warn">Both inputs loaded: clear one (Audio → audio or Inpaint) to generate</span>
        <span class="sg-lock" title="Length follows the input audio (and any strength past its end), so every render's length matches its input"></span>
        ${spans ? `<span class="sg-modes" title="Span: drag draws a plateau at the strength · Points: click places a key">
          <button class="sg-mode on" data-m="span">Span</button><button class="sg-mode" data-m="point">Points</button></span>
        <label class="sg-ubox" title="Denoising strength: 0 keeps the frame, 1 regenerates it from noise. Sets new plateaus, or the selected box / key">
          <span>Strength</span><input class="sg-u" type="range" min="0" max="1" step="0.01" value="1"><b class="sg-uv">1.00</b></label>
        <button class="sg-btn sg-inv" title="Invert: strength u becomes 1 − u (kept and regenerated swap)">Invert</button>
        <button class="sg-btn sg-clr" title="Remove the curve">Clear</button>
        <button class="sg-btn sg-full" title="Full screen (F) · Esc exits"><svg viewBox="0 0 16 16"><path class="o" d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4"/><path class="i" d="M6 2v4H2M14 6h-4V2M10 14v-4h4M2 10h4v4"/></svg><span>Exit full screen</span></button>` : ""}
      </div>
      <div class="sg-stage">
        <div class="sg-imgwrap"><img alt="spectrogram" draggable="false"></div>
        <div class="sg-pad" title="Past the audio's end: zero-padded"></div>
        <div class="sg-beyond"></div>
        <div class="sg-len"><span></span></div>
        ${spans ? `<svg class="sg-curve" preserveAspectRatio="none"><defs><linearGradient id="sg-ug-${role}" x1="0" y1="1" x2="0" y2="0" gradientUnits="objectBoundingBox">
          <stop offset="0" stop-color="${ucol(0)}"/><stop offset=".5" stop-color="${ucol(0.5)}"/><stop offset="1" stop-color="${ucol(1)}"/></linearGradient></defs>
          <path class="fill"/><path class="line"/></svg>` : ""}
        <div class="sg-segs"></div>
        <div class="sg-keys"></div>
        <div class="sg-hov"><span></span></div>
        <div class="sg-ph"></div>
        <div class="sg-load"></div>
      </div>
      ${spans ? `<button class="sg-x" title="Exit full screen (Esc)">✕</button>` : ""}
      <div class="sg-ruler"></div>
      ${spans ? `<div class="sg-foot"><span class="sg-sum"></span><span class="sg-time">0.0s</span><button class="sg-help" tabindex="-1" title="${EMPTY}\n\n${KEYS}">?</button></div>` : ""}
      <audio preload="auto"></audio>`;
    blk.appendChild(el);
    const e = {
      role, spans, el, blk, url: null, dur: 0, len: 0, T: 1, sel: null, drag: null, raf: 0, stopAt: null, mode: "span",
      stage: el.querySelector(".sg-stage"), img: el.querySelector("img"), segs: el.querySelector(".sg-segs"),
      keys: el.querySelector(".sg-keys"), svg: el.querySelector(".sg-curve"),
      au: el.querySelector("audio"), ph: el.querySelector(".sg-ph"), hov: el.querySelector(".sg-hov"),
    };
    wire(e);
    return e;
  }

  const x2t = (e, x) => {
    const r = e.stage.getBoundingClientRect();
    return Math.max(0, Math.min(e.T, ((x - r.left) / r.width) * e.T));
  };
  const y2u = (e, y) => {
    const r = e.stage.getBoundingClientRect();
    return clamp(1 - (y - r.top) / r.height, 0, 1);
  };
  const pct = (e, t) => `${(t / e.T) * 100}%`;

  function snap(e, t, skipT, free) {
    if (free) return t;
    const m = [0, e.dur, e.len, e.au.currentTime || 0];
    K.forEach((k) => { if (!skipT.includes(k[0])) m.push(k[0]); });
    const px = e.T / Math.max(1, e.stage.clientWidth);   // seconds per pixel
    let best = null, bd = 7 * px;
    for (const v of m) { const d = Math.abs(v - t); if (v >= 0 && d < bd) { bd = d; best = v; } }
    return best != null ? best : Math.round(t / SPL) * SPL;
  }
  function snapU(e, u, free) {
    if (free) return u;
    const tol = 6 / Math.max(1, e.stage.clientHeight);
    for (const v of [0, 0.25, 0.5, 0.75, 1, brush]) if (Math.abs(u - v) < tol) return v;
    return Math.round(u * 100) / 100;
  }

  function selBox() {                               // the selected plateau, re-found by its start
    const s = Object.values(eds).map((x) => x.sel).find((x) => x && x.kind === "p");
    if (!s) return null;
    return boxes().find((b) => Math.abs(b.a - s.a) < 1e-3) || null;
  }
  function syncSlider(e) {
    const inp = e.el.querySelector(".sg-u");
    if (!inp || document.activeElement === inp) return;
    let u = brush;
    if (e.sel && e.sel.kind === "k" && K[e.sel.j]) u = K[e.sel.j][1];
    else if (e.sel && e.sel.kind === "p") { const b = selBox(); if (b) u = b.u; }
    inp.value = u;
    e.el.querySelector(".sg-uv").textContent = f2(u);
    inp.style.setProperty("--uc", ucol(u));
  }

  function drawCurve(e) {
    if (!e.svg) return;
    const key = `${ser()}|${e.T}|${e.stage.clientWidth}`;
    if (e.svg._k === key) return;
    e.svg._k = key;
    const W = Math.max(1, e.stage.clientWidth), H = Math.max(1, e.stage.clientHeight);
    e.svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const fill = e.svg.querySelector(".fill"), line = e.svg.querySelector(".line");
    fill.setAttribute("fill", `url(#sg-ug-${e.role})`);
    line.setAttribute("stroke", `url(#sg-ug-${e.role})`);
    if (!K.length) { fill.setAttribute("d", ""); line.setAttribute("d", ""); return; }
    const fps = 1 / SPL, n = Math.ceil(e.T * fps), sx = W / e.T;
    let d = "";
    for (let f = 0; f < n; f++) {                   // per latent frame, exactly what the sampler gets
      const u = at((f + 0.5) / fps), y = (1 - u) * H;
      d += `${f ? "L" : "M"}${(f * SPL * sx).toFixed(1)} ${y.toFixed(1)}L${(Math.min(e.T, (f + 1) * SPL) * sx).toFixed(1)} ${y.toFixed(1)}`;
    }
    line.setAttribute("d", d);
    fill.setAttribute("d", `M0 ${H}` + d.replace(/^M/, "L") + `L${W} ${H}Z`);
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
      e.el.classList.toggle("has", K.length > 0);
      e.el.classList.toggle("pmode", e.mode === "point");
      drawCurve(e);
      const sb = e.sel && e.sel.kind === "p" ? selBox() : null;
      const html = boxes().map((b) => `<div class="sg-s${sb && sb.i === b.i ? " sel" : ""}${e.len && b.a >= e.len ? " past" : ""}"
        data-i="${b.i}" style="left:${pct(e, b.a)};width:${pct(e, b.b - b.a)};--c:${ucol(b.u)};--u:${b.u}">
        <i class="h l"></i><i class="h r"></i><i class="h t"></i><span class="lab">${b.u < 1 ? `${f2(b.u)} · ` : ""}${fmt(b.a)} – ${fmt(b.b)}</span>
        <button class="x" title="Remove (⌫)">×</button></div>`).join("");
      if (e.segs._h !== html) { e.segs.innerHTML = html; e.segs._h = html; }
      const edge = (j) => (K[j - 1] && K[j - 1][0] === K[j][0]) || (K[j + 1] && K[j + 1][0] === K[j][0]);
      const kh = K.map((k, j) => `<i class="sg-k${e.sel && e.sel.kind === "k" && e.sel.j === j ? " sel" : ""}${edge(j) ? " edge" : ""}" data-j="${j}"
        style="left:${pct(e, k[0])};bottom:${k[1] * 100}%;--c:${ucol(k[1])}" title="${f2(k[1])} @ ${fmt(k[0])}"></i>`).join("");
      if (e.keys._h !== kh) { e.keys.innerHTML = kh; e.keys._h = kh; }
      const L = e.len || e.T, fps = 1 / SPL, n = Math.ceil(L * fps);
      let full = 0, soft = 0;
      for (let f = 0; f < n; f++) { const u = at((f + 0.5) / fps); if (u >= 1) full++; else if (u > 0) soft++; }
      sum.textContent = K.length ? [full && `${fmt(full * SPL)} regenerated`, soft && `${fmt(soft * SPL)} soft`,
        `${fmt(Math.max(0, n - full - soft) * SPL)} kept`].filter(Boolean).join(" · ")
        : "No curve: the reference is unused · drag across the spectrogram to draw one";
      syncSlider(e);
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
  function seek(e, t) { e.au.currentTime = Math.min(t, e.dur || 0); tick(e); }

  // ── interaction ──
  function setStrength(e, u) {                      // slider: the selected box / key, else the brush
    u = clamp(u, 0, 1);
    if (e.sel && e.sel.kind === "k" && K[e.sel.j]) { K[e.sel.j][1] = u; commit(); return; }
    const b = e.sel && e.sel.kind === "p" ? selBox() : null;
    if (b) { K = setRange(K, b.a, b.b, u); commit(); if (u <= 0) e.sel = null; return; }
    brush = u;
    Object.values(eds).forEach(draw);
  }

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
    el.querySelector(".sg-ruler").addEventListener("pointerdown", (ev) => seek(e, x2t(e, ev.clientX)));
    if (e.spans) {
      el.querySelector(".sg-clr").addEventListener("click", () => {
        if (!K.length && !Object.keys(extra).length) return;
        snapshot(); K = []; extra = {}; e.sel = null; commit();   // also drops a restored take's anchors
      });
      el.querySelector(".sg-full").addEventListener("click", () => full(e, !e.full));
      el.querySelector(".sg-x").addEventListener("click", () => full(e, false));
      el.querySelector(".sg-inv").addEventListener("click", () => {
        snapshot();
        K = K.length ? K.map((k) => [k[0], 1 - k[1]]) : [[0, 1]];
        e.sel = null; commit();
      });
      el.querySelectorAll(".sg-mode").forEach((b) => b.addEventListener("click", () => {
        e.mode = b.dataset.m;
        el.querySelectorAll(".sg-mode").forEach((x) => x.classList.toggle("on", x === b));
        draw(e);
      }));
      const inp = el.querySelector(".sg-u");
      let pre = null;
      inp.addEventListener("pointerdown", () => { pre = ser(); });
      inp.addEventListener("input", () => {
        el.querySelector(".sg-uv").textContent = f2(+inp.value);
        inp.style.setProperty("--uc", ucol(+inp.value));
        setStrength(e, +inp.value);
      });
      inp.addEventListener("change", () => {
        if (pre != null && ser() !== pre) { undo.push(pre); redo.length = 0; }
        pre = null;
      });
    }

    stage.addEventListener("pointermove", (ev) => {
      if (e.drag) return;
      const t = x2t(e, ev.clientX);
      e.hov.style.left = pct(e, t);
      e.hov.querySelector("span").textContent = e.spans && e.mode === "point"
        ? `${fmt(t)} · ${f2(y2u(e, ev.clientY))}` : fmt(t);
    });
    stage.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0) return;
      el.focus({ preventScroll: true });
      const before0 = ser(), t = x2t(e, ev.clientX);
      const kd = e.spans && ev.target.closest(".sg-k");
      const sd = e.spans && !kd && ev.target.closest(".sg-s");
      if (sd && ev.target.closest(".x")) {
        const b = boxes().find((x) => x.i === +sd.dataset.i);
        if (b) { snapshot(); K = heal(K, b.a, b.b); e.sel = null; commit(); }
        ev.preventDefault(); return;
      }
      if (kd) {
        const j = +kd.dataset.j;
        e.sel = { kind: "k", j };
        e.drag = { mode: "key", j, x0: ev.clientX, y0: ev.clientY, moved: false, before: before0 };
      } else if (sd) {
        const b = boxes().find((x) => x.i === +sd.dataset.i);
        if (!b) return;
        const h = ev.target.closest(".h");
        const mode = h ? (h.classList.contains("l") ? "l" : h.classList.contains("r") ? "r" : "u") : "move";
        e.sel = { kind: "p", a: b.a };
        // the box comes off the curve (healed) and is re-laid on every move; ⌥ leaves the original
        const base = ev.altKey && mode === "move" ? K.map((k) => k.slice()) : heal(K, b.a, b.b);
        e.drag = { mode, b, base, t0: t, x0: ev.clientX, y0: ev.clientY, moved: false, before: before0 };
      } else if (e.spans && e.mode === "point") {
        const u = snapU(e, y2u(e, ev.clientY), ev.shiftKey), tt = snap(e, t, [], ev.shiftKey);
        snapshot();
        K.push([tt, u]); K.sort((a, b) => a[0] - b[0]);
        const j = K.findIndex((k) => k[0] === tt && k[1] === u);
        e.sel = { kind: "k", j };
        e.drag = { mode: "key", j, x0: ev.clientX, y0: ev.clientY, moved: true, before: ser(), added: true };
        commitLight(e);
      } else {
        e.drag = { mode: "new", base: K.map((k) => k.slice()), t0: snap(e, t, [], ev.shiftKey), x0: ev.clientX, moved: false, before: before0 };
      }
      stage.setPointerCapture(ev.pointerId);
      el.classList.add("dragging");
      draw(e);
      ev.preventDefault();
    });
    function commitLight(x) {                       // mid-drag: redraw without normalising the keys
      Object.values(eds).forEach(draw);
    }
    stage.addEventListener("pointermove", (ev) => {
      const d = e.drag;
      if (!d || !e.spans) return;
      if (!d.moved && Math.abs(ev.clientX - d.x0) < 4 && Math.abs(ev.clientY - (d.y0 ?? ev.clientY)) < 4) return;
      d.moved = true;
      const free = ev.shiftKey;
      let t = x2t(e, ev.clientX), lab = "";
      if (d.mode === "new") {
        t = snap(e, t, [], free);
        const a = Math.min(d.t0, t), b = Math.max(d.t0, t);
        K = b - a >= SPL ? setRange(d.base, a, b, brush) : d.base.map((k) => k.slice());
        e.sel = { kind: "p", a: r3(a) };
        lab = `${brush < 1 ? f2(brush) + " · " : ""}${fmt(a)} – ${fmt(b)}`;
      } else if (d.mode === "key") {
        const k = K[d.j];
        if (!k) return;
        const lo = d.j > 0 ? K[d.j - 1][0] : 0, hi = d.j + 1 < K.length ? K[d.j + 1][0] : e.T;
        k[0] = clamp(snap(e, t, [k[0]], free), lo, hi);
        k[1] = snapU(e, y2u(e, ev.clientY), free);
        lab = `${f2(k[1])} @ ${fmt(k[0])}`;
      } else {
        const { b, base } = d;
        let a = b.a, z = b.b, u = b.u;
        if (d.mode === "move") {
          const w = b.b - b.a;
          let s = clamp(b.a + (t - d.t0), 0, e.T - w);
          const sa = snap(e, s, [b.a, b.b], free), sb = snap(e, s + w, [b.a, b.b], free);
          s = Math.abs(sa - s) <= Math.abs(sb - (s + w)) ? sa : sb - w;
          a = Math.max(0, s); z = Math.min(e.T, s + w);
        } else if (d.mode === "l") a = clamp(snap(e, t, [b.a], free), 0, b.b - SPL);
        else if (d.mode === "r") z = clamp(snap(e, t, [b.b], free), b.a + SPL, e.T);
        else u = snapU(e, y2u(e, ev.clientY), free);
        K = u > 0 ? setRange(base, a, z, u) : base.map((k) => k.slice());
        e.sel = { kind: "p", a: r3(a) };
        lab = `${f2(u)} · ${fmt(a)} – ${fmt(z)}`;
      }
      e.hov.style.left = pct(e, t);
      e.hov.querySelector("span").textContent = lab;
      commitLight(e);
    });
    const endDrag = (ev) => {
      const d = e.drag;
      if (!d) return;
      e.drag = null;
      el.classList.remove("dragging");
      e.hov.querySelector("span").textContent = fmt(x2t(e, ev.clientX));
      try { stage.releasePointerCapture(ev.pointerId); } catch (x) { /* released */ }
      if (!d.moved) {
        if (d.mode === "new") { e.sel = null; seek(e, x2t(e, ev.clientX)); draw(e); }
        return;
      }
      const keyAt = d.mode === "key" && K[d.j] ? K[d.j].slice() : null;
      if (!d.added && ser() !== d.before) { undo.push(d.before); redo.length = 0; }   // an added key: snapshot() ran
      commit();
      if (keyAt) {                                  // keep the dragged key selected after norm()
        const j = K.findIndex((k) => k[0] === r3(keyAt[0]) && k[1] === r4(keyAt[1]));
        e.sel = j >= 0 ? { kind: "k", j } : null;
        draw(e);
      }
    };
    stage.addEventListener("pointerup", endDrag);
    stage.addEventListener("pointercancel", endDrag);
    stage.addEventListener("dblclick", (ev) => {
      if (!e.spans || ev.target.closest(".x")) return;
      const kd = ev.target.closest(".sg-k");
      if (kd) { snapshot(); K.splice(+kd.dataset.j, 1); e.sel = null; commit(); return; }
      const t = x2t(e, ev.clientX), b = boxes().find((x) => x.a <= t && t <= x.b);   // audition it
      if (b) play(e, b.a, b.b);
    });
    el.addEventListener("keydown", (ev) => {
      const k = ev.key, b = e.sel && e.sel.kind === "p" ? selBox() : null;
      const kk = e.sel && e.sel.kind === "k" ? K[e.sel.j] : null;
      if (k === " ") { au.paused ? (b ? play(e, b.a, b.b) : play(e)) : au.pause(); ev.preventDefault(); return; }
      if (!e.spans) return;
      if ((ev.metaKey || ev.ctrlKey) && k.toLowerCase() === "z") {
        history(ev.shiftKey ? redo : undo, ev.shiftKey ? undo : redo); ev.preventDefault(); return;
      }
      if (k === "Escape") { e.sel = null; draw(e); return; }   // full screen: the window-level handler below
      if ((k === "f" || k === "F") && !ev.metaKey && !ev.ctrlKey && !ev.altKey) { full(e, !e.full); ev.preventDefault(); return; }
      if (!b && !kk) return;
      if (k === "Backspace" || k === "Delete") {
        snapshot();
        if (b) K = heal(K, b.a, b.b); else K.splice(e.sel.j, 1);
        e.sel = null; commit(); ev.preventDefault();
      } else if (k === "ArrowLeft" || k === "ArrowRight") {
        const dt = (k === "ArrowLeft" ? -1 : 1) * (ev.shiftKey ? 1 : SPL);
        snapshot();
        if (b) {
          const w = b.b - b.a, a = clamp(b.a + dt, 0, e.T - w);
          K = setRange(heal(K, b.a, b.b), a, a + w, b.u);
          e.sel = { kind: "p", a: r3(a) };
        } else {
          const j = e.sel.j, lo = j > 0 ? K[j - 1][0] : 0, hi = j + 1 < K.length ? K[j + 1][0] : e.T;
          kk[0] = clamp(kk[0] + dt, lo, hi);
        }
        commit();
        ev.preventDefault();
      }
    });
    new ResizeObserver(() => draw(e)).observe(stage);
  }

  // Full screen: the editor moves to <body> (gradio's ancestors may transform or clip a fixed child) and a
  // comment holds its slot in the block; the stage flexes to fill the viewport, the ResizeObserver redraws.
  // Esc leaves full screen wherever focus is (a gradio control, the page): window capture, before anyone else
  addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    const e = Object.values(eds).find((x) => x && x.full);
    if (!e) return;
    if (e.sel) { e.sel = null; draw(e); } else full(e, false);
    ev.preventDefault(); ev.stopImmediatePropagation();
  }, true);
  function full(e, on) {
    if (!!e.full === on) return;
    e.full = on;
    if (on) { e.home = document.createComment("sa3seg"); e.el.replaceWith(e.home); document.body.appendChild(e.el); }
    else if (e.home) { e.home.replaceWith(e.el); e.home = null; }
    e.el.classList.toggle("full", on);
    document.documentElement.classList.toggle("sa3seg-full", on);
    e.el.focus({ preventScroll: true });
  }

  function layout(e) {
    // inpaint: the timeline reaches Length (strength past the audio = continuation); a2a: the guide
    // is cropped / zero-padded to Length too, so show the same frame
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
    if (i && i.url) return i.dur ? Math.max(i.dur, end()) : null;
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
      Object.values(eds).forEach((e) => { e.sel = null; });
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
        if (e) { e.au.pause(); if (e.full) { e.el.remove(); document.documentElement.classList.remove("sa3seg-full"); } }
        e = eds[role] = build(role, blk);
        e.len = len;
      }
      const url = srcOf(blk);
      blk.classList.toggle("sa3seg-on", !!url);
      // the accordion around it lights up (segments.css) even when collapsed: a forgotten reference
      // still steers every render, so it must be visible at a glance
      const acc = blk.closest(".block:has(> .label-wrap)");
      if (acc) acc.classList.toggle("sa3-has-audio", !!url);
      if (url !== e.url) {
        if (url) setSrc(e, url);
        else { e.url = null; e.au.pause(); e.au.removeAttribute("src"); full(e, false); }
      }
      e.locked = !!tgt;
      if (len !== e.len) { e.len = len; layout(e); }
      else draw(e);
    }
    gate(!!(eds.inp && eds.inp.url && eds.a2a && eds.a2a.url));
  }, 400);
}
