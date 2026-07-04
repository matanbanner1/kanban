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
