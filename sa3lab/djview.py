"""DJ view: a clip full screen, its dadaspec scrolling right to left under a fixed playhead,
the way a Pioneer CDJ / rekordbox shows a track (browser side: djview.js / .css).

The card's own dadaspec is 1920 columns for the whole clip (16 columns/s on a 120 s render),
too coarse once a few seconds fill a screen. So the view shows the card's image at once and
swaps in a sharper one from GET /sa3/djspec?name|id=…&mono=0|1: the same dadaspec (hispec.py)
at ~COLS_PER_S real columns per second, capped at MAX_COLS. Computed on a request thread
(~1 s for 120 s, chunked STFT so memory stays small), cached per file + mtime.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
from fastapi.responses import JSONResponse, Response

import hispec
import library

COLS_PER_S = 120
MAX_COLS = 24000          # 200 s at full density; longer clips get fewer columns per second
SR = 44100


@lru_cache(maxsize=6)
def _render(path: str, mtime: float, mono: bool) -> bytes:
    y = library._up.read_audio_any(path)                       # (2, T) float32 @ 44.1 kHz
    pcm = (np.clip(y.T, -1, 1) * 32767).astype(np.int16)
    width = int(min(MAX_COLS, max(hispec.WIDTH, y.shape[-1] / SR * COLS_PER_S)))
    return hispec.render_jpeg(pcm, SR, width=width, mono=mono)


def install_routes() -> None:
    from gradio import routes

    def djspec_get(name: str = "", id: str = "", mono: int = 0):
        p = library.resolve_id(id) if id else library._audio_path(name)
        if p is None or not Path(p).is_file():
            return JSONResponse({"error": "unknown file"}, status_code=404)
        try:
            b = _render(str(p), p.stat().st_mtime, bool(mono))
        except Exception as e:
            return JSONResponse({"error": f"render failed: {e}"}, status_code=500)
        return Response(b, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})

    orig = routes.App.create_app

    def create_app(*a, **kw):
        app = orig(*a, **kw)
        app.add_api_route("/sa3/djspec", djspec_get, methods=["GET"])
        ours = [r for r in app.router.routes if getattr(r, "path", "").startswith("/sa3/")]
        app.router.routes[:] = ours + [r for r in app.router.routes if r not in ours]
        return app
    routes.App.create_app = staticmethod(create_app)
