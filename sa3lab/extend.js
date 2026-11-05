() => {
  // "extend" on every clip's label strip (server side + the why: extend.py). A small menu picks
  // how much to add (+15 / +30 / +60 s) and how much of the clip's ending to redo (0 / 2 / 5 s).
  // It runs the studio's restore-settings path with `__extend__:<add>:<redo>:<clip>` (restore()
  // turns the clip's settings into the extension's: Length + N, the clip as inpaint reference,
  // the range), and once that restore succeeded studio.py calls window.sa3AfterRestore(), which
  // presses Generate. __sa3GenRaw makes the Promptless panel let that click through untouched,
  // so a LATENT clip renders its own spec once, never the panel's N-draw / ladder mode.
  const KEY = "sa3-extend";
  const pref = Object.assign({ redo: 0 }, (() => { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { return {}; } })());
  const ADDS = [15, 30, 60], REDOS = [0, 2, 5];
  const ICON = '<svg viewBox="0 0 16 16"><path d="M2 8h8.5M8 4.5L11.5 8 8 11.5M14 3v10" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  let menu = null, forName = null, pending = 0;

  function close() { if (menu) menu.classList.remove("on"); forName = null; }
  function paint() {
    menu.innerHTML = `<div class="h">Extend this clip</div>
      <div class="row">${ADDS.map((a) => `<button data-add="${a}">+${a} s</button>`).join("")}</div>
      <div class="h2" title="Regenerate the clip's last seconds too, so a clip that fades out gets a new ending instead of a continued fade">Redo the ending</div>
      <div class="row seg">${REDOS.map((r) => `<button data-redo="${r}" class="${r === pref.redo ? "on" : ""}">${r ? r + " s" : "off"}</button>`).join("")}</div>
      <div class="ft">Same prompt, seed and model. The clip is kept as is and continued.</div>`;
  }
  function open(btn, name) {
    if (!menu) {
      menu = document.createElement("div");
      menu.id = "sa3-extmenu";
      document.body.appendChild(menu);
      menu.addEventListener("mousedown", (e) => e.stopPropagation());
      menu.addEventListener("click", (e) => {
        const r = e.target.closest("[data-redo]");
        if (r) { pref.redo = +r.dataset.redo; localStorage.setItem(KEY, JSON.stringify(pref)); paint(); return; }
        const a = e.target.closest("[data-add]");
        if (a && forName) { const n = forName; close(); go(n, +a.dataset.add, pref.redo); }
      });
    }
    forName = name;
    paint();
    menu.classList.add("on");
    const b = btn.getBoundingClientRect(), w = menu.offsetWidth, h = menu.offsetHeight;
    menu.style.left = Math.max(8, Math.min(b.right - w, innerWidth - w - 8)) + "px";
    menu.style.top = (b.bottom + 6 + h < innerHeight ? b.bottom + 6 : Math.max(8, b.top - 6 - h)) + "px";
  }
  function go(name, add, redo) {
    const b = document.getElementById("sa3-restore-btn");
    if (!b) return;
    document.querySelector("#sa3-lib.open .x")?.click();       // started from the Library: show the main page
    window.__sa3RestoreName = `__extend__:${add}:${redo}:${name}`;
    pending = Date.now();
    b.click();
  }
  window.sa3AfterRestore = () => {
    const t = pending;
    pending = 0;
    if (!t || Date.now() - t > 15000) return;                  // a plain "settings" restore: nothing to render
    // let the restored values land (and the Promptless panel adopt a LATENT spec) before the click snapshots them
    setTimeout(() => {
      const g = document.getElementById("sa3-generate");
      if (!g) return;
      window.__sa3GenRaw = true;
      try { g.click(); } finally { window.__sa3GenRaw = false; }
    }, 800);
  };

  setInterval(() => {
    document.querySelectorAll(".sa3-lab:not([data-ext])").forEach((el) => {
      const rs = el.querySelector(".rs");
      if (!rs) return;
      el.dataset.ext = "1";
      const b = document.createElement("button");
      b.className = "ex";
      b.title = "Extend: continue this clip (same prompt, seed and model; the clip is kept)";
      b.innerHTML = `${ICON}<span>extend</span>`;
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        if (menu && menu.classList.contains("on") && forName === el.dataset.name) close();
        else open(b, el.dataset.name);
      });
      rs.before(b);
    });
  }, 500);
  addEventListener("mousedown", close);
  addEventListener("scroll", (e) => { if (!menu || !menu.contains(e.target)) close(); }, true);
  addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
}
