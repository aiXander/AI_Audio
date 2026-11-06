() => {
  // UI settings: a gear stacked above studio.js's focus (sidebar) button, left of the logo.
  // Its popover holds browser-only display prefs, saved in localStorage ("sa3-uiprefs"):
  //  - spec:  "stereo" = dadaspec as rendered (L on top, R mirrored below, bass at the centre
  //           line) | "mono" = one panel, bass at the bottom. The fold happens here, on a canvas,
  //           from the image the server already sent: pixel row y of L averaged with the mirrored
  //           row of R. That is the mean of the two channels' spectrograms (no phase cancellation,
  //           unlike a true L+R downmix), so no re-render and no server change.
  //  - spech: height multiplier for the main list's spectrograms (studio.css sizes them so 3
  //           cards fill the column; this scales that).
  //  - wide:  drop the 1440 px page cap.
  //  - bg:    the animated waterfall background (studio.js background() also stops drawing).
  // Body-level rules go in a <style> of our own: gradio prefixes its css (same as studio.js's focus mode).
  const KEY = "sa3-uiprefs";
  const DEF = { spec: "stereo", spech: 1, wide: false, bg: true };
  let P = { ...DEF };
  try { P = { ...DEF, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; } catch (e) { /* defaults */ }
  const save = () => localStorage.setItem(KEY, JSON.stringify(P));

  const css = document.createElement("style");
  document.head.appendChild(css);
  function applyCss() {
    const r = [];
    if (P.spec === "mono") r.push('body.sa3-mono img[alt="spectrogram"]:not([data-sa3m]) { visibility: hidden; }');
    if (P.spech !== 1) r.push(`body #sa3-out#sa3-out#sa3-out .blk img[alt="spectrogram"] { height: calc(max(90px, calc((100vh - 78px - 24px) / 3 - 117px)) * ${P.spech}) !important; }`);
    if (P.wide) r.push("body .gradio-container.gradio-container { max-width: none !important; }");
    if (!P.bg) r.push("body #sa3-bg, body::before, body::after { display: none !important; }");
    css.textContent = r.join("\n");
    document.body.classList.toggle("sa3-mono", P.spec === "mono");
    document.body.classList.toggle("sa3-nobg", !P.bg);
  }

  // ── mono fold ──
  const cache = new Map();                    // key of the original src → blob URL of the folded jpeg
  const keyOf = (s) => (s.startsWith("data:") ? s.length + ":" + s.slice(-160) : s);   // data URIs are ~400 KB
  const pending = new Set();
  function fold(img, orig) {
    const k = keyOf(orig);
    if (cache.has(k)) return show(img, orig, cache.get(k));
    if (pending.has(img)) return;
    if (!img.complete || !img.naturalWidth) {   // lazy Library rows: wait for the load
      if (!img._sa3wait) { img._sa3wait = true; img.addEventListener("load", () => { img._sa3wait = false; scan(); }, { once: true }); }
      return;
    }
    pending.add(img);
    try {
      const w = img.naturalWidth, h = img.naturalHeight, half = h >> 1;
      const c = document.createElement("canvas");
      c.width = w; c.height = h;
      const g = c.getContext("2d", { willReadFrequently: true });
      g.drawImage(img, 0, 0);
      const src = g.getImageData(0, 0, w, h).data;
      const out = g.createImageData(w, half), d = out.data, row = w * 4;
      for (let y = 0; y < half; y++) {          // L row y (highs at the top) + R row h-1-y (highs at the bottom)
        const a = y * row, b = (h - 1 - y) * row;
        for (let x = 0; x < row; x += 4) {
          d[a + x] = (src[a + x] + src[b + x]) >> 1;
          d[a + x + 1] = (src[a + x + 1] + src[b + x + 1]) >> 1;
          d[a + x + 2] = (src[a + x + 2] + src[b + x + 2]) >> 1;
          d[a + x + 3] = 255;
        }
      }
      c.height = half;
      g.putImageData(out, 0, 0);
      c.toBlob((bl) => {
        pending.delete(img);
        if (!bl) return img.setAttribute("data-sa3m", "");
        cache.set(k, URL.createObjectURL(bl));
        scan();                                 // shows it (cached now), or folds a src that changed meanwhile
      }, "image/jpeg", 0.92);
    } catch (e) {
      pending.delete(img);
      img.setAttribute("data-sa3m", "");      // unfoldable (e.g. tainted): show it as is
      console.warn("[sa3] mono spectrogram skipped", e);
    }
  }
  function show(img, orig, u) {
    img._sa3orig = orig; img._sa3out = u;
    img.src = u;
    img.setAttribute("data-sa3m", "");
  }
  function scan() {
    for (const img of document.querySelectorAll('img[alt="spectrogram"]')) {
      const s = img.getAttribute("src") || "";
      const ours = !!img._sa3out && s === img._sa3out;
      if (P.spec === "mono") {
        if (ours) continue;
        img.removeAttribute("data-sa3m");       // a fresh src (re-render): hide until folded
        if (s) fold(img, s);
      } else if (ours) {
        img.src = img._sa3orig;
        img.removeAttribute("data-sa3m");
      }
    }
  }
  new MutationObserver(scan).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["src"] });

  // ── gear + popover ──
  const GEAR = '<svg viewBox="0 0 16 16"><path d="M6.9 1.6h2.2l.35 1.75 1.2.5 1.5-1 1.55 1.55-1 1.5.5 1.2 1.75.35v2.2l-1.75.35-.5 1.2 1 1.5-1.55 1.55-1.5-1-1.2.5-.35 1.75H6.9l-.35-1.75-1.2-.5-1.5 1-1.55-1.55 1-1.5-.5-1.2-1.75-.35V6.9l1.75-.35.5-1.2-1-1.5 1.55-1.55 1.5 1 1.2-.5z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" fill="none"/><circle cx="8" cy="8" r="2.1" stroke="currentColor" stroke-width="1.3" fill="none"/></svg>';
  let pop = null;
  const seg = (k, opts) => `<div class="up-seg" data-k="${k}">${opts.map(([v, l, t]) =>
    `<button type="button" data-v="${v}"${t ? ` title="${t}"` : ""} class="${String(P[k]) === String(v) ? "on" : ""}">${l}</button>`).join("")}</div>`;
  function build() {
    pop.innerHTML = `<div class="up-h">Display <span>this browser only</span></div>
      <div class="up-row"><label>Spectrogram</label>${seg("spec", [["stereo", "Stereo", "L on top, R mirrored below"], ["mono", "Mono", "L and R averaged, one panel, bass at the bottom"]])}</div>
      <div class="up-row"><label>Spectrogram height</label><div class="up-rng"><input type="range" min="0.5" max="3" step="0.1" value="${P.spech}"><b>${P.spech.toFixed(1)}×</b></div></div>
      <div class="up-row"><label>Page width</label>${seg("wide", [["false", "1440 px"], ["true", "Full"]])}</div>
      <div class="up-row"><label>Background</label>${seg("bg", [["true", "Animated"], ["false", "Off", "also saves some CPU"]])}</div>
      <div class="up-foot"><button type="button" class="up-reset">Reset</button></div>`;
  }
  function set(k, v) {
    P[k] = v; save(); applyCss();
    if (k === "spec") scan();
    if (k !== "spech") build();
  }
  function toggle(want) {
    if (!pop) {
      pop = document.createElement("div");
      pop.id = "sa3-uipop";
      document.body.appendChild(pop);
      pop.addEventListener("click", (e) => {
        const b = e.target.closest(".up-seg button");
        if (b) {
          const k = b.parentElement.dataset.k, v = b.dataset.v;
          return set(k, v === "true" ? true : v === "false" ? false : v);
        }
        if (e.target.closest(".up-reset")) { P = { ...DEF }; save(); applyCss(); scan(); build(); }
      });
      pop.addEventListener("input", (e) => {
        if (e.target.type !== "range") return;
        set("spech", +(+e.target.value).toFixed(1));
        e.target.nextElementSibling.textContent = P.spech.toFixed(1) + "×";
      });
      document.addEventListener("mousedown", (e) => {
        if (pop.classList.contains("open") && !pop.contains(e.target) && !e.target.closest("#sa3-uibtn")) toggle(false);
      });
      document.addEventListener("keydown", (e) => { if (e.key === "Escape" && pop.classList.contains("open")) toggle(false); });
    }
    const open = want === undefined ? !pop.classList.contains("open") : want;
    if (open) {
      build();
      const r = document.getElementById("sa3-sidestack").getBoundingClientRect();   // under the gear + focus column
      pop.style.top = (r.bottom + 8) + "px";
      pop.style.left = Math.max(8, r.left) + "px";
    }
    pop.classList.toggle("open", open);
    document.getElementById("sa3-uibtn")?.classList.toggle("on", open);
  }
  // studio.js mountFocus prepends #sa3-focusbtn to the brand; we wrap it in a column with the gear on top
  function mount() {
    const fb = document.getElementById("sa3-focusbtn");
    if (!fb || fb.parentElement.id === "sa3-sidestack") return;
    const stack = document.createElement("div");
    stack.id = "sa3-sidestack";
    fb.before(stack);
    const b = document.createElement("button");
    b.id = "sa3-uibtn";
    b.type = "button";
    b.title = "Display settings";
    b.innerHTML = GEAR;
    b.addEventListener("click", () => toggle());
    stack.append(b, fb);
  }
  new MutationObserver(mount).observe(document.body, { childList: true, subtree: true });

  applyCss();
  mount();
  scan();
}
