() => {
  // Render-on chip next to Queue (remote.py): a local / remote toggle. "Remote" (green) while the
  // RTX 3090 box takes jobs, "Local" otherwise (hover: why). Click → Remote (box when free, else
  // local) / Local only. Pref values stay "auto" / "mac".
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  let st = null, pop = null;

  async function load(method, body) {
    try {
      const r = await fetch("sa3/remote", method ? { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
                                                 : { cache: "no-store" });
      if (r.ok) st = await r.json();
    } catch (e) { /* server restarting */ }
    paint();
  }
  function mount() {
    const top = document.getElementById("sa3-right") || document.getElementById("sa3-top");
    if (!top || document.getElementById("sa3-rchip")) return !!top;
    const b = document.createElement("button");
    b.id = "sa3-rchip";
    b.type = "button";
    b.innerHTML = `<i></i><span>…</span>`;
    b.addEventListener("click", (e) => { e.stopPropagation(); toggle(); });
    const q = document.getElementById("sa3-qbtn");
    if (q && q.parentElement === top) top.insertBefore(b, q); else top.appendChild(b);
    paint();
    return true;
  }
  function paint() {
    const b = document.getElementById("sa3-rchip");
    if (!b || !st) return;
    const beast = st.target === "beast";
    b.classList.toggle("beast", beast);
    b.classList.toggle("pinned", st.pref === "mac");
    b.querySelector("span").textContent = beast ? "Remote" : (st.pref === "mac" ? "Local" : "Local (fallback)");
    const h = st.health;
    b.title = beast
      ? `Remote: rendering on ${h.host} (${h.device || "GPU"}, MLX ${h.mlx_version})` + (h.loaded && h.loaded.length ? "" : " · models load on the first job")
      : `Local: rendering on this machine (${st.reason || ""})`;
    if (pop && pop.classList.contains("open")) build();
  }
  function build() {
    const h = st && st.health;
    const row = (v, label, sub) => `<button type="button" class="rc-opt${st && st.pref === v ? " on" : ""}" data-v="${v}">
        <b>${label}</b><span>${sub}</span></button>`;
    pop.innerHTML = `<div class="rc-h">Render on</div>
      ${row("auto", "Remote · GPU box", "render on the RTX 3090 box; falls back to local when it’s busy or offline")}
      ${row("mac", "Local only", "render on this machine, never send jobs to the box")}
      <div class="rc-foot">${st && st.target === "beast" ? `● ${esc(h.host)} ready${h.loaded && h.loaded.length ? "" : " (models load on the first job)"}`
        : `● Local: ${esc(st ? st.reason : "…")}`}</div>`;
  }
  function toggle(want) {
    if (!pop) {
      pop = document.createElement("div");
      pop.id = "sa3-rpop";
      document.body.appendChild(pop);
      pop.addEventListener("click", (e) => {
        const o = e.target.closest("[data-v]");
        if (o) { load("POST", { pref: o.dataset.v }); toggle(false); }
        e.stopPropagation();
      });
      document.addEventListener("click", () => toggle(false));
    }
    const open = want ?? !pop.classList.contains("open");
    if (open) {
      build();
      const r = document.getElementById("sa3-rchip").getBoundingClientRect();
      pop.style.top = `${r.bottom + 6}px`;
      pop.style.right = `${Math.max(8, window.innerWidth - r.right)}px`;
      load();
    }
    pop.classList.toggle("open", open);
  }

  const t = setInterval(() => { if (mount()) clearInterval(t); }, 300);
  load();
  setInterval(() => { if (!document.hidden) load(); }, 10000);
}
