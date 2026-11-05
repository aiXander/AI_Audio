"""Auto-restart SA3 Studio when its code changes (what `sa3lab/studio` runs by default).

gradio's own reload mode (`gradio app.py`) needs a module-level `demo`, and
studio.py builds the UI by patching upstream's sa3_gradio before calling its
main(), so instead this supervisor runs studio.py as a child process and:

- polls the mtimes of sa3lab/*.py|css|js and upstream's sa3_gradio.py,
- on a change, waits until no render is running or queued (GET /sa3/progress)
  and the server has stayed idle for IDLE_S, then restarts the child (the model
  pre-warm is the only cost, ~10-20 s). The idle window matters: /sa3/progress
  goes inactive when the model call returns, but the render is then still being
  saved and sent to the page; a server too busy to answer counts as busy too,
- after a crash (e.g. a syntax error mid-edit) waits for the next change
  instead of crash-looping.

The browser side lives in studio.js: it polls GET /sa3/boot and reloads the page
when the server's boot id changes, restoring the prompt you were typing.
Ctrl-C (or `make stop`) stops both. `sa3lab/studio --no-watch` skips all this.
"""
from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
UPSTREAM = HERE.parent / "stable-audio-3/optimized/mlx/scripts/sa3_gradio.py"


def _snapshot() -> dict[str, float]:
    files = [p for ext in ("*.py", "*.css", "*.js") for p in HERE.glob(ext)] + [UPSTREAM]
    out = {}
    for p in files:
        try:
            out[str(p)] = p.stat().st_mtime
        except OSError:
            pass
    return out


IDLE_S = 8.0     # continuous idle needed before a restart (render delivery tail, back-to-back queue)


def _busy(port: str) -> bool:
    """A render running or queued. Only a refused connection (server down) reads as idle:
    a timeout means the server is alive but too loaded to answer, i.e. mid-render."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/sa3/progress", timeout=5) as r:
            d = json.load(r)
            return bool(d.get("active") or d.get("waiting"))
    except urllib.error.URLError as e:
        return not isinstance(e.reason, ConnectionRefusedError)
    except Exception:
        return True


def main() -> None:
    args = sys.argv[1:]
    port = args[args.index("--port") + 1] if "--port" in args else "7860"
    cmd = [sys.executable, str(HERE / "studio.py"), *args]
    child: subprocess.Popen | None = None

    def start():
        nonlocal child
        child = subprocess.Popen(cmd)

    def stop():
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()

    def bye(*_):
        stop()
        sys.exit(0)
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)

    snap = _snapshot()
    start()
    crashed = False
    while True:
        time.sleep(1.0)
        if not crashed and child.poll() is not None:
            crashed = True
            print(f"[devwatch] studio exited ({child.returncode}); restarting on the next code change",
                  flush=True)
        now = _snapshot()
        if now == snap:
            continue
        changed = sorted({Path(k).name for k in set(now) | set(snap) if now.get(k) != snap.get(k)})
        time.sleep(0.6)                     # let an editor finish writing
        snap = _snapshot()
        idle_since, told = None, False
        while child.poll() is None:          # a dead child restarts at once
            if _busy(port):
                idle_since = None
                if not told:
                    print("[devwatch] code changed; waiting for the running render to finish", flush=True)
                    told = True
            elif idle_since is None:
                idle_since = time.time()
            elif time.time() - idle_since >= IDLE_S:
                break
            time.sleep(1)
        print(f"[devwatch] code changed ({', '.join(changed)}); restarting studio", flush=True)
        stop()
        start()
        crashed = False


if __name__ == "__main__":
    main()
