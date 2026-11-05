"""Length-stable seeds: every render's noise is cut from one fixed-size canvas.

Upstream draws the diffusion noise as `mx.random.normal((1, 256, T_lat), key=seed)`, once
for the start point (sa3_gradio.run_generation) and once per step inside the ping-pong
sampler (sa3_pipeline.sample_flow_pingpong re-noises between steps). The shape is part of
the draw, so the same seed at 40 s and 60 s gave uncorrelated noise (r ≈ 0.001): changing
Length was secretly changing the seed.

`install()` swaps `mx.random.normal` for a version that, for a keyed `(B, 256, T)` draw with
T ≤ T_MAX, draws `(B, 256, T_MAX)` and keeps the first T frames. Same seed → same noise
over the shared opening at any length; the duration token and full-sequence attention are
then the only things a Length change alters. Unkeyed or other-shaped draws pass through.

T_MAX is a constant forever (changing it re-rolls every seed): 4608 latent frames covers
Medium's 380 s maximum. The cost is ~1.2 M normals per draw (negligible next to the DiT).

Renders made with this carry `"noise": "crop4608"` in their sidecar's `gen` block; renders
without the key used upstream's per-length noise, so restoring one of those and pressing
Generate gives a cousin of that clip, not the same clip.
"""
from __future__ import annotations

T_MAX = 4608
TAG = f"crop{T_MAX}"


def install(library) -> None:
    import mlx.core as mx

    orig = mx.random.normal
    if getattr(orig, "_sa3_crop", False):
        return

    def normal(shape=(), *a, **kw):
        shp = tuple(shape) if isinstance(shape, (tuple, list)) else None
        if (kw.get("key") is not None and shp and len(shp) == 3 and shp[1] == 256
                and 0 < shp[2] < T_MAX):
            return orig((shp[0], 256, T_MAX), *a, **kw)[:, :, : shp[2]]
        return orig(shape, *a, **kw)
    normal._sa3_crop = True
    mx.random.normal = normal

    orig_rec = library.gen_record

    def gen_record(*a, **kw):
        g = orig_rec(*a, **kw)
        g["noise"] = TAG
        return g
    library.gen_record = gen_record
