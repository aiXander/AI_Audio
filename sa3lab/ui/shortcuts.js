// SA3 UI kit: one keyboard registry per page, with a help overlay (press ?).
//   bind("mod+z", "Undo", fn)   bind("space", "Play / stop", fn)   bind("shift+mod+z", …)
// Keys: lower-case e.key ("space" for " ", "backspace", "arrowleft", …); "mod" = ⌘ on a Mac, ctrl
// elsewhere. Typing in a text input / select / textarea never triggers a binding (sliders and checkboxes do).
const binds = [];
const isMac = /Mac/.test(navigator.platform);
export function bind(combo, label, fn, { group = "" } = {}) { binds.push({ combo, label, fn, group }); }
function norm(e) {
  const k = e.key === " " ? "space" : e.key.toLowerCase();
  const mods = [e.shiftKey && "shift", (isMac ? e.metaKey : e.ctrlKey) && "mod", e.altKey && "alt"].filter(Boolean);
  return [...mods, k].join("+");
}
const pretty = (c) => c.replace("mod", isMac ? "⌘" : "Ctrl").replace("shift", "⇧").replace("alt", "⌥").replace("space", "Space")
  .replace("backspace", "⌫").replace("arrowleft", "←").replace("arrowright", "→").replace("arrowup", "↑").replace("arrowdown", "↓").split("+").join(" ");
export function help() {
  const old = document.querySelector(".ui-keys");
  if (old) return old.remove();
  const d = document.createElement("div");
  d.className = "ui-keys";
  d.innerHTML = "<b>Keyboard</b>" + binds.map((b) => `<div><kbd>${pretty(b.combo)}</kbd><span>${b.label}</span></div>`).join("") + '<div class="muted">? toggles this</div>';
  d.onclick = () => d.remove();
  document.body.appendChild(d);
}
document.addEventListener("keydown", (e) => {
  // text fields and dropdowns keep their keys; sliders / checkboxes don't (a focused gain slider must not swallow ⌘⌫ or ⌘C)
  if (e.target.closest && e.target.closest('input:not([type=range]):not([type=checkbox]):not([type=radio]), select, textarea, [contenteditable]')) return;
  if (document.querySelector(".ui-modal-back")) return;
  if (e.key === "?") { e.preventDefault(); return help(); }
  const n = norm(e);
  const b = binds.find((x) => x.combo === n);
  if (b) { e.preventDefault(); b.fn(e); }
});
