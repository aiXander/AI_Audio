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
    if (!m || !rt || !seed) return;            // unknown format: leave upstream's text
    const head = t.split("Inference:")[0];
    const parts = head.split(/\s+·\s+/).map((s) => s.trim()).filter(Boolean);
    const prompt = parts.shift() || "(no prompt)";
    const neg = parts.filter((p) => p.startsWith("neg:"));
    const notes = parts.filter((p) => !p.startsWith("neg:"));
    const [tot, t5, smp, dec] = m.slice(1).map(Number);
    const other = Math.max(0, tot - t5 - smp - dec);
    const load = notes.find((n) => n.startsWith("DiT-load"));
    const tip = [`render ${sec(tot)}`, `text ${sec(t5)}`, `sampling ${sec(smp)}`, `decode ${sec(dec)}`]
      .concat(other > 50 ? [`load / other ${sec(other)}`] : [])
      .concat(sl ? [`${sl[1]} latent frames`] : []).join(" · ");
    host.innerHTML = `
      <div class="sa3-stats">
        <div class="p" title="${esc(prompt)}">${copyBtn(`data-copy="${esc(prompt)}"`)}${esc(prompt)}</div>
        ${neg.map((n) => `<div class="neg">${esc(n)}</div>`).join("")}
        ${warnHtml}
        <div class="row">
          <span class="k"><b>${Number(rt[1]).toFixed(1)}×</b>realtime</span>
          <span class="k seed" title="Click to put this seed in the Seed box" onclick="window.sa3SetSeed('${seed[1]}')"><b>${seed[1]}</b>seed</span>
          <div class="bar" title="${esc(tip)}${load ? " · " + esc(load) : ""}">
            <i class="t5" style="flex:${t5}"></i><i class="smp" style="flex:${smp}"></i><i class="dec" style="flex:${dec}"></i>${other > 50 ? `<i class="oth" style="flex:${other}"></i>` : ""}
          </div>
        </div>
      </div>`;
  }

  // Hide empty output slots; show an empty state in the player slot.
  // LoRA slots: a slot is active when it holds a dropped file or a loras/ folder pick.
  // Marks folder-picked slots (CSS drops their now-redundant drop zone) and puts
  // "N active" on the accordion header. Hidden slots aren't rendered at all.
  const LORA_NONE = "from loras/ folder…";      // studio.py _lora_library placeholder
  function loraSync() {
    let n = 0;
    for (const f of document.querySelectorAll(".block.sa3-lora-file")) {
      const card = f.closest(".styler");
      const dd = card && card.querySelector(".sa3-lora-dd input");
      const picked = !!dd && !!dd.value && dd.value !== LORA_NONE && !f.querySelector(".file-preview");
      if (card) card.classList.toggle("sa3-lora-picked", picked);
      if (picked || f.querySelector(".file-preview")) n++;
    }
    const hd = [...document.querySelectorAll(".label-wrap")].find((b) => b.textContent.trim().startsWith("LoRA adapters"));
    if (!hd) return;
    // gradio renders an accordion's content only after its first open, so open +
    // close it once on load (same frame, no flash) to count preloaded/restored slots
    if (!loraPrimed) {
      loraPrimed = true;
      if (!hd.classList.contains("open")) { hd.click(); requestAnimationFrame(() => { hd.click(); setTimeout(loraSync, 60); }); }
    }
    const lab = hd.querySelector(":scope > span");
    if (lab) n ? lab.setAttribute("data-lora", `${n} active`) : lab.removeAttribute("data-lora");
  }
  let loraPrimed = false;
  for (const ev of ["input", "change", "focusout", "click"]) document.addEventListener(ev, () => setTimeout(loraSync, 60), true);

  // Focus mode: the sidebar icon left of the logo (or "\") collapses the whole controls column so the
  // list of renders gets the full width; remembered in localStorage. Generate stays in the top bar.
  // Its rules live in a <style> of our own: body-level selectors can't go through gradio's css
  // (it prefixes every rule with .gradio-container).
  const FOCUS_KEY = "sa3-focus";
  const focusCss = document.createElement("style");
  focusCss.textContent = "body.sa3-focus #sa3-ctl, body.sa3-focus #sa3-split { display: none !important; }" +
    "body.sa3-focus #sa3-out { flex: 1 1 100% !important; max-width: none !important; }";
  document.head.appendChild(focusCss);
  const setFocus = (on) => {
    document.body.classList.toggle("sa3-focus", on);
    localStorage.setItem(FOCUS_KEY, on ? "1" : "");
    const b = document.getElementById("sa3-focusbtn");
    if (b) { b.classList.toggle("on", on); b.title = on ? "Show the controls (\\)" : "Hide the controls: full-width list (\\)"; }
  };
  const ICON_SIDEBAR = '<svg viewBox="0 0 16 16"><rect x="1.8" y="2.8" width="12.4" height="10.4" rx="2" stroke="currentColor" stroke-width="1.4" fill="none"/><path d="M6 3v10" stroke="currentColor" stroke-width="1.4"/><path class="fill" d="M2.6 3.6h2.7v8.8H2.6z" fill="currentColor"/></svg>';
  function mountFocus() {
    const brand = document.querySelector("#sa3-top .sa3h-brand");
    if (!brand || document.getElementById("sa3-focusbtn")) return;
    const b = document.createElement("button");
    b.id = "sa3-focusbtn";
    b.innerHTML = ICON_SIDEBAR;
    b.addEventListener("click", () => setFocus(!document.body.classList.contains("sa3-focus")));
    brand.prepend(b);
    setFocus(!!localStorage.getItem(FOCUS_KEY));
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "\\" && !e.metaKey && !e.ctrlKey && !e.altKey && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
      e.preventDefault(); setFocus(!document.body.classList.contains("sa3-focus"));
    }
  });

  // Column splitter: hovering the gap between controls (#sa3-ctl) and output (#sa3-out)
  // shows a grab bar; drag it to resize. The width sticks (localStorage) and the bar
  // fades out 1 s after the last hover/drag movement.
  const SPLIT_KEY = "sa3-split";
  const splitCss = document.createElement("style");
  document.head.appendChild(splitCss);
  const applySplit = (pct) => {
    splitCss.textContent = pct ? `#sa3-ctl{flex:0 0 calc(${pct}% - 6px) !important;max-width:none !important}` +
      `#sa3-out{flex:1 1 0 !important;max-width:none !important}` : "";
  };
  applySplit(parseFloat(localStorage.getItem(SPLIT_KEY)) || 0);
  function mountSplit() {
    const out = document.getElementById("sa3-out"), ctl = document.getElementById("sa3-ctl");
    if (!out || !ctl || out.parentElement !== ctl.parentElement) return;
    const row = out.parentElement;
    if (row.querySelector(":scope > #sa3-split")) return;
    const h = document.createElement("div");
    h.id = "sa3-split";
    h.innerHTML = '<div class="grip"></div>';
    row.appendChild(h);
    const grip = h.firstChild;
    let hideT = null, dwellT = null, drag = false, lastY = 0;
    const place = () => {   // centre the hit zone on the gap
      const a = ctl.getBoundingClientRect(), b = out.getBoundingClientRect(), r = row.getBoundingClientRect();
      const side = a.top < b.bottom && b.top < a.bottom && b.left > a.right - 1;   // not stacked (narrow screens)
      h.style.display = side ? "" : "none";
      h.style.left = ((a.right + b.left) / 2 - r.left) + "px";
    };
    const poke = (e) => {
      h.classList.add("on");
      if (e) grip.style.top = Math.max(30, e.clientY - h.getBoundingClientRect().top) + "px";
      clearTimeout(hideT);
      hideT = setTimeout(() => { if (!drag) h.classList.remove("on"); }, 1000);
    };
    // only a deliberate hover shows it: the cursor must stay in the gap for 0.5 s
    // (sliding across between the columns never triggers it)
    h.addEventListener("mouseenter", (e) => {
      lastY = e.clientY;
      clearTimeout(dwellT);
      dwellT = setTimeout(() => poke({ clientY: lastY }), 500);
    });
    h.addEventListener("mouseleave", () => clearTimeout(dwellT));
    h.addEventListener("mousemove", (e) => { lastY = e.clientY; if (h.classList.contains("on")) poke(e); });
    h.addEventListener("mousedown", (e) => {
      e.preventDefault(); drag = true; poke(e);
      document.body.classList.add("sa3-resizing");
      const move = (ev) => {
        const r = row.getBoundingClientRect();
        const pct = Math.min(62, Math.max(24, ((ev.clientX - r.left) / r.width) * 100));
        applySplit(pct); localStorage.setItem(SPLIT_KEY, pct.toFixed(2));
        place(); poke(ev);
      };
      const up = () => {
        drag = false; document.body.classList.remove("sa3-resizing"); poke();
        removeEventListener("mousemove", move); removeEventListener("mouseup", up);
      };
      addEventListener("mousemove", move); addEventListener("mouseup", up);
    });
    h.addEventListener("dblclick", () => { applySplit(0); localStorage.removeItem(SPLIT_KEY); place(); });   // back to 40/60
    place();
    new ResizeObserver(place).observe(row);
    addEventListener("resize", place);
  }

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
    applyMainFilter();
    mountMore();
    hideDeleted();
    progressLoop(anyBusy);
    loraSync();
    mountSplit();
    mountFocus();
  }
  // ── history rows: tiny transport bar above a full-width spectrogram ──
  const fmt = (t) => !isFinite(t) ? "0:00" : `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  const ICON_PLAY = '<svg viewBox="0 0 16 16"><path d="M4 2.5v11l9-5.5z" fill="currentColor"/></svg>';
  const ICON_PAUSE = '<svg viewBox="0 0 16 16"><path d="M4 2.5h3v11H4zM9 2.5h3v11H9z" fill="currentColor"/></svg>';
  const ICON_DL = '<svg viewBox="0 0 16 16"><path d="M8 2v8m0 0L4.5 6.5M8 10l3.5-3.5M3 13h10" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  // library rows serve audio as sa3/audio?name=…, so they carry the name in data-name
  const clipName = (a) => a.dataset.name || decodeURIComponent((a.getAttribute("src") || "").split("/").pop() || "clip.wav");
  const ICON_COPY = '<svg viewBox="0 0 16 16"><rect x="5.5" y="5.5" width="8" height="8" rx="1.8" stroke="currentColor" stroke-width="1.4" fill="none"/><path d="M10.5 3.5v-.2A1.3 1.3 0 0 0 9.2 2H3.8A1.3 1.3 0 0 0 2.5 3.3v5.4A1.3 1.3 0 0 0 3.8 10h.2" stroke="currentColor" stroke-width="1.4" fill="none"/></svg>';
  const ICON_OK = '<svg viewBox="0 0 16 16"><path d="M3 8.5l3 3 7-7" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const ICON_RESTORE = '<svg viewBox="0 0 16 16"><path d="M2.8 8a5.2 5.2 0 1 0 1.6-3.8M2.5 2.2v3h3" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/><circle cx="8" cy="8" r="1.4" fill="currentColor"/></svg>';
  const ICON_FILTER = '<svg viewBox="0 0 16 16"><path d="M2 4h12M4.5 8h7M7 12h2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
  const ICON_TRASH = '<svg viewBox="0 0 16 16"><path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.3a1 1 0 0 0 1 .9h3.8a1 1 0 0 0 1-.9l.6-8.3M6.8 7v4.2M9.2 7v4.2" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
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

  // click a clip's prompt line -> copy its full prompt (from the sidecar) + a brief "prompt copied"
  // pill at the cursor; hovering the line shows a copy icon on its right. This CSS goes in our own
  // <style>: gradio's css prefixer splits on commas, which would break the data: URI below.
  const capCss = document.createElement("style");
  capCss.textContent = `.sa3-cap[data-copy-name] { position: relative; cursor: pointer; padding-right: 24px !important; }
    .sa3-cap[data-copy-name]::after { content: ""; position: absolute; right: 2px; top: 1px; width: 14px; height: 14px;
      background: currentColor; opacity: 0; transition: opacity .12s;
      -webkit-mask: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><rect x='5.5' y='5.5' width='8' height='8' rx='1.8' stroke='black' stroke-width='1.4' fill='none'/><path d='M10.5 3.5v-.2A1.3 1.3 0 0 0 9.2 2H3.8A1.3 1.3 0 0 0 2.5 3.3v5.4A1.3 1.3 0 0 0 3.8 10h.2' stroke='black' stroke-width='1.4' fill='none'/></svg>") center / contain no-repeat; }
    .sa3-cap[data-copy-name]:hover { color: #e9e9f1 !important; }
    .sa3-cap[data-copy-name]:hover::after { opacity: .85; }
    .sa3-cap[data-copy-name].copied::after { opacity: 1; background: #7ee2a8; animation: sa3-pop .32s ease-out;
      -webkit-mask-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><path d='M3 8.5l3 3 7-7' stroke='black' stroke-width='2' fill='none' stroke-linecap='round' stroke-linejoin='round'/></svg>"); }
    @keyframes sa3-pop { 0% { transform: scale(.3); opacity: 0; } 60% { transform: scale(1.25); opacity: 1; } 100% { transform: scale(1); } }
    #sa3-toast { position: fixed; z-index: 400; pointer-events: none; padding: 3px 8px; border-radius: 999px;
      font: 500 10.5px "JetBrains Mono", ui-monospace, monospace; color: #b9f0cf; background: rgba(18,18,26,0.62);
      border: 1px solid rgba(126,226,168,0.25); backdrop-filter: blur(6px);
      opacity: 0; transform: translate(-50%, -100%) translateY(2px); transition: opacity .15s, transform .15s; }
    #sa3-toast.on { opacity: .9; transform: translate(-50%, -100%) translateY(-6px); }`;
  document.head.appendChild(capCss);
  let toastEl = null, toastT = null;
  function toast(txt, x, y) {
    if (!toastEl) { toastEl = document.createElement("div"); toastEl.id = "sa3-toast"; document.body.appendChild(toastEl); }
    toastEl.textContent = txt;
    toastEl.style.left = x + "px"; toastEl.style.top = y + "px";
    toastEl.classList.add("on");
    clearTimeout(toastT);
    toastT = setTimeout(() => toastEl.classList.remove("on"), 750);
  }
  document.addEventListener("click", async (e) => {
    const cap = e.target.closest && e.target.closest(".sa3-cap[data-copy-name]");
    if (!cap || String(getSelection()).length) return;      // selecting text: leave it alone
    e.preventDefault(); e.stopPropagation();
    const name = cap.dataset.copyName, x = e.clientX, y = e.clientY;
    const text = ((await getLabel(name)) || labels.get(name) || libByName.get(name) || {}).prompt;
    if (!text) return;
    try { await navigator.clipboard.writeText(text); } catch (err) { console.warn("[sa3] copy failed", err); return; }
    toast("prompt copied", x, y);
    cap.classList.add("copied");                          // copy icon pops into a green check
    clearTimeout(cap._cpT);
    cap._cpT = setTimeout(() => cap.classList.remove("copied"), 900);
  }, true);

  // "use these settings": studio.py's restore handler reads the name from here
  window.sa3Restore = (name) => {
    const b = document.getElementById("sa3-restore-btn");
    if (!b || !name) return;
    window.__sa3RestoreName = name;
    b.click();
  };

  function enhanceHistory() {
    document.querySelectorAll(".sa3-player .blk audio:not([data-sa3]), #sa3-hist .blk audio:not([data-sa3]), #sa3-more .blk audio:not([data-sa3]), #sa3-lib .blk audio:not([data-sa3])").forEach((a) => {
      a.dataset.sa3 = "1";
      const row = a.parentElement && a.parentElement.parentElement;   // flex row: [audio wrap, spectrogram wrap]
      if (!row) return;
      row.classList.add("sa3-hrow");
      a.parentElement.classList.add("sa3-native");
      const bar = document.createElement("div");
      bar.className = "sa3-mini";
      bar.innerHTML = `<button class="pp" title="Play / pause">${ICON_PLAY}</button>
        <span class="tm">0:00 / 0:00</span><span class="sp"></span>
        <a class="dl" title="Download" href="${a.getAttribute("src")}" download="${esc(clipName(a))}">${ICON_DL}</a>`;   // the whole row drags (makeDraggable); enhanceLabels puts the label strip between .tm and .dl
      row.insertBefore(bar, row.firstChild);
      const cap = row.nextElementSibling;
      if (cap && cap.tagName === "DIV" && !cap.classList.contains("sa3-lab")) {
        cap.classList.add("sa3-cap");
        // prompt only: upstream's "Xm ago ·" prefix and its "· neg: … · seed … · cfg … · lora …" tail
        // go (the settings button's hover card shows all of those)
        cap.textContent = cap.textContent.replace(/^\s*(?:\d+[smhd] ago|just now)\s*·\s*/, "")
          .split(/\s·\s(?:neg: |seed -?\d)/)[0].trim();
        cap.dataset.copyName = clipName(a);     // click anywhere on the prompt line copies the prompt
      }
      const pp = bar.querySelector(".pp"), tm = bar.querySelector(".tm");
      const sync = () => {
        pp.innerHTML = a.paused ? ICON_PLAY : ICON_PAUSE;
        tm.textContent = `${fmt(a.currentTime)} / ${fmt(isFinite(a.duration) ? a.duration : Number(a.dataset.dur))}`;
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
    document.querySelectorAll("#sa3-out .blk:not([data-sa3drag]), #sa3-lib .blk:not([data-sa3drag])").forEach((blk) => {
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
        if (libEl && libEl.contains(blk)) libEl.classList.add("dragging");   // let the drop land on the inputs behind
      });
      blk.addEventListener("dragend", () => {
        document.body.classList.remove("sa3-dragging");
        if (libEl) libEl.classList.remove("dragging");
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
  // LoRA badge: only on renders that had ≥1 active LoRA; hover lists them (instant CSS tooltip, data-tip)
  const ICON_LORA = '<svg viewBox="0 0 16 16"><path d="M8 2.2l5.8 3L8 8.2 2.2 5.2z" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linejoin="round"/><path d="M2.2 8.1L8 11.1l5.8-3M2.2 10.9L8 13.9l5.8-3" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  function loraChip(ls) {
    if (!ls || !ls.length) return "";
    const tip = ls.map((l) => (l.name || "?").replace(/\.safetensors$/, "")
      + (l.strength !== undefined && l.strength !== null && l.strength !== 1 ? ` ×${l.strength}` : "")
      + (l.steps ? ` @ steps ${l.steps[0]}–${l.steps[1]}` : "")).join("\n");
    return `<span class="tag lora" data-tip="${esc((ls.length > 1 ? ls.length + " LoRAs\n" : "LoRA\n") + tip)}">${ICON_LORA}${ls.length > 1 ? `<b>${ls.length}</b>` : ""}</span>`;
  }
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
    const lr = libByName.get(name);
    if (lr) Object.assign(lr, patch);          // Library filters + counts see the edit
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
    tg.innerHTML = loraChip(d.loras) + bpmChip(d.bpm) + (d.tags || []).map((t, i) =>
      `<span class="tag">${esc(t)}<button data-i="${i}" title="Remove tag">×</button></span>`).join("");
  }
  function enhanceLabels() {
    document.querySelectorAll("#sa3-out .blk, #sa3-lib .blk").forEach((blk) => {
      const a = blk.querySelector("audio");
      if (!a) return;
      const name = clipName(a);
      // history / Library rows: the label strip lives in the transport bar (play · time · labels · download)
      const mini = blk.querySelector(".sa3-mini");
      const dock = (x) => { if (mini && x.parentElement !== mini) mini.insertBefore(x, mini.querySelector(".dl")); };
      let el = blk.querySelector(".sa3-lab");
      if (el && el.dataset.name === name) { dock(el); return; }
      if (el) el.remove();
      el = document.createElement("div");
      el.className = "sa3-lab";
      el.dataset.name = name;
      el.innerHTML = `<div class="sc" title="Score 0-10 (click the lit one again to clear)">${
        [...Array(11).keys()].map((v) => `<button data-v="${v}">${v}</button>`).join("")}</div>
        <div class="tags"></div>
        <input class="add" placeholder="+ tag" maxlength="60" spellcheck="false">
        <button class="rs">${ICON_RESTORE}<span>settings</span></button>
        <button class="rm" title="Delete: scores it 0 and moves it to outputs/sa3-gradio/deleted/ (kept as a negative training example)">${ICON_TRASH}</button>`;
      el.addEventListener("dragstart", (e) => { e.preventDefault(); e.stopPropagation(); });
      el.addEventListener("mousedown", (e) => e.stopPropagation());
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        const d = labels.get(name) || { score: null, tags: [] };
        const sb = e.target.closest(".sc button");
        if (sb) { const v = Number(sb.dataset.v); saveLabel(name, { score: d.score === v ? null : v }); return; }
        if (e.target.closest(".rs")) { window.sa3Restore(name); if (libEl && libEl.contains(el)) toggleLibrary(false); return; }
        if (e.target.closest(".rm")) { deleteClip(name); return; }
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
      dock(el);
      getLabel(name).then(() => paintLabel(el));
      paintLabel(el);
    });
  }

  // ── settings hover card: rest 0.5 s on a clip's "settings" button → every setting that made it
  //    (GET /sa3/settings = what the button restores), in a floating card. Fetched once per clip. ──
  const setCache = new Map();
  let setCard = null, setTimer = null, setBtn = null;
  const num = (v) => (typeof v === "number" ? +v.toFixed(3) : v);
  const base = (x) => (x || "").split("/").pop();
  function settingsHtml(d) {
    const rows = [];
    const add = (k, v, cls = "") => { if (v !== undefined && v !== null && v !== "") rows.push(`<dt>${k}</dt><dd class="${cls}">${v}</dd>`); };
    const a2a = d.mode && d.mode.includes("a2a") || d.mode === "audio-to-audio";
    add("Model", [d.dit, d.decoder].filter(Boolean).map(esc).join(" · "));
    add("Mode", d.mode && d.mode !== "text-to-audio" ? esc(d.mode) : null);
    add("Seed", d.seed, "m");
    add("Length", d.seconds !== undefined ? `${num(d.seconds)} s` : null, "m");
    add("Steps", d.steps, "m");
    add("CFG", num(d.cfg), "m");
    add("APG", num(d.apg), "m");
    add(a2a ? "Init noise" : "Sigma max", num(d.sigma_max), "m");
    add("Negative", d.negative_prompt ? esc(d.negative_prompt) : null);
    add("LoRAs", (d.loras || []).map((l) => `<span class="lr">${esc(base(l.path || l.name).replace(/\.safetensors$/, ""))}<i>${
      l.strength !== 1 ? ` ×${num(l.strength)}` : ""}${l.steps ? ` @ ${l.steps[0]}–${l.steps[1]}` : ""}</i></span>`).join("") || null);
    add("A→A input", d.a2a_audio ? esc(base(d.a2a_audio)) : null);
    add("Inpaint", d.inpaint_audio || d.inpaint_range_sec ? esc([base(d.inpaint_audio),
      d.inpaint_range_sec ? `${num(d.inpaint_range_sec[0])}–${num(d.inpaint_range_sec[1])} s` : ""].filter(Boolean).join(" · ")) : null);
    return `<div class="pr">${esc(d.prompt || "(no prompt)")}</div><dl>${rows.join("")}</dl>
      <div class="ft">${d.full === false ? "older render: steps, APG and LoRAs weren't recorded · " : ""}click to restore all</div>`;
  }
  function placeSetCard() {
    if (!setCard || !setBtn) return;
    const b = setBtn.getBoundingClientRect(), w = setCard.offsetWidth, h = setCard.offsetHeight;
    const left = Math.max(8, Math.min(b.right - w, innerWidth - w - 8));
    const top = b.bottom + 8 + h < innerHeight ? b.bottom + 8 : Math.max(8, b.top - 8 - h);
    setCard.style.left = left + "px"; setCard.style.top = top + "px";
  }
  async function showSettings(btn) {
    const name = btn.closest(".sa3-lab")?.dataset.name;
    if (!name) return;
    if (!setCache.has(name)) {
      try { const r = await fetch("sa3/settings?name=" + encodeURIComponent(name)); setCache.set(name, r.ok ? await r.json() : null); }
      catch (e) { return; }
    }
    const d = setCache.get(name);
    if (!d || setBtn !== btn) return;          // pointer already left
    if (!setCard) { setCard = document.createElement("div"); setCard.id = "sa3-setcard"; document.body.appendChild(setCard); }
    setCard.innerHTML = settingsHtml(d);
    setCard.classList.add("on");
    placeSetCard();
  }
  function hideSettings() {
    clearTimeout(setTimer); setTimer = null; setBtn = null;
    if (setCard) setCard.classList.remove("on");
  }
  document.addEventListener("mouseover", (e) => {
    const btn = e.target.closest && e.target.closest(".sa3-lab .rs");
    if (btn === setBtn) return;
    hideSettings();
    if (!btn) return;
    setBtn = btn;
    setTimer = setTimeout(() => showSettings(btn), 500);
  });
  for (const ev of ["scroll", "mousedown", "keydown"]) addEventListener(ev, hideSettings, true);

  // ── delete: POST /sa3/delete (library.py) scores the clip 0 and moves it to deleted/.
  //    Hidden at once; gradio re-renders history from its own state, so tick() keeps
  //    re-hiding every block of a deleted clip (a reload drops them for good) ──
  const deleted = new Set();
  function hideDeleted() {
    if (!deleted.size) return;
    document.querySelectorAll("#sa3-out .blk, #sa3-lib .blk").forEach((b) => {
      const a = b.querySelector("audio");
      if (a && deleted.has(clipName(a))) {
        if (!a.paused) a.pause();
        b.classList.add("sa3-deleted");
      }
    });
    const cur = document.querySelector(".sa3-player audio"), tm = document.querySelector(".sa3-timing");
    if (tm) tm.classList.toggle("sa3-deleted", !!cur && deleted.has(clipName(cur)));   // the current clip's stats line
  }
  async function deleteClip(name) {
    deleted.add(name);
    hideDeleted();
    try {
      const r = await fetch("sa3/delete?name=" + encodeURIComponent(name), { method: "POST" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      labels.delete(name);
      libRows = libRows.filter((x) => x.name !== name);
      libByName.delete(name);
      if (libEl && libEl.classList.contains("open")) renderLibrary();
    } catch (e) {              // not deleted on disk: bring it back
      console.warn("[sa3] delete failed", e);
      deleted.delete(name);
      document.querySelectorAll(".blk.sa3-deleted").forEach((b) => b.classList.remove("sa3-deleted"));
      hideDeleted();
    }
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
  // tags, measured BPM. Each row is built in the same raw shape upstream's
  // render_player(small=True) gives the main-page history, so enhanceHistory /
  // enhanceLabels / makeDraggable turn it into the identical row (mini transport,
  // spectrogram, caption, score + tags + settings strip). Filters persist in
  // localStorage and, unless "main" is off, also hide main-page history rows.
  const LIB_KEY = "sa3-lib-filters";
  const LIB_DEF = { q: "", bmin: "", bmax: "", tiers: [], smin: 0, smax: 10, unscored: true, tags: [], dits: [], sort: "new", main: true };
  const TIERS = [["green", "on tempo"], ["yellow", "close"], ["orange", "off"], ["none", "no BPM asked"], ["nobeat", "no beat"]];
  let LF = { ...LIB_DEF };
  try { LF = { ...LIB_DEF, ...JSON.parse(localStorage.getItem(LIB_KEY) || "{}") }; } catch (e) { /* keep defaults */ }
  let libRows = [], libByName = new Map(), libEl = null, libTimer = null, libLoaded = false, libFetching = false, libLastFetch = 0;
  const libRowEls = new Map();                 // name -> row element, reused across re-renders (keeps playback)
  const mainVis = new Map();                   // name -> shown on the main page; reset only when the filters change
  const libActive = () => Object.keys(LIB_DEF).filter((k) => k !== "sort" && k !== "main" && JSON.stringify(LF[k]) !== JSON.stringify(LIB_DEF[k])).length;

  function mountLibrary() {
    const top = document.getElementById("sa3-right") || document.getElementById("sa3-top");
    if (!top || document.getElementById("sa3-libbtn")) return;
    const btn = document.createElement("button");
    btn.id = "sa3-libbtn";
    btn.title = "Browse and filter every render (L)";
    btn.innerHTML = `${ICON_FILTER}<span>Library</span><b></b>`;
    btn.addEventListener("click", () => toggleLibrary());
    top.appendChild(btn);
    paintLibBadge();
    loadLibrary();                             // the main-page filter needs the rows too
  }
  function paintLibBadge() {
    const b = document.querySelector("#sa3-libbtn b");
    if (b) { const n = libActive(); b.textContent = n || ""; b.style.display = n ? "" : "none"; }
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && libEl && libEl.classList.contains("open")) { toggleLibrary(false); return; }
    if (e.key === "l" && !e.metaKey && !e.ctrlKey && !e.altKey && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) toggleLibrary();
  });

  // main page: hide history rows the active filters exclude. A row's verdict is
  // cached until the filters change, so scoring a clip never yanks it away mid-edit.
  function applyMainFilter() {
    const hist = document.getElementById("sa3-hist");
    if (!hist) return;
    const on = LF.main && libActive() > 0 && libLoaded;
    let hidden = 0, missing = false;
    hist.querySelectorAll(".blk").forEach((b) => {
      const a = b.querySelector("audio");
      if (!a) return;
      const name = clipName(a);
      let vis = true;
      if (on) {
        const r = libByName.get(name);
        if (!r) missing = true;
        else { if (!mainVis.has(name)) mainVis.set(name, libFilter(r)); vis = mainVis.get(name); }
      }
      b.classList.toggle("sa3-filtered", !vis);
      if (!vis) hidden++;
    });
    const head = hist.previousElementSibling;
    if (head) {
      let note = head.querySelector(".sa3-fnote");
      const txt = hidden ? `· ${hidden} hidden by Library filters` : "";
      if (!txt && note) note.remove();
      else if (txt) {
        if (!note) {
          note = document.createElement("span");
          note.className = "sa3-fnote";
          note.title = "Open the Library to change the filters";
          note.addEventListener("click", () => toggleLibrary(true));
          head.appendChild(note);
        }
        if (note.textContent !== txt) note.textContent = txt;
      }
    }
    if (on && missing && !libFetching && Date.now() - libLastFetch > 4000) loadLibrary();   // a new render
  }

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
            <label class="ck main" title="Hide main-page history rows these filters exclude"><input type="checkbox" class="mainf"> Filter the main page too</label>
            <div class="f"><span class="h">Prompt contains</span><input class="q" type="search" placeholder="sitar techno…" spellcheck="false"></div>
            <div class="f"><span class="h">Measured BPM</span>
              <div class="rng"><input class="bmin" type="number" min="0" max="300" placeholder="min"><i>–</i><input class="bmax" type="number" min="0" max="300" placeholder="max"></div></div>
            <div class="f"><span class="h">BPM vs prompt</span><div class="chips tiers"></div></div>
            <div class="f"><span class="h">Score<b class="sv"></b></span>
              <div class="dual"><div class="fill"></div>
                <input type="range" class="smin" min="0" max="10" step="1" aria-label="Minimum score">
                <input type="range" class="smax" min="0" max="10" step="1" aria-label="Maximum score"></div>
              <div class="dticks">${[...Array(11).keys()].map((v) => `<span>${v}</span>`).join("")}</div>
              <label class="ck"><input type="checkbox" class="unscored"> include unscored</label></div>
            <div class="f"><span class="h">Tags <em>all must match</em></span><div class="chips tagc"></div></div>
            <div class="f"><span class="h">Model</span><div class="chips ditc"></div></div>
            <button class="reset">Reset filters</button>
          </aside>
          <div class="list"></div>
        </div>
      </div>`;
    document.body.appendChild(libEl);
    const $ = (s) => libEl.querySelector(s);
    libEl.addEventListener("mousedown", (e) => { if (e.target === libEl) toggleLibrary(false); });
    $(".x").addEventListener("click", () => toggleLibrary(false));
    const set = (patch) => {
      LF = { ...LF, ...patch }; localStorage.setItem(LIB_KEY, JSON.stringify(LF));
      mainVis.clear(); renderLibrary(); paintLibBadge(); applyMainFilter();
    };
    $(".q").addEventListener("input", (e) => set({ q: e.target.value }));
    for (const k of ["bmin", "bmax"]) $("." + k).addEventListener("input", (e) => set({ [k]: e.target.value }));
    const lo = $(".smin"), hi = $(".smax");
    lo.addEventListener("input", () => { if (+lo.value > +hi.value) lo.value = hi.value; paintScore(); set({ smin: +lo.value }); });
    hi.addEventListener("input", () => { if (+hi.value < +lo.value) hi.value = lo.value; paintScore(); set({ smax: +hi.value }); });
    $(".unscored").addEventListener("change", (e) => set({ unscored: e.target.checked }));
    $(".mainf").addEventListener("change", (e) => set({ main: e.target.checked }));
    $(".sort").addEventListener("change", (e) => set({ sort: e.target.value }));
    $(".reset").addEventListener("click", () => { set({ ...LIB_DEF, sort: LF.sort, main: LF.main }); syncLibInputs(); });
    const toggleIn = (key, v) => set({ [key]: LF[key].includes(v) ? LF[key].filter((x) => x !== v) : [...LF[key], v] });
    libEl.addEventListener("click", (e) => {
      const c = e.target.closest("[data-chip]");
      if (c) { toggleIn(c.dataset.chip, c.dataset.v); return; }
      const sk = e.target.closest("[data-seek]");          // click the spectrogram to seek
      if (!sk) return;
      const a = sk.closest(".blk").querySelector("audio"), f = (e.clientX - sk.getBoundingClientRect().left) / sk.clientWidth;
      const go = () => { a.currentTime = f * (a.duration || 0); a.play(); };
      if (a.readyState >= 1) go(); else { a.addEventListener("loadedmetadata", go, { once: true }); a.preload = "metadata"; a.load(); }
    });
    // media events don't bubble: listen in the capture phase for every row's <audio>
    libEl.addEventListener("play", (e) => {
      document.querySelectorAll("audio").forEach((o) => { if (o !== e.target) o.pause(); });
    }, true);
    libEl.addEventListener("timeupdate", (e) => {
      const a = e.target, p = a.closest(".blk")?.querySelector(".ph");
      if (p && a.duration) p.style.left = (100 * a.currentTime / a.duration) + "%";
    }, true);
    syncLibInputs();
  }
  function paintScore() {
    const lo = libEl.querySelector(".smin"), hi = libEl.querySelector(".smax");
    const a = +lo.value, b = +hi.value;
    const fill = libEl.querySelector(".dual .fill");
    fill.style.left = a * 10 + "%"; fill.style.right = (100 - b * 10) + "%";
    lo.style.zIndex = a === b && a >= 5 ? 3 : 1;           // stacked thumbs: the one that can still move wins
    hi.style.zIndex = 2;
    libEl.querySelector(".sv").textContent = a === 0 && b === 10 ? "any" : a === b ? `${a}` : `${a}–${b}`;
  }
  function syncLibInputs() {
    const $ = (s) => libEl.querySelector(s);
    $(".q").value = LF.q; $(".bmin").value = LF.bmin; $(".bmax").value = LF.bmax;
    $(".smin").value = LF.smin; $(".smax").value = LF.smax; $(".unscored").checked = LF.unscored; $(".sort").value = LF.sort;
    $(".mainf").checked = LF.main;
    paintScore();
  }
  async function loadLibrary() {
    libFetching = true;
    try {
      const r = await fetch("sa3/library");
      if (r.ok) {
        libRows = await r.json();
        libByName = new Map(libRows.map((x) => [x.name, x]));
        libLoaded = true;
        for (const x of libRows) {           // seed the label cache: no per-row GET /sa3/label
          if (labels.has(x.name)) continue;
          labels.set(x.name, { score: x.score, tags: x.tags || [], bpm: x.bpm, prompt: x.prompt, loras: x.loras || [] });
          if (x.bpm && x.bpm.pending) pollBpm(x.name, 0);
        }
      }
    } catch (e) { console.warn("[sa3] library fetch failed", e); }
    libFetching = false; libLastFetch = Date.now();
    renderLibrary();
    applyMainFilter();
    clearTimeout(libTimer);                     // BPM still being measured: refresh shortly
    if (libEl && libEl.classList.contains("open") && libRows.some((x) => x.bpm && x.bpm.pending)) libTimer = setTimeout(loadLibrary, 2500);
  }
  function toggleLibrary(want) {
    if (!libEl) buildLibrary();
    const open = want === undefined ? !libEl.classList.contains("open") : want;
    libEl.classList.toggle("open", open);
    document.getElementById("sa3-libbtn")?.classList.toggle("on", open);
    if (open) { loadLibrary(); setTimeout(() => libEl.querySelector(".q").focus({ preventScroll: true }), 60); }
    else { clearTimeout(libTimer); libEl.querySelectorAll("audio").forEach((a) => a.pause()); }
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
  const DIT_SHORT = { medium: "med", "sm-music": "sm-mus", "sm-sfx": "sm-sfx" };
  // one library row, in upstream's render_player(small=True) shape (enhanced by tick())
  function libRowEl(r) {
    let el = libRowEls.get(r.name);
    if (!el) { el = buildRow(r); libRowEls.set(r.name, el); }
    return el;
  }
  function buildRow(r) {
    const n = encodeURIComponent(r.name);
    const meta = [r.dit ? DIT_SHORT[r.dit] || r.dit : null,
      r.cfg !== undefined && r.cfg !== null && r.cfg !== 1 ? `cfg ${r.cfg}` : null,
      r.mode && r.mode !== "text-to-audio" ? r.mode : null, r.lora ? "LoRA" : null, libAgo(r.ts)].filter(Boolean);
    el = document.createElement("div");
    el.className = "blk";
    el.dataset.key = r.name;
    el.innerHTML = `
      <div style="display:flex; gap:8px; align-items:center">
        <div style="flex:1; min-width:0"><audio controls preload="none" style="width:100%" data-name="${esc(r.name)}"
          ${r.seconds ? `data-dur="${r.seconds}"` : ""} src="sa3/audio?name=${n}"></audio></div>
        <div style="flex:1; min-width:0">${r.has_spec ? `<div data-seek style="position:relative; cursor:pointer" title="click to seek">
          <img loading="lazy" src="sa3/spec?name=${n}" alt="spectrogram" style="width:100%; display:block">
          <div class="ph" style="position:absolute; top:0; bottom:0; left:0%; width:2px; pointer-events:none"></div></div>` : ""}</div>
      </div>
      <div style="font-size:0.8em; margin:2px 0; color:#888">${esc(r.prompt || "(no prompt)")} · seed ${esc(String(r.seed ?? "?"))}${meta.length ? " · " + esc(meta.join(" · ")) : ""}</div>`;
    return el;
  }

  // ── main list pagination: the session list holds the newest 10 (studio.py HISTORY_ON_LOAD);
  //    each time the output column is scrolled to the bottom, 10 older renders are appended
  //    in #sa3-more (built from GET /sa3/library rows, outside gradio's slots so a re-render of
  //    the history doesn't wipe them). With "Filter the main page too" on, they're filtered. ──
  const PAGE = 10;
  let moreN = 0, moreEl = null, moreSig = "", moreBusy = false, moreEnd = false;
  const moreRowEls = new Map();
  function mountMore() {
    const hs = document.querySelector(".block.sa3-history"), out = document.getElementById("sa3-out");
    if (!hs || !out) return;
    if (!moreEl) {
      moreEl = document.createElement("div");
      moreEl.id = "sa3-more";
      moreEl.addEventListener("click", (e) => {             // click the spectrogram to seek
        const sk = e.target.closest("[data-seek]");
        if (!sk) return;
        const a = sk.closest(".blk").querySelector("audio"), f = (e.clientX - sk.getBoundingClientRect().left) / sk.clientWidth;
        const go = () => { a.currentTime = f * (a.duration || 0); a.play(); };
        if (a.readyState >= 1) go(); else { a.addEventListener("loadedmetadata", go, { once: true }); a.preload = "metadata"; a.load(); }
      });
      moreEl.addEventListener("play", (e) => document.querySelectorAll("audio").forEach((o) => { if (o !== e.target) o.pause(); }), true);
      moreEl.addEventListener("timeupdate", (e) => {
        const a = e.target, ph = a.closest(".blk")?.querySelector(".ph");
        if (ph && a.duration) ph.style.left = (100 * a.currentTime / a.duration) + "%";
      }, true);
    }
    if (hs.nextElementSibling !== moreEl) hs.after(moreEl);
    if (!out.dataset.sa3more) {
      out.dataset.sa3more = "1";
      out.addEventListener("scroll", () => { if (out.scrollTop + out.clientHeight >= out.scrollHeight - 60) loadMore(); }, { passive: true });
    }
    const sig = [moreN, moreEnd, LF.main && libActive() ? JSON.stringify(LF) : "", libRows.length,
      document.querySelectorAll(".sa3-player audio, #sa3-hist audio").length, document.querySelector(".sa3-player audio")?.src].join("|");
    if (sig !== moreSig) { moreSig = sig; renderMore(); }
  }
  function moreRows() {
    const shown = [...document.querySelectorAll(".sa3-player audio, #sa3-hist audio")].map(clipName);
    const seen = new Set(shown);
    const ts = shown.map((n) => libByName.get(n)?.ts).filter((t) => t !== undefined);
    const floor = ts.length ? Math.min(...ts) : Infinity;   // only renders older than the session list
    let rows = libRows.filter((r) => !seen.has(r.name) && !deleted.has(r.name) && r.ts < floor);
    if (LF.main && libActive() > 0) rows = rows.filter(libFilter);
    return rows.sort((a, b) => b.ts - a.ts);
  }
  function renderMore() {
    if (!moreEl) return;
    const rows = moreRows();
    const kids = rows.slice(0, moreN).map((r) => {
      let el = moreRowEls.get(r.name);
      if (!el) { el = buildRow(r); moreRowEls.set(r.name, el); }
      return el;
    });
    if (moreEnd && rows.length <= moreN) {
      const end = document.createElement("div"); end.className = "end"; end.textContent = "That's every render.";
      kids.push(end);
    }
    moreEl.replaceChildren(...kids);           // re-inserted synchronously: a playing row keeps playing
  }
  async function loadMore() {
    if (moreBusy) return;
    moreBusy = true;
    try {
      if (!libLoaded || Date.now() - libLastFetch > 30000) await loadLibrary();
      if (moreRows().length > moreN) moreN += PAGE;
      if (moreRows().length <= moreN) moreEnd = true;   // bottom reached: say so instead of staying silent
      mountMore();
    } finally { moreBusy = false; }
  }
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
    const note = (cls, txt) => { const d = document.createElement("div"); d.className = cls; d.textContent = txt; return d; };
    const kids = shown.map(libRowEl);
    if (rows.length > shown.length) kids.push(note("more", `+ ${rows.length - shown.length} more: narrow the filters`));
    if (!shown.length) kids.push(note("empty", libRows.length ? "Nothing matches these filters." : "No renders yet."));
    $(".list").replaceChildren(...kids);        // re-inserted synchronously: a playing row keeps playing
  }

  // ── progress: best-effort ETA from past render speeds (GET /sa3/progress) ──
  // The server only moves its projected finish time on real transitions (a sampling step
  // finished, decode started; bumps `rev`). Here we anchor t0/end on each new rev and animate
  // bar + countdown locally every frame, so the display is smooth between transitions. The bar
  // eases toward its target and never moves backwards (a later finish just makes it stall).
  let progTimer = null, progEl = null, progRaf = null;
  const prog = { run: null, rev: -1, t0: 0, end: 0, shown: 0, label: "", txt: "", waiting: 0 };
  function showQueued(n) {          // "+N queued" on Generate and in the progress line
    const g = document.getElementById("sa3-generate");
    if (g) { if (n > 0) g.dataset.q = n; else delete g.dataset.q; }
    prog.waiting = n;
  }
  function progressFrame() {
    const fill = progEl.querySelector("i"), txt = progEl.querySelector(".txt");
    if (!progEl.classList.contains("wait") && prog.rev >= 0) {
      const now = performance.now(), span = Math.max(1, prog.end - prog.t0);
      const over = now > prog.end;
      const target = over ? 0.985 : Math.min(0.985, (now - prog.t0) / span);
      prog.shown = Math.max(prog.shown, prog.shown + (target - prog.shown) * 0.08);
      fill.style.width = (prog.shown * 100).toFixed(2) + "%";
      const ls = Math.ceil((prog.end - now) / 1000);
      const left = over || ls < 1 ? "almost done"
        : `~${ls >= 60 ? Math.floor(ls / 60) + "m " + String(ls % 60).padStart(2, "0") : ls} s left`;
      const t = `${prog.label} · ${left}${prog.waiting ? ` · +${prog.waiting} queued` : ""}`;
      if (t !== prog.txt) { txt.textContent = t; prog.txt = t; }
    }
    progRaf = requestAnimationFrame(progressFrame);
  }
  function progressLoop(busy) {
    const bar = document.getElementById("sa3-actions");
    if (bar && !progEl) {
      progEl = document.createElement("div");
      progEl.id = "sa3-progress";
      progEl.innerHTML = '<div class="track"><i></i></div><span class="txt"></span>';
    }
    if (bar && progEl && progEl.parentElement !== bar) bar.appendChild(progEl);
    if (busy && !progTimer && progEl) {
      Object.assign(prog, { run: null, rev: -1, shown: 0, txt: "" });
      const poll = async () => {
        try {
          const d = await (await fetch("sa3/progress")).json();
          showQueued(d.waiting || 0);
          if (d.active) {
            progEl.classList.remove("wait");
            const now = performance.now();
            if (d.run !== prog.run) {            // next job of a batch: start the bar over
              prog.run = d.run; prog.rev = -1; prog.shown = 0;
            }
            if (d.rev !== prog.rev) {            // a real transition: re-anchor the projection
              prog.rev = d.rev;
              prog.t0 = now - d.elapsed_s * 1000;
              prog.end = now + d.left_s * 1000;
            }
            prog.label = d.phase === "sampling" ? `sampling ${Math.min(d.step + 1, d.steps)}/${d.steps}`
              : d.phase === "decode" ? "decoding" : "encoding prompt";
          } else if (prog.rev < 0) {
            progEl.classList.add("wait");
            progEl.querySelector("i").style.width = "0%";
            progEl.querySelector(".txt").textContent = prog.txt = "waiting for the model…";
          }
        } catch (e) { /* server busy: keep last */ }
      };
      poll();
      progTimer = setInterval(poll, 300);
      if (!progRaf) progRaf = requestAnimationFrame(progressFrame);
      // only show it if busy lasts (page-load restore also flips busy briefly)
      setTimeout(() => { if (progTimer) progEl.classList.add("on"); }, 600);
    } else if (!busy && progTimer) {
      clearInterval(progTimer); progTimer = null;
      cancelAnimationFrame(progRaf); progRaf = null;
      showQueued(0);
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
