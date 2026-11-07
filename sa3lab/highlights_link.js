() => {
  // Highlights on the studio's history rows (composer spec 3.1; server: highlights.py; modal: the UI
  // kit's /sa3/ui/highlight.js, loaded with import() so the kit stays the one implementation).
  // On every row's dadaspec: click still seeks (upstream's onclick); a drag of ≥ 4 px selects a range
  // and opens the highlight modal (Save stars the render and stores the range in its sidecar);
  // saved highlights draw as amber bands; a click on a band opens its menu (loop, edit, open in the
  // composer, remove). Because a drag on the dadaspec now selects, dragging a clip into an input
  // starts from its transport bar or caption, not its spectrogram.
  const kit = (m) => import(`/sa3/ui/${m}.js`);
  const css = (href) => { if (!document.querySelector(`link[href="${href}"]`)) { const l = document.createElement("link"); l.rel = "stylesheet"; l.href = href; document.head.appendChild(l); } };
  css("/sa3/ui/vars.css"); css("/sa3/ui/components.css");
  const cache = new Map();                          // ref → Promise<view>
  const refOf = (a) => {                            // the row's render: {id} from a gradio file path, else {name} (active folder)
    if (a.dataset.name) return { name: a.dataset.name };
    const src = decodeURIComponent(a.getAttribute("src") || "");
    const i = src.indexOf("/outputs/");
    if (src.includes("file=") && i >= 0) return { id: src.slice(i + 9).split("?")[0] };
    return { name: src.split("/").pop().split("?")[0] };
  };
  const key = (r) => (r.id ? "id:" + r.id : "name:" + r.name);
  function view(r, fresh = false) {
    if (fresh || !cache.has(key(r))) {
      const q = new URLSearchParams(r).toString();
      cache.set(key(r), fetch("/sa3/highlights?" + q, { cache: "no-store" }).then((x) => (x.ok ? x.json() : null)).catch(() => null));
    }
    return cache.get(key(r));
  }
  function enhance() {
    document.querySelectorAll(".blk").forEach((blk) => {
      const a = blk.querySelector("audio"), img = blk.querySelector('img[alt="spectrogram"], [data-seek] img');
      if (!a || !img || img.dataset.sa3hl) return;
      img.dataset.sa3hl = "1";
      const wrap = img.parentElement;
      wrap.style.position = wrap.style.position || "relative";
      const cv = document.createElement("canvas");
      cv.className = "sa3-hlcv";
      wrap.appendChild(cv);
      const r = refOf(a);
      const dur = () => (isFinite(a.duration) && a.duration ? a.duration : +a.dataset.dur || (wrap._v && wrap._v.duration) || 0);
      let bands = [], sel = null;
      const draw = () => {
        const w = wrap.clientWidth, h = img.clientHeight || wrap.clientHeight, dpr = devicePixelRatio || 1;
        if (!w || !h) return;
        cv.width = w * dpr; cv.height = h * dpr; cv.style.width = w + "px"; cv.style.height = h + "px";
        const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
        const D = dur(); if (!D) return;
        for (const b of bands) { const x0 = (b.s0 / D) * w, x1 = (b.s1 / D) * w; g.fillStyle = "rgba(255,212,121,0.14)"; g.fillRect(x0, 0, x1 - x0, h); g.fillStyle = "rgba(255,212,121,0.95)"; g.fillRect(x0, 0, x1 - x0, 3); }
        if (sel) { const x0 = (sel[0] / D) * w, x1 = (sel[1] / D) * w; g.fillStyle = "rgba(0,0,0,0.5)"; g.fillRect(0, 0, x0, h); g.fillRect(x1, 0, w - x1, h); g.strokeStyle = "#c4bbff"; g.lineWidth = 1.5; g.strokeRect(x0 + 0.75, 0.75, x1 - x0 - 1.5, h - 1.5); }
      };
      const load = (fresh) => view(r, fresh).then((v) => { if (v) { wrap._v = v; bands = v.highlights || []; draw(); } });
      load();
      a.addEventListener("loadedmetadata", draw);
      new ResizeObserver(draw).observe(wrap);
      let down = null, dragged = false;
      const tAt = (e) => { const b = wrap.getBoundingClientRect(); return Math.max(0, Math.min(dur(), ((e.clientX - b.left) / b.width) * dur())); };
      wrap.addEventListener("pointerdown", (e) => { if (e.button !== 0) return; down = { x: e.clientX, t: tAt(e), id: e.pointerId }; dragged = false; }, true);
      wrap.addEventListener("pointermove", (e) => {
        if (!down) return;
        if (!dragged && Math.abs(e.clientX - down.x) >= 4) { dragged = true; try { wrap.setPointerCapture(down.id); } catch (er) { /* gone */ } }
        if (dragged) { const t = tAt(e); sel = [Math.min(down.t, t), Math.max(down.t, t)]; draw(); }
      }, true);
      wrap.addEventListener("pointerup", async (e) => {
        if (!down) return;
        const was = dragged, t0 = down.t;
        down = null;
        if (was) {
          const rg = sel;
          setTimeout(() => { sel = null; draw(); }, 0);
          if (!rg || rg[1] - rg[0] < 0.25) return;
          const v = wrap._v || (await view(r));
          if (!v) return;
          (await kit("highlight")).openHighlight({ id: v.id, range: rg,
            // the server already starred it; the row's star (studio.js's own state) learns it by its own click path (idempotent POST)
            onSaved: () => { load(true); const st = wrap.closest(".blk")?.querySelector(".sa3-lab .star"); if (st && !st.classList.contains("on")) st.click(); },
            onComposer: (id, s0, s1) => window.open(`/sa3/page/composer?add=${encodeURIComponent(id)}&s0=${s0.toFixed(3)}&s1=${s1.toFixed(3)}`, "sa3-composer") });
          return;
        }
        const b = bands.find((x) => t0 >= x.s0 && t0 <= x.s1);
        if (!b) return;                             // a plain click: upstream's seek runs
        e.stopPropagation();
        const v = wrap._v;
        const { menu } = await kit("menu");
        const loop = () => {
          a.currentTime = b.s0; a.play();
          const stop = () => { if (a.currentTime >= b.s1) { a.currentTime = b.s0; } };
          a.addEventListener("timeupdate", stop);
          a.addEventListener("pause", () => a.removeEventListener("timeupdate", stop), { once: true });
        };
        menu(e.clientX, e.clientY, [
          { label: "Loop this highlight", fn: loop },
          { label: "Edit…", fn: async () => (await kit("highlight")).openHighlight({ id: v.id, hl: b, range: [b.s0, b.s1], onSaved: () => load(true) }) },
          { label: "Open in composer", fn: () => window.open(`/sa3/page/composer?add=${encodeURIComponent(v.id)}&s0=${b.s0}&s1=${b.s1}`, "sa3-composer") },
          "-",
          { label: "Remove highlight", danger: true, fn: async () => { await fetch("/sa3/highlights", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: v.id, op: "remove", h: { id: b.id } }) }); load(true); } },
        ]);
      }, true);
      // a drag on the dadaspec must not seek (upstream onclick) or start the row's HTML5 drag
      wrap.addEventListener("click", (e) => { if (dragged || bands.some((x) => { const t = tAt(e); return t >= x.s0 && t <= x.s1; })) { e.stopPropagation(); e.preventDefault(); dragged = false; } }, true);
      blk.addEventListener("dragstart", (e) => { if (down || e.target === img || wrap.contains(e.target)) e.preventDefault(); }, true);
      img.setAttribute("draggable", "false");
    });
  }
  setInterval(enhance, 800);
  enhance();
}
