"""Studio add-ons' browser assets (anarchy_ui.*, outdir.*, extend.*, uiprefs.*), merged into studio's CSS/JS.

Gradio takes one JS function expression, so studio.js's `() => {…}` and each add-on's run
inside one wrapper, each in its own scope. Each add-on keeps its server side in its .py.
"""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).parent


def assets(css: str, js: str, *names: str) -> tuple[str, str]:
    fns = [js] + [(HERE / f"{n}.js").read_text() for n in names]
    css = "\n".join([css] + [(HERE / f"{n}.css").read_text() for n in names])
    body = "\n".join("(" + f.strip().rstrip(";") + ")();" for f in fns)
    return css, "() => {\n" + body + "\n}"
