() => {
  // Promptless (anarchy) panel. Server side + the spec grammar: anarchy_ui.py,
  // anarchy/sampler.py. While the panel is Active, the Prompt box shows the live
  // `anarchy: …` spec (the user's text prompt is stashed and comes back on deactivate)
  // and the studio's own Generate button renders it: a capture-phase click listener
  // swaps in the spec(s) for the chosen mode (one draw / N random directions / a
  // temperature ladder) and re-clicks Generate, so queue / Stop / history / labels all
  // just work. Restoring a clip's settings syncs the panel: a LATENT clip's spec loads
  // into the panel (and activates it), a TEXT clip's prompt deactivates it.
  const KEY = "sa3-anarchy";
  const saved = (() => { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { return {}; } })();
  const st = Object.assign({ active: false, text: "", fit: null, sampler: "tensor-pca", Tlo: 0.5, Thi: 2,
                             Tdist: "uniform", d: "", Lauto: true, L: 30, c: 0, k: 3, m: 30, mode: "one", n: 4 }, saved);
  if (saved.T !== undefined && saved.Tlo === undefined) { st.Tlo = st.Thi = saved.T; }   // pre-range state
  delete st.open; delete st.T; delete st.temps;
  const save = () => localStorage.setItem(KEY, JSON.stringify(st));
  let fits = [], samplers = {}, busy = false, lastSeen = null;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const IS_SPEC = /^\s*anarchy:/i;
  const BOOT = Date.now();
  const T_MIN = 0.1, T_MAX = 4;

  const labelled = (txt) => [...document.querySelectorAll(".block")].find((b) => {
    const l = b.querySelector("[data-testid='block-info'], label span");
    return l && l.textContent.trim() === txt;
  });
  const promptBox = () => { const b = labelled("Prompt"); return b && b.querySelector("textarea"); };
  const seedBox = () => { const b = labelled("Seed"); return b && b.querySelector("input, textarea"); };
  const setVal = (el, v) => {
    el.value = v;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  };

  const fit = () => fits.find((f) => f.name === st.fit) || fits[0];
  const fmtT = (T) => +(+T).toFixed(3);
  // one concrete spec — what the server renders and the sidecar records
  // d === "" (blank Direction box) = a new random direction per render: concrete specs get
  // one from dir(); the Prompt-box mirror shows `d?` (anarchy_ui.py resolves it for pregen)
  function spec(T, d = st.d) {
    const f = fit();
    let s = `anarchy: ${f ? f.name : st.fit || "?"} ${st.sampler} T${fmtT(T)} d${d === "" ? "?" : d}`;
    if (!st.Lauto && st.sampler !== "mutate") s += ` L${st.L}`;
    if (st.sampler === "tensor-pca" && +st.c > 0) s += ` c${st.c}`;
    if (st.sampler === "cocktail") s += ` k${st.k}`;
    if (st.sampler === "mutate") s += ` m${st.m} | ${(st.text || "").replace(/\s+/g, " ").trim()}`;
    return s;
  }
  const fixedT = () => +st.Tlo === +st.Thi;
  // what the Prompt box shows while Active: the spec, with the temperature range when it is one
  // (never rendered as-is: Generate swaps in concrete specs)
  const shown = () => fixedT() ? spec(st.Tlo)
    : spec(0).replace(/ T0 /, ` T${fmtT(st.Tlo)}–${fmtT(st.Thi)}(${st.Tdist}) `);
  // a temperature from the range: uniform, or a Gaussian centred on the middle with σ = range/4
  // (±2σ spans the range), redrawn when it falls outside
  function drawT() {
    const lo = +st.Tlo, hi = +st.Thi;
    if (hi <= lo) return lo;
    if (st.Tdist !== "gaussian") return lo + Math.random() * (hi - lo);
    for (let i = 0; i < 50; i++) {
      const g = Math.sqrt(-2 * Math.log(1 - Math.random())) * Math.cos(2 * Math.PI * Math.random());
      const T = (lo + hi) / 2 + g * (hi - lo) / 4;
      if (T >= lo && T <= hi) return T;
    }
    return (lo + hi) / 2;
  }
  const ladderTs = () => {
    const n = Math.max(1, st.n | 0), lo = +st.Tlo, hi = +st.Thi;
    return n === 1 ? [(lo + hi) / 2] : Array.from({ length: n }, (_, i) => lo + (hi - lo) * i / (n - 1));
  };
  const rnd = () => Math.floor(Math.random() * 100000);

  // a spec string → panel state (restore-settings from a LATENT clip); false if it isn't one we can show
  function adopt(s) {
    const body = s.trim().replace(/^anarchy:\s*/i, ""), bar = body.indexOf("|");
    const toks = (bar < 0 ? body : body.slice(0, bar)).trim().split(/\s+/);
    if (!toks[0] || toks[0].startsWith("@")) return false;
    const n = { sampler: "tensor-pca", T: 1, d: "", Lauto: true, c: 0 };
    let r;
    if (bar >= 0) n.text = body.slice(bar + 1).trim();
    for (const t of toks) {
      const m = t.match(/^([TdLckm])(-?\d+(?:\.\d+)?)$/);
      if (samplers[t] || ["tensor-pca", "tensor-diag", "bag", "cocktail", "mutate"].includes(t)) n.sampler = t;
      else if (t === "d?") n.d = "";                              // the panel's own mirror text
      else if ((r = t.match(/^T(\d+(?:\.\d+)?)[–-](\d+(?:\.\d+)?)\((uniform|gaussian)\)$/))) { n.Tlo = +r[1]; n.Thi = +r[2]; n.Tdist = r[3]; }
      else if (m) { if (m[1] === "L") { n.Lauto = false; n.L = +m[2]; } else n[m[1]] = +m[2]; }
      else if (!n.fit) n.fit = t;
      else return false;
    }
    if (!n.fit) return false;
    if (n.Tlo === undefined) n.Tlo = n.Thi = n.T;
    delete n.T;          // a restored clip pins its exact temperature
    Object.assign(st, n);
    return true;
  }

  // ── the Prompt box mirrors the panel while Active ──
  function syncPrompt() {
    const p = promptBox();
    if (!p || busy) return;
    const blk = p.closest(".block");
    if (blk) blk.classList.toggle("an-prompt-locked", st.active);
    p.readOnly = st.active;
    if (!st.active) { lastSeen = p.value; return; }
    const want = shown();
    if (p.value !== want) { setVal(p, want); }
    lastSeen = want;
  }
  function setActive(on) {
    const p = promptBox();
    if (on && !st.active) {
      if (p && !IS_SPEC.test(p.value)) st.text = p.value;
      else if (p && IS_SPEC.test(p.value)) adopt(p.value);
    }
    if (!on && st.active && p) { p.readOnly = false; setVal(p, st.text || ""); }
    st.active = on;
    save();
    repaint();
    syncPrompt();
  }
  // something other than us changed the Prompt box (restore settings, typing before readOnly kicked in)
  function watchPrompt() {
    const p = promptBox();
    if (!p || busy) return;
    // page load: gradio's last-prompt prefill and studio.js's reload-keep land in the first
    // seconds; never let those flip the toggle, just re-assert the panel's state
    if (lastSeen === null || Date.now() - BOOT < 3500) { syncPrompt(); return; }
    if (p.value === lastSeen) return;
    const v = p.value;
    lastSeen = v;
    if (IS_SPEC.test(v)) {
      if (!st.active && document.activeElement === p) return;      // hand-typing a spec while inactive: leave it
      if (adopt(v)) { if (!st.active) { st.active = true; } save(); repaint(); syncPrompt(); }
    } else if (st.active) {
      st.text = v; st.active = false; save(); repaint(); syncPrompt();
    }
  }

  // ── Generate while Active: render the chosen mode's spec(s) ──
  let bypass = false;
  async function submit(specs, fixedSeed) {
    const p = promptBox(), g = document.getElementById("sa3-generate"), sb = seedBox();
    if (!p || !g) return;
    busy = true;
    const prevSeed = sb ? sb.value : "";
    try {
      if (fixedSeed && sb && !sb.value.trim()) setVal(sb, String(1 + Math.floor(Math.random() * 9999)));
      for (const s of specs) {
        setVal(p, s);
        await sleep(120);            // let gradio take the new value before the click snapshots it
        bypass = true; g.click(); bypass = false;
        await sleep(250);
      }
      if (fixedSeed && sb && !prevSeed.trim()) { await sleep(200); setVal(sb, prevSeed); }
    } finally {
      busy = false;
      syncPrompt();
    }
  }
  document.addEventListener("click", (e) => {
    if (bypass || window.__sa3GenRaw || !st.active) return;      // __sa3GenRaw: extend.js renders the restored spec as is
    const g = e.target.closest && e.target.closest("#sa3-generate");
    if (!g || g.disabled) return;
    e.preventDefault(); e.stopImmediatePropagation();
    if (busy) return;
    const dir = () => (st.d === "" ? rnd() : st.d);
    if (st.mode === "ladder") { const d = dir(); submit(ladderTs().map((T) => spec(T, d)), true); }
    else if (st.mode === "many") submit(Array.from({ length: Math.max(1, st.n | 0) }, () => spec(drawT(), rnd())), false);
    else submit([spec(drawT(), dir())], false);
  }, true);

  // ── panel ──
  const TIP = {
    active: "When on, the normal Generate button renders sampled T5Gemma conditioning instead of a text prompt. " +
            "The Prompt box shows what will render; your text prompt comes back when you switch off.",
    fit: "A Gaussian model of the conditioning vectors of a prompt corpus (made by sa3lab/anarchy/fit.py). " +
         "Draws land in the region of conditioning space those prompts cover. Only fits made for the selected Model work.",
    sampler: "How a conditioning tensor is drawn from the fit.",
    "tensor-pca": "Draws the whole prompt at once inside the corpus's main PCA axes: stays closest to what real prompts look like.",
    "tensor-diag": "Every coordinate drawn from its own Gaussian, ignoring correlations: lands off the prompt manifold, often stranger textures.",
    bag: "Draws L independent token vectors from the token-level fit (rest is padding): a bag of words with no sentence structure.",
    cocktail: "Picks k concepts from the fit's concept vocabulary (word clusters like 'drone / granular / tape') and fills the prompt with " +
              "copies of them: few strong ideas instead of a diffuse soup. Temperature 0 = the pure concepts, 1 = real words from " +
              "those clusters, above 1 = exaggerated. The picked concepts show in the clip's settings card.",
    mutate: "Starts from your own text prompt (Anchor below) and swaps a share of its tokens for sampled ones: " +
            "explore the neighbourhood of a prompt you know. Temperature sets how wild the swapped-in tokens are.",
    k: "How many concepts go in the cocktail. 1–2 = a strong, focused idea; 5+ = a blend.",
    m: "Share of the anchor prompt's tokens replaced by sampled ones. 0% = your prompt as is, 100% = a random bag of the same length.",
    text: "The text prompt being mutated. Kept with the clip (it's part of the spec), so the render stays reproducible.",
    T: "How far from the corpus mean a draw goes. 1 = the corpus's own spread, below 1 tamer and more average, " +
       "above 1 wilder and further off-manifold. Each render picks its temperature from this range; set both ends equal for a fixed temperature.",
    Tlo: "Lowest temperature to draw from.",
    Thi: "Highest temperature to draw from.",
    uniform: "Every temperature in the range is equally likely.",
    gaussian: "Bell curve centred on the middle of the range (σ = range / 4), so the ends are rare.",
    d: "Seed of the random direction in conditioning space. Same direction + same fit = the same draw, " +
       "only scaled by temperature. Leave empty for a new random direction on every render " +
       "(a ladder then picks one for all its steps). Random ×N always rolls its own.",
    dice: "Pick a new random direction.",
    L: "How many token vectors the sampled prompt has, like prompt length in words. NOT audio length.",
    Lauto: "Draw the token count from the corpus's prompt-length histogram (seeded by the direction).",
    c: "Skip the first PCA axes. On grammar-music-100, PC1–2 encode wording (prompt length, prefix) rather than sound, " +
       "so skipping them spends the temperature on sonic variation. tensor-pca only.",
    mode: "What one click on Generate (or ⌘↵) queues.",
    one: "One render: the direction above, at a temperature drawn from the range.",
    many: "N renders, each with a new random direction and its own temperature from the range.",
    ladder: "N renders of the direction above at evenly spaced temperatures from low to high, all with one pinned noise seed, so only the temperature changes.",
    n: "How many renders one Generate queues.",
  };
  const tip = (k) => ` data-tip="${esc(TIP[k] || "")}"`;
  const MODES = { one: "One draw", many: "Random ×N", ladder: "Temperature ladder" };
  const pct = (T) => ((T - T_MIN) / (T_MAX - T_MIN) * 100).toFixed(2) + "%";

  function html() {
    const f = fit();
    const fitOpts = fits.map((x) => `<option value="${esc(x.name)}"${f && x.name === f.name ? " selected" : ""}>${esc(x.name)}</option>`).join("");
    const sm = Object.keys(samplers).map((k) =>
      `<button type="button" class="an-seg${k === st.sampler ? " on" : ""}" data-s="${k}"${tip(k)}>${k}</button>`).join("");
    const md = Object.entries(MODES).map(([k, lab]) =>
      `<button type="button" class="an-seg${k === st.mode ? " on" : ""}" data-m="${k}"${tip(k)}>${lab}</button>`).join("");
    const Lmax = (f && f.L_crop) || 80;
    const field = (label, tk, right, body, hint = "") => `
      <div class="an-field"><div class="an-lab"><span${tip(tk)}>${label}<i class="an-q">?</i></span>${right ? `<span class="an-r">${right}</span>` : ""}</div>
        ${body}${hint ? `<div class="an-hint">${hint}</div>` : ""}</div>`;
    const num = (k, v, min, max, step, cls = "an-num") =>
      `<input type="number" min="${min}" max="${max}" step="${step}" value="${v}" data-k="${k}" class="${cls}"${tip(k)}>`;
    const radio = (v) => `<label class="an-radio"${tip(v)}><input type="radio" name="an-tdist" value="${v}" data-k="Tdist"${st.Tdist === v ? " checked" : ""}><i></i>${v}</label>`;
    const modeArg = st.mode === "one" ? ""
      : `<div class="an-inline"><span${tip("n")}>${st.mode === "ladder" ? "Steps" : "N"}</span>${num("n", st.n, 1, 32, 1)}
         ${st.mode === "ladder" ? `<span class="an-hint an-ladder">${ladderTs().map(fmtT).map((x) => x.toFixed(2)).join(" · ")}</span>` : ""}</div>`;
    return `
      <label class="an-head"${tip("active")}>
        <span class="an-switch"><input type="checkbox" data-act="active"${st.active ? " checked" : ""}><i></i></span>
        <span class="an-title"><b>Promptless</b><span class="an-sub">${st.active
          ? (st.sampler === "mutate" ? "Active: Generate mutates the Anchor prompt" : "Active: Generate samples, Prompt box ignored")
          : "sample the conditioning space instead of writing a prompt"}</span></span>
      </label>
      <div class="an-body"${st.active ? "" : " hidden"}>
        ${fits.length ? "" : `<div class="an-warn">No fits yet: run <code>sa3lab/anarchy/fit.py</code> on a corpus.</div>`}
        ${field("Fit", "fit", f ? `${f.n_prompts} prompts · ${esc(f.dits.join(", "))}` : "", `<select data-k="fit"${tip("fit")}>${fitOpts}</select>`)}
        ${field("Sampler", "sampler", "", `<div class="an-segs">${sm}</div>`, esc(samplers[st.sampler] || ""))}
        ${field("Temperature", "T", `${num("Tlo", st.Tlo, 0, 10, 0.05)}<span class="an-dash">–</span>${num("Thi", st.Thi, 0, 10, 0.05)}`,
          `<div class="an-dual" style="--lo:${pct(Math.max(T_MIN, st.Tlo))};--hi:${pct(Math.min(T_MAX, st.Thi))}"><b></b>
             <span class="an-th" data-th="Tlo"${tip("Tlo")}></span><span class="an-th" data-th="Thi"${tip("Thi")}></span></div>
           <div class="an-scale">${[1, 2, 3].map((t) => `<span style="left:${pct(t)}">${t}</span>`).join("")}</div>
           <div class="an-radios${fixedT() ? " an-dim" : ""}">${radio("uniform")}${radio("gaussian")}</div>`)}
        ${field("Direction", "d", "",
          `<div class="an-inline">${num("d", st.d, 0, "", 1, "an-grow").replace("<input ", '<input placeholder="random every render" ')}
           <button type="button" class="an-btn an-icon" data-act="dice"${tip("dice")}>⚄</button></div>`)}
        ${st.sampler === "mutate" ? "" : field("Prompt tokens", "L", `<label class="an-chk"${tip("Lauto")}><input type="checkbox" data-k="Lauto"${st.Lauto ? " checked" : ""}> from corpus</label>`,
          `<div class="an-inline"><input type="range" min="1" max="${Lmax}" step="1" value="${st.L}" data-k="L"${st.Lauto ? " disabled" : ""}${tip("L")}>
           <span class="an-val">${st.Lauto ? "auto" : st.L + " tok"}</span></div>`)}
        ${st.sampler === "tensor-pca" ? field("Skip PCs", "c", num("c", st.c, 0, 50, 1), "") : ""}
        ${st.sampler === "cocktail" ? field("Concepts", "k", num("k", st.k, 1, 8, 1), "") : ""}
        ${st.sampler === "mutate" ? field("Anchor prompt", "text", "",
          `<textarea rows="2" data-k="text" class="an-anchor" placeholder="the text prompt to mutate"${tip("text")}>${esc(st.text || "")}</textarea>`,
          (st.text || "").trim() ? "" : "Type a prompt to mutate.") +
          field("Mutate", "m", `<span class="an-val an-mval">${st.m}%</span>`,
          `<input type="range" min="0" max="100" step="5" value="${st.m}" data-k="m"${tip("m")}>`) : ""}
        <div class="an-sep"></div>
        ${field("Generate makes", "mode", "", `<div class="an-segs an-full">${md}</div>${modeArg}`)}
        <div class="an-foot">Length, model and seed come from the controls below.</div>
      </div>`;
  }

  let card = null;
  function repaint() { if (card) card.innerHTML = html(); }

  // ── hover tooltips: one floating card for every [data-tip] in the panel ──
  const tipEl = document.createElement("div");
  tipEl.id = "sa3-an-tip";
  document.body.appendChild(tipEl);
  let tipT = null, tipFor = null;
  function showTip(el) {
    const r = el.getBoundingClientRect();
    tipEl.textContent = el.dataset.tip;
    tipEl.classList.add("on");
    const w = tipEl.offsetWidth, h = tipEl.offsetHeight;
    const x = Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2));
    const y = r.bottom + 8 + h > innerHeight ? r.top - h - 8 : r.bottom + 8;
    tipEl.style.left = x + "px"; tipEl.style.top = y + "px";
  }
  const hideTip = () => { clearTimeout(tipT); tipFor = null; tipEl.classList.remove("on"); };

  function rangeMoved(c, from) {
    for (const kk of ["Tlo", "Thi"]) c.querySelectorAll(`input[data-k="${kk}"]`).forEach((x) => { if (x !== from) x.value = fmtT(st[kk]); });
    const d = c.querySelector(".an-dual");
    if (d) { d.style.setProperty("--lo", pct(Math.max(T_MIN, st.Tlo))); d.style.setProperty("--hi", pct(Math.min(T_MAX, st.Thi))); }
    c.querySelector(".an-radios")?.classList.toggle("an-dim", fixedT());
    const lad = c.querySelector(".an-ladder"); if (lad) lad.textContent = ladderTs().map((x) => x.toFixed(2)).join(" · ");
  }

  // two-thumb temperature slider: grab the nearer thumb (when they coincide, the side you
  // pressed on decides), drag it, the other end is a wall
  function dualDown(c, e) {
    const d = e.target.closest(".an-dual");
    if (!d || e.button !== 0) return;
    e.preventDefault(); hideTip();
    const r = d.getBoundingClientRect();
    const at = (x) => Math.round((T_MIN + Math.min(1, Math.max(0, (x - r.left) / r.width)) * (T_MAX - T_MIN)) / 0.05) * 0.05;
    const v0 = at(e.clientX);
    const which = Math.abs(st.Thi - st.Tlo) < 1e-9 ? (v0 < st.Tlo ? "Tlo" : "Thi")
      : (Math.abs(v0 - st.Tlo) <= Math.abs(v0 - st.Thi) ? "Tlo" : "Thi");
    d.classList.add("drag");
    const move = (ev) => {
      let v = +at(ev.clientX).toFixed(2);
      v = which === "Tlo" ? Math.min(v, st.Thi) : Math.max(v, st.Tlo);
      if (v === st[which]) return;
      st[which] = v;
      rangeMoved(c, null);
      syncPrompt();
    };
    const up = () => {
      d.classList.remove("drag");
      removeEventListener("pointermove", move); removeEventListener("pointerup", up);
      save();
    };
    addEventListener("pointermove", move); addEventListener("pointerup", up);
    move(e);
  }

  function wire(c) {
    c.addEventListener("pointerdown", (e) => dualDown(c, e));
    c.addEventListener("mouseover", (e) => {
      const el = e.target.closest("[data-tip]");
      if (el === tipFor) return;
      hideTip();
      if (!el || !el.dataset.tip) return;
      tipFor = el;
      tipT = setTimeout(() => showTip(el), 350);
    });
    c.addEventListener("mouseleave", hideTip);
    c.addEventListener("mousedown", hideTip);
    c.addEventListener("click", (e) => {
      const s = e.target.closest("[data-s]");
      if (s) { st.sampler = s.dataset.s; save(); repaint(); syncPrompt(); return; }
      const m = e.target.closest("[data-m]");
      if (m) { st.mode = m.dataset.m; save(); repaint(); return; }
      const a = e.target.closest("[data-act]");
      if (a && a.dataset.act === "dice") { st.d = rnd(); save(); repaint(); syncPrompt(); }
    });
    c.addEventListener("change", (e) => {
      if (e.target.dataset.act === "active") setActive(e.target.checked);
      const k = e.target.dataset.k;
      if (k === "Tlo" || k === "Thi" || k === "n") repaint();    // settle the numbers / ladder preview
    });
    c.addEventListener("input", (e) => {
      const k = e.target.dataset.k;
      if (!k) return;
      const t = e.target;
      st[k] = t.type === "checkbox" ? t.checked : (t.type === "range" || t.type === "number") ? +t.value : t.value;
      if (k === "d" && t.value.trim() === "") st.d = "";          // blank = random, never 0
      if (k === "Tlo" && st.Tlo > st.Thi) st.Thi = st.Tlo;      // typing past the other end drags it along
      if (k === "Thi" && st.Thi < st.Tlo) st.Tlo = st.Thi;
      save();
      if (k === "Tlo" || k === "Thi") rangeMoved(c, t);
      if (k === "n") rangeMoved(c, t);
      if (k === "Lauto" || k === "fit") repaint();
      if (k === "L") { const v = c.querySelector(".an-val"); if (v) v.textContent = st.L + " tok"; }
      if (k === "m") { const v = c.querySelector(".an-mval"); if (v) v.textContent = st.m + "%"; }
      syncPrompt();
    });
  }

  function mount() {
    if (!document.getElementById("sa3-anarchy")) {
      const p = labelled("Prompt"), ctl = document.getElementById("sa3-ctl");
      if (!p || !ctl) return;
      let anchor = p;                          // the prompt's row: the direct child of the controls column
      while (anchor.parentElement && anchor.parentElement !== ctl && ctl.contains(anchor.parentElement)) anchor = anchor.parentElement;
      card = document.createElement("div");
      card.id = "sa3-anarchy";
      repaint();
      wire(card);
      anchor.after(card);
    }
    card.classList.toggle("an-on", st.active);
    watchPrompt();
  }

  // ── source badges: every clip card says TEXT (prompt) or LATENT (sampled conditioning) ──
  const LATENT_RE = /^anarchy[:\s]/i;
  function summary(t) {
    const full = t.replace(/^anarchy:?\s*/i, "");
    if (full.startsWith("@")) return "saved latent · " + full.slice(1).split("/").pop().replace(/\.mp3\.cond\.npz$|\.wav\.cond\.npz$|\.cond\.npz$/, "");
    const bar = full.indexOf("|"), body = bar < 0 ? full : full.slice(0, bar).trim();
    const parts = [];
    for (const tok of body.split(/\s+/)) {
      const m = tok.match(/^([TdLckm])(-?\d+(?:\.\d+)?)$/);
      if (m) parts.push({ T: "T ", d: "dir ", L: "", c: "skip ", k: "", m: "" }[m[1]] + m[2]
        + ({ L: " tok", c: " PCs", k: " concepts", m: "% mutated" }[m[1]] || ""));
      else parts.push(tok);
    }
    if (bar >= 0) { const x = full.slice(bar + 1).trim(); parts.push("“" + (x.length > 40 ? x.slice(0, 40) + "…" : x) + "”"); }
    return parts.join(" · ");
  }
  function badges() {
    document.querySelectorAll(".sa3-cap:not([data-an])").forEach((cap) => {
      const t = cap.textContent.trim();
      if (!t) return;
      cap.dataset.an = "1";
      const latent = LATENT_RE.test(t);
      const b = document.createElement("span");
      b.className = "an-badge " + (latent ? "an-latent" : "an-text");
      b.textContent = latent ? "LATENT" : "TEXT";
      b.title = latent ? "sampled conditioning (no prompt): " + t : "rendered from a text prompt";
      if (latent) {
        cap.title = t;
        cap.textContent = summary(t);
        cap.classList.add("an-latent-cap");
        cap.closest(".blk")?.classList.add("an-latent-blk");
      }
      cap.prepend(b);
    });
  }
  setInterval(badges, 600);

  // ── distance pill on every latent clip's spectrogram: how far its conditioning sits from the
  //    fit's corpus centre (RMS z-score; real prompts ≈ 0.5–0.95, a typical one ≈ 1). Read from
  //    the sidecar via GET /sa3/settings, cached per clip: gradio rebuilds the history HTML on
  //    every render, so pills are re-added from the cache each time. ──
  const distCache = new Map();                    // clip name → number | null | Promise
  const distHue = (d) => d <= 1 ? 200 - 60 * Math.min(1, Math.max(0, (d - 0.4) / 0.6))   // tame: blue → green
                                : 140 - 140 * Math.min(1, (d - 1) / 2);                  // wild: green → yellow → red at 3
  function distOf(name) {
    if (!distCache.has(name)) {
      distCache.set(name, fetch("sa3/settings?name=" + encodeURIComponent(name)).then((r) => r.ok ? r.json() : null)
        .then((j) => { const v = j && j.anarchy && j.anarchy.distance; distCache.set(name, typeof v === "number" ? v : null); })
        .catch(() => distCache.set(name, null)));
    }
    return distCache.get(name);
  }
  function distPills() {
    document.querySelectorAll("#sa3-out .blk, #sa3-lib .blk, #sa3-more .blk").forEach((blk) => {
      const a = blk.querySelector("audio"), ph = blk.querySelector(".ph");
      if (!a || !ph || blk.querySelector(".an-dist")) return;
      const name = a.dataset.name || decodeURIComponent((a.getAttribute("src") || "").split("/").pop());
      if (!/^anarchy/i.test(name)) return;
      const v = distOf(name);
      if (typeof v !== "number") return;          // pending (next pass paints it) or not a sampled clip
      const p = document.createElement("span");
      p.className = "an-dist";
      p.textContent = "dist " + v.toFixed(2);
      p.style.setProperty("--h", distHue(v).toFixed(0));
      p.title = "Distance from the fit's corpus centre (RMS z-score). Real prompts sit at 0.5–0.95, " +
                "1 = a typical prompt (green); blue = tamer, yellow → red = further off the prompt manifold (red at 3). " +
                "Not comparable between samplers: cocktail and mutate reuse real word vectors and read low.";
      p.addEventListener("click", (e) => e.stopPropagation());   // the spectrogram under it seeks on click
      ph.parentElement.appendChild(p);
    });
  }
  setInterval(distPills, 600);

  // ── settings hover card: the latent block (studio.js settingsHtml calls this) ──
  window.sa3SettingsExtra = (d, esc, num) => {
    const rows = [], add = (k, v, cls = "m") => { if (v !== undefined && v !== null && v !== "") rows.push(`<dt>${k}</dt><dd class="${cls}">${v}</dd>`); };
    const a = d.anarchy;
    const latent = !!a || LATENT_RE.test(d.prompt || "");
    add("Source", latent ? '<span class="an-badge an-latent">LATENT</span> sampled conditioning' : '<span class="an-badge an-text">TEXT</span> prompt', "");
    if (!a) return rows;
    if (a.source_file) { add("Latent from", esc(a.source_file), ""); return rows; }
    add("Fit", esc([a.fit, a.fit_corpus && `(${a.fit_corpus.split("/").pop()})`].filter(Boolean).join(" ")), "");
    add("Sampler", esc(a.sampler || ""));
    add("Temperature", a.T !== undefined ? num(a.T) : (a.temperature !== undefined ? num(a.temperature) : null));
    add("Direction", a.d !== undefined ? a.d : a.direction);
    add("Prompt tokens", a.L !== undefined ? a.L : a.length);
    add("Skip PCs", a.c && a.sampler === "tensor-pca" ? a.c : null);
    if (a.concepts) add("Concepts", a.concepts.map((c) => esc(c.words.join(" / "))).join("<br>"), "");
    if (a.sampler === "mutate") {
      add("Anchor", esc(a.text || ""), "");
      add("Mutated", a.mutated !== undefined ? `${a.mutated} of ${a.text_tokens} tokens (${a.m}%)` : null);
    }
    add("Distance", a.distance !== undefined ? num(a.distance) : null);
    add("Latent file", a.cond_npz ? esc(a.cond_npz) : null, "");
    return rows;
  };

  fetch("sa3/anarchy/fits", { cache: "no-store" }).then((r) => r.json()).then((j) => {
    fits = j.fits || []; samplers = j.samplers || {};
    if (!st.fit && fits[0]) st.fit = fits[0].name;
    repaint(); syncPrompt();
  }).catch(() => {});
  setInterval(mount, 500);
}
