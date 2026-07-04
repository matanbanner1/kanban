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

    def _content_length(self):
        """Return (length, err). On a non-numeric header, replies 400 and err is True."""
        raw = self.headers.get("Content-Length", "0") or "0"
        try:
            return int(raw), False
        except ValueError:
            self._drain_body()
            self._reply(400, {"message": "bad content-length"})
            return None, True

    def _drain_body(self):
        """Read-and-discard the declared request body so the socket closes cleanly.

        BaseHTTPRequestHandler defaults to HTTP/1.0 (socket closes after the
        handler returns). If we reply early without reading a client's
        pending body off the wire, the OS may RST the connection instead of
        closing it cleanly, which some clients (e.g. browser fetch()) surface
        as a network error rather than the intended HTTP status.

        Bounded to a small multiple of MAX_BODY: draining just past that is
        enough to make realistic oversized-but-plausible bodies (e.g. just
        over 1 MB) close cleanly. A pathologically huge Content-Length from
        an abusive client may still end in a reset — that's an acceptable
        outcome for that abuse case, and we avoid an unbounded read that
        would itself be a DoS vector.
        """
        raw = self.headers.get("Content-Length", "0") or "0"
        try:
            length = int(raw)
        except ValueError:
            return
        cap = MAX_BODY + 1
        remaining = min(length, cap)
        chunk_size = 65536
        while remaining > 0:
            chunk = self.rfile.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)

    def _read_body(self):
        """Return (data, err). On error, already sends the response; err is True."""
        length, err = self._content_length()
        if err:
            return None, True
        if length > MAX_BODY:
            self._drain_body()
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
            self._drain_body()
            return self._reply(401, {"message": "unauthorized"})
        m = re.match(r"^/b/([^/]+)$", self.path)
        if not m:
            self._drain_body()
            return self._reply(404, {"message": "not found"})
        bin_id = m.group(1)
        if not BIN_ID_RE.match(bin_id):
            self._drain_body()
            return self._reply(400, {"message": "bad bin id"})
        data, err = self._read_body()
        if err:
            return
        write_blob(bin_id, data)
        return self._reply(200, {"metadata": {"id": bin_id}})

    def do_POST(self):
        if not self._authed():
            self._drain_body()
            return self._reply(401, {"message": "unauthorized"})
        if self.path != "/b":
            self._drain_body()
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
