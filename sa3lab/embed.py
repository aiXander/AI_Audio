"""Similarity over the starred set (composer spec 3.2): window embeddings, "more like this", a 2-D map.

Two free embeddings (Xander, 2026-11-06: no CLAP / DINOv2 download yet; E1 / E4 stay a later add-on):

- **E2 "sound"**: a SAME-L latent fingerprint. Each starred render is encoded once with the studio's
  own SAME-L encoder (the one inpaint uses), **inside the studio process, on the MLX thread, only
  while the render queue is idle** (never a second model in RAM, never delaying a render: one
  render per idle check, in ≤ 120 s chunks). The latents are cached in `outputs/.embeddings/<audio
  sha256>.npz` (float16, ~0.5 MB per minute) and every window is derived from them: 8 s windows,
  hop 4 s, feature = per-channel mean + std over the window's frames. A highlight or any range
  is the same feature over its frames.
- **E3 "prompt"**: what the render was asked to be (condsrc.py answers what it was conditioned
  on). Anarchy renders: the mean of the stored `(256, 768)` `.cond.npz` bag over tokens. Text
  renders: T5Gemma's output for the prompt, mean over the real tokens (in-process encoder, same
  MLX thread rule). Composer takes (any multi-anchor render): those per-anchor vectors averaged,
  each weighted by its mean weight over the take; embeddings written before this get theirs
  filled in by the idle worker (`_refresh_prompt`). Per render, not per window. Imports have none.

Retrieval z-scores every feature dimension over the whole index (so loud channels don't dominate),
then ranks by cosine. **Moments, not files:** "more like this" for (render, range) returns the
best window per other render (and other, non-overlapping windows of the same render), each a
render id + [t0, t1] that opens pre-selected in the highlight modal. The map is a PCA of each
render's mean feature with numpy k-means clusters (k = min(6, n / 3)); cluster names are numbers
until tags or CLAP exist.

Routes (studio process):

    GET /sa3/embed/status                     → {"starred", "encoded", "pending", "idle", "last_error"}
    GET /sa3/embed/similar?id=&s0=&s1=&mode=sound|prompt&k=12  → {"query", "results": [{id, t0, t1, score}]}
    GET /sa3/embed/map?mode=sound|prompt      → {"points": [{id, x, y, cluster}], "k"}

Experiment 0g (blind triplets, `sa3lab/bench/embed_triplets.py`) decides which mode is the default.
Trap: an encode while Xander clicks Generate would delay his render by the encode time (~1-3 s per
minute of audio); the worker re-checks idleness right before each chunk.
"""
from __future__ import annotations

import math
import threading
import time
from pathlib import Path

import numpy as np

import library

EMB = library.OUTPUTS / ".embeddings"
WIN, HOP = 8.0, 4.0
SR, SPL = 44100, 4096
FPS = SR / SPL
CHUNK_S = 120.0
V = 1

_up = None
_state = {"last_error": None, "busy": None}
_idx: dict = {}


def _sha(p: Path) -> str:
    import sys
    sys.path.insert(0, str(Path(__file__).parent / "composer"))
    import compose
    return compose.sha256(p)


def starred() -> list[Path]:
    d = library.STAR_DIR
    return sorted({ln.resolve() for ln in d.iterdir() if ln.suffix.lower() in library.AUDIO_EXT}) if d.is_dir() else []


def _file(p: Path) -> Path:
    return EMB / f"{_sha(p)}.npz"


# ── encoding (studio process, MLX thread, idle queue only) ──────────────────────────────────
def _idle() -> bool:
    return not library.progress().get("active") and library.jobs_waiting() == 0


def _encode_lat(path: Path) -> np.ndarray:
    """(C, T) float32 SAME-L latents of a whole file, encoded in ≤ CHUNK_S pieces."""
    up = _up
    mx = up.mx
    y = up.read_audio_any(str(path))
    enc, pad_mod = up.get_encoder("same-l")
    out = []
    step = int(CHUNK_S * FPS) * SPL
    for i0 in range(0, y.shape[-1], step):
        if not _idle():
            raise InterruptedError("queue busy")
        seg = y[:, i0:i0 + step]
        T = max(1, math.ceil(seg.shape[-1] / SPL))
        eT = T
        if (T * 16) % pad_mod:
            eT = math.ceil((T * 16) / pad_mod) * pad_mod // 16
        seg = np.pad(seg, ((0, 0), (0, eT * SPL - seg.shape[-1])))
        lat = up.mlx_call(lambda: _enc(enc, up.patch_audio(seg[None, ...], patch_size=256), T))
        out.append(lat)
    return np.concatenate(out, axis=-1)


def _enc(enc, patches, T):
    mx = _up.mx
    lat = enc(mx.array(patches))[..., :T]
    mx.eval(lat)
    return np.array(lat.astype(mx.float32))[0]


def _text_vec(pr: str) -> np.ndarray:
    """T5Gemma's output for a text prompt, mean over the real tokens (MLX thread)."""
    up = _up

    def run():
        e, m = up.get_t5().encode([str(pr)], max_len=256)
        e = np.array(e.astype(up.mx.float32))[0]
        m = np.array(m).astype(bool).reshape(-1)[:e.shape[0]]
        return e[m].mean(0) if m.any() else e.mean(0)
    return up.mlx_call(run)


def _prompt_vec(p: Path) -> np.ndarray | None:
    """E3 (condsrc.py decides what the render was conditioned on): an anarchy bag's token mean, a
    text prompt's T5Gemma mean, or for a multi-anchor render (a composer take) the mean of its
    anchors' vectors weighted by each anchor's mean weight over the render. `anarchy:` prompt
    anchors that the take's bank does not hold are left out (the weights renormalise)."""
    import condsrc
    s = condsrc.of_render(p)
    if s["kind"] == "bag":
        return condsrc.tokens(s["anchors"][0]).astype(np.float32).mean(0)
    if s["kind"] == "prompt":
        pr = s["anchors"][0]["prompt"]
        return None if str(pr).startswith("anarchy:") else _text_vec(pr)
    if s["kind"] != "multi":
        return None
    anchors, w = condsrc.mean_weights(p)
    acc, tot = None, 0.0
    for a, wi in zip(anchors, w):
        if wi <= 1e-4:
            continue
        a = condsrc.expand(a)
        try:
            v = _text_vec(a["prompt"]) if condsrc.plain_prompt(a) else condsrc.tokens(a).astype(np.float32).mean(0)
        except (ValueError, KeyError):                   # an unstored anarchy spec / a source that is gone
            continue
        acc = v * wi if acc is None else acc + v * wi
        tot += wi
    return acc / tot if acc is not None else None


def encode_one(p: Path) -> None:
    f = _file(p)
    lat = _encode_lat(p)
    pv = _prompt_vec(p)
    EMB.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.stem + ".tmp.npz")
    np.savez(tmp, v=np.array(V), lat=lat.astype(np.float16), prompt=(pv if pv is not None else np.zeros(0, np.float32)),
             id=np.array(library.render_id(p) or ""))
    tmp.replace(f)


def _worker() -> None:
    while True:
        time.sleep(5)
        try:
            if not _idle():
                continue
            todo = [p for p in starred() if not _file(p).is_file()]
            if not todo:
                _refresh_prompt()
                continue
            p = todo[0]
            _state["busy"] = library.render_id(p)
            encode_one(p)
            _idx.clear()
        except InterruptedError:
            pass
        except Exception as e:  # noqa: BLE001
            _state["last_error"] = f"{type(e).__name__}: {e}"[:300]
            time.sleep(30)
        finally:
            _state["busy"] = None


_pv_seen: set = set()


def _refresh_prompt() -> None:
    """Embeddings written before composer takes had an E3 (empty prompt vector): fill one in,
    at most one per idle tick, checking each file once per process."""
    import condsrc
    for p in starred():
        f = _file(p)
        if str(f) in _pv_seen or not f.is_file():
            continue
        _pv_seen.add(str(f))
        z = dict(np.load(f))
        if z["prompt"].size or condsrc.summary(p)["kind"] != "multi":
            continue
        pv = _prompt_vec(p)
        if pv is not None:
            tmp = f.with_name(f.stem + ".tmp.npz")
            np.savez(tmp, **{**z, "prompt": pv.astype(np.float32)})
            tmp.replace(f)
            _idx.clear()
        return


def install(up) -> None:
    global _up
    _up = up
    threading.Thread(target=_worker, name="sa3-embed", daemon=True).start()


# ── features + index ────────────────────────────────────────────────────────────────────────
def feat(lat: np.ndarray, f0: int, f1: int) -> np.ndarray:
    x = lat[:, max(0, f0):max(f0 + 1, f1)].astype(np.float32)
    return np.concatenate([x.mean(1), x.std(1)])


def index() -> dict:
    """{"sound": (rows, X), "prompt": (rows, X)}; rows = (id, t0, t1); rebuilt when files change."""
    files = sorted(EMB.glob("*.npz")) if EMB.is_dir() else []
    have = {p: _file(p) for p in starred()}
    sig = tuple(sorted((str(f), f.stat().st_mtime) for f in have.values() if f.is_file()))
    if _idx.get("sig") == sig:
        return _idx
    rows_s, X_s, rows_p, X_p = [], [], [], []
    for p, f in have.items():
        if not f.is_file():
            continue
        z = np.load(f)
        rid = library.render_id(p)
        lat = z["lat"].astype(np.float32)
        T = lat.shape[1]
        w, h = int(WIN * FPS), int(HOP * FPS)
        for f0 in range(0, max(1, T - w + 1), h):
            rows_s.append((rid, round(f0 / FPS, 2), round(min(T, f0 + w) / FPS, 2)))
            X_s.append(feat(lat, f0, f0 + w))
        if z["prompt"].size:
            rows_p.append((rid, 0.0, round(T / FPS, 2)))
            X_p.append(z["prompt"])
    _idx.clear()
    _idx.update(sig=sig, files=len(files), sound=_norm(rows_s, X_s), prompt=_norm(rows_p, X_p))
    return _idx


def _norm(rows, X):
    if not X:
        return rows, np.zeros((0, 1)), None
    X = np.stack(X).astype(np.float64)
    mu, sd = X.mean(0), X.std(0) + 1e-6
    Z = (X - mu) / sd
    Z /= np.linalg.norm(Z, axis=1, keepdims=True) + 1e-9
    return rows, Z, (mu, sd)


def query_vec(rid: str, s0: float | None, s1: float | None, mode: str):
    ix = index()
    rows, Z, st = ix[mode]
    if st is None:
        return None
    p = library.resolve_id(rid)
    if p is None or not _file(p).is_file():
        return None
    z = np.load(_file(p))
    if mode == "prompt":
        if not z["prompt"].size:
            return None
        v = z["prompt"].astype(np.float64)
    else:
        lat = z["lat"].astype(np.float32)
        a = 0 if s0 is None else int(s0 * FPS)
        b = lat.shape[1] if s1 is None else int(math.ceil(s1 * FPS))
        v = feat(lat, a, b).astype(np.float64)
    v = (v - st[0]) / st[1]
    return v / (np.linalg.norm(v) + 1e-9)


def similar(rid: str, s0=None, s1=None, mode: str = "sound", k: int = 12) -> dict:
    rows, Z, _ = index()[mode]
    q = query_vec(rid, s0, s1, mode)
    if q is None or not len(rows):
        return {"query": {"id": rid, "s0": s0, "s1": s1}, "results": [],
                "note": "not embedded yet (the worker encodes starred renders while the queue is idle)"}
    sc = Z @ q
    best: dict = {}
    for i in np.argsort(-sc):
        r = rows[i]
        if r[0] == rid and (s0 is None or (r[1] < (s1 or 1e9) and r[2] > (s0 or 0))):
            continue                                   # the query itself (or a window overlapping it)
        key = r[0] if r[0] != rid else rid + "#self"     # best window per other render, + one elsewhere in the query's own
        if key in best:
            continue
        best[key] = {"id": r[0], "t0": r[1], "t1": r[2], "score": round(float(sc[i]), 4)}
        if len(best) >= k:
            break
    return {"query": {"id": rid, "s0": s0, "s1": s1, "mode": mode}, "results": list(best.values())}


def kmeans(X: np.ndarray, k: int, iters: int = 50, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    C = X[rng.choice(len(X), k, replace=False)]
    for _ in range(iters):
        lab = np.argmin(((X[:, None, :] - C[None]) ** 2).sum(-1), 1)
        C2 = np.stack([X[lab == j].mean(0) if (lab == j).any() else C[j] for j in range(k)])
        if np.allclose(C2, C):
            break
        C = C2
    return lab


def map2d(mode: str = "sound") -> dict:
    rows, Z, _ = index()[mode]
    if not len(rows):
        return {"points": [], "k": 0}
    ids = sorted({r[0] for r in rows})
    V = np.stack([Z[[i for i, r in enumerate(rows) if r[0] == rid]].mean(0) for rid in ids])
    Vc = V - V.mean(0)
    if len(ids) >= 3:
        U, S, Wt = np.linalg.svd(Vc, full_matrices=False)
        XY = Vc @ Wt[:2].T
    else:
        XY = np.zeros((len(ids), 2))
    k = max(1, min(6, len(ids) // 3))
    lab = kmeans(V, k) if k > 1 else np.zeros(len(ids), int)
    span = np.abs(XY).max() or 1.0
    return {"k": k, "points": [{"id": rid, "x": round(float(x / span), 4), "y": round(float(y / span), 4),
                                "cluster": int(c)} for rid, (x, y), c in zip(ids, XY, lab)]}


def status() -> dict:
    s = starred()
    enc = sum(1 for p in s if _file(p).is_file())
    return {"starred": len(s), "encoded": enc, "pending": len(s) - enc, "idle": _idle(),
            "busy": _state["busy"], "last_error": _state["last_error"]}


def install_routes() -> None:
    from fastapi.responses import JSONResponse
    from gradio import routes

    def status_get():
        return JSONResponse(status())

    def similar_get(id: str, s0: float | None = None, s1: float | None = None, mode: str = "sound", k: int = 12):
        if mode not in ("sound", "prompt"):
            return JSONResponse({"error": "mode is sound | prompt"}, status_code=400)
        return JSONResponse(similar(id, s0, s1, mode, max(1, min(50, k))))

    def map_get(mode: str = "sound"):
        if mode not in ("sound", "prompt"):
            return JSONResponse({"error": "mode is sound | prompt"}, status_code=400)
        return JSONResponse(map2d(mode))

    orig = routes.App.create_app

    def create_app(*a, **kw):
        app = orig(*a, **kw)
        app.add_api_route("/sa3/embed/status", status_get, methods=["GET"])
        app.add_api_route("/sa3/embed/similar", similar_get, methods=["GET"])
        app.add_api_route("/sa3/embed/map", map_get, methods=["GET"])
        ours = [r for r in app.router.routes if getattr(r, "path", "").startswith("/sa3/")]
        app.router.routes[:] = ours + [r for r in app.router.routes if r not in ours]
        return app
    routes.App.create_app = staticmethod(create_app)
