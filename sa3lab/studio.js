() => {
  // Always dark: gradio switches theme mode via the __theme URL param.
  const u = new URL(window.location.href);
  if (u.searchParams.get("__theme") !== "dark") {
    u.searchParams.set("__theme", "dark");
    window.location.replace(u.href);
    return;
  }

  document.title = "SA3 Studio";

  // ⌘/Ctrl + Enter → Generate
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      const b = document.getElementById("sa3-generate");
      if (b) { e.preventDefault(); b.click(); }
    }
  });

  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const sec = (ms) => (ms / 1000).toFixed(ms < 10000 ? 2 : 1) + " s";

  function setSeed(v) {
    const blk = [...document.querySelectorAll(".block")].find((b) => {
      const l = b.querySelector("label span, [data-testid='block-info']");
      return l && l.textContent.trim() === "Seed";
    });
    const inp = blk && blk.querySelector("input, textarea");
    if (!inp) return;
    inp.value = v;
    inp.dispatchEvent(new Event("input", { bubbles: true }));
    inp.dispatchEvent(new Event("change", { bubbles: true }));
  }
  window.sa3SetSeed = setSeed;

  // Rebuild upstream's one-line timing readout as a stats card.
  function formatTiming(slot) {
    if (slot.querySelector(".sa3-stats")) return;
    const host = [...slot.querySelectorAll("*")].find((n) =>
      n.textContent.includes("Inference:") &&
      ![...n.children].some((c) => c.textContent.includes("Inference:")));
    if (!host) return;
    const warns = [...host.querySelectorAll("div")].filter((d) => d.textContent.trim().startsWith("note:"));
    const warnHtml = warns.map((d) => `<div class="warn">${esc(d.textContent.trim())}</div>`).join("");
    warns.forEach((d) => d.remove());
    const t = host.textContent.replace(/ /g, " ");
    const m = t.match(/Inference:\s*(\d+)\s*ms\s*\(t5=(\d+)\s*·\s*sample=(\d+)\s*·\s*decode=(\d+)\)/);
    const rt = t.match(/([\d.]+)×\s*realtime/);
    const seed = t.match(/seed:\s*(-?\d+)/);
    const sl = t.match(/seq_len:\s*(\d+)/);
    const ns = t.match(/samples:\s*(\d+)/);
    if (!m || !rt || !seed) return;            // unknown format: leave upstream's text
    const head = t.split("Inference:")[0];
    const parts = head.split(/\s+·\s+/).map((s) => s.trim()).filter(Boolean);
    const prompt = parts.shift() || "(no prompt)";
    const neg = parts.filter((p) => p.startsWith("neg:"));
    const notes = parts.filter((p) => !p.startsWith("neg:"));
    const [tot, t5, smp, dec] = m.slice(1).map(Number);
    const other = Math.max(0, tot - t5 - smp - dec);
    const audioS = ns ? (Number(ns[1]) / 44100) : null;
    const load = notes.find((n) => n.startsWith("DiT-load"));
    const tip = [`text ${sec(t5)}`, `sampling ${sec(smp)}`, `decode ${sec(dec)}`]
      .concat(other > 50 ? [`load / other ${sec(other)}`] : [])
      .concat(sl ? [`${sl[1]} latent frames`] : []).join(" · ");
    host.innerHTML = `
      <div class="sa3-stats">
        <div class="p" title="${esc(prompt)}">${copyBtn(`data-copy="${esc(prompt)}"`)}${esc(prompt)}</div>
        ${neg.map((n) => `<div class="neg">${esc(n)}</div>`).join("")}
        ${warnHtml}
        <div class="row">
          <span class="k"><b>${sec(tot)}</b>render</span>
          <span class="k"><b>${Number(rt[1]).toFixed(1)}×</b>realtime</span>
          <span class="k"><b>${audioS ? audioS.toFixed(0) + " s" : "–"}</b>audio</span>
          <span class="k seed" title="Click to put this seed in the Seed box" onclick="window.sa3SetSeed('${seed[1]}')"><b>${seed[1]}</b>seed</span>
          <div class="bar" title="${esc(tip)}${load ? " · " + esc(load) : ""}">
            <i class="t5" style="flex:${t5}"></i><i class="smp" style="flex:${smp}"></i><i class="dec" style="flex:${dec}"></i>${other > 50 ? `<i class="oth" style="flex:${other}"></i>` : ""}
          </div>
        </div>
      </div>`;
  }

  // Hide empty output slots; show an empty state in the player slot.
  function tick() {
    let anyBusy = false;
    for (const cls of ["sa3-player", "sa3-timing", "sa3-error", "sa3-queued", "sa3-history"]) {
      const el = document.querySelector("." + cls);
      if (!el) continue;
      const content = el.querySelector(".prose") || el;
      const hasContent = !!content.textContent.trim() || !!content.querySelector("audio, img");
      const status = el.querySelector(":scope > .wrap");
      const busy = !!status && !status.classList.contains("hide");
      anyBusy = anyBusy || busy;
      // side slots collapse whenever they hold nothing; the player keeps its
      // empty state but drops it while a generation is running
      const empty = !hasContent && (cls !== "sa3-player" || !busy);
      el.classList.toggle("is-empty", empty);
      if (cls === "sa3-player") el.classList.toggle("is-busy", busy && !hasContent);
    }
    document.body.classList.toggle("sa3-busy", anyBusy);   // reveals Stop
    const tm = document.querySelector(".sa3-timing");
    if (tm) formatTiming(tm);
    enhanceHistory();
    makeDraggable();
    enhanceLabels();
    mountLibrary();
    progressLoop(anyBusy);
  }
  // ── history rows: tiny transport bar above a full-width spectrogram ──
  const fmt = (t) => !isFinite(t) ? "0:00" : `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  const ICON_PLAY = '<svg viewBox="0 0 16 16"><path d="M4 2.5v11l9-5.5z" fill="currentColor"/></svg>';
  const ICON_PAUSE = '<svg viewBox="0 0 16 16"><path d="M4 2.5h3v11H4zM9 2.5h3v11H9z" fill="currentColor"/></svg>';
  const ICON_DL = '<svg viewBox="0 0 16 16"><path d="M8 2v8m0 0L4.5 6.5M8 10l3.5-3.5M3 13h10" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const ICON_GRIP = '<svg viewBox="0 0 16 16"><g fill="currentColor"><circle cx="6" cy="4" r="1.2"/><circle cx="10" cy="4" r="1.2"/><circle cx="6" cy="8" r="1.2"/><circle cx="10" cy="8" r="1.2"/><circle cx="6" cy="12" r="1.2"/><circle cx="10" cy="12" r="1.2"/></g></svg>';
  const clipName = (a) => decodeURIComponent((a.getAttribute("src") || "").split("/").pop() || "clip.wav");
  const ICON_COPY = '<svg viewBox="0 0 16 16"><rect x="5.5" y="5.5" width="8" height="8" rx="1.8" stroke="currentColor" stroke-width="1.4" fill="none"/><path d="M10.5 3.5v-.2A1.3 1.3 0 0 0 9.2 2H3.8A1.3 1.3 0 0 0 2.5 3.3v5.4A1.3 1.3 0 0 0 3.8 10h.2" stroke="currentColor" stroke-width="1.4" fill="none"/></svg>';
  const ICON_OK = '<svg viewBox="0 0 16 16"><path d="M3 8.5l3 3 7-7" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const ICON_RESTORE = '<svg viewBox="0 0 16 16"><path d="M2.8 8a5.2 5.2 0 1 0 1.6-3.8M2.5 2.2v3h3" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/><circle cx="8" cy="8" r="1.4" fill="currentColor"/></svg>';
  const ICON_FILTER = '<svg viewBox="0 0 16 16"><path d="M2 4h12M4.5 8h7M7 12h2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
  const copyBtn = (attrs) => `<button class="sa3-cp" ${attrs} title="Copy prompt">${ICON_COPY}</button>`;

  // one delegated handler for every copy icon: data-copy holds the text, or
  // data-name points at a render whose full prompt comes from its sidecar
  document.addEventListener("click", async (e) => {
    const b = e.target.closest(".sa3-cp");
    if (!b) return;
    e.preventDefault(); e.stopPropagation();
    let text = b.dataset.copy;
    if (text === undefined && b.dataset.name) text = ((await getLabel(b.dataset.name)) || labels.get(b.dataset.name) || {}).prompt;
    if (!text) return;
    try { await navigator.clipboard.writeText(text); } catch (err) { console.warn("[sa3] copy failed", err); return; }
    b.innerHTML = ICON_OK; b.classList.add("ok");
    setTimeout(() => { b.innerHTML = ICON_COPY; b.classList.remove("ok"); }, 1100);
  }, true);

  // "use these settings": studio.py's restore handler reads the name from here
  window.sa3Restore = (name) => {
    const b = document.getElementById("sa3-restore-btn");
    if (!b || !name) return;
    window.__sa3RestoreName = name;
    b.click();
  };

  function enhanceHistory() {
    document.querySelectorAll("#sa3-hist .blk audio:not([data-sa3])").forEach((a) => {
      a.dataset.sa3 = "1";
      const row = a.parentElement && a.parentElement.parentElement;   // flex row: [audio wrap, spectrogram wrap]
      if (!row) return;
      row.classList.add("sa3-hrow");
      a.parentElement.classList.add("sa3-native");
      const bar = document.createElement("div");
      bar.className = "sa3-mini";
      bar.innerHTML = `<button class="pp" title="Play / pause">${ICON_PLAY}</button>
        <span class="tm">0:00 / 0:00</span><span class="sp"></span>
        <a class="dl" title="Download" href="${a.getAttribute("src")}" download="${esc(clipName(a))}">${ICON_DL}</a>
        <span class="grip" title="Drag onto an audio input">${ICON_GRIP}</span>`;
      row.insertBefore(bar, row.firstChild);
      const cap = row.nextElementSibling;
      if (cap && cap.tagName === "DIV" && !cap.classList.contains("sa3-lab")) {
        cap.classList.add("sa3-cap");
        const tn = cap.firstChild;
        if (tn && tn.nodeType === 3) tn.textContent = tn.textContent.replace(/^\s*\d+[smhd] ago\s*·\s*/, "");
        cap.insertAdjacentHTML("afterbegin", copyBtn(`data-name="${esc(clipName(a))}"`));
      }
      const pp = bar.querySelector(".pp"), tm = bar.querySelector(".tm");
      const sync = () => {
        pp.innerHTML = a.paused ? ICON_PLAY : ICON_PAUSE;
        tm.textContent = `${fmt(a.currentTime)} / ${fmt(a.duration)}`;
        bar.classList.toggle("on", !a.paused);
      };
      pp.addEventListener("click", (e) => { e.preventDefault(); a.paused ? a.play() : a.pause(); });
      ["play", "pause", "timeupdate", "loadedmetadata", "ended"].forEach((ev) => a.addEventListener(ev, sync));
      sync();
    });
  }

  // ── drag any rendered clip onto an audio input on the left ──
  const MIME = "application/x-sa3-audio";
  function makeDraggable() {
    document.querySelectorAll("#sa3-out .blk:not([data-sa3drag])").forEach((blk) => {
      const a = blk.querySelector("audio");
      if (!a) return;
      blk.dataset.sa3drag = "1";
      blk.setAttribute("draggable", "true");
      blk.querySelectorAll("img").forEach((im) => im.setAttribute("draggable", "false"));
      blk.addEventListener("dragstart", (e) => {
        const src = new URL(a.getAttribute("src"), location.href).href;
        e.dataTransfer.setData(MIME, JSON.stringify({ src, name: clipName(a) }));
        e.dataTransfer.setData("text/uri-list", src);
        e.dataTransfer.effectAllowed = "copy";
        document.body.classList.add("sa3-dragging");
      });
      blk.addEventListener("dragend", () => {
        document.body.classList.remove("sa3-dragging");
        document.querySelectorAll(".sa3-drop.sa3-over").forEach((b) => b.classList.remove("sa3-over"));
      });
    });
  }
  const isOurs = (e) => e.dataTransfer && [...e.dataTransfer.types].includes(MIME);
  const dropBlock = (e) => e.target.closest && e.target.closest(".block.sa3-drop");
  document.addEventListener("dragover", (e) => {
    if (!isOurs(e)) return;
    const b = dropBlock(e);
    document.querySelectorAll(".sa3-drop.sa3-over").forEach((x) => x !== b && x.classList.remove("sa3-over"));
    if (b) { e.preventDefault(); e.stopPropagation(); e.dataTransfer.dropEffect = "copy"; b.classList.add("sa3-over"); }
  }, true);
  document.addEventListener("drop", async (e) => {
    if (!isOurs(e)) return;
    e.preventDefault(); e.stopPropagation();
    document.body.classList.remove("sa3-dragging");
    const b = dropBlock(e);
    if (!b) return;
    b.classList.remove("sa3-over");
    const { src, name } = JSON.parse(e.dataTransfer.getData(MIME));
    b.classList.add("sa3-loading");
    try {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      let input = b.querySelector("input[type=file]");
      if (!input) {   // field already holds audio: clear it to get the upload input back
        const clr = b.querySelector('button[aria-label="Clear"], button[title="Clear"], button[aria-label="clear"]');
        if (clr) clr.click();
        for (let i = 0; i < 30 && !(input = b.querySelector("input[type=file]")); i++) await sleep(100);
      }
      if (!input) throw new Error("no upload input in target");
      const blob = await (await fetch(src)).blob();
      const file = new File([blob], name, { type: blob.type || "audio/mpeg" });
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    } catch (err) {
      console.warn("[sa3] drop failed", err);
    } finally {
      b.classList.remove("sa3-loading");
    }
  }, true);

  // ── dataset labels: 0-10 score + free-text tags under every clip ──
  // Saved instantly to the render's sidecar json via POST /sa3/label (library.py).
  const labels = new Map();          // file name -> {score, tags}
  const pending = new Set();
  async function getLabel(name) {
    if (labels.has(name)) return labels.get(name);
    if (pending.has(name)) return null;
    pending.add(name);
    try {
      const r = await fetch("sa3/label?name=" + encodeURIComponent(name));
      const d = r.ok ? await r.json() : { score: null, tags: [] };
      labels.set(name, d);
      if (d.bpm && d.bpm.pending) pollBpm(name, 0);
      return d;
    } finally { pending.delete(name); }
  }
  // the server measures tempo in a background thread (tempo.py); poll until it lands
  function pollBpm(name, n) {
    if (n > 20) return;
    setTimeout(async () => {
      try {
        const r = await fetch("sa3/label?name=" + encodeURIComponent(name));
        if (!r.ok) return;
        const d = await r.json();
        if (d.bpm && d.bpm.pending) return pollBpm(name, n + 1);
        labels.set(name, { ...labels.get(name), bpm: d.bpm });
        document.querySelectorAll(".sa3-lab").forEach((el) => { if (el.dataset.name === name) paintLabel(el); });
      } catch (e) { /* server restarting: give up quietly */ }
    }, 1500 + n * 500);
  }
  // auto BPM chip: measured tempo, coloured by how well it matches the prompt's BPM
  function bpmChip(b) {
    if (!b || b.error) return "";
    if (b.pending) return '<span class="tag bpm pend" title="Measuring tempo…">♩ …</span>';
    const fold = b.fold === 2 ? " · half-time" : b.fold === 0.5 ? " · double-time" : "";
    const tip = b.tier === "nobeat" ? `No clear beat (periodicity ${b.conf})`
      : `Measured ${b.bpm} BPM` + (b.prompt_bpm ? ` · prompt asked ${b.prompt_bpm}` +
        ` · off by ${(b.err * 100).toFixed(1)}%${fold}` : " · prompt names no BPM") + ` · beat strength ${b.conf}`;
    const txt = b.tier === "nobeat" ? "no beat" : `${Math.round(b.bpm)} BPM${fold ? (b.fold === 2 ? " ½×" : " 2×") : ""}`;
    return `<span class="tag bpm ${b.tier}" title="${esc(tip)}">♩ ${txt}</span>`;
  }
  async function saveLabel(name, patch) {
    const cur = labels.get(name) || { score: null, tags: [] };
    labels.set(name, { ...cur, ...patch });
    document.querySelectorAll(".sa3-lab").forEach((el) => { if (el.dataset.name === name) paintLabel(el); });
    try {
      const r = await fetch("sa3/label", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, ...patch }) });
      if (r.ok) labels.set(name, { ...labels.get(name), ...(await r.json()) });
    } catch (e) { console.warn("[sa3] label save failed", e); }
  }
  function paintLabel(el) {
    const d = labels.get(el.dataset.name);
    if (!d) return;
    el.querySelectorAll(".sc button").forEach((b) => {
      const v = Number(b.dataset.v);
      b.classList.toggle("on", d.score !== null && d.score !== undefined && v === d.score);
      b.classList.toggle("lit", d.score !== null && d.score !== undefined && v <= d.score);
    });
    el.classList.toggle("scored", d.score !== null && d.score !== undefined);
    const tg = el.querySelector(".tags");
    tg.innerHTML = bpmChip(d.bpm) + (d.tags || []).map((t, i) =>
      `<span class="tag">${esc(t)}<button data-i="${i}" title="Remove tag">×</button></span>`).join("");
  }
  function enhanceLabels() {
    document.querySelectorAll("#sa3-out .blk").forEach((blk) => {
      const a = blk.querySelector("audio");
      if (!a) return;
      const name = clipName(a);
      let el = blk.querySelector(":scope > .sa3-lab");
      if (el && el.dataset.name === name) return;
      if (el) el.remove();
      el = document.createElement("div");
      el.className = "sa3-lab";
      el.dataset.name = name;
      el.innerHTML = `<div class="sc" title="Score 0-10 (click the lit one again to clear)">${
        [...Array(11).keys()].map((v) => `<button data-v="${v}">${v}</button>`).join("")}</div>
        <div class="tags"></div>
        <input class="add" placeholder="+ tag" maxlength="60" spellcheck="false">
        <button class="rs" title="Restore every setting that made this render (model, prompt, seed, length, steps, CFG, LoRAs, inputs)">${ICON_RESTORE}<span>settings</span></button>`;
      el.addEventListener("dragstart", (e) => { e.preventDefault(); e.stopPropagation(); });
      el.addEventListener("mousedown", (e) => e.stopPropagation());
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        const d = labels.get(name) || { score: null, tags: [] };
        const sb = e.target.closest(".sc button");
        if (sb) { const v = Number(sb.dataset.v); saveLabel(name, { score: d.score === v ? null : v }); return; }
        if (e.target.closest(".rs")) { window.sa3Restore(name); return; }
        const tb = e.target.closest(".tag button");
        if (tb) { const t = [...(d.tags || [])]; t.splice(Number(tb.dataset.i), 1); saveLabel(name, { tags: t }); }
      });
      const inp = el.querySelector(".add");
      inp.addEventListener("keydown", (e) => {
        e.stopPropagation();
        if (e.key !== "Enter" && e.key !== ",") return;
        e.preventDefault();
        const add = inp.value.split(",").map((t) => t.trim()).filter(Boolean);
        inp.value = "";
        if (!add.length) return;
        const d = labels.get(name) || { score: null, tags: [] };
        const have = new Set((d.tags || []).map((t) => t.toLowerCase()));
        saveLabel(name, { tags: [...(d.tags || []), ...add.filter((t) => !have.has(t.toLowerCase()))] });
      });
      blk.appendChild(el);
      getLabel(name).then(() => paintLabel(el));
      paintLabel(el);
    });
  }

  // ── auto-reload: devwatch.py restarts the server on code changes; reload the
  //    page once the new server answers, carrying the unsent prompt over ──
  (function bootWatch() {
    let boot = null;
    const promptBox = () => {
      const blk = [...document.querySelectorAll(".block")].find((b) => {
        const l = b.querySelector("[data-testid='block-info']");
        return l && l.textContent.trim() === "Prompt";
      });
      return blk && blk.querySelector("textarea");
    };
    const KEEP = "sa3-reload-prompt";
    const kept = sessionStorage.getItem(KEEP);
    if (kept !== null) {
      sessionStorage.removeItem(KEEP);
      const put = (n) => {
        const t = promptBox();
        if (!t) { if (n < 40) setTimeout(() => put(n + 1), 250); return; }
        setTimeout(() => { t.value = kept; t.dispatchEvent(new Event("input", { bubbles: true })); }, 800);  // after gradio's own load-time prefill
      };
      put(0);
    }
    setInterval(async () => {
      try {
        const r = await fetch("sa3/boot", { cache: "no-store" });
        if (!r.ok) return;
        const id = (await r.json()).id;
        if (boot === null) boot = id;
        else if (id !== boot) {
          const t = promptBox();
          if (t) sessionStorage.setItem(KEEP, t.value);
          location.reload();
        }
      } catch (e) { /* server restarting */ }
    }, 1500);
  })();

  // ── Library: top-right button → modal that browses + filters every render ──
  // Rows come from GET /sa3/library (library.py): prompt, model, seed, score,
  // tags, measured BPM. Filters persist in localStorage.
  const LIB_KEY = "sa3-lib-filters";
  const LIB_DEF = { q: "", bmin: "", bmax: "", tiers: [], smin: 0, smax: 10, unscored: true, tags: [], dits: [], sort: "new" };
  const TIERS = [["green", "on tempo"], ["yellow", "close"], ["orange", "off"], ["none", "no BPM asked"], ["nobeat", "no beat"]];
  let LF = { ...LIB_DEF };
  try { LF = { ...LIB_DEF, ...JSON.parse(localStorage.getItem(LIB_KEY) || "{}") }; } catch (e) { /* keep defaults */ }
  let libRows = [], libEl = null, libTimer = null, libPlaying = null;
  const libPlayer = new Audio();
  const libActive = () => Object.keys(LIB_DEF).filter((k) => k !== "sort" && JSON.stringify(LF[k]) !== JSON.stringify(LIB_DEF[k])).length;

  function mountLibrary() {
    const top = document.getElementById("sa3-top");
    if (!top || document.getElementById("sa3-libbtn")) return;
    const btn = document.createElement("button");
    btn.id = "sa3-libbtn";
    btn.title = "Browse and filter every render (L)";
    btn.innerHTML = `${ICON_FILTER}<span>Library</span><b></b>`;
    btn.addEventListener("click", () => toggleLibrary());
    top.appendChild(btn);
    paintLibBadge();
  }
  function paintLibBadge() {
    const b = document.querySelector("#sa3-libbtn b");
    if (b) { const n = libActive(); b.textContent = n || ""; b.style.display = n ? "" : "none"; }
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && libEl && libEl.classList.contains("open")) { toggleLibrary(false); return; }
    if (e.key === "l" && !e.metaKey && !e.ctrlKey && !e.altKey && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) toggleLibrary();
  });

  function buildLibrary() {
    libEl = document.createElement("div");
    libEl.id = "sa3-lib";
    libEl.innerHTML = `
      <div class="pane" role="dialog" aria-label="Render library">
        <header>
          <h3>Library</h3><span class="cnt"></span><span class="sp"></span>
          <label class="srt">sort <select class="sort">
            <option value="new">newest</option><option value="score">score</option>
            <option value="bpm">BPM</option><option value="old">oldest</option></select></label>
          <button class="x" title="Close (Esc)">×</button>
        </header>
        <div class="body">
          <aside>
            <div class="f"><span class="h">Prompt contains</span><input class="q" type="search" placeholder="sitar techno…" spellcheck="false"></div>
            <div class="f"><span class="h">Measured BPM</span>
              <div class="rng"><input class="bmin" type="number" min="0" max="300" placeholder="min"><i>–</i><input class="bmax" type="number" min="0" max="300" placeholder="max"></div></div>
            <div class="f"><span class="h">BPM vs prompt</span><div class="chips tiers"></div></div>
            <div class="f"><span class="h">Score</span>
              <div class="rng"><input class="smin" type="number" min="0" max="10"><i>–</i><input class="smax" type="number" min="0" max="10"></div>
              <label class="ck"><input type="checkbox" class="unscored"> include unscored</label></div>
            <div class="f"><span class="h">Tags <em>all must match</em></span><div class="chips tagc"></div></div>
            <div class="f"><span class="h">Model</span><div class="chips ditc"></div></div>
            <button class="reset">Reset filters</button>
          </aside>
          <div class="list"></div>
        </div>
      </div>`;
    libEl.appendChild(libPlayer);               // in the DOM, so the background visualiser hears it
    document.body.appendChild(libEl);
    const $ = (s) => libEl.querySelector(s);
    libEl.addEventListener("mousedown", (e) => { if (e.target === libEl) toggleLibrary(false); });
    $(".x").addEventListener("click", () => toggleLibrary(false));
    const set = (patch) => { LF = { ...LF, ...patch }; localStorage.setItem(LIB_KEY, JSON.stringify(LF)); renderLibrary(); paintLibBadge(); };
    $(".q").addEventListener("input", (e) => set({ q: e.target.value }));
    for (const k of ["bmin", "bmax"]) $("." + k).addEventListener("input", (e) => set({ [k]: e.target.value }));
    for (const k of ["smin", "smax"]) $("." + k).addEventListener("input", (e) => set({ [k]: e.target.value === "" ? LIB_DEF[k] : Number(e.target.value) }));
    $(".unscored").addEventListener("change", (e) => set({ unscored: e.target.checked }));
    $(".sort").addEventListener("change", (e) => set({ sort: e.target.value }));
    $(".reset").addEventListener("click", () => { set({ ...LIB_DEF, sort: LF.sort }); syncLibInputs(); });
    const toggleIn = (key, v) => set({ [key]: LF[key].includes(v) ? LF[key].filter((x) => x !== v) : [...LF[key], v] });
    libEl.addEventListener("click", (e) => {
      const c = e.target.closest("[data-chip]");
      if (c) { toggleIn(c.dataset.chip, c.dataset.v); return; }
      const r = e.target.closest(".r");
      if (!r) return;
      const name = r.dataset.name;
      if (e.target.closest(".pp")) {
        if (libPlaying === name && !libPlayer.paused) libPlayer.pause();
        else {
          document.querySelectorAll("audio").forEach((a) => { if (a !== libPlayer) a.pause(); });
          if (libPlaying !== name) { libPlayer.src = "sa3/audio?name=" + encodeURIComponent(name); libPlaying = name; }
          libPlayer.play();
        }
      } else if (e.target.closest(".rs")) { window.sa3Restore(name); toggleLibrary(false); }
      else if (e.target.closest(".spec")) {          // click the spectrogram to seek
        const im = e.target.closest(".spec"), f = (e.clientX - im.getBoundingClientRect().left) / im.clientWidth;
        if (libPlaying !== name) { libPlayer.src = "sa3/audio?name=" + encodeURIComponent(name); libPlaying = name; }
        const go = () => { libPlayer.currentTime = f * (libPlayer.duration || 0); libPlayer.play(); };
        libPlayer.readyState >= 1 ? go() : libPlayer.addEventListener("loadedmetadata", go, { once: true });
      }
    });
    ["play", "pause", "ended", "timeupdate"].forEach((ev) => libPlayer.addEventListener(ev, paintLibPlayback));
    syncLibInputs();
  }
  function syncLibInputs() {
    const $ = (s) => libEl.querySelector(s);
    $(".q").value = LF.q; $(".bmin").value = LF.bmin; $(".bmax").value = LF.bmax;
    $(".smin").value = LF.smin; $(".smax").value = LF.smax; $(".unscored").checked = LF.unscored; $(".sort").value = LF.sort;
  }
  function paintLibPlayback() {
    if (!libEl) return;
    libEl.querySelectorAll(".r").forEach((r) => {
      const on = r.dataset.name === libPlaying && !libPlayer.paused;
      r.classList.toggle("on", on);
      r.querySelector(".pp").innerHTML = on ? ICON_PAUSE : ICON_PLAY;
      const ph = r.querySelector(".ph");
      if (ph) ph.style.left = r.dataset.name === libPlaying && libPlayer.duration ? (100 * libPlayer.currentTime / libPlayer.duration) + "%" : "-4px";
    });
  }
  async function fetchLibrary() {
    try {
      const r = await fetch("sa3/library");
      if (r.ok) libRows = await r.json();
    } catch (e) { console.warn("[sa3] library fetch failed", e); }
    renderLibrary();
    clearTimeout(libTimer);                     // BPM still being measured: refresh shortly
    if (libEl.classList.contains("open") && libRows.some((x) => x.bpm && x.bpm.pending)) libTimer = setTimeout(fetchLibrary, 2500);
  }
  function toggleLibrary(want) {
    if (!libEl) buildLibrary();
    const open = want === undefined ? !libEl.classList.contains("open") : want;
    libEl.classList.toggle("open", open);
    document.getElementById("sa3-libbtn")?.classList.toggle("on", open);
    if (open) { fetchLibrary(); setTimeout(() => libEl.querySelector(".q").focus({ preventScroll: true }), 60); }
    else { clearTimeout(libTimer); libPlayer.pause(); }
  }
  function libFilter(r) {
    const words = LF.q.toLowerCase().split(/\s+/).filter(Boolean);
    const pr = (r.prompt || "").toLowerCase();
    if (words.some((w) => !pr.includes(w))) return false;
    const b = r.bpm || {};
    const hasBpm = b.bpm !== undefined && b.tier !== "nobeat" && !b.pending;
    if ((LF.bmin !== "" || LF.bmax !== "") && !hasBpm) return false;
    if (LF.bmin !== "" && b.bpm < Number(LF.bmin)) return false;
    if (LF.bmax !== "" && b.bpm > Number(LF.bmax)) return false;
    if (LF.tiers.length && !LF.tiers.includes(b.tier)) return false;
    if (r.score === null || r.score === undefined) { if (!LF.unscored) return false; }
    else if (r.score < LF.smin || r.score > LF.smax) return false;
    const tg = new Set((r.tags || []).map((t) => t.toLowerCase()));
    if (LF.tags.some((t) => !tg.has(t.toLowerCase()))) return false;
    if (LF.dits.length && !LF.dits.includes(r.dit)) return false;
    return true;
  }
  const libAgo = (ts) => {
    const d = Date.now() / 1000 - ts;
    if (d < 3600) return Math.max(1, Math.round(d / 60)) + "m ago";
    if (d < 86400) return Math.round(d / 3600) + "h ago";
    return new Date(ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  };
  function renderLibrary() {
    if (!libEl) return;
    const $ = (s) => libEl.querySelector(s);
    const chip = (key, v, label, n, extra = "") =>
      `<button data-chip="${key}" data-v="${esc(v)}" class="${LF[key].includes(v) ? "on" : ""} ${extra}">${esc(label)}${n !== undefined ? `<i>${n}</i>` : ""}</button>`;
    const count = (f) => libRows.reduce((m, r) => { for (const k of f(r)) m.set(k, (m.get(k) || 0) + 1); return m; }, new Map());
    const tierN = count((r) => [r.bpm && r.bpm.tier].filter(Boolean));
    $(".tiers").innerHTML = TIERS.map(([t, l]) => chip("tiers", t, l, tierN.get(t) || 0, "t-" + t)).join("");
    const tagN = count((r) => r.tags || []);
    const tags = [...new Set([...tagN.keys(), ...LF.tags])].sort((a, b) => (tagN.get(b) || 0) - (tagN.get(a) || 0) || a.localeCompare(b));
    $(".tagc").innerHTML = tags.length ? tags.map((t) => chip("tags", t, t, tagN.get(t) || 0)).join("") : '<span class="none">no tags yet</span>';
    const ditN = count((r) => [r.dit].filter(Boolean));
    $(".ditc").innerHTML = [...ditN.keys()].sort().map((d) => chip("dits", d, d, ditN.get(d))).join("");
    let rows = libRows.filter(libFilter);
    const sc = (r) => (r.score === null || r.score === undefined ? -1 : r.score);
    const bp = (r) => (r.bpm && r.bpm.bpm && r.bpm.tier !== "nobeat" ? r.bpm.bpm : 1e9);
    if (LF.sort === "score") rows.sort((a, b) => sc(b) - sc(a) || b.ts - a.ts);
    else if (LF.sort === "bpm") rows.sort((a, b) => bp(a) - bp(b) || b.ts - a.ts);
    else if (LF.sort === "old") rows.sort((a, b) => a.ts - b.ts);
    $(".cnt").textContent = `${rows.length} of ${libRows.length}`;
    const shown = rows.slice(0, 200);
    $(".list").innerHTML = shown.length ? shown.map((r) => `
      <div class="r" data-name="${esc(r.name)}">
        <button class="pp" title="Play / pause">${ICON_PLAY}</button>
        <div class="mid">
          ${r.has_spec ? `<div class="spec"><img loading="lazy" src="sa3/spec?name=${encodeURIComponent(r.name)}" alt=""><span class="ph"></span></div>` : ""}
          <div class="pr">${copyBtn(`data-copy="${esc(r.prompt)}"`)}<span title="${esc(r.prompt)}">${esc(r.prompt || "(no prompt)")}</span></div>
          <div class="meta">
            <span class="k">${libAgo(r.ts)}</span><span class="k">${esc(r.dit || "?")}</span>
            <span class="k">seed ${esc(String(r.seed ?? "?"))}</span>${r.seconds ? `<span class="k">${Math.round(r.seconds)} s</span>` : ""}
            ${r.steps ? `<span class="k">${r.steps} st</span>` : ""}${r.cfg !== undefined && r.cfg !== null ? `<span class="k">cfg ${r.cfg}</span>` : ""}
            ${r.mode && r.mode !== "text-to-audio" ? `<span class="k">${esc(r.mode)}</span>` : ""}${r.lora ? '<span class="k">LoRA</span>' : ""}
          </div>
          <div class="sa3-lab lib">${r.score !== null && r.score !== undefined ? `<span class="scb">${r.score}<i>/10</i></span>` : ""}${bpmChip(r.bpm)}${(r.tags || []).map((t) =>
            `<span class="tag" data-chip="tags" data-v="${esc(t)}" title="Filter on this tag">${esc(t)}</span>`).join("")}</div>
        </div>
        <button class="rs" title="Restore every setting that made this render">${ICON_RESTORE}<span>settings</span></button>
      </div>`).join("") + (rows.length > shown.length ? `<div class="more">+ ${rows.length - shown.length} more: narrow the filters</div>` : "")
      : `<div class="empty">${libRows.length ? "Nothing matches these filters." : "No renders yet."}</div>`;
    paintLibPlayback();
  }

  // ── progress: best-effort ETA from past render speeds (GET /sa3/progress) ──
  let progTimer = null, progEl = null;
  function progressLoop(busy) {
    const bar = document.getElementById("sa3-actions");
    if (bar && !progEl) {
      progEl = document.createElement("div");
      progEl.id = "sa3-progress";
      progEl.innerHTML = '<div class="track"><i></i></div><span class="txt"></span>';
    }
    if (bar && progEl && progEl.parentElement !== bar) bar.appendChild(progEl);
    if (busy && !progTimer) {
      const poll = async () => {
        try {
          const d = await (await fetch("sa3/progress")).json();
          const fill = progEl.querySelector("i"), txt = progEl.querySelector(".txt");
          if (d.active) {
            progEl.classList.remove("wait");
            fill.style.width = (d.frac * 100).toFixed(1) + "%";
            const ph = d.phase === "sampling" ? `sampling ${Math.min(d.step + 1, d.steps)}/${d.steps}`
              : d.phase === "decode" ? "decoding" : "encoding prompt";
            const left = d.left_s >= 1 ? `~${d.left_s >= 60 ? Math.floor(d.left_s / 60) + "m " : ""}${Math.round(d.left_s % 60)} s left` : "almost done";
            txt.textContent = `${ph} · ${left}`;
          } else {
            progEl.classList.add("wait");
            fill.style.width = "0%";
            txt.textContent = "waiting for the model…";
          }
        } catch (e) { /* server busy: keep last */ }
      };
      poll();
      progTimer = setInterval(poll, 300);
      // only show it if busy lasts (page-load restore also flips busy briefly)
      setTimeout(() => { if (progTimer) progEl.classList.add("on"); }, 600);
    } else if (!busy && progTimer) {
      clearInterval(progTimer); progTimer = null;
      if (progEl) {
        progEl.querySelector("i").style.width = "100%";
        setTimeout(() => { if (!progTimer) progEl.classList.remove("on"); }, 600);
      }
    }
  }

  // ── background: a dim spectrogram waterfall crawling right→left (the real FFT
  //    while a clip plays, a synthetic dub-techno-ish pattern when idle), an
  //    oscilloscope trace, and short glitch bursts (slice shifts, RGB tears,
  //    datamosh blocks) that fire on kicks. Drawn at 1/3 res, upscaled pixelated.
  (function background() {
    const cv = document.createElement("canvas");
    cv.id = "sa3-bg";
    document.body.prepend(cv);
    const g = cv.getContext("2d");
    const wf = document.createElement("canvas"), wg = wf.getContext("2d");   // waterfall buffer
    const SCALE = 1 / 3;
    let W = 0, H = 0, col = null, rowF = null;
    const FLO = 30, FHI = 16000;
    const resize = () => {
      W = cv.width = wf.width = Math.max(64, Math.round(innerWidth * SCALE));
      H = cv.height = wf.height = Math.max(64, Math.round(innerHeight * SCALE));
      wg.fillStyle = "#09090d"; wg.fillRect(0, 0, W, H);
      col = wg.createImageData(1, H);
      rowF = Float32Array.from({ length: H }, (_, y) => FLO * Math.pow(FHI / FLO, 1 - y / (H - 1)));   // top = highs
    };
    resize(); addEventListener("resize", resize);
    const still = matchMedia("(prefers-reduced-motion: reduce)").matches;

    // colour map: near-black → indigo → violet → pink → white-hot, kept dim
    const LUT = (() => {
      const stops = [[0, [9, 9, 13]], [0.3, [32, 26, 80]], [0.55, [110, 90, 230]], [0.78, [235, 95, 175]], [1, [255, 230, 240]]];
      const out = new Uint8ClampedArray(256 * 3);
      for (let i = 0; i < 256; i++) {
        const v = i / 255;
        let k = 0; while (k < stops.length - 2 && v > stops[k + 1][0]) k++;
        const [a, ca] = stops[k], [b, cb] = stops[k + 1], f = (v - a) / (b - a);
        for (let c = 0; c < 3; c++) out[i * 3 + c] = ca[c] + (cb[c] - ca[c]) * f;
      }
      return out;
    })();

    // audio: tap whichever <audio> is playing (one plays at a time upstream)
    let ctx = null, an = null, buf = null, tbuf = null;
    const tapped = new WeakSet();
    document.addEventListener("play", async (e) => {
      const el = e.target;
      if (!(el instanceof HTMLMediaElement) || tapped.has(el)) return;
      if (!(navigator.userActivation && navigator.userActivation.hasBeenActive)) return;
      try {
        if (!ctx) {
          ctx = new AudioContext();
          an = ctx.createAnalyser(); an.fftSize = 2048; an.smoothingTimeConstant = 0.5;
          buf = new Uint8Array(an.frequencyBinCount); tbuf = new Uint8Array(an.fftSize);
        }
        await ctx.resume();
        if (ctx.state !== "running") return;     // never route audio into a muted graph
        const src = ctx.createMediaElementSource(el);
        src.connect(ctx.destination); src.connect(an);
        tapped.add(el);
      } catch (err) { console.warn("[sa3] bg audio tap skipped", err); }
    }, true);

    const band = (lo, hi) => {                  // mean energy 0..1 between two Hz
      const bin = ctx.sampleRate / an.fftSize;
      let s = 0, n = 0;
      for (let i = Math.max(1, Math.floor(lo / bin)); i <= Math.min(buf.length - 1, Math.ceil(hi / bin)); i++) { s += buf[i]; n++; }
      return n ? s / n / 255 : 0;
    };
    const env = { lo: 0, mid: 0, hi: 0 };
    const follow = (k, v) => { env[k] += (v - env[k]) * (v > env[k] ? 0.35 : 0.06); };

    // idle "music": a wandering chord's partials, a kick every beat, off-beat hats
    const BPM = 118, beat = 60 / BPM;
    const gauss = (x, w) => Math.exp(-(x * x) / (2 * w * w));
    const idleCol = (t) => {
      const ph = (t % beat) / beat, kick = Math.exp(-ph * 9), hat = Math.exp(-((ph + 0.5) % 1) * 22);
      const root = 55 * Math.pow(2, Math.round(3 * Math.sin(t * 0.05)) / 12);
      const chord = [1, 1.5, 2, 2.52, 3, 4, 5.04, 6, 8];
      const swell = 0.55 + 0.45 * Math.sin(t * 0.37);
      const out = new Float32Array(H);
      for (let y = 0; y < H; y++) {
        const f = rowF[y], lf = Math.log2(f);
        let v = 0.09 * Math.random() * (0.6 + 0.4 * Math.sin(lf * 3 + t * 0.3));                                          // noise floor
        for (let k = 0; k < chord.length; k++) v += (0.6 / (1 + k * 0.3)) * swell * gauss(lf - Math.log2(root * chord[k]), 0.03);
        v += 0.95 * kick * gauss(lf - Math.log2(50 + 60 * ph), 0.45);          // kick: pitch-dropping thump
        if (f > 4000) v += 0.55 * hat * Math.random();                          // hats: bright noise ticks
        out[y] = v;
      }
      return out;
    };

    let glitch = 0, slices = [], lastLo = 0, nextIdle = 0, last = 0;
    const tear = () => {                        // pick a fresh set of displaced slices
      slices = Array.from({ length: 2 + (Math.random() * 6 | 0) }, () => ({
        y: Math.random() * H | 0, h: 1 + (Math.random() * H * 0.06 | 0), dx: (Math.random() - 0.5) * W * 0.18 | 0,
      }));
    };
    function frame(now) {
      if (!still) requestAnimationFrame(frame);
      if (!still && now - last < 33) return;    // ~30 fps is plenty for this
      last = now;
      const t = now / 1000;
      let lo = 0, mid = 0, hi = 0, playing = false;
      if (an) {
        playing = [...document.querySelectorAll("audio")].some((a) => !a.paused && tapped.has(a));
        if (playing) { an.getByteFrequencyData(buf); an.getByteTimeDomainData(tbuf); lo = band(30, 160); mid = band(300, 2000); hi = band(4000, 14000); }
      }
      follow("lo", lo); follow("mid", mid); follow("hi", hi);

      // 1. waterfall: scroll left one pixel, paint the newest column at the right edge
      wg.drawImage(wf, -1, 0);
      let vals;
      if (playing) {
        const bin = ctx.sampleRate / an.fftSize;
        vals = rowF.map((f) => { const x = f / bin, i = Math.min(buf.length - 2, x | 0), fr = x - i; return Math.pow((buf[i] * (1 - fr) + buf[i + 1] * fr) / 255, 1.8) * 1.15; });
      } else vals = idleCol(t);
      const gain = playing ? 0.8 : 0.6;
      for (let y = 0; y < H; y++) {
        const li = Math.min(255, Math.max(0, Math.pow(Math.max(0, vals[y] * gain), 0.65) * 255)) | 0, p = y * 4;
        col.data[p] = LUT[li * 3]; col.data[p + 1] = LUT[li * 3 + 1]; col.data[p + 2] = LUT[li * 3 + 2]; col.data[p + 3] = 255;
      }
      wg.putImageData(col, W - 1, 0);

      g.globalCompositeOperation = "source-over";
      g.drawImage(wf, 0, 0);

      // 2. glitch: on kicks while playing, at random moments when idle
      const hit = playing ? env.lo - lastLo > 0.06 && env.lo > 0.35 : t > nextIdle;
      lastLo = env.lo;
      if (hit && glitch <= 0) { glitch = 3 + (Math.random() * 5 | 0); tear(); if (!playing) nextIdle = t + 2.5 + Math.random() * 6; }
      if (glitch > 0) {
        glitch--;
        if (Math.random() < 0.4) tear();
        for (const s of slices) {
          g.drawImage(wf, 0, s.y, W, s.h, s.dx, s.y, W, s.h);                 // displaced slice
          g.globalCompositeOperation = "lighter";
          g.globalAlpha = 0.5;
          g.fillStyle = "rgba(255,40,120,0.35)"; g.fillRect(s.dx + 2, s.y, W, 1);      // RGB tear edges
          g.fillStyle = "rgba(40,230,220,0.30)"; g.fillRect(s.dx - 2, s.y + s.h - 1, W, 1);
          g.globalAlpha = 1; g.globalCompositeOperation = "source-over";
        }
        if (Math.random() < 0.5) {                                                  // datamosh block
          const bw = W * (0.04 + Math.random() * 0.12) | 0, bh = H * (0.02 + Math.random() * 0.08) | 0;
          g.globalAlpha = 0.8;
          g.drawImage(wf, Math.random() * (W - bw), Math.random() * (H - bh), bw, bh, Math.random() * (W - bw), Math.random() * (H - bh), bw, bh);
          g.globalAlpha = 1;
        }
      }

      // 3. oscilloscope across the lower third (real waveform while playing)
      g.globalCompositeOperation = "lighter";
      const y0 = H * 0.68, amp = H * (playing ? 0.12 : 0.03);
      g.beginPath();
      for (let x = 0; x <= W; x += 2) {
        let v;
        if (playing) v = (tbuf[Math.floor((x / W) * (tbuf.length - 1))] - 128) / 128;
        else v = 0.6 * Math.sin(x * 0.05 + t * 2.1) * Math.sin(x * 0.007 - t * 0.4) + 0.25 * Math.sin(x * 0.31 + t * 9);
        x ? g.lineTo(x, y0 + v * amp) : g.moveTo(x, y0 + v * amp);
      }
      g.strokeStyle = `rgba(79,209,197,${(playing ? 0.3 + 0.45 * env.mid : 0.22).toFixed(3)})`;
      g.lineWidth = 1; g.stroke();
      g.globalCompositeOperation = "source-over";
    }
    requestAnimationFrame(frame);   // reduced motion: draws once, then holds
  })();

  let queued = false;
  new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; tick(); });
  }).observe(document.body, { childList: true, subtree: true, characterData: true });
  tick();
}
