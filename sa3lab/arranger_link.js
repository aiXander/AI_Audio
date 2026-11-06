() => {
  // The SA3 Arranger link (server side + the why: arranger_link.py). An "Arranger" button next
  // to Filter opens the arranger (starting its server if needed), and a poll picks up the
  // arranger's "Send to SA3" records: each σ of a record is loaded through the restore-settings
  // path (`__arranger__:<k>:<file>`, like extend.js), and for Send + Generate / σ ladder the
  // Generate button is pressed once that restore landed, one σ at a time.
  const URL = "http://127.0.0.1:7861/";
  const ICON = '<svg viewBox="0 0 16 16"><path d="M2 3.5h5M2 8h9M2 12.5h7M9 3.5h5M13 8h1M11 12.5h3" stroke="currentColor" stroke-width="1.7" fill="none" stroke-linecap="round"/></svg>';
  let busy = false, waiter = null;

  const prev = window.sa3AfterRestore;            // extend.js's hook: keep it working
  window.sa3AfterRestore = () => {
    if (prev) prev();
    if (waiter) { const w = waiter; waiter = null; w(); }
  };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  function restore(name) {
    return new Promise((resolve) => {
      const b = document.getElementById("sa3-restore-btn");
      if (!b) return resolve(false);
      const t = setTimeout(() => { waiter = null; resolve(false); }, 20000);   // a failed restore shows its own error
      waiter = () => { clearTimeout(t); resolve(true); };
      window.__sa3RestoreName = name;
      b.click();
    });
  }
  function generate() {
    const g = document.getElementById("sa3-generate");
    if (!g) return;
    window.__sa3GenRaw = true;                    // Promptless panel: let this click through as is
    try { g.click(); } finally { window.__sa3GenRaw = false; }
  }
  async function run(rec) {
    busy = true;
    try {
      document.querySelector("#sa3-lib.open .x")?.click();
      for (let k = 0; k < rec.n; k++) {
        if (!(await restore(`__arranger__:${k}:${rec.file}`))) break;
        if (!rec.generate) break;
        await sleep(800);                         // let the restored values land before the click snapshots them
        generate();
        await sleep(1500);
      }
    } finally { busy = false; }
  }
  async function poll() {
    if (busy) return;
    try {
      const r = await fetch("/sa3/arranger/inbox");
      const d = await r.json();
      if (d && d.file) run(d);
    } catch (e) { /* studio restarting */ }
  }
  setInterval(poll, 1500);

  async function open() {
    const w = window.open("", "sa3-arranger");    // reuse the arranger window if one is open
    try {
      const r = await (await fetch("/sa3/arranger/start", { method: "POST" })).json();
      if (!r.up) { if (w) w.close(); alert(`The arranger didn't start; see ${r.log || "outputs/arranger/server.log"}`); return; }
    } catch (e) { /* try anyway */ }
    let blank = false;
    try { blank = !w.location.href || w.location.href === "about:blank"; } catch (e) { /* cross-origin: already the arranger */ }
    if (w && blank) w.location.href = URL;
    else if (w) w.focus();
    else window.open(URL, "sa3-arranger");
  }
  function mount() {
    const top = document.getElementById("sa3-right");
    if (!top || document.getElementById("sa3-arrbtn")) return !!top;
    const b = document.createElement("button");
    b.id = "sa3-arrbtn";
    b.title = "Arranger: sketch a song structure (BPM, key, sections, drums, bass, chords, melody), then send it here as an audio-to-audio job";
    b.innerHTML = `${ICON}<span>Arranger</span>`;
    b.addEventListener("click", open);
    const lib = document.getElementById("sa3-libbtn");
    if (lib) top.insertBefore(b, lib); else top.appendChild(b);
    return true;
  }
  const iv = setInterval(() => { if (mount() && document.getElementById("sa3-libbtn")) clearInterval(iv); }, 500);
}
