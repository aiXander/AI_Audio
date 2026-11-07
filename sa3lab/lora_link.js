() => {
  // The studio's LoRA panel = the UI kit's LoRA stack (sa3lab/ui/lora.js, the same editor as the
  // Composer's Render settings), mounted into #sa3-lora-kit (studio.py puts it first in the "LoRA
  // adapters" accordion; lora_link.css hides upstream's slot widgets). Data path: the stack reads
  // and writes the hidden JSON box #sa3-lora-data; studio.py's lora_apply turns each edit into
  // upstream's 3 slots (what /generate reads), and a model switch / restore writes the incoming
  // slots back into the box, which this poll picks up. Also puts "N active" on the accordion header.
  const css = (href) => { if (!document.querySelector(`link[href="${href}"]`)) { const l = document.createElement("link"); l.rel = "stylesheet"; l.href = href; document.head.appendChild(l); } };
  const $box = () => document.querySelector("#sa3-lora-data textarea, #sa3-lora-data input");
  const parse = (v) => { try { const d = JSON.parse(v || "[]"); return Array.isArray(d) ? d : []; } catch (e) { return []; } };
  function stepsNow() {
    const b = [...document.querySelectorAll("#sa3-ctl .block")].find((x) => { const l = x.querySelector("label span, [data-testid='block-info']"); return l && l.textContent.trim() === "Steps"; });
    const v = b && +(b.querySelector("input[type=number]") || {}).value;
    return v > 0 ? v : 8;
  }
  function badge(n) {
    const hd = [...document.querySelectorAll(".label-wrap")].find((b) => b.textContent.trim().startsWith("LoRA adapters"));
    const lab = hd && hd.querySelector(":scope > span");
    if (lab) n ? lab.setAttribute("data-lora", `${n} active`) : lab.removeAttribute("data-lora");
  }
  let st = null, host = null, written = null, mod = null, mounting = false, steps = 0;
  function write(list) {
    const b = $box();
    if (!b) return;
    const v = JSON.stringify(list);
    b.value = v; written = v;
    b.dispatchEvent(new Event("input", { bubbles: true }));
    badge(list.length);
  }
  async function mount(el) {
    css("/sa3/ui/vars.css"); css("/sa3/ui/components.css");
    mod = mod || (await import("/sa3/ui/lora.js"));
    el.innerHTML = "";
    st = new mod.LoraStack(el, { value: parse(($box() || {}).value), maxSteps: stepsNow, onChange: write });
    host = el;
  }
  setInterval(async () => {
    const b = $box();
    if (!b) return;
    if (b.value !== written) {                     // set from the server (restore, model switch, session load)
      written = b.value;
      const l = parse(b.value);
      badge(l.length);
      if (st) st.set(l);
    }
    const el = document.getElementById("sa3-lora-kit");
    if (el && (!st || host !== el || !el.firstChild) && !mounting) { mounting = true; try { await mount(el); } finally { mounting = false; } }
    const n = stepsNow();
    if (st && n !== steps) { steps = n; st.paint(); }   // the step window's "of N" follows Steps
  }, 400);
}
