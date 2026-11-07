"""Experiment 0g (composer spec 3.2): which embedding finds moments that *sound* related? Blind triplets.

    PY=stable-audio-3/optimized/mlx/.venv/bin/python
    $PY sa3lab/bench/embed_triplets.py build [--n 40]     # needs outputs/.embeddings (the studio's idle worker)
    cd outputs/composer-exp/0g && python3 serve.py        # → http://127.0.0.1:8765, answers saved to labels.json
    $PY sa3lab/bench/embed_triplets.py analyze

Each triplet is 8 s of an anchor A and two candidates B / C from other starred renders; the page asks
"which is closer to A?" (B, C, or can't tell), blind and in random order. Candidates come from
embed.py's index: **sound** (E2, SAME-L latent window fingerprint: the best window of the nearest
other render) vs **prompt** (E3, the source conditioning: the nearest render by what it was asked to
be, its window at the same relative position as A's), and as a sanity control **sound** vs **random**
(a random window of a random other render). `analyze` counts how often each method's candidate was
picked: if E2 doesn't beat random, the fingerprint is noise; E2 vs E3 sets the browser's default
"similar" mode. Triplets triangles are faster to answer than pairs and compare embeddings directly.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "composer")]
import embed  # noqa: E402
import library  # noqa: E402

ROOT = library.OUTPUTS / "composer-exp" / "0g"
WIN = 8.0

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>0g triplets</title>
<style>body{background:#09090d;color:#e8e8f0;font:14px/1.5 Inter,system-ui,sans-serif;max-width:760px;margin:30px auto;padding:0 16px}
.t{padding:14px;border:1px solid #2a2a35;border-radius:12px;margin:12px 0;background:#111117}.row{display:flex;gap:10px;align-items:center;margin:6px 0}
.row b{width:24px}audio{flex:1}button{background:#1d1d27;color:#e8e8f0;border:1px solid #333;border-radius:8px;padding:6px 12px;cursor:pointer}
button.on{background:#7464f0;border-color:#9d8fff}.muted{color:#8b8b9c}#prog{position:sticky;top:0;background:#09090d;padding:6px 0}</style></head>
<body><h2>Which is closer to A?</h2><p class="muted">Listen to A, then B and C (8 s each). Pick the one that sounds more related to A (texture, feel, sound), or "can't tell". Saved as you go.</p>
<div id="prog"></div><div id="list"></div><script>
let T=[],L={};
fetch("triplets.json").then(r=>r.json()).then(d=>{T=d;return fetch("labels.json").then(r=>r.ok?r.json():{}).catch(()=>({}))}).then(l=>{L=l||{};draw()});
function save(){fetch("labels",{method:"POST",body:JSON.stringify(L)});prog()}
function prog(){document.getElementById("prog").textContent=Object.keys(L).length+" / "+T.length+" answered"}
function draw(){const h=document.getElementById("list");h.innerHTML="";T.forEach((t,i)=>{const d=document.createElement("div");d.className="t";
d.innerHTML=`<div class="muted">#${i+1}</div>`+["A","B","C"].map(k=>`<div class="row"><b>${k}</b><audio controls preload="none" src="clips/${t[k]}"></audio></div>`).join("")+
`<div class="row">${["B","C","?"].map(v=>`<button data-v="${v}" class="${L[t.id]===v?"on":""}">${v==="?"?"can't tell":v+" is closer"}</button>`).join("")}</div>`;
d.querySelectorAll("button").forEach(b=>b.onclick=()=>{L[t.id]=b.dataset.v;d.querySelectorAll("button").forEach(x=>x.classList.toggle("on",x===b));save()});
d.querySelectorAll("audio").forEach(a=>a.addEventListener("play",()=>document.querySelectorAll("audio").forEach(o=>{if(o!==a)o.pause()})));h.appendChild(d)});prog()}
</script></body></html>"""

SERVE = '''import http.server, json, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
class H(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); d = json.loads(self.rfile.read(n) or b"{}")
        open("labels.json", "w").write(json.dumps(d, indent=1)); self.send_response(200); self.end_headers()
print("http://127.0.0.1:8765"); http.server.ThreadingHTTPServer(("127.0.0.1", 8765), H).serve_forever()
'''


def cut(rid: str, t0: float, out: Path) -> None:
    p = library.resolve_id(rid)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0, t0):.3f}", "-t", str(WIN), "-i", str(p), "-af",
                    "loudnorm=I=-18:TP=-1.5:LRA=11", "-ar", "44100", "-ac", "2", str(out)], check=True)


def build(n: int, seed: int = 0) -> None:
    ix = embed.index()
    rows, Z, _ = ix["sound"]
    if len(rows) < 10:
        raise SystemExit(f"only {len(rows)} sound windows embedded: let the studio's worker finish (GET /sa3/embed/status)")
    prow, PZ, _ = ix["prompt"]
    pid = {r[0]: i for i, r in enumerate(prow)}
    rng = random.Random(seed)
    ids = sorted({r[0] for r in rows})
    by = {i: [k for k, r in enumerate(rows) if r[0] == i] for i in ids}
    (ROOT / "clips").mkdir(parents=True, exist_ok=True)
    trips, tries = [], 0
    while len(trips) < n and tries < n * 20:
        tries += 1
        a = rng.randrange(len(rows))
        ra = rows[a]
        sc = Z @ Z[a]
        best = max((k for k in range(len(rows)) if rows[k][0] != ra[0]), key=lambda k: sc[k])
        kind = "prompt" if (len(trips) % 8) < 5 and ra[0] in pid else "random"
        if kind == "prompt":
            ps = PZ @ PZ[pid[ra[0]]]
            other = [i for i in np.argsort(-ps) if prow[i][0] != ra[0]]
            if not other:
                continue
            rid = prow[other[0]][0]
            frac = ra[1] / max(1e-6, max(rows[k][2] for k in by[ra[0]]))
            ks = by[rid]
            c = ks[min(len(ks) - 1, int(frac * len(ks)))]
        else:
            rid = rng.choice([i for i in ids if i != ra[0]])
            c = rng.choice(by[rid])
        if rows[c][0] == rows[best][0] and abs(rows[c][1] - rows[best][1]) < WIN:
            continue                                   # both methods picked the same moment: no question
        tid = f"t{len(trips):02d}"
        files = {}
        for key, k in (("A", a), ("sound", best), (kind, c)):
            f = f"{tid}_{key}.wav"
            cut(rows[k][0], rows[k][1], ROOT / "clips" / f)
            files[key] = f
        order = ["sound", kind]
        rng.shuffle(order)
        trips.append({"id": tid, "A": files["A"], "B": files[order[0]], "C": files[order[1]], "_B": order[0], "_C": order[1],
                      "_src": {"A": rows[a], "sound": rows[best], kind: rows[c]}, "_kind": kind})
    pub = [{k: v for k, v in t.items() if not k.startswith("_")} for t in trips]
    (ROOT / "triplets.json").write_text(json.dumps(pub, indent=1))
    (ROOT / "blind_key.json").write_text(json.dumps(trips, indent=1))
    (ROOT / "index.html").write_text(PAGE)
    (ROOT / "serve.py").write_text(SERVE)
    print(f"{len(trips)} triplets → {ROOT} ({sum(t['_kind'] == 'prompt' for t in trips)} sound vs prompt, "
          f"{sum(t['_kind'] == 'random' for t in trips)} sound vs random)")


def analyze() -> None:
    key = {t["id"]: t for t in json.loads((ROOT / "blind_key.json").read_text())}
    lab = json.loads((ROOT / "labels.json").read_text()) if (ROOT / "labels.json").is_file() else {}
    tally: dict = {}
    for tid, v in lab.items():
        t = key.get(tid)
        if not t:
            continue
        k = t["_kind"]
        win = "unsure" if v == "?" else t["_B"] if v == "B" else t["_C"]
        tally.setdefault(k, {}).setdefault(win, 0)
        tally[k][win] += 1
    print(json.dumps(tally, indent=1))
    (ROOT / "result.json").write_text(json.dumps(tally, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "analyze"])
    ap.add_argument("--n", type=int, default=40)
    a = ap.parse_args()
    build(a.n) if a.cmd == "build" else analyze()
