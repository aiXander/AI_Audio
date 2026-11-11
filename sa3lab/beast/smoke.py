"""Drive the box's render worker directly: one job in, the float32 WAV out (no studio involved).

    python3 sa3lab/beast/smoke.py [--url http://100.117.34.17:7870] [--prompt …] [--seconds 6] [--seed 4825]
        [--steps 8] [--cfg 1] [--neg …] [--a2a in.wav --noise 0.8] [--inpaint ref.wav --range 2 4]
        [--lora file.safetensors:0.8[:lo-hi]] [--strength '[[2,0],[2,1],[4,1],[4,0]]'] [--cancel-after 3]
        [--code-hash auto|none] [--out outputs/beast-smoke/smoke.wav]

Prints every event, the timings and the path of the WAV (+ the worker's raw archive copy). `--cancel-after N`
posts /cancel once N sampler steps are done (the stream must end with a Stopped error). Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import sys
import time
import urllib.parse
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
AI_AUDIO = HERE.parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://100.117.34.17:7870")
    ap.add_argument("--prompt", default="TrackType: Music, Genre: Dub Techno, BPM: 120, Moods: Hypnotic")
    ap.add_argument("--neg", default="")
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--seed", type=int, default=4825)
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--cfg", type=float, default=1.0)
    ap.add_argument("--a2a")
    ap.add_argument("--noise", type=float, default=1.0, help="a2a init noise level (= sigma_max)")
    ap.add_argument("--inpaint")
    ap.add_argument("--range", type=float, nargs=2)
    ap.add_argument("--strength", help="segments strength keys as JSON (needs --inpaint)")
    ap.add_argument("--lora", action="append", default=[])
    ap.add_argument("--cancel-after", type=int)
    ap.add_argument("--code-hash", default="none", help="'auto' = this tree's hash (the worker 409s on a mismatch)")
    ap.add_argument("--out", default=str(AI_AUDIO / "outputs" / "beast-smoke" / "smoke.wav"))
    a = ap.parse_args()
    u = urllib.parse.urlsplit(a.url)

    def req(method, path, body=None, headers=None, timeout=60):
        c = http.client.HTTPConnection(u.hostname, u.port, timeout=timeout)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r, c

    def blob(path) -> str:
        data = Path(path).read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        ext = Path(path).suffix.lower().lstrip(".") or "bin"
        r, c = req("HEAD", f"/blobs/{sha}?ext={ext}")
        r.read(); c.close()
        if r.status != 200:
            r, c = req("PUT", f"/blobs/{sha}?ext={ext}", body=data, headers={"Content-Length": str(len(data))})
            print("upload", Path(path).name, r.status, r.read().decode()); c.close()
        return f"blob:{sha}.{ext}"

    specs = []
    for s in a.lora:
        path, strength, *rng = s.split(":")
        lo_hi = [int(x) for x in rng[0].split("-")] if rng else None
        specs.append({"path": blob(path), "strength": float(strength), "steps": lo_hi})
    args = {"dit_name": "medium", "decoder_name": "same-l", "prompt": a.prompt, "negative_prompt": a.neg,
            "seconds": a.seconds, "steps": a.steps, "seed": a.seed, "cfg": a.cfg, "apg": 1.0,
            "sigma_max": a.noise if a.a2a else 1.0,
            "a2a_audio_path": blob(a.a2a) if a.a2a else None,
            "inpaint_audio_path": blob(a.inpaint) if a.inpaint else None,
            "inpaint_range_sec": a.range or ([0.0, a.seconds] if a.inpaint and a.strength else None),
            "lora_specs": specs or None}
    seg = {"strength": json.loads(a.strength), "mask": "B", "kept": "free"} if a.strength else None
    ch = None
    if a.code_hash == "auto":
        sys.path.insert(0, str(HERE))
        from codehash import code_hash
        ch = code_hash(AI_AUDIO)
    job = {"job": uuid.uuid4().hex, "code_hash": ch, "args": args, "seg": seg,
           "meta": {"outdir": "beast-smoke", "mac_host": "smoke", "submitted": time.time()}}
    body = json.dumps(job).encode()
    t0 = time.time()
    r, c = req("POST", "/render", body=body, headers={"Content-Type": "application/json",
                                                     "Content-Length": str(len(body))}, timeout=600)
    if r.status != 200:
        print("render refused:", r.status, r.read().decode())
        return 1
    done, cancelled = None, False
    for line in r:
        ev = json.loads(line)
        if ev["ev"] != "tick":
            print(f"{time.time() - t0:6.2f}s", json.dumps(ev)[:300])
        if a.cancel_after is not None and ev["ev"] == "step" and ev["i"] >= a.cancel_after and not cancelled:
            cancelled = True
            rr, cc = req("POST", f"/cancel/{job['job']}")
            print("cancel →", rr.read().decode()); cc.close()
        if ev["ev"] == "done":
            done = ev
        if ev["ev"] == "error":
            return 0 if (a.cancel_after is not None and ev["type"] == "Stopped") else 1
    c.close()
    if not done:
        print("stream ended without done")
        return 1
    rr, cc = req("GET", done["audio"])
    data = rr.read(); cc.close()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"wall {time.time() - t0:.2f}s · {len(data) / 1e6:.1f} MB → {out}  (raw copy: {done.get('raw')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
