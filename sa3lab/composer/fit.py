"""Fit: mismatch detection and recommended fixes for a composer project (spec 4.2, 4.3).

    report(proj)            → {"pieces": [...], "seams": [...], "overlaps": [...], "issues": [...], "target": {...}}
    recommend(proj, rep)    → {index in project.pieces: fix dict}  (what "Fit all" writes into each piece's `fix`)

Rows are per *active* piece in time order: `i` (that order, used by seams / overlaps) and `pi`
(the index in project.json's pieces / sequence).

Every active piece is measured once per *pre-fix* audio identity (aligned + cut, no fix chain:
keyfind.analyze → tempo, beat phase, key, LUFS, 3 bands, width), cached in the project folder's
`fit.json` by that key. Badges then use the values *predicted after the piece's current fix*
(transpose shifts the key, gain the LUFS, EQ the bands, width scales the side/mid ratio), so
applying a fix turns its badge green without a re-measure; the A/B toggle (server.py,
`/sa3/composer/ab`) lets the ears check the prediction.

**Targets** (the project, spec 4.1): BPM = the layout's; key = `key` in project.json, else the
longest piece with a confident key; loudness / bands / width = `lufs` in project.json, else the
longest active piece (the bed). The longest piece is the reference and never gets a recommended fix.

| Dimension | ok / warn / bad (spec 4.2) | grey when |
|---|---|---|
| tempo | < 2 % / < 8 % / more (octave-folded, measured on the aligned audio; a range too short to measure uses layout's 8 s in-context reading × the stretch) | no confident beat |
| grid | first beat < 20 / < 60 ms off the project grid (the snap's own phase, `compose.first_beat`; re-measuring the cut piece flipped busy breakbeats by half a beat) | no beat or no project BPM |
| key | compatible (same, relative, ±1 fifth) / ±1-2 st / further | no confident key |
| loudness | < 2 / < 5 dB / more | silent |
| bands | max per-band delta < 3 / < 6 dB / more | — |
| width | < 0.15 / < 0.3 / more | — |
| lora | same LoRA set as the project / differs (warn: soft regions lose that sound) | — |

Seams (time-adjacent pieces) check loudness, key and tempo between the two pieces; overlaps (> 0.5 s
of two layers sounding together) check tempo (< 1 %), beat phase (< 10 ms), key, **band masking**
(both layers within 6 dB of each other in a band that is within 6 dB of each one's loudest band:
cut it on the quieter layer's mix strip) and **headroom** (the master meter's peak above the
limiter's ceiling).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [p for p in (str(HERE), str(HERE.parent)) if p not in sys.path]

import compose  # noqa: E402
import keyfind  # noqa: E402

ANALYSIS_V = 1
BANDS = ("low", "mid", "high")


def _state(v, ok, warn):
    if v is None:
        return "na"
    return "ok" if v < ok else "warn" if v < warn else "bad"


def _measure(proj: dict) -> tuple[dict, dict]:
    """(layout without fixes, {pre-fix audio key: analysis}) with fit.json as the cache."""
    d = Path(proj["_dir"])
    cache_f = d / "fit.json"
    try:
        cache = json.loads(cache_f.read_text())
    except Exception:
        cache = {}
    bare = {**proj}
    for k in ("pieces", "sequence"):
        if bare.get(k):
            bare[k] = [{**it, "fix": None, "_pi": n} if "id" in it else it for n, it in enumerate(bare[k])]
    lay = compose.layout(bare, d / "cache" / "prefix")
    out, dirty = {}, False
    for pc in lay["pieces"]:
        k = pc["audio_key"]
        a = cache.get(k)
        if not a or a.get("v") != ANALYSIS_V:
            a = {"v": ANALYSIS_V, **keyfind.analyze(pc["audio"])}
            cache[k] = a
            dirty = True
        out[k] = a
    if dirty:
        keep = {pc["audio_key"] for pc in lay["pieces"]}
        cache = {k: v for k, v in cache.items() if k in keep} | {k: cache[k] for k in keep}
        cache_f.write_text(json.dumps(cache))
    compose._prune(d / "cache" / "prefix", {pc["audio_key"] for pc in lay["pieces"]})
    return lay, out


def _after(a: dict, fix: dict | None) -> dict:
    """Analysis values predicted after the fix chain."""
    f = fix or {}
    b = dict(a)
    st = float(f.get("transpose") or 0)
    if a.get("key") and abs(st) >= 0.5:
        i = keyfind.key_index(a["key"])
        b["key"] = keyfind.key_name((i % 12 + int(round(st))) % 12 + (12 if i >= 12 else 0))
    eq = list(f.get("eq") or [0, 0, 0]) + [0, 0, 0]
    if a.get("bands"):
        lin = [10 ** ((x + g) / 10) for x, g in zip(a["bands"], eq[:3])]
        tot = sum(lin) or 1
        b["bands"] = [round(10 * np.log10(v / tot + 1e-12), 2) for v in lin]
        boost = 10 * np.log10(tot)                     # the EQ also moves the overall level
    else:
        boost = 0.0
    if a.get("lufs") is not None:
        b["lufs"] = round(a["lufs"] + float(f.get("gain") or 0) + boost, 2)
    if a.get("width") is not None and f.get("width") is not None:
        b["width"] = round(a["width"] * float(f["width"]), 3)
    return b


def _loras(rid: str) -> list:
    try:
        p = compose.piece_audio_path(rid)
        return sorted(x.get("name") or x.get("path") or "" for x in (compose._sidecar(p).get("gen") or {}).get("loras") or [])
    except SystemExit:
        return []


def _tempo(pc: dict, a: dict) -> tuple[float | None, bool]:
    """(tempo of the aligned piece, measured-in-context?): the analysis of the aligned audio when it
    found a confident beat, else (ranges under tempo.py's minimum) layout's 8 s in-context reading
    of the source times the stretch."""
    if a.get("bpm") and a.get("bpm_conf", 1.0) >= compose.BEAT_CONF:
        return float(a["bpm"]), False
    return (compose.aligned_bpm(pc), True) if pc["beat"] else (None, False)


def report(proj: dict) -> dict:
    lay, an = _measure(proj)
    pcs = lay["pieces"]
    bpm = lay["bpm"]
    P = 60.0 / bpm if bpm else None
    real = proj.get("sequence") or proj.get("pieces") or []
    items = [real[pc["_item"]["_pi"]] for pc in pcs]
    post = [_after(an[pc["audio_key"]], it.get("fix")) for pc, it in zip(pcs, items)]
    pre = [an[pc["audio_key"]] for pc in pcs]
    ref_i = max(range(len(pcs)), key=lambda i: pcs[i]["dur"]) if pcs else None
    key_t = proj.get("key")
    if not key_t:
        conf = [i for i in sorted(range(len(pcs)), key=lambda i: -pcs[i]["dur"]) if post[i].get("key")]
        key_t = post[conf[0]]["key"] if conf else None
    tgt = {"bpm": bpm, "key": key_t, "ref": ref_i,
           "lufs": proj.get("lufs") if proj.get("lufs") is not None else (post[ref_i]["lufs"] if ref_i is not None else None),
           "bands": post[ref_i]["bands"] if ref_i is not None else None,
           "width": post[ref_i]["width"] if ref_i is not None else None,
           "loras": sorted(x.get("name") or x.get("path") or "" for x in (proj.get("model") or {}).get("loras") or [])}
    rows, issues = [], []
    for i, (pc, a, b) in enumerate(zip(pcs, pre, post)):
        dims = {}
        tb, ctx = _tempo(pc, a)
        if pc["beat"] and bpm and tb:
            ft = compose.fold(tb, bpm)
            r = abs(ft / bpm - 1)
            dims["tempo"] = {"state": _state(r, 0.02, 0.08), "value": tb,
                             "msg": f"{ft:.1f} BPM vs {bpm:.1f} ({r * 100:.1f} %)" + (" (range too short: measured around it)" if ctx else "")}
        else:
            dims["tempo"] = {"state": "na", "msg": "no beat" if not pc["beat"] else "no project BPM" if not bpm
                             else "tempo not measurable in this range (no clear beat)"}
        fb = compose.first_beat(pc, P) if pc["beat"] else None
        if fb is not None:
            off = ((pc["at"] + fb - compose.PRE_ROLL) % P)
            off = min(off, P - off) * 1000
            dims["grid"] = {"state": _state(off, 20, 60), "value": round(off, 1), "msg": f"first beat {off:.0f} ms off the grid"}
        else:
            dims["grid"] = {"state": "na", "msg": "no beat" if not pc["beat"] else "no project BPM" if not P
                            else "no measurable beat phase in this range"}
        if b.get("key") and key_t:
            c = keyfind.compatible(b["key"], key_t)
            dist = keyfind.key_distance(b["key"], key_t)
            st = "ok" if c else ("warn" if abs(dist) <= 2 else "bad")
            dims["key"] = {"state": st, "value": b["key"], "msg": f"{b['key']} vs {key_t}" + (f" ({c})" if c else f" ({dist:+d} st)")}
        else:
            dims["key"] = {"state": "na", "msg": f"no key (best guess {a.get('key_guess')}, conf {a.get('key_conf')})"}
        if b.get("lufs") is not None and tgt["lufs"] is not None:
            dl = b["lufs"] - tgt["lufs"]
            dims["loudness"] = {"state": _state(abs(dl), 2, 5), "value": b["lufs"], "msg": f"{b['lufs']:.1f} LUFS ({dl:+.1f} dB vs {tgt['lufs']:.1f})"}
        else:
            dims["loudness"] = {"state": "na", "msg": "silent"}
        if b.get("bands") and tgt["bands"]:
            db = [x - y for x, y in zip(b["bands"], tgt["bands"])]
            m = max(abs(x) for x in db)
            dims["bands"] = {"state": _state(m, 3, 6), "value": db,
                             "msg": "bands vs the bed: " + " · ".join(f"{n} {x:+.1f}" for n, x in zip(BANDS, db))}
        if b.get("width") is not None and tgt["width"] is not None:
            dw = b["width"] - tgt["width"]
            dims["width"] = {"state": _state(abs(dw), 0.15, 0.3), "value": b["width"], "msg": f"width {b['width']:.2f} vs {tgt['width']:.2f}"}
        lo = _loras(pc["id"])
        dims["lora"] = ({"state": "ok", "msg": "same LoRAs as the project"} if lo == tgt["loras"]
                        else {"state": "warn", "msg": f"rendered with {', '.join(lo) or 'no LoRA'}, project uses "
                              f"{', '.join(tgt['loras']) or 'none'}: soft regions lose that sound"})
        if i == ref_i:
            for k in ("loudness", "bands", "width"):
                if k in dims:
                    dims[k] = {**dims[k], "state": "ok", "msg": dims[k]["msg"] + " (the reference)"}
        rows.append({"i": i, "pi": pc["_item"]["_pi"], "id": pc["id"], "at": pc["at"], "dur": pc["dur"], "track": pc["track"], "ref": i == ref_i,
                     "dims": dims, "analysis": a, "after": b, "fix": items[i].get("fix") or {}})
        for k, v in dims.items():
            if v["state"] in ("warn", "bad"):
                issues.append({"kind": "piece", "i": i, "dim": k, "state": v["state"], "msg": v["msg"]})
    order = sorted(range(len(pcs)), key=lambda i: pcs[i]["at"])
    seams, overlaps = [], []
    for x, y in zip(order, order[1:]):
        A, B = pcs[x], pcs[y]
        if B["at"] < A["at"] + A["dur"] - 0.5:
            continue
        s = {"a": x, "b": y, "t": round(A["at"] + A["dur"], 3), "dims": {}}
        if post[x].get("lufs") is not None and post[y].get("lufs") is not None:
            dl = post[y]["lufs"] - post[x]["lufs"]
            s["dims"]["loudness"] = {"state": _state(abs(dl), 2, 5), "msg": f"{dl:+.1f} dB across the seam"}
        c = keyfind.compatible(post[x].get("key"), post[y].get("key"))
        if post[x].get("key") and post[y].get("key"):
            s["dims"]["key"] = {"state": "ok" if c else "warn", "msg": f"{post[x]['key']} → {post[y]['key']}"}
        seams.append(s)
        for k, v in s["dims"].items():
            if v["state"] in ("warn", "bad"):
                issues.append({"kind": "seam", "a": x, "b": y, "dim": k, "state": v["state"], "msg": v["msg"]})
    for x in range(len(pcs)):
        for y in range(x + 1, len(pcs)):
            A, B = pcs[x], pcs[y]
            ov = min(A["at"] + A["dur"], B["at"] + B["dur"]) - max(A["at"], B["at"])
            if ov <= 0.5:
                continue
            o = {"a": x, "b": y, "t0": round(max(A["at"], B["at"]), 3), "t1": round(min(A["at"] + A["dur"], B["at"] + B["dur"]), 3), "dims": {}}
            pa, pb = pre[x], pre[y]
            ta, tb = _tempo(A, pa)[0], _tempo(B, pb)[0]
            if A["beat"] and B["beat"] and ta and tb:
                r = abs(compose.fold(ta, tb) / tb - 1)
                o["dims"]["tempo"] = {"state": _state(r, 0.01, 0.04), "msg": f"{compose.fold(ta, bpm or tb):.1f} vs {compose.fold(tb, bpm or tb):.1f} BPM"}
                fa, fb = compose.first_beat(A, P), compose.first_beat(B, P)
                if fa is not None and fb is not None:
                    d = ((A["at"] + fa) - (B["at"] + fb)) % P
                    d = min(d, P - d) * 1000
                    o["dims"]["phase"] = {"state": _state(d, 10, 30), "msg": f"beats {d:.0f} ms apart"}
            if post[x].get("key") and post[y].get("key"):
                c = keyfind.compatible(post[x]["key"], post[y]["key"])
                o["dims"]["key"] = {"state": "ok" if c else "bad", "msg": f"{post[x]['key']} over {post[y]['key']}"}
            ga, gb = _level(items[x]), _level(items[y])
            if post[x].get("bands") and post[y].get("bands") and post[x].get("lufs") is not None and post[y].get("lufs") is not None:
                la = [post[x]["lufs"] + ga + v for v in post[x]["bands"]]
                lb = [post[y]["lufs"] + gb + v for v in post[y]["bands"]]
                hit = [n for n in range(3) if abs(la[n] - lb[n]) < 6 and post[x]["bands"][n] > -6 and post[y]["bands"][n] > -6]
                if hit:
                    q = x if (post[x]["lufs"] + ga) < (post[y]["lufs"] + gb) else y
                    o["dims"]["masking"] = {"state": "warn", "bands": [BANDS[n] for n in hit], "quieter": q,
                                            "msg": f"both loud in the {', '.join(BANDS[n] for n in hit)}: cut it on piece {q + 1}'s mix strip"}
                else:
                    o["dims"]["masking"] = {"state": "ok", "msg": "no shared loud band"}
            overlaps.append(o)
            for k, v in o["dims"].items():
                if v["state"] in ("warn", "bad"):
                    issues.append({"kind": "overlap", "a": x, "b": y, "dim": k, "state": v["state"], "msg": v["msg"]})
    try:
        meter = json.loads((Path(proj["_dir"]) / "project.resolved.json").read_text())["resolved"]["meter"]
    except Exception:
        meter = {}
    ceil = float((proj.get("master") or {}).get("ceiling", -1.0))
    head = {"state": "na", "msg": "not built"}
    if meter.get("peak") is not None:
        head = {"state": "warn" if meter["peak"] >= ceil - 0.05 and overlaps else "ok",
                "msg": f"peak {meter['peak']:.1f} dBFS, {meter.get('lufs')} LUFS" + (" (limiter engaged)" if meter["peak"] >= ceil - 0.05 else "")}
        if head["state"] == "warn":
            issues.append({"kind": "master", "dim": "headroom", "state": "warn", "msg": head["msg"] + ": lower layer gains"})
    return {"target": tgt, "pieces": rows, "seams": seams, "overlaps": overlaps, "headroom": head, "issues": issues}


def _level(it: dict) -> float:
    return float((it.get("mix") or {}).get("gain") or 0)


def recommend(proj: dict, rep: dict | None = None) -> dict:
    """{piece index: fix} in recommendation order (spec 4.3): transpose to the smallest compatible
    move, gain match to the target LUFS, 3-band EQ match (70 % of the delta, ±6 dB, only past 3 dB),
    width match (past 0.15). The reference piece is left alone."""
    rep = rep or report(proj)
    t = rep["target"]
    out = {}
    for r in rep["pieces"]:
        if r["ref"]:
            continue
        a = r["analysis"]
        fix = {}
        if a.get("key") and t["key"] and not keyfind.compatible(a["key"], t["key"]):
            d = keyfind.key_distance(a["key"], t["key"])
            if d:
                fix["transpose"] = d
        if a.get("bands") and t["bands"]:
            eq = [float(np.clip(0.7 * (y - x), -6, 6)) if abs(y - x) > 3 else 0.0 for x, y in zip(a["bands"], t["bands"])]
            if any(eq):
                fix["eq"] = [round(v, 1) for v in eq]
        if a.get("width") and t["width"] and abs(a["width"] - t["width"]) > 0.15:
            fix["width"] = round(float(np.clip(t["width"] / max(a["width"], 1e-3), 0.3, 3.0)), 3)
        if a.get("lufs") is not None and t["lufs"] is not None:
            pred = _after(a, fix)
            g = t["lufs"] - pred["lufs"]
            if abs(g) > 0.5:
                fix["gain"] = round(g, 1)
        out[r["pi"]] = fix
    return out
