#!/usr/bin/env python3
"""Serve an experiment folder's A/B page (abpage.py) and auto-save its notes to `ab_labels.json`.

    python3 sa3lab/bench/abserve.py outputs/<exp> [--port 8960]      # prints the URL to open
    nohup python3 sa3lab/bench/abserve.py outputs/<exp> > outputs/<exp>/abserve.log 2>&1 & disown

Stdlib only (any python3). Binds 127.0.0.1, the first free port from --port. `/` redirects to
compare.html. ab_labels.json = {"direction": str, "labels": {<clip src as the page names it>:
{"score": 1-5 | null, "note": str, "card": str, "label": str}}}; only labelled clips are kept.
Trap (as in the labeling-ui skill): the open page owns ab_labels.json and rewrites it whole on the next
edit, so a hand edit is lost while a tab is open; close it or reload right after.
"""
import argparse
import errno
import json
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("folder")
ap.add_argument("--port", type=int, default=8960)
ap.add_argument("--page", default="compare.html")
A = ap.parse_args()
FOLDER = Path(A.folder).resolve()
LABELS = FOLDER / "ab_labels.json"
LOCK = threading.Lock()


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(FOLDER), **k)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def _json(self, data, status=200):
        b = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/":
            self.send_response(302)
            self.send_header("Location", "/" + A.page)
            self.end_headers()
        elif p == "/api/labels":
            try:
                self._json({"direction": "", "labels": {}, **json.loads(LABELS.read_text())})
            except FileNotFoundError:
                self._json({"direction": "", "labels": {}})
        elif self.headers.get("Range"):
            self._range(p)
        else:
            super().do_GET()

    def _range(self, p):
        """Byte ranges: without them Chrome can't seek an <audio> (currentTime snaps back to 0)."""
        f = Path(self.translate_path(p))
        if not f.is_file():
            return super().do_GET()
        size = f.stat().st_size
        a, _, b = self.headers["Range"].removeprefix("bytes=").partition("-")
        start = int(a) if a else max(0, size - int(b))
        end = min(int(b), size - 1) if a and b else size - 1
        if start >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            return self.end_headers()
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(str(f)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with f.open("rb") as fh:
            fh.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = fh.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_POST(self):
        port = self.server.server_address[1]
        if self.headers.get("Origin") not in (None, f"http://127.0.0.1:{port}"):
            return self._json({"error": "origin not allowed"}, 403)
        if self.path.split("?")[0] != "/api/labels":
            return self._json({"error": "not found"}, 404)
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with LOCK:
            tmp = LABELS.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            os.replace(tmp, LABELS)
        self._json({"ok": True})


def bind():
    for port in range(A.port, A.port + 50):
        try:
            return ThreadingHTTPServer(("127.0.0.1", port), H)
        except OSError as e:
            if e.errno != errno.EADDRINUSE:
                raise
    raise SystemExit(f"no free port from {A.port}")


if __name__ == "__main__":
    s = bind()
    print(f"http://127.0.0.1:{s.server_address[1]}/", flush=True)
    s.serve_forever()
