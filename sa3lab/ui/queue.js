// SA3 UI kit: the render Queue modal — every page's view of the studio's one render queue.
//
//   toggleQueue()            open / close (builds on first use); toggleQueue(true|false) forces it
//   isOpen()                 → bool
//   onChange((d) => …)       d = {running, waiting}: after every load while open (and on close)
//
// The queue is gradio's own (renderq.py: GET / POST /sa3/queue): Generate clicks in the studio,
// Composer takes (composer/server.py submits every take at once) and anarchy/enqueue.py batches all
// wait in it, FIFO. The running job can be skipped (stops only it); waiting jobs can be deleted,
// moved with the arrows or dragged into a new order. Polls once a second while open. Used by the
// studio's Queue button (studio.js) and the kit shell's queue chip (shell.js), so the modal and its
// CSS live only here (injected on first use; the id `sa3-queue` is the studio's original).

const CSS = `
#sa3-queue { position: fixed; inset: 0; z-index: 200; background: rgba(5,5,9,0.35); backdrop-filter: blur(3px);
  opacity: 0; pointer-events: none; transition: opacity .18s ease; }
#sa3-queue.open { opacity: 1; pointer-events: auto; }
#sa3-queue .pane { position: absolute; top: 58px; right: 22px; width: min(560px, calc(100vw - 44px)); max-height: calc(100vh - 80px);
  display: flex; flex-direction: column; border-radius: 16px; overflow: hidden;
  background: rgba(14,14,20,0.95); border: 1px solid rgba(255,255,255,0.08);
  box-shadow: 0 30px 80px -20px rgba(0,0,0,0.8), 0 0 0 1px rgba(155,140,255,0.06);
  transform-origin: top right; transform: translateY(-8px) scale(0.97); transition: transform .2s cubic-bezier(.2,.8,.2,1); }
#sa3-queue.open .pane { transform: none; }
#sa3-queue header { display: flex; align-items: center; gap: 10px; padding: 11px 14px; border-bottom: 1px solid rgba(255,255,255,0.06); }
#sa3-queue h3 { margin: 0; font-size: 15px; font-weight: 700; color: #f4f4fa; letter-spacing: -0.01em; }
#sa3-queue .cnt { font: 500 11.5px "JetBrains Mono", monospace; color: #7d7d8e; }
#sa3-queue .sp { flex: 1; }
#sa3-queue .clr { background: transparent; border: 1px dashed rgba(255,255,255,0.14); color: #8b8b9c; border-radius: 8px; padding: 3px 10px; font-size: 12px; cursor: pointer; }
#sa3-queue .clr:hover:not(:disabled) { color: #ff8a9a; border-color: rgba(255,90,110,0.5); }
#sa3-queue .clr:disabled { opacity: .35; cursor: default; }
#sa3-queue .x { width: 28px; height: 28px; border-radius: 8px; border: 0; background: transparent; color: #8b8b9c; font-size: 20px; line-height: 1; cursor: pointer; }
#sa3-queue .x:hover { background: rgba(255,255,255,0.07); color: #fff; }
#sa3-queue .list { overflow-y: auto; padding: 10px; display: flex; flex-direction: column; gap: 6px; }
#sa3-queue .empty { padding: 26px 10px; text-align: center; color: #6f6f80; font-size: 12.5px; line-height: 1.6; }
#sa3-queue .qj { position: relative; display: grid; grid-template-columns: 44px 1fr auto; gap: 10px; align-items: center;
  padding: 8px 10px; border-radius: 10px; background: rgba(255,255,255,0.025); border: 1px solid rgba(255,255,255,0.07); transition: opacity .15s, border-color .12s; }
#sa3-queue .qj:not(.run) { cursor: grab; }
#sa3-queue .qj:hover { border-color: rgba(255,255,255,0.13); }
#sa3-queue .qj.run { background: rgba(155,140,255,0.07); border-color: rgba(155,140,255,0.35); }
#sa3-queue .qj.drag, #sa3-queue .qj.gone { opacity: .35; }
#sa3-queue .qj.above::before, #sa3-queue .qj.below::after { content: ""; position: absolute; left: 6px; right: 6px; height: 2px; border-radius: 2px;
  background: linear-gradient(90deg, #9b8cff, #ff6fb5); }
#sa3-queue .qj.above::before { top: -5px; } #sa3-queue .qj.below::after { bottom: -5px; }
#sa3-queue .hd { display: flex; align-items: center; gap: 6px; font: 600 11px "JetBrains Mono", monospace; color: #6f6f80; }
#sa3-queue .grip { color: #4f4f60; letter-spacing: -2px; }
#sa3-queue .now { font-size: 9.5px; text-transform: uppercase; letter-spacing: .06em; color: #c4bbff; animation: sa3-qpulse 1.6s ease-in-out infinite; }
@keyframes sa3-qpulse { 50% { opacity: .45; } }
#sa3-queue .bd { min-width: 0; }
#sa3-queue .pr { color: #e3e3ee; font-size: 12.5px; line-height: 1.35; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
#sa3-queue .pr i { color: #6f6f80; }
#sa3-queue .ch { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 5px; }
#sa3-queue .ch span { padding: 1px 7px; border-radius: 99px; font: 500 10.5px "JetBrains Mono", monospace; color: #9b9bab;
  background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.06); max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
#sa3-queue .ch .lo { color: #ffb3dc; border-color: rgba(255,111,181,0.25); }
#sa3-queue .ch .au { color: #8fe3d8; border-color: rgba(79,209,197,0.25); }
#sa3-queue .ch .ng { color: #c9a0a8; }
#sa3-queue .ac { display: flex; align-items: center; gap: 3px; }
#sa3-queue .ac button { width: 24px; height: 24px; display: inline-flex; align-items: center; justify-content: center; border-radius: 7px; border: 0;
  background: transparent; color: #7d7d8e; font-size: 9px; cursor: pointer; }
#sa3-queue .ac button svg { width: 14px; height: 14px; }
#sa3-queue .ac button:hover:not(:disabled) { background: rgba(255,255,255,0.07); color: #fff; }
#sa3-queue .ac button:disabled { opacity: .25; cursor: default; }
#sa3-queue .ac .del:hover { color: #ff8a9a !important; background: rgba(255,90,110,0.12) !important; }
#sa3-queue .ac .skip { width: auto; padding: 0 10px; font-size: 11.5px; font-weight: 600; color: #ff8a9a; border: 1px solid rgba(255,90,110,0.35); }
#sa3-queue .ac .skip:hover:not(:disabled) { background: rgba(255,90,110,0.14) !important; color: #ffb0bb !important; }
`;
const ICON_TRASH = '<svg viewBox="0 0 16 16"><path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.3a1 1 0 0 0 1 .9h3.8a1 1 0 0 0 1-.9l.6-8.3M6.8 7v4.2M9.2 7v4.2" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const QMIME = "application/x-sa3-qjob";
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
let qEl = null, qTimer = null, qData = { running: null, waiting: [] }, qDrag = null, qSig = "";
const subs = new Set();

export const isOpen = () => !!(qEl && qEl.classList.contains("open"));
export function onChange(cb) { subs.add(cb); return () => subs.delete(cb); }
const notify = () => subs.forEach((cb) => { try { cb(qData); } catch (e) { console.warn("[sa3] queue listener", e); } });

function qChips(p) {
  const c = [];
  const n = (v) => (v === null || v === undefined ? "?" : +(+v).toFixed(2));
  if (p.dit) c.push(p.dit + (p.decoder ? ` · ${p.decoder}` : ""));
  if (p.seconds) c.push(`${n(p.seconds)} s`);
  if (p.steps) c.push(`${p.steps} steps`);
  if (p.cfg !== null && p.cfg !== undefined) c.push(`cfg ${n(p.cfg)}` + (p.apg !== undefined && p.apg !== 1 ? ` · apg ${n(p.apg)}` : ""));
  c.push(p.seed ? `seed ${p.seed}` : "random seed");
  if (p.sigma !== undefined) c.push(`σ ${n(p.sigma)}`);
  let h = c.map((t) => `<span>${esc(String(t))}</span>`).join("");
  for (const l of p.loras || []) h += `<span class="lo" title="LoRA">${esc(l.name)} ×${n(l.strength)}</span>`;
  if (p.a2a) h += `<span class="au" title="Audio → audio guide">a2a ${esc(p.a2a.audio)} @${n(p.a2a.noise)}</span>`;
  if (p.inpaint) h += `<span class="au" title="Inpaint span">inpaint ${esc(p.inpaint.audio)} ${p.inpaint.keys != null ? p.inpaint.keys + (p.inpaint.keys === 1 ? " strength key" : " strength keys") : `${n(p.inpaint.from)}–${n(p.inpaint.to)} s`}</span>`;
  if (p.neg) h += `<span class="ng" title="Negative prompt">neg: ${esc(p.neg)}</span>`;
  return h;
}
function qRow(job, i) {
  const p = job.preview || {}, run = i < 0;
  const d = document.createElement("div");
  d.className = "qj" + (run ? " run" : "");
  d.dataset.id = job.id;
  const pr = p.prompt ? esc(p.prompt) : "<i>(no prompt)</i>";
  const acts = run
    ? (job.stopping ? `<button class="skip" disabled title="Stops at the next sampler step or decoder chunk (a few seconds); nothing is saved">stopping…</button>`
      : `<button class="skip" title="Stop this render only; the next job starts right away">Skip</button>`)
    : `<button class="up" title="Move up"${i === 0 ? " disabled" : ""}>▲</button>
       <button class="dn" title="Move down"${i === qData.waiting.length - 1 ? " disabled" : ""}>▼</button>
       <button class="del" title="Remove from the queue">${ICON_TRASH}</button>`;
  d.innerHTML = `
    <div class="hd">${run ? `<span class="now">${job.stopping ? "stopping" : "rendering"}${job.on ? " on " + esc(job.on) : ""}</span>` : `<span class="grip" title="Drag to reorder">⋮⋮</span><span class="ix">${i + 1}</span>`}</div>
    <div class="bd"><div class="pr" title="${esc(p.prompt || "")}">${pr}</div><div class="ch">${qChips(p)}</div></div>
    <div class="ac">${acts}</div>`;
  if (!run) d.draggable = true;
  return d;
}
function renderQueue() {
  if (!qEl) return;
  const sig = JSON.stringify(qData);
  if (sig === qSig || qDrag) return;             // unchanged, or mid-drag: keep the DOM
  qSig = sig;
  const list = qEl.querySelector(".list"), kids = [];
  if (qData.running) kids.push(qRow(qData.running, -1));
  qData.waiting.forEach((j, i) => kids.push(qRow(j, i)));
  if (!kids.length) {
    const e = document.createElement("div");
    e.className = "empty";
    e.innerHTML = "Nothing queued.<br>Every Generate click in the studio and every Composer take adds a job here.";
    kids.push(e);
  }
  list.replaceChildren(...kids);
  const w = qData.waiting.length;
  qEl.querySelector(".cnt").textContent = (qData.running ? "1 rendering" : "idle") + (w ? ` · ${w} waiting` : "");
  qEl.querySelector(".clr").disabled = !w;
  notify();
}
async function loadQueue() {
  try { const r = await fetch("/sa3/queue", { cache: "no-store" }); if (r.ok) { qData = await r.json(); renderQueue(); } }
  catch (e) { /* server restarting */ }
}
async function qPost(body) {
  try {
    const r = await fetch("/sa3/queue", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json();
    if (d.running !== undefined) { qData = { running: d.running, waiting: d.waiting }; qSig = ""; renderQueue(); }
    if (!d.ok && d.error) console.warn("[sa3] queue:", d.error);
  } catch (e) { console.warn("[sa3] queue op failed", e); }
}
function buildQueue() {
  if (!document.getElementById("ui-queue-css")) {
    const st = document.createElement("style");
    st.id = "ui-queue-css"; st.textContent = CSS;
    document.head.appendChild(st);
  }
  qEl = document.createElement("div");
  qEl.id = "sa3-queue";
  qEl.innerHTML = `
    <div class="pane" role="dialog" aria-label="Render queue">
      <header><h3>Queue</h3><span class="cnt"></span><span class="sp"></span>
        <button class="clr" title="Remove every waiting job (the running one keeps going)">Clear waiting</button>
        <button class="x" title="Close (Esc)">×</button></header>
      <div class="list"></div>
    </div>`;
  document.body.appendChild(qEl);
  qEl.addEventListener("mousedown", (e) => { if (e.target === qEl) toggleQueue(false); });
  qEl.querySelector(".x").addEventListener("click", () => toggleQueue(false));
  qEl.querySelector(".clr").addEventListener("click", () => qPost({ op: "clear" }));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && isOpen()) { e.stopPropagation(); toggleQueue(false); } }, true);
  const list = qEl.querySelector(".list");
  list.addEventListener("click", (e) => {
    const b = e.target.closest("button"), row = e.target.closest(".qj");
    if (!b || !row || b.disabled) return;
    const id = row.classList.contains("run") ? qData.running && qData.running.id : row.dataset.id;
    const i = qData.waiting.findIndex((j) => j.id === row.dataset.id);
    if (b.classList.contains("skip")) { b.disabled = true; b.textContent = "stopping…"; qPost({ op: "skip", id }); }
    else if (b.classList.contains("del")) { row.classList.add("gone"); qPost({ op: "delete", id }); }
    else if (b.classList.contains("up")) qPost({ op: "move", id, to: i - 1 });
    else if (b.classList.contains("dn")) qPost({ op: "move", id, to: i + 1 });
  });
  // drag to reorder: drop above/below the row under the cursor
  const clearMarks = () => list.querySelectorAll(".above, .below").forEach((x) => x.classList.remove("above", "below"));
  list.addEventListener("dragstart", (e) => {
    const row = e.target.closest(".qj:not(.run)");
    if (!row) return;
    qDrag = row.dataset.id;
    e.dataTransfer.setData(QMIME, qDrag);
    e.dataTransfer.effectAllowed = "move";
    row.classList.add("drag");
  });
  list.addEventListener("dragover", (e) => {
    if (!qDrag) return;
    const row = e.target.closest(".qj:not(.run)");
    e.preventDefault();
    clearMarks();
    if (!row || row.dataset.id === qDrag) return;
    const r = row.getBoundingClientRect();
    row.classList.add(e.clientY < r.top + r.height / 2 ? "above" : "below");
  });
  list.addEventListener("drop", (e) => {
    if (!qDrag) return;
    e.preventDefault();
    const row = list.querySelector(".above, .below"), id = qDrag;
    clearMarks();
    if (!row) return;
    const ids = qData.waiting.map((j) => j.id).filter((x) => x !== id);
    const to = ids.indexOf(row.dataset.id) + (row.classList.contains("below") ? 1 : 0);
    qDrag = null;
    qPost({ op: "move", id, to });
  });
  list.addEventListener("dragend", () => {
    clearMarks();
    list.querySelectorAll(".drag").forEach((x) => x.classList.remove("drag"));
    if (qDrag) { qDrag = null; qSig = ""; renderQueue(); }
  });
}
export function toggleQueue(want) {
  if (!qEl) buildQueue();
  const open = want === undefined ? !isOpen() : want;
  qEl.classList.toggle("open", open);
  clearInterval(qTimer); qTimer = null;
  if (open) { qSig = ""; loadQueue(); qTimer = setInterval(loadQueue, 1000); }
  notify();
}
