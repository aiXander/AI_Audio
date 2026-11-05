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
    host.innerHTML = `
      <div class="sa3-stats">
        <div class="p">${esc(prompt)}</div>
        ${neg.map((n) => `<div class="neg">${esc(n)}</div>`).join("")}
        ${notes.length ? `<div class="notes">${notes.map((n) => `<span>${esc(n)}</span>`).join("")}</div>` : ""}
        ${warnHtml}
        <div class="kpis">
          <div><b>${sec(tot)}</b><span>render</span></div>
          <div><b>${Number(rt[1]).toFixed(1)}×</b><span>realtime</span></div>
          <div><b>${audioS ? audioS.toFixed(1) + " s" : "–"}</b><span>audio</span></div>
          <div class="seed" title="Click to put this seed in the Seed box" onclick="window.sa3SetSeed('${seed[1]}')"><b>${seed[1]}</b><span>seed</span></div>
        </div>
        <div class="bar">
          <i class="t5" style="flex:${t5}"></i><i class="smp" style="flex:${smp}"></i><i class="dec" style="flex:${dec}"></i>${other > 50 ? `<i class="oth" style="flex:${other}"></i>` : ""}
        </div>
        <div class="legend">
          <span><em style="background:#ff6fb5"></em>text ${sec(t5)}</span>
          <span><em style="background:#9b8cff"></em>sampling ${sec(smp)}</span>
          <span><em style="background:#4fd1c5"></em>decode ${sec(dec)}</span>
          ${other > 50 ? `<span><em style="background:#3a3a48"></em>load / other ${sec(other)}</span>` : ""}
          ${sl ? `<span>${sl[1]} latent frames</span>` : ""}
        </div>
      </div>`;
  }

  // Hide empty output slots; show an empty state in the player slot.
  function tick() {
    for (const cls of ["sa3-player", "sa3-timing", "sa3-error", "sa3-queued", "sa3-history"]) {
      const el = document.querySelector("." + cls);
      if (!el) continue;
      const content = el.querySelector(".prose") || el;
      const hasContent = !!content.textContent.trim() || !!content.querySelector("audio, img");
      const status = el.querySelector(":scope > .wrap");
      const busy = !!status && !status.classList.contains("hide");
      // side slots collapse whenever they hold nothing; the player keeps its
      // empty state but drops it while a generation is running
      const empty = !hasContent && (cls !== "sa3-player" || !busy);
      el.classList.toggle("is-empty", empty);
      if (cls === "sa3-player") el.classList.toggle("is-busy", busy && !hasContent);
    }
    const tm = document.querySelector(".sa3-timing");
    if (tm) formatTiming(tm);
    enhanceHistory();
    makeDraggable();
  }
  // ── history rows: tiny transport bar above a full-width spectrogram ──
  const fmt = (t) => !isFinite(t) ? "0:00" : `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  const ICON_PLAY = '<svg viewBox="0 0 16 16"><path d="M4 2.5v11l9-5.5z" fill="currentColor"/></svg>';
  const ICON_PAUSE = '<svg viewBox="0 0 16 16"><path d="M4 2.5h3v11H4zM9 2.5h3v11H9z" fill="currentColor"/></svg>';
  const ICON_DL = '<svg viewBox="0 0 16 16"><path d="M8 2v8m0 0L4.5 6.5M8 10l3.5-3.5M3 13h10" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const ICON_GRIP = '<svg viewBox="0 0 16 16"><g fill="currentColor"><circle cx="6" cy="4" r="1.2"/><circle cx="10" cy="4" r="1.2"/><circle cx="6" cy="8" r="1.2"/><circle cx="10" cy="8" r="1.2"/><circle cx="6" cy="12" r="1.2"/><circle cx="10" cy="12" r="1.2"/></g></svg>';
  const clipName = (a) => decodeURIComponent((a.getAttribute("src") || "").split("/").pop() || "clip.wav");

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

  let queued = false;
  new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; tick(); });
  }).observe(document.body, { childList: true, subtree: true, characterData: true });
  tick();
}
