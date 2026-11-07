() => {
  // The Inpaint strength editor on the UI kit's timeline (sa3lab/ui/strength.js, curve rules shared in
  // ui/curve.js), opt-in: the "Timeline editor" toggle on the Inpaint input (remembered in
  // localStorage "sa3-uiprefs".strengthEditor = "timeline"). When on, segments.js's own editor is
  // hidden but keeps running underneath (Length sync, the both-inputs gate, the a2a viewer); both
  // read and write the same hidden box (#sa3-seg-data), so switching back and forth keeps the curve.
  // Gains: zoom (⌘ + wheel) / pan (wheel) on long references. Once Xander prefers it, segments.js's
  // canvas code can go and this becomes the default.
  const PREF = "sa3-uiprefs";
  const on = () => { try { return JSON.parse(localStorage.getItem(PREF) || "{}").strengthEditor === "timeline"; } catch (e) { return false; } };
  const setOn = (v) => { try { const p = JSON.parse(localStorage.getItem(PREF) || "{}"); p.strengthEditor = v ? "timeline" : "classic"; localStorage.setItem(PREF, JSON.stringify(p)); } catch (e) { /* private window */ } };
  const css = (href) => { if (!document.querySelector(`link[href="${href}"]`)) { const l = document.createElement("link"); l.rel = "stylesheet"; l.href = href; document.head.appendChild(l); } };
  const $box = () => document.querySelector("#sa3-seg-data textarea, #sa3-seg-data input");
  let ed = null, host = null, written = null, mod = null;
  function srcOf(blk) {
    const a = blk.querySelector("a[download][href]");
    if (a && /file=/.test(a.href)) return a.href;
    const au = [...blk.querySelectorAll("audio")].find((x) => !x.closest(".sa3seg, .ui-strength") && /^https?:/.test(x.currentSrc || x.src || ""));
    return au ? au.currentSrc || au.src : null;
  }
  function lengthNow() {
    const b = [...document.querySelectorAll("#sa3-ctl .block")].find((x) => { const l = x.querySelector("label span, [data-testid='block-info']"); return l && l.textContent.trim() === "Length"; });
    const v = b && +(b.querySelector("input[type=number]") || {}).value;
    return v > 0 ? v : 0;
  }
  async function mount(blk) {
    css("/sa3/ui/vars.css"); css("/sa3/ui/components.css");
    mod = mod || (await import("/sa3/ui/strength.js"));
    host = document.createElement("div");
    host.className = "sa3-stl";
    blk.appendChild(host);
    ed = new mod.StrengthEditor(host, { onChange: (keys) => {
      const b = $box(); if (!b) return;
      let d = {};
      try { d = JSON.parse(b.value || "{}"); } catch (e) { d = {}; }
      d.strength = keys;                                  // keep any other keys (cond, composite, mask)
      const v = JSON.stringify(d);
      b.value = v; written = v;
      b.dispatchEvent(new Event("input", { bubbles: true }));
    } });
  }
  function toggleBtn(blk) {
    const had = blk.querySelector(".sa3-stl-tg");
    if (had) { if (had.nextElementSibling) blk.appendChild(had); return; }   // stays last: editors mount above it
    const b = document.createElement("button");
    b.className = "sa3-stl-tg";
    b.title = "Switch the strength editor between the classic one and the UI kit's timeline (zoom + pan)";
    blk.appendChild(b);
    const paint = () => { b.textContent = on() ? "Classic editor" : "Timeline editor (beta)"; document.documentElement.classList.toggle("sa3-stl-on", on()); };
    b.onclick = () => { setOn(!on()); paint(); written = null; };
    paint();
  }
  setInterval(async () => {
    const blk = document.querySelector(".block.sa3-seg-inp");
    if (!blk) return;
    toggleBtn(blk);
    if (!on()) return;
    if (!ed || !host.isConnected) { if (!ed || !host.isConnected) { ed = null; await mount(blk); } }
    const url = srcOf(blk);
    host.hidden = !url;
    ed.setSource(url);
    ed.setLength(lengthNow());
    const b = $box();
    if (b && b.value !== written && !ed.drag) {
      written = b.value;
      try { const d = JSON.parse(b.value || "{}"); ed.setKeys(Array.isArray(d.strength) ? d.strength : []); } catch (e) { ed.setKeys([]); }
    }
  }, 400);
}
