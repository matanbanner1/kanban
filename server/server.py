#!/usr/bin/env python3
"""Tiny JSONBin-compatible JSON blob store for the kanban board.

Stores one JSON blob per bin id as <DATA_DIR>/<id>.json. Standard library only.
Configuration comes from environment variables (see the globals below).
"""
import hmac
import json
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ── Configuration (overridable via env; tests override the module globals) ──
DATA_DIR = os.environ.get("KANBAN_DATA_DIR", "/var/lib/kanban-store")
MASTER_KEY = os.environ.get("KANBAN_MASTER_KEY", "")
ALLOWED_ORIGIN = os.environ.get("KANBAN_ALLOWED_ORIGIN", "https://matanbanner1.github.io")
HOST = os.environ.get("KANBAN_HOST", "127.0.0.1")
PORT = int(os.environ.get("KANBAN_PORT", "18790"))
MAX_BODY = 1_000_000  # 1 MB

BIN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_write_lock = threading.Lock()


# ── Blob storage ────────────────────────────────────────────────────────────
def bin_path(bin_id):
    return os.path.join(DATA_DIR, bin_id + ".json")


def read_blob(bin_id):
    with open(bin_path(bin_id), "r", encoding="utf-8") as f:
        return json.load(f)


def write_blob(bin_id, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = bin_path(bin_id) + ".tmp"
    with _write_lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, bin_path(bin_id))


# ── HTTP handler ────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "X-Master-Key, Content-Type, X-Bin-Name, X-Bin-Private",
        )

    def _reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        key = self.headers.get("X-Master-Key", "")
        return bool(MASTER_KEY) and hmac.compare_digest(key, MASTER_KEY)

    def _read_body(self):
        """Return (data, err). On error, already sends the response; err is True."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > MAX_BODY:
            self._reply(413, {"message": "payload too large"})
            return None, True
        raw = self.rfile.read(length) if length else b""
        try:
            return (json.loads(raw) if raw else {}), False
        except json.JSONDecodeError:
            self._reply(400, {"message": "invalid json"})
            return None, True

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._authed():
            return self._reply(401, {"message": "unauthorized"})
        m = re.match(r"^/b/([^/]+)/latest$", self.path)
        if not m:
            return self._reply(404, {"message": "not found"})
        bin_id = m.group(1)
        if not BIN_ID_RE.match(bin_id):
            return self._reply(400, {"message": "bad bin id"})
        try:
            record = read_blob(bin_id)
        except FileNotFoundError:
            return self._reply(404, {"message": "bin not found"})
        return self._reply(200, {"record": record})

    def do_PUT(self):
        if not self._authed():
            return self._reply(401, {"message": "unauthorized"})
        m = re.match(r"^/b/([^/]+)$", self.path)
        if not m:
            return self._reply(404, {"message": "not found"})
        bin_id = m.group(1)
        if not BIN_ID_RE.match(bin_id):
            return self._reply(400, {"message": "bad bin id"})
        data, err = self._read_body()
        if err:
            return
        write_blob(bin_id, data)
        return self._reply(200, {"metadata": {"id": bin_id}})

    def do_POST(self):
        if not self._authed():
            return self._reply(401, {"message": "unauthorized"})
        if self.path != "/b":
            return self._reply(404, {"message": "not found"})
        data, err = self._read_body()
        if err:
            return
        bin_id = secrets.token_hex(12)
        write_blob(bin_id, data)
        return self._reply(200, {"metadata": {"id": bin_id}})

    def log_message(self, *args):
        pass  # keep systemd journal quiet


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
