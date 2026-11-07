// SA3 UI kit: the app shell (one top bar on every kit page: brand, page tabs, the studio's
// render progress) and toasts.
//
//   const sh = mountShell(document.body, {active: "composer"})   → {bar, right}  (right: page widgets)
//   toast("text", {error: true, ms: 5000})
//
// Every page lives in the tab host (/sa3/app, app.js): one fixed bar with the page tabs, each page
// in its own kept-alive iframe, so a page keeps playing / rendering / holding its state while you
// look at another. Inside the host a kit page's bar is "embedded": no brand or tabs of its own, just
// a gap the host's tabs sit over (--sa3-host-left, set by the host), then the page's own widgets
// and the queue, all on one row. Opened on its own, a kit page redirects into the host (add ?solo=1
// to keep it standalone, e.g. for debugging).

export const PAGES = [
  { key: "studio", label: "Studio", href: "/?__theme=dark", own: false, tip: "generate, label and browse renders (the gradio studio)" },
  { key: "library", label: "Library", href: "/sa3/page/library", own: true, tip: "your starred renders and highlights: filter, search by sound, map" },
  { key: "composer", label: "Composer", href: "/sa3/page/composer", own: true, tip: "arrange clips on tracks and let SA3 fill and glue them" },
  { key: "arranger", label: "Arranger", href: "http://127.0.0.1:7861/", own: false, tip: "sketch a song structure (drums, bass, chords) and send it to the studio" },
];

export function hostOf(win = window) {             // the tab host this page runs in, or null
  try { return win.parent !== win ? win.parent.__sa3host || null : null; } catch (e) { return null; }
}

export function mountShell(host, { active } = {}) {
  const embedded = !!hostOf();
  if (!embedded && !new URLSearchParams(location.search).has("solo")) {
    location.replace(`/sa3/ui/app.html${location.search ? "?q=" + encodeURIComponent(location.search) : ""}#${active}`);
    throw new Error("sa3: opening this page inside the tab host");   // stop the page's own setup
  }
  const bar = document.createElement("header");
  bar.className = "ui-shell" + (embedded ? " embedded" : "");
  bar.innerHTML = embedded
    ? `<nav class="hostgap"></nav><div class="right"><div class="q" title="the studio's render queue"></div></div>`
    : `<div class="brand"><i></i>SA3</div><nav></nav><div class="right"><div class="q" title="the studio's render queue"></div></div>`;
  if (!embedded) {
    const nav = bar.querySelector("nav");
    for (const p of PAGES) {
      const a = document.createElement("a");
      a.href = p.href; a.textContent = p.label; a.title = p.tip;
      if (p.key === "arranger") a.target = "sa3-arranger";
      if (p.key === active) a.className = "on";
      nav.appendChild(a);
    }
  }
  host.prepend(bar);
  queueWidget(bar.querySelector(".q"));
  return { bar, right: bar.querySelector(".right") };
}

// the studio's render progress in a header chip (polled every 2 s)
export function queueWidget(q) {
  async function poll() {
    try {
      const d = await (await fetch("/sa3/progress", { cache: "no-store" })).json();
      if (!d.active) q.innerHTML = d.waiting ? `${d.waiting} waiting` : "studio idle";
      else {
        const tot = d.elapsed_s + d.left_s, pct = tot > 0 ? Math.min(100, (100 * d.elapsed_s) / tot) : 0;
        q.innerHTML = `<span>${d.phase || "rendering"} · ${Math.round(d.left_s)} s left${d.waiting ? ` · ${d.waiting} waiting` : ""}</span><span class="bar"><b style="width:${pct.toFixed(0)}%"></b></span>`;
      }
    } catch (e) { q.textContent = "studio offline"; }
  }
  poll();
  return setInterval(poll, 2000);
}

let box = null;
export function toast(text, { error = false, ms = 4000 } = {}) {
  if (!box) { box = document.createElement("div"); box.className = "ui-toasts"; document.body.appendChild(box); }
  const t = document.createElement("div");
  t.className = "ui-toast" + (error ? " err" : "");
  t.textContent = text;
  box.appendChild(t);
  setTimeout(() => t.remove(), error ? Math.max(ms, 8000) : ms);
}
