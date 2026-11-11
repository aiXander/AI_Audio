() => {
  // DJ view (server side + the why: djview.py). A "DJ" button on every clip's label strip opens
  // the clip full screen, laid out like a Pioneer CDJ: the zoomed dadaspec scrolls right to left
  // under a fixed centre playhead, the whole-track overview sits below it (played part greyed,
  // minute ticks, the zoom window boxed), the time readout shows REMAIN by default (click: TIME).
  // One slider sets how many seconds are in view: 2-32 s, 8 s by default (≈ 4 bars at 128 BPM,
  // a CDJ's default zoom), saved in localStorage. It plays the card's own <audio>, so the studio's
  // play stamp, transport bar and playhead all stay in sync.
  // Keys: space play/pause, ← / → 1 s (shift: 8 s), Esc closes. Drag the big view to scrub,
  // click it to play/pause, wheel to zoom; click / drag the overview to seek.
  const KEY = "sa3-djview";
  const WMIN = 2, WMAX = 32, WDEF = 8, PH = 0.5;     // seconds in view; playhead at the centre
  const TILE = 4096;                                  // a sharp image is ~15k px: split under the GPU texture limit
  let pref = { win: WDEF, remain: true };
  try { pref = { ...pref, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; } catch (e) { /* defaults */ }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(pref)); } catch (e) { /* private window */ } };
  const ICON = '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="6.2" stroke="currentColor" stroke-width="1.4" fill="none"/><circle cx="8" cy="8" r="1.6" fill="currentColor"/><path d="M8 1.8v2.4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>';
  const PLAY = '<svg viewBox="0 0 16 16"><path d="M4.5 2.8v10.4L13 8z" fill="currentColor"/></svg>';
  const PAUSE = '<svg viewBox="0 0 16 16"><path d="M4 2.8h2.8v10.4H4zM9.2 2.8H12v10.4H9.2z" fill="currentColor"/></svg>';
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const refOf = (a) => {                              // same as highlights_link.js: {id} from a gradio file path, else {name}
    if (a.dataset.name) return { name: a.dataset.name };
    const src = decodeURIComponent(a.getAttribute("src") || "");
    const i = src.indexOf("/outputs/");
    if (src.includes("file=") && i >= 0) return { id: src.slice(i + 9).split("?")[0] };
    return { name: src.split("/").pop().split("?")[0] };
  };
  const wToV = (w) => Math.round(1000 * Math.log(w / WMIN) / Math.log(WMAX / WMIN));   // log slider
  const vToW = (v) => WMIN * Math.pow(WMAX / WMIN, v / 1000);
  const fmt = (t, tenths = true) => {
    t = Math.max(0, t);
    const m = Math.floor(t / 60), s = t - m * 60;
    return `${String(m).padStart(2, "0")}:${tenths ? s.toFixed(1).padStart(4, "0") : String(Math.floor(s)).padStart(2, "0")}`;
  };

  let el = null, main, ov, a = null, tiles = [], ovImg = null, raf = 0, clock = { t: 0, at: 0 };

  async function tilesOf(img) {
    const w = img.naturalWidth, h = img.naturalHeight, out = [];
    for (let x = 0; x < w; x += TILE) {
      const sw = Math.min(TILE, w - x);
      out.push({ x, w: sw, h, bmp: window.createImageBitmap ? await createImageBitmap(img, x, 0, sw, h) : img, sx: window.createImageBitmap ? 0 : x });
    }
    return { cols: w, list: out };
  }
  function build() {
    el = document.createElement("div");
    el.id = "sa3-dj";
    el.innerHTML = `<div class="top"><div class="ttl"></div><div class="st"></div><button class="x" title="Close (Esc)">×</button></div>
      <div class="mainw"><canvas class="main"></canvas></div>
      <canvas class="ov" title="Click or drag to seek"></canvas>
      <div class="bar">
        <button class="pp" title="Play / pause (space)">${PLAY}</button>
        <div class="tm" title="Time mode: click to switch between remaining and elapsed"><i></i><b></b></div>
        <div class="bpm"></div>
        <label class="zm"><span>View</span><input type="range" min="0" max="1000" step="1"><b></b></label>
        <div class="hint">space play · drag scrub · wheel zoom · esc close</div>
      </div>`;
    document.body.appendChild(el);
    main = el.querySelector(".main"); ov = el.querySelector(".ov");
    const rng = el.querySelector(".zm input");
    rng.addEventListener("input", () => { pref.win = vToW(+rng.value); paintZoom(); save(); });
    el.querySelector(".x").addEventListener("click", close);
    el.querySelector(".pp").addEventListener("click", toggle);
    el.querySelector(".tm").addEventListener("click", () => { pref.remain = !pref.remain; save(); });
    el.addEventListener("mousedown", (e) => e.stopPropagation());
    main.addEventListener("wheel", (e) => {
      e.preventDefault();
      pref.win = Math.max(WMIN, Math.min(WMAX, pref.win * Math.exp(e.deltaY * 0.002)));
      paintZoom(); save();
    }, { passive: false });
    let drag = null;                                  // big view: grab and scrub like a platter
    main.addEventListener("pointerdown", (e) => { if (e.button === 0) { drag = { x: e.clientX, t: now(), moved: false, id: e.pointerId }; } });
    main.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x;
      if (!drag.moved && Math.abs(dx) < 4) return;
      if (!drag.moved) { drag.moved = true; try { main.setPointerCapture(drag.id); } catch (er) { /* gone */ } }
      seek(drag.t - (dx / main.clientWidth) * pref.win);
    });
    const up = () => { if (drag && !drag.moved) toggle(); drag = null; };
    main.addEventListener("pointerup", up);
    main.addEventListener("pointercancel", () => { drag = null; });
    let od = false;                                   // overview: seek where you click
    const oseek = (e) => { const b = ov.getBoundingClientRect(); seek(((e.clientX - b.left) / b.width) * dur()); };
    ov.addEventListener("pointerdown", (e) => { od = true; ov.setPointerCapture(e.pointerId); oseek(e); });
    ov.addEventListener("pointermove", (e) => { if (od) oseek(e); });
    ov.addEventListener("pointerup", () => { od = false; });
    document.addEventListener("fullscreenchange", () => { if (el.classList.contains("on") && !document.fullscreenElement && el._fs) close(); });
    document.addEventListener("keydown", (e) => {
      if (!el.classList.contains("on")) return;
      const k = e.key;
      if (k === "Escape") close();
      else if (k === " ") toggle();
      else if (k === "ArrowLeft" || k === "ArrowRight") seek(now() + (k === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 8 : 1));
      else return;
      e.preventDefault(); e.stopImmediatePropagation();
    }, true);
  }
  const dur = () => (a && isFinite(a.duration) && a.duration) || 0;
  // a smooth clock: currentTime advances in audio-callback steps, so extrapolate between them
  function now() {
    if (!a) return 0;
    const ct = a.currentTime;
    if (a.paused) { clock = { t: ct, at: performance.now() }; return ct; }
    const p = clock.t + ((performance.now() - clock.at) / 1000) * (a.playbackRate || 1);
    if (Math.abs(p - ct) > 0.06) { clock = { t: ct, at: performance.now() }; return ct; }
    return p;
  }
  function seek(t) { if (!a || !dur()) return; a.currentTime = Math.max(0, Math.min(dur() - 0.01, t)); clock = { t: a.currentTime, at: performance.now() }; }
  function toggle() { if (!a) return; if (a.paused) a.play().catch(() => {}); else a.pause(); }
  function paintZoom() {
    el.querySelector(".zm input").value = wToV(pref.win);
    el.querySelector(".zm b").textContent = `${pref.win < 10 ? pref.win.toFixed(1) : Math.round(pref.win)} s`;
  }
  function fit(cv) {
    const d = devicePixelRatio || 1, w = Math.round(cv.clientWidth * d), h = Math.round(cv.clientHeight * d);
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
    return [w, h, d];
  }
  function tickStep(span, n) {                        // a round step giving ~n ticks over span seconds
    for (const s of [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300]) if (span / s <= n) return s;
    return 600;
  }
  function draw() {
    raf = requestAnimationFrame(draw);
    if (!a) return;
    const D = dur(), t = now();
    // ── zoomed view ──
    const [W, H, d] = fit(main), g = main.getContext("2d");
    g.fillStyle = "#000"; g.fillRect(0, 0, W, H);
    if (D && tiles.list) {
      const cps = tiles.cols / D, t0 = t - pref.win * PH;
      const s0 = t0 * cps, s1 = (t0 + pref.win) * cps, k = W / (s1 - s0);
      g.imageSmoothingEnabled = true; g.imageSmoothingQuality = "high";
      for (const tl of tiles.list) {
        const lo = Math.max(s0, tl.x, 0), hi = Math.min(s1, tl.x + tl.w, tiles.cols);
        if (hi <= lo) continue;
        g.drawImage(tl.bmp, tl.sx + lo - tl.x, 0, hi - lo, tl.h, (lo - s0) * k, 0, (hi - lo) * k + 0.75, H);
      }
      // time grid: faint full-height lines + labels along the bottom
      const step = tickStep(pref.win, 9), sub = step / (step >= 2 ? 4 : 2);
      g.font = `${11 * d}px "JetBrains Mono", ui-monospace, monospace`; g.textBaseline = "bottom";
      for (let s = Math.ceil(t0 / sub) * sub; s <= t0 + pref.win; s += sub) {
        if (s < 0 || s > D) continue;
        const x = Math.round(((s - t0) / pref.win) * W) + 0.5, major = Math.abs(s / step - Math.round(s / step)) < 1e-6;
        g.fillStyle = major ? "rgba(255,255,255,0.16)" : "rgba(255,255,255,0.06)";
        g.fillRect(x, 0, d, H);
        if (major) { g.fillStyle = "rgba(255,255,255,0.55)"; g.fillText(fmt(s, step < 1), x + 4 * d, H - 4 * d); }
      }
    }
    // playhead: white line, red caps (as on the CDJ)
    const px = Math.round(W * PH);
    g.fillStyle = "rgba(0,0,0,0.55)"; g.fillRect(px - 2 * d, 0, 5 * d, H);
    g.fillStyle = "#fff"; g.fillRect(px - d / 2 - 0.5 * d, 0, 2 * d, H);
    g.fillStyle = "#ff3b3b";
    for (const [y, dir] of [[0, 1], [H, -1]]) { g.beginPath(); g.moveTo(px - 7 * d, y); g.lineTo(px + 7 * d, y); g.lineTo(px, y + dir * 9 * d); g.fill(); }
    // ── overview ──
    const [OW, OH] = fit(ov), o = ov.getContext("2d");
    o.fillStyle = "#000"; o.fillRect(0, 0, OW, OH);
    const bar = Math.round(OH * 0.78);
    if (ovImg) o.drawImage(ovImg, 0, 0, OW, bar);
    if (D) {
      const x = (t / D) * OW;
      o.fillStyle = "rgba(10,10,14,0.62)"; o.fillRect(0, 0, x, bar);          // played part greyed
      const wx0 = ((t - pref.win * PH) / D) * OW, ww = (pref.win / D) * OW;
      o.strokeStyle = "rgba(255,255,255,0.7)"; o.lineWidth = d; o.strokeRect(wx0 + d / 2, d / 2, Math.max(2 * d, ww - d), bar - d);
      o.fillStyle = "rgba(255,255,255,0.45)";                                   // minute ticks (30 s on short clips)
      const mt = D > 150 ? 60 : D > 40 ? 30 : 10;
      for (let s = mt; s < D; s += mt) o.fillRect(Math.round((s / D) * OW), bar + 2 * d, d, OH - bar - 2 * d);
      o.fillStyle = "#fff"; o.fillRect(Math.round(x) - d, 0, 2 * d, OH);
      // readouts
      const tm = el.querySelector(".tm");
      tm.querySelector("i").textContent = pref.remain ? "REMAIN" : "TIME";
      tm.querySelector("b").textContent = (pref.remain ? "-" : "") + fmt(pref.remain ? D - t : t);
    }
    const pp = el.querySelector(".pp"), on = !a.paused;
    if (pp._on !== on) { pp._on = on; pp.innerHTML = on ? PAUSE : PLAY; pp.classList.toggle("on", on); }
  }
  async function open(blk) {
    const audio = blk.querySelector("audio"), img = blk.querySelector('img[alt="spectrogram"]');
    if (!audio || !img) return;
    if (!el) build();
    a = audio; tiles = {}; ovImg = img;
    if (!isFinite(a.duration)) { a.preload = "auto"; a.load(); }
    clock = { t: a.currentTime, at: performance.now() };
    const r = refOf(a), q = new URLSearchParams(r).toString(), mono = document.body.classList.contains("sa3-mono");
    el.querySelector(".ttl").textContent = "";
    el.querySelector(".bpm").textContent = "";
    el.querySelector(".st").textContent = "sharpening…";
    paintZoom();
    el.classList.add("on");
    el._fs = false;
    try { await el.requestFullscreen({ navigationUI: "hide" }); el._fs = true; } catch (e) { /* not allowed here: stays a full-window overlay */ }
    if (!raf) raf = requestAnimationFrame(draw);
    const me = a;
    if (img.complete && img.naturalWidth) tilesOf(img).then((x) => { if (a === me && !tiles.sharp) tiles = x; });
    fetch("/sa3/settings?" + q).then((x) => (x.ok ? x.json() : null)).then((s) => { if (a === me && s) el.querySelector(".ttl").textContent = s.prompt || ""; }).catch(() => {});
    fetch("/sa3/label?" + q).then((x) => (x.ok ? x.json() : null)).then((l) => {
      const b = l && l.bpm;
      if (a === me && b && b.tier !== "nobeat" && b.bpm) el.querySelector(".bpm").innerHTML = `<b>${b.bpm.toFixed(1)}</b><i>BPM</i>`;
    }).catch(() => {});
    const hi = new Image();
    hi.onload = () => tilesOf(hi).then((x) => { if (a !== me) return; x.sharp = true; tiles = x; el.querySelector(".st").textContent = ""; });
    hi.onerror = () => { if (a === me) el.querySelector(".st").textContent = ""; };
    hi.src = `/sa3/djspec?${q}&mono=${mono ? 1 : 0}`;
  }
  function close() {
    if (!el) return;
    el.classList.remove("on");
    cancelAnimationFrame(raf); raf = 0;
    if (document.fullscreenElement === el) document.exitFullscreen().catch(() => {});
    el._fs = false;
    for (const t of tiles.list || []) if (t.bmp && t.bmp.close) t.bmp.close();
    a = null; tiles = {}; ovImg = null;
  }

  setInterval(() => {
    document.querySelectorAll(".sa3-lab:not([data-dj])").forEach((lab) => {
      const at = lab.querySelector(".ex") || lab.querySelector(".rs");
      if (!at) return;
      lab.dataset.dj = "1";
      const b = document.createElement("button");
      b.className = "dj";
      b.title = "DJ view: full screen, the spectrogram scrolling under a fixed playhead (like a CDJ)";
      b.innerHTML = `${ICON}<span>DJ</span>`;
      b.addEventListener("click", (e) => { e.stopPropagation(); open(lab.closest(".blk")); });
      at.before(b);
    });
  }, 500);
}
