"""LoRA adapters, one model for every page: the loras/ library, uploads, and the bridge between the
UI kit's LoRA stack (sa3lab/ui/lora.js) and upstream's three gradio LoRA slots.

The list. A page holds its LoRAs as a JSON list, at most `MAX` long:
    [{"path": "/abs/x.safetensors", "strength": 1.0, "steps": null | [from, to]}, …]
`steps` null = every sampler step; [from, to] is 1-based and inclusive, like upstream's Min / Max
step sliders. The studio keeps it in a CSS-hidden box (#sa3-lora-data, studio.py), the Composer in
project.json `model.loras`.

The slots. Upstream's /generate takes 3 LoRA slots × (file, dropdown pick, strength, min, max)
after its 17 controls (renderq.py, anarchy/enqueue.py and the Composer's submit all build or read
that order). Those slots stay the data carriers: `to_slots` fills them from a list, `from_slots`
reads one back, so the API, the queue modal and saved sessions never changed when the kit took
over the panel. A path under loras/ goes in the dropdown (studio.py sets allow_custom_value, so a
file added after launch, or sent through gradio_client, is accepted); any other path goes in the
file slot.

Routes (studio process, so the Composer page reaches them same-origin):
    GET  /sa3/loras          → {"root", "items": [{"name", "path"}]}  (natural sort, subfolders included)
    POST /sa3/loras/upload   multipart `file` (.safetensors) → {"name", "path"}, saved under
                               loras/uploads/ (gitignored, *.safetensors), so it joins the library
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "stable-audio-3" / "loras"
UPLOADS = LIB / "uploads"
MAX = 3                          # upstream's slot count
NONE = ""                        # upstream's dropdown placeholder value (sa3_gradio._DD_NONE)


def library() -> list[tuple[str, str]]:
    """(name, abspath) of every .safetensors under loras/ (subfolders included, dot-folders skipped),
    name = path relative to loras/ without the extension. Natural sort: digit runs compare as
    numbers, so `step=2000` lands before `step=14000`."""
    if not LIB.is_dir():
        return []

    def natural(p):
        return [(0, int(t), "") if t.isdigit() else (1, 0, t.lower())
                for t in re.split(r"(\d+)", str(p.relative_to(LIB))) if t]
    hits = sorted((p for p in LIB.rglob("*.safetensors")
                   if not any(part.startswith(".") for part in p.relative_to(LIB).parts)), key=natural)
    return [(str(p.relative_to(LIB).with_suffix("")), str(p)) for p in hits]


def clean(items, n_steps: int | None = None) -> list[dict]:
    """A list as a page sent it → the canonical form (bad rows dropped, at most MAX, strength and
    step window clamped; a window covering every step becomes null)."""
    if isinstance(items, str):
        try:
            items = json.loads(items or "[]")
        except ValueError:
            items = []
    out = []
    for x in items if isinstance(items, list) else []:
        if not isinstance(x, dict) or not x.get("path"):
            continue
        st = x.get("steps")
        if isinstance(st, (list, tuple)) and len(st) == 2:
            lo, hi = max(1, int(st[0] or 1)), int(st[1] or 0) or (n_steps or 99)
            if n_steps:
                hi = min(hi, n_steps)
            st = None if lo <= 1 and (n_steps is None or hi >= n_steps) else [lo, max(lo, hi)]
        else:
            st = None
        out.append({"path": str(x["path"]), "strength": round(min(10.0, max(0.0, float(x.get("strength", 1.0)))), 3),
                    "steps": st})
    return out[:MAX]


def to_slots(items, n_steps: int) -> list:
    """A list → upstream's 15 flat slot values (file, pick, strength, min, max) × 3; empty slots
    are (None, NONE, 1.0, 1, n_steps), i.e. no adapter."""
    lib = {p for _, p in library()}
    vals = []
    for i, x in enumerate(clean(items, n_steps) + [None] * MAX):
        if i == MAX:
            break
        if x is None:
            vals += [None, NONE, 1.0, 1, n_steps]
            continue
        lo, hi = x["steps"] or (1, n_steps)
        pick = x["path"] in lib or not os.path.isfile(x["path"])     # missing → the pick, so the render's error names it
        vals += [None if pick else x["path"], x["path"] if pick else NONE, x["strength"], int(lo), int(hi)]
    return vals


def from_slots(vals, n_steps: int, vis=None) -> list[dict]:
    """Upstream's 15 slot values (+ its per-slot visibility, when known) → a list."""
    out = []
    for i in range(MAX):
        f, dd, strength, lo, hi = list(vals[5 * i: 5 * i + 5]) + [None] * max(0, 5 - len(vals[5 * i: 5 * i + 5]))
        path = f or (dd if dd and dd != NONE else None)
        if not path or (vis is not None and i < len(vis) and not vis[i]):
            continue
        out.append({"path": str(path), "strength": float(strength if strength is not None else 1.0),
                    "steps": [int(lo or 1), int(hi or n_steps)]})
    return clean(out, n_steps)


def install_routes() -> None:
    """GET /sa3/loras, POST /sa3/loras/upload (patched into gradio's FastAPI app, like library.py)."""
    from fastapi import UploadFile
    from fastapi.responses import JSONResponse
    from gradio import routes

    def list_get():
        return JSONResponse({"root": str(LIB), "items": [{"name": n, "path": p} for n, p in library()]})

    def upload_post(file: UploadFile):
        name = Path(file.filename or "").name
        if not name.endswith(".safetensors"):
            return JSONResponse({"error": "a LoRA is a .safetensors file"}, status_code=400)
        UPLOADS.mkdir(parents=True, exist_ok=True)
        dst = UPLOADS / name
        with dst.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        return JSONResponse({"name": str(dst.relative_to(LIB).with_suffix("")), "path": str(dst)})

    orig = routes.App.create_app

    def create_app(*a, **kw):
        app = orig(*a, **kw)
        app.add_api_route("/sa3/loras", list_get, methods=["GET"])
        app.add_api_route("/sa3/loras/upload", upload_post, methods=["POST"])
        return app
    routes.App.create_app = staticmethod(create_app)
