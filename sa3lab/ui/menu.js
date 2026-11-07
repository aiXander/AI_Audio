// SA3 UI kit: popup menus (right-click / "…" buttons). Closes on pick, Esc, or a click elsewhere.
//   menu(x, y, [{label, fn, danger, disabled, hint} | "-"])
let open = null;
export function closeMenu() { if (open) { open.remove(); open = null; } }
export function menu(x, y, items) {
  closeMenu();
  const m = document.createElement("div");
  m.className = "ui-menu";
  for (const it of items) {
    if (it === "-") { const s = document.createElement("div"); s.className = "sep"; m.appendChild(s); continue; }
    const b = document.createElement("button");
    b.className = it.danger ? "danger" : "";
    b.disabled = !!it.disabled;
    b.innerHTML = `<span></span>${it.hint ? `<i>${it.hint}</i>` : ""}`;
    b.firstChild.textContent = it.label;
    b.onclick = (e) => { e.stopPropagation(); closeMenu(); if (it.fn) it.fn(); };
    m.appendChild(b);
  }
  document.body.appendChild(m);
  const r = m.getBoundingClientRect();
  m.style.left = Math.min(x, innerWidth - r.width - 8) + "px";
  m.style.top = Math.min(y, innerHeight - r.height - 8) + "px";
  open = m;
  setTimeout(() => document.addEventListener("pointerdown", (e) => { if (open && !open.contains(e.target)) closeMenu(); }, { once: true }), 0);
  return m;
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });
