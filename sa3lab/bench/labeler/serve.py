#!/usr/bin/env python3
"""Local server for the blind audio A/B page (label.html), adapted from the labeling-ui skill.

    uv run --no-project serve.py          prints the URL to open

Items = every sidecar .json here with an "audio" key whose file exists. Labels auto-save
to labels.json = {"direction": str, "labels": {clip_id: {score, works, note}}}.
blind_key.json (clip_id -> config/decoder) is only handed out by /api/reveal, which the
page calls when the listener presses Reveal. autoscores.json (optional) rides along.
"""
import argparse, errno, json, os, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FOLDER = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8947)
parser.add_argument("--labels", type=Path, default=FOLDER / "labels.json")
ARGS = parser.parse_args()
LOCK = threading.Lock()
EMPTY = {"direction": "", "labels": {}}


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return default


def scan_items():
    items = []
    for p in FOLDER.glob("*.json"):
        d = read_json(p, None)
        if isinstance(d, dict) and d.get("audio") and (FOLDER / d["audio"]).exists():
            d.setdefault("id", p.stem)
            items.append(d)
    return sorted(items, key=lambda d: (d.get("group_order", 0), d.get("order", 0)))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(FOLDER), **k)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self.send_response(302); self.send_header("Location", "/label.html"); self.end_headers()
        elif path == "/api/items":
            self.send_json(scan_items())
        elif path == "/api/labels":
            self.send_json({**EMPTY, **read_json(ARGS.labels, {})})
        elif path == "/api/config":
            self.send_json({"title": FOLDER.parent.name, "chips": [], **read_json(FOLDER / "label_config.json", {}),
                            "folder": str(FOLDER)})
        elif path == "/api/reveal":
            self.send_json({"key": read_json(FOLDER / "blind_key.json", {}),
                            "auto": read_json(FOLDER / "autoscores.json", {})})
        elif path.endswith(".json"):  # keep the key off the plain file route
            self.send_json({"error": "not found"}, 404)
        elif path.endswith(".wav") and (FOLDER / path.lstrip("/")).is_file():
            self.send_audio(FOLDER / path.lstrip("/"))
        else:
            super().do_GET()

    def send_audio(self, f):
        """Byte-range responses: without them Chrome can't seek in <audio> (scrubber, synced A/B)."""
        size = f.stat().st_size
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes="):
            a, _, b = rng[6:].split(",")[0].partition("-")
            if a:
                start, end = int(a), int(b) if b else size - 1
            else:
                start = size - int(b)
            end, status = min(end, size - 1), 206
        self.send_response(status)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(f, "rb") as fh:
            fh.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = fh.read(min(1 << 16, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_POST(self):
        port = self.server.server_address[1]
        if self.headers.get("Origin") not in (None, f"http://127.0.0.1:{port}"):
            return self.send_json({"error": "origin not allowed"}, 403)
        if self.path.split("?")[0] != "/api/labels":
            return self.send_json({"error": "not found"}, 404)
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with LOCK:
            tmp = ARGS.labels.with_name(ARGS.labels.name + ".tmp")
            tmp.write_text(json.dumps(data, indent=2) + "\n")
            os.replace(tmp, ARGS.labels)
        self.send_json({"ok": True})


def bind():
    for port in range(ARGS.port, ARGS.port + 50):
        try:
            return ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError as e:
            if e.errno != errno.EADDRINUSE:
                raise
    raise SystemExit(f"no free port from {ARGS.port}")


if __name__ == "__main__":
    server = bind()
    print(f"http://127.0.0.1:{server.server_address[1]}/", flush=True)
    server.serve_forever()
