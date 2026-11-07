// SA3 UI kit: right-click any clip → "Show in Finder" (POST /sa3/reveal: `open -R` on the server,
// which is this Mac, so Finder opens the clip's folder with the file selected).
//
//   revealItem({id} | {name})   a menu item to append to a page's own right-click menu
//   install()                   page-wide: right-click on anything marked data-sa3-id (render id) or
//                               data-sa3-name (bare filename in the studio's active folder) opens a
//                               menu with that item; a handler that already called preventDefault wins
// ClipCard marks its card, so every Browser / card list gets it for free.
import * as api from "./api.js";
import { menu } from "./menu.js";
import { toast } from "./shell.js";

export async function reveal(ref) {
  try {
    const r = await api.post("/sa3/reveal", ref);
    if (r && r.error) throw new Error(r.error);
  } catch (e) { toast("can't show it in Finder: " + e.message + " (run make start if this is new)", { error: true }); }
}

export const revealItem = (ref) => ({ label: "Show in Finder", fn: () => reveal(ref) });

let on = false;
export function install() {
  if (on) return;
  on = true;
  document.addEventListener("contextmenu", (e) => {
    if (e.defaultPrevented) return;
    const t = e.target.closest && e.target.closest("[data-sa3-id], [data-sa3-name]");
    if (!t) return;
    e.preventDefault();
    menu(e.clientX, e.clientY, [revealItem(t.dataset.sa3Id ? { id: t.dataset.sa3Id } : { name: t.dataset.sa3Name })]);
  });
}
