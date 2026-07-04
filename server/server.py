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
# Socket read timeout: bounds how long a blocking self.rfile.read() (in
# _drain_body/_read_body) can hang on a client that declares a body but never
# sends it. Without this, an unauthenticated client can tie up a thread
# indefinitely (slowloris-style DoS) since ThreadingHTTPServer spawns one
# thread per connection. See StreamRequestHandler.setup(), which applies this
# via self.connection.settimeout(self.timeout).
REQUEST_TIMEOUT = int(os.environ.get("KANBAN_REQUEST_TIMEOUT", "15"))

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
    # StreamRequestHandler.setup() applies this via
    # self.connection.settimeout(self.timeout), bounding every blocking
    # socket read (including self.rfile.read() in _drain_body/_read_body).
    # Without it, a client that declares a body but never sends it (or never
    # closes) can hang a thread forever — see REQUEST_TIMEOUT above.
    timeout = REQUEST_TIMEOUT

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
        # Compare as bytes: hmac.compare_digest raises TypeError on non-ASCII
        # str inputs, which would otherwise crash the handler thread (fails
        # closed, but with a stderr/journal traceback) on a header containing
        # non-ASCII bytes. Bytes comparison stays constant-time and never
        # raises for this input.
        return bool(MASTER_KEY) and hmac.compare_digest(
            key.encode("utf-8", "surrogatepass"), MASTER_KEY.encode("utf-8")
        )

    def _abandon(self):
        """Give up on a connection whose body read failed (timeout or reset).

        The socket is dead or unresponsive at this point, so attempting to
        write a response would itself raise. Mark the connection to be
        closed and let the server tear it down quietly instead.
        """
        self.close_connection = True

    def _content_length(self):
        """Return (length, err). On a non-numeric or negative header, replies
        400 and err is True. A negative value would otherwise pass the
        `int(...)` parse, slip past the caller's `length > MAX_BODY` cap
        (false for negatives), and turn `self.rfile.read(length)` into an
        unbounded read-until-EOF — defeating MAX_BODY entirely.
        """
        raw = self.headers.get("Content-Length", "0") or "0"
        try:
            length = int(raw)
        except ValueError:
            length = None
        if length is None or length < 0:
            if not self._drain_body():
                return None, True
            self._reply(400, {"message": "bad content-length"})
            return None, True
        return length, False

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

        Returns True if the drain completed (or there was nothing to drain),
        False if the socket errored/timed out mid-read — callers must treat
        False as "connection is gone, do not attempt to reply".
        """
        raw = self.headers.get("Content-Length", "0") or "0"
        try:
            length = int(raw)
        except ValueError:
            return True
        cap = MAX_BODY + 1
        remaining = min(length, cap)
        chunk_size = 65536
        while remaining > 0:
            try:
                chunk = self.rfile.read(min(chunk_size, remaining))
            except OSError:
                # Covers socket.timeout (slowloris-style stalled client) and
                # connection resets while reading — either way the socket is
                # unusable, so abandon quietly rather than let the exception
                # surface as a stderr traceback.
                self._abandon()
                return False
            if not chunk:
                break
            remaining -= len(chunk)
        return True

    def _read_body(self):
        """Return (data, err). On error, already sends the response; err is True."""
        length, err = self._content_length()
        if err:
            return None, True
        if length > MAX_BODY:
            if not self._drain_body():
                return None, True
            self._reply(413, {"message": "payload too large"})
            return None, True
        try:
            raw = self.rfile.read(length) if length else b""
        except OSError:
            self._abandon()
            return None, True
        try:
            return (json.loads(raw) if raw else {}), False
        except json.JSONDecodeError:
            self._reply(400, {"message": "invalid json"})
            return None, True

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        # Private Network Access: Chrome blocks a public page (github.io) from
        # reaching a private IP unless the preflight opts in. On-tailnet devices
        # resolve this host to a private tailnet IP via MagicDNS, so grant it.
        if self.headers.get("Access-Control-Request-Private-Network") == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
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
            if not self._drain_body():
                return
            return self._reply(401, {"message": "unauthorized"})
        m = re.match(r"^/b/([^/]+)$", self.path)
        if not m:
            if not self._drain_body():
                return
            return self._reply(404, {"message": "not found"})
        bin_id = m.group(1)
        if not BIN_ID_RE.match(bin_id):
            if not self._drain_body():
                return
            return self._reply(400, {"message": "bad bin id"})
        data, err = self._read_body()
        if err:
            return
        write_blob(bin_id, data)
        return self._reply(200, {"metadata": {"id": bin_id}})

    def do_POST(self):
        if not self._authed():
            if not self._drain_body():
                return
            return self._reply(401, {"message": "unauthorized"})
        if self.path != "/b":
            if not self._drain_body():
                return
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
