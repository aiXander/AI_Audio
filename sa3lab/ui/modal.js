// SA3 UI kit: modal dialogs. One overlay per modal; Esc or a click on the backdrop closes it.
//   const m = modal({title, body: HTMLElement | html string, actions: [{label, primary, danger, fn(m)}],
//                    wide: false, onClose()})   → {el, body, close()}
// An action's fn may return false to keep the modal open.
export function modal({ title = "", body = "", actions = [], wide = false, onClose = null } = {}) {
  const wrap = document.createElement("div");
  wrap.className = "ui-modal-back";
  wrap.innerHTML = `<div class="ui-modal${wide ? " wide" : ""}" role="dialog"><div class="ui-modal-head"><b></b><button class="ui-btn small ghost x" title="close (Esc)">✕</button></div><div class="ui-modal-body"></div><div class="ui-modal-foot"></div></div>`;
  wrap.querySelector("b").textContent = title;
  const bd = wrap.querySelector(".ui-modal-body");
  if (typeof body === "string") bd.innerHTML = body; else if (body) bd.appendChild(body);
  const foot = wrap.querySelector(".ui-modal-foot");
  let closed = false;
  const h = { el: wrap, body: bd, close };
  function close() {
    if (closed) return;
    closed = true;
    wrap.remove();
    document.removeEventListener("keydown", key, true);
    if (onClose) onClose();
  }
  function key(e) { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); } }
  for (const a of actions) {
    const b = document.createElement("button");
    b.className = "ui-btn" + (a.primary ? " primary" : "") + (a.danger ? " danger" : "") + (a.ghost ? " ghost" : "");
    b.textContent = a.label;
    if (a.left) b.style.marginRight = "auto";
    b.onclick = async () => { const r = a.fn ? await a.fn(h) : undefined; if (r !== false && !a.keep) close(); };
    foot.appendChild(b);
  }
  if (!actions.length) foot.remove();
  wrap.querySelector(".x").onclick = close;
  wrap.addEventListener("pointerdown", (e) => { if (e.target === wrap) close(); });
  document.addEventListener("keydown", key, true);
  document.body.appendChild(wrap);
  return h;
}
