() => {
  // Folder chip under the Filter (Library) button: pick the active output folder (outdir.py).
  // Switching: POST /sa3/outdir, then the hidden apply button reloads the history column
  // and the hidden destination textbox; studio.js's Library rows are refetched.
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  let state = { active: "", folders: [] }, pop = null;
  const ICON = '<svg viewBox="0 0 16 16" width="14" height="14"><path d="M1.5 4.5a1 1 0 0 1 1-1h3.6l1.4 1.5h6a1 1 0 0 1 1 1v6.5a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1z" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linejoin="round"/></svg>';

  async function refresh() {
    try { const r = await fetch("sa3/outdir", { cache: "no-store" }); if (r.ok) state = await r.json(); } catch (e) {}
    paint();
  }
  function paint() {
    const b = document.getElementById("sa3-dirbtn");
    if (b) b.querySelector("span").textContent = state.active || "…";
    if (pop && pop.classList.contains("open")) build();
  }
  function build() {
    pop.innerHTML = `<div class="od-h">Output folder <span>view + destination of new renders</span></div>
      <div class="od-list">${state.folders.map((f) => `
        <button type="button" class="od-row${f.rel === state.active ? " on" : ""}" data-rel="${esc(f.rel)}">
          <span class="od-name">${esc(f.rel)}</span><span class="od-n">${f.n}</span></button>`).join("")}</div>
      <form class="od-new"><input type="text" placeholder="new folder, e.g. anarchy/my-session" spellcheck="false">
        <button type="submit">Create</button></form>
      <div class="od-foot">Renders already queued keep their folder.</div>`;
  }
  async function choose(rel) {
    const r = await fetch("sa3/outdir", { method: "POST", headers: { "Content-Type": "application/json" },
                                            body: JSON.stringify({ path: rel }) });
    const j = await r.json();
    if (!r.ok) { alertBox(j.error || "could not switch"); return; }
    state = j;
    const btn = document.querySelector("#sa3-outdir-apply");   // reload history + dest textbox
    if (btn) btn.click();
    document.getElementById("sa3-more")?.remove();
    if (window.sa3ReloadLibrary) window.sa3ReloadLibrary();
    toggle(false);
    paint();
  }
  function alertBox(msg) {
    const e = pop && pop.querySelector(".od-foot");
    if (e) { e.textContent = msg; e.classList.add("err"); }
  }
  function toggle(want) {
    if (!pop) {
      pop = document.createElement("div");
      pop.id = "sa3-dirpop";
      document.body.appendChild(pop);
      pop.addEventListener("click", (e) => { const r = e.target.closest("[data-rel]"); if (r) choose(r.dataset.rel); });
      pop.addEventListener("submit", (e) => { e.preventDefault(); const v = pop.querySelector(".od-new input").value.trim(); if (v) choose(v); });
      document.addEventListener("mousedown", (e) => {
        if (pop.classList.contains("open") && !pop.contains(e.target) && !e.target.closest("#sa3-dirbtn")) toggle(false);
      });
    }
    const open = want === undefined ? !pop.classList.contains("open") : want;
    if (open) {
      build();
      const b = document.getElementById("sa3-dirbtn").getBoundingClientRect();
      pop.style.top = (b.bottom + 6) + "px";
      pop.style.right = Math.max(8, window.innerWidth - b.right) + "px";
      refresh();
    }
    pop.classList.toggle("open", open);
  }
  // the chip sits on the top bar's second row, right under studio.js's Filter (Library) button:
  // both go into one column (#sa3-libstack) at the end of #sa3-right
  function mount() {
    if (document.getElementById("sa3-dirbtn")) return;
    const lib = document.getElementById("sa3-libbtn");
    if (!lib) return;
    const stack = document.createElement("div");
    stack.id = "sa3-libstack";
    lib.before(stack);
    stack.appendChild(lib);
    const btn = document.createElement("button");
    btn.id = "sa3-dirbtn";
    btn.title = "Output folder: what the history + Filter show, and where new renders go";
    btn.innerHTML = `${ICON}<span>${esc(state.active || "…")}</span>`;
    btn.addEventListener("click", () => toggle());
    stack.appendChild(btn);
  }
  refresh();
  setInterval(mount, 700);
}
