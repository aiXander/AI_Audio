// SA3 UI kit: the tab host at /sa3/app (served by composer/server.py; `make start` opens it). One
// fixed bar with the page tabs (shell.js PAGES), and every page in its own iframe that is created on
// first visit and then only hidden, never unloaded: the studio keeps rendering and polling the
// arranger's inbox, the composer keeps playing, the arranger keeps its sketch, and switching back
// shows each exactly as it was. The Studio frame is created at start (its arranger-inbox poll and
// queue live there).
//
// Over a kit page (Library, Composer: `own` in PAGES) the bar shrinks to brand + tabs and the page's
// own embedded bar (shell.js) carries its widgets and the queue on the same row; the host tells it
// how wide the tabs are (--sa3-host-left). Over the Studio and the Arranger the bar is full width
// and the page starts below it.
//
// Links between pages go through the host: same-origin frames get a patched window.open, so the
// studio's Composer / Arranger buttons, "Open in composer" and the Library's add button switch tabs
// instead of opening windows (a composer that is already open gets ?add=… via its __sa3nav hook).
// The active tab is the URL hash (#composer) and survives a reload. A top-level visit to a page
// redirects here (studio.js, shell.js; ?solo=1 opts out).

import { PAGES, queueWidget, toast } from "/sa3/ui/shell.js";

const bar = document.getElementById("hostbar"), nav = bar.querySelector("nav"), box = document.getElementById("frames");
const F = {};                                       // key → iframe
let active = null;
window.__sa3host = { go: (key, search) => go(key, search) };

for (const p of PAGES) {
  const a = document.createElement("a");
  a.href = "#" + p.key; a.textContent = p.label; a.title = p.tip; a.dataset.k = p.key;
  a.onclick = (e) => { e.preventDefault(); go(p.key); };
  nav.appendChild(a);
}
queueWidget(bar.querySelector(".q"));

const page = (key) => PAGES.find((p) => p.key === key) || PAGES[0];
function src(p, search = "") {
  if (!search) return p.href;
  const u = new URL(p.href, location.href);
  new URLSearchParams(search).forEach((v, k) => u.searchParams.set(k, v));
  return u.href;
}
function leftWidth() { return Math.ceil(bar.querySelector(".left").getBoundingClientRect().right) - 16; }
function syncLeft(f) {                              // the embedded bar's gap = the host's brand + tabs
  try { f.contentDocument.documentElement.style.setProperty("--sa3-host-left", leftWidth() + "px"); } catch (e) { /* cross-origin */ }
}
function fakeWin() { return { closed: false, focus() {}, close() {}, postMessage() {}, location: { href: "about:blank" } }; }
function route(url, name) {                         // a frame's window.open → a tab switch (or undefined: really open)
  if (name === "sa3-arranger" || /:7861(\/|$)/.test(String(url || ""))) { go("arranger"); return fakeWin(); }
  if (!url) return undefined;
  let u;
  try { u = new URL(url, location.href); } catch (e) { return undefined; }
  if (u.origin !== location.origin) return undefined;
  if (u.pathname === "/sa3/page/composer") { go("composer", u.search); return fakeWin(); }
  if (u.pathname === "/sa3/page/library") { go("library", u.search); return fakeWin(); }
  if (u.pathname === "/" || u.pathname === "") { go("studio"); return fakeWin(); }
  return undefined;
}
function patch(f) {
  syncLeft(f);
  try {
    const w = f.contentWindow, orig = w.open.bind(w);
    w.open = (url, name, feat) => route(url, name) || orig(url, name, feat);
  } catch (e) { /* cross-origin (the arranger): its links stay its own */ }
}
function make(p, search) {
  const f = document.createElement("iframe");
  f.allow = "autoplay; clipboard-read; clipboard-write; fullscreen";
  f.style.top = p.own ? "0" : "44px";
  f.style.height = p.own ? "100%" : "calc(100% - 44px)";
  f.addEventListener("load", () => patch(f));
  f.src = src(p, search);
  box.appendChild(f);
  F[p.key] = f;
  return f;
}
async function arrangerUp() {                       // the arranger is its own server: start it on first visit
  try {
    const r = await (await fetch("/sa3/arranger/start", { method: "POST" })).json();
    if (!r.up) toast(`The arranger didn't start; see ${r.log || "outputs/arranger/server.log"}`, { error: true });
  } catch (e) { /* try the frame anyway */ }
}
async function go(key, search = "") {
  const p = page(key);
  let f = F[p.key];
  if (!f) {
    show(p);                                         // the tab lights up at once; the frame follows
    if (p.key === "arranger") await arrangerUp();
    f = make(p, search);
  } else if (search) {
    let handled = false;
    try { const w = f.contentWindow; if (w.__sa3nav) { w.__sa3nav(search); handled = true; } } catch (e) { /* cross-origin */ }
    if (!handled) f.src = src(p, search);
  }
  show(p);
}
function show(p) {
  active = p.key;
  for (const [k, f] of Object.entries(F)) f.classList.toggle("on", k === p.key);
  for (const a of nav.querySelectorAll("a")) a.classList.toggle("on", a.dataset.k === p.key);
  bar.classList.toggle("overlay", p.own);
  document.title = `SA3 · ${p.label}`;
  history.replaceState(null, "", location.pathname + "#" + p.key);   // /sa3/app, or /sa3/ui/app.html (same page)
  try { localStorage.setItem("sa3-app-tab", p.key); } catch (e) { /* private window */ }
  const f = F[p.key];
  if (f) { syncLeft(f); try { f.contentWindow.focus(); } catch (e) { /* not loaded yet */ } }
}

new ResizeObserver(() => Object.values(F).forEach(syncLeft)).observe(bar.querySelector(".left"));
document.fonts && document.fonts.ready.then(() => Object.values(F).forEach(syncLeft));

// start: the Studio always (its inbox poll), then the tab from the hash / last visit, with ?q= from a redirect
const q = new URLSearchParams(location.search).get("q") || "";
let first = location.hash.slice(1);
if (!PAGES.some((p) => p.key === first)) { try { first = localStorage.getItem("sa3-app-tab") || "studio"; } catch (e) { first = "studio"; } }
if (first !== "studio") make(page("studio"), "");
go(first, q);
