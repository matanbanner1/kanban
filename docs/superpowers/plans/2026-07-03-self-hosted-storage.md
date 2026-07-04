# Self-hosted Storage Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace JSONBin.io with a tiny self-hosted JSON blob store on `spark-8cae`, exposed publicly over HTTPS via Tailscale Funnel, so the kanban board has no third-party quota limits.

**Architecture:** A single-file Python stdlib HTTP server stores one JSON blob per "bin" as a file on disk, behind a JSONBin-compatible API (`GET /b/<id>/latest`, `PUT /b/<id>`). It runs under systemd on `127.0.0.1:18790` and is exposed at `https://spark-8cae.tailb9692d.ts.net:8443` by Tailscale Funnel. The frontend stays on GitHub Pages; only its `API` constant changes.

**Tech Stack:** Python 3.12 (stdlib only: `http.server`, `json`, `hmac`, `secrets`), `unittest` for tests, systemd, Tailscale Funnel.

## Global Constraints

- **Stdlib only** — no `pip install`, no third-party Python packages.
- **Target server:** Ubuntu 24.04, ARM64 (`aarch64`), Python 3.12, already runs Tailscale (`spark-8cae.tailb9692d.ts.net`, tailnet IP `100.77.58.49`).
- **Do not disturb** the existing `tailscale serve` on `:443` → `localhost:18789`.
- **Allowed CORS origin:** `https://matanbanner1.github.io` (exact string).
- **Bin id format:** `^[A-Za-z0-9_-]{1,64}$`.
- **Max request body:** 1,000,000 bytes.
- **Secret** (`KANBAN_MASTER_KEY`) never committed to git; injected via systemd `EnvironmentFile`.
- Repo code lives in `server/`; deployed to `/opt/kanban-store/` on the server; data in `/var/lib/kanban-store/`.

---

### Task 1: Storage helpers (pure functions, no HTTP)

Build the file-backed blob store and validation as importable functions, fully unit-tested without any network.

**Files:**
- Create: `server/server.py`
- Test: `server/test_server.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `BIN_ID_RE` — compiled regex `^[A-Za-z0-9_-]{1,64}$`
  - `bin_path(bin_id: str) -> str` — absolute path to the bin's JSON file under `DATA_DIR`
  - `read_blob(bin_id: str) -> Any` — parsed JSON; raises `FileNotFoundError` if absent
  - `write_blob(bin_id: str, data: Any) -> None` — atomic write (temp file + `os.replace`)
  - Module globals overridable in tests: `DATA_DIR`, `MASTER_KEY`, `ALLOWED_ORIGIN`, `HOST`, `PORT`, `MAX_BODY`

- [ ] **Step 1: Write the failing test**

Create `server/test_server.py`:

```python
import json
import os
import tempfile
import unittest

import server


class BlobStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        server.DATA_DIR = self.tmp

    def test_write_then_read_roundtrip(self):
        server.write_blob("mybin", {"todo": [1, 2], "done": []})
        self.assertEqual(server.read_blob("mybin"), {"todo": [1, 2], "done": []})

    def test_read_missing_raises_filenotfound(self):
        with self.assertRaises(FileNotFoundError):
            server.read_blob("nope")

    def test_write_is_atomic_no_tmp_left(self):
        server.write_blob("mybin", {"a": 1})
        leftovers = [f for f in os.listdir(self.tmp) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_bin_id_regex(self):
        self.assertTrue(server.BIN_ID_RE.match("abc-123_DEF"))
        self.assertFalse(server.BIN_ID_RE.match("../etc"))
        self.assertFalse(server.BIN_ID_RE.match("has/slash"))
        self.assertFalse(server.BIN_ID_RE.match(""))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && python3 -m unittest test_server -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server'` (or `AttributeError` once the file exists but functions don't).

- [ ] **Step 3: Write minimal implementation**

Create `server/server.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd server && python3 -m unittest test_server -v`
Expected: PASS (4 tests: roundtrip, missing, atomic, regex).

- [ ] **Step 5: Commit**

```bash
git add server/server.py server/test_server.py
git commit -m "feat(server): file-backed JSON blob store with atomic writes"
```

---

### Task 2: HTTP layer — GET / PUT / POST / OPTIONS with auth + CORS

Add the request handler on top of Task 1's helpers, tested against a live in-process server.

**Files:**
- Modify: `server/server.py` (append `Handler` class + `main()`)
- Modify: `server/test_server.py` (append `HttpTest`)

**Interfaces:**
- Consumes: `read_blob`, `write_blob`, `BIN_ID_RE`, `MASTER_KEY`, `ALLOWED_ORIGIN`, `MAX_BODY` from Task 1.
- Produces:
  - `Handler` — `BaseHTTPRequestHandler` subclass implementing `do_GET/do_PUT/do_POST/do_OPTIONS`
  - `main() -> None` — starts a `ThreadingHTTPServer` on `(HOST, PORT)`
  - Endpoints: `GET /b/<id>/latest → {"record": ...}`, `PUT /b/<id> → {"metadata": {"id": ...}}`, `POST /b → {"metadata": {"id": <new>}}`, `OPTIONS * → 204`

- [ ] **Step 1: Write the failing test**

Append to `server/test_server.py`:

```python
import http.client
import threading
from http.server import ThreadingHTTPServer


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        server.DATA_DIR = cls.tmp
        server.MASTER_KEY = "testkey"
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def req(self, method, path, body=None, key="testkey", ctype="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        headers = {}
        if key is not None:
            headers["X-Master-Key"] = key
        if body is not None:
            headers["Content-Type"] = ctype
        conn.request(method, path, body, headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def test_put_then_get_roundtrip(self):
        resp, _ = self.req("PUT", "/b/board1", json.dumps({"todo": [1]}))
        self.assertEqual(resp.status, 200)
        resp, data = self.req("GET", "/b/board1/latest")
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(data)["record"], {"todo": [1]})

    def test_get_unknown_bin_404(self):
        resp, _ = self.req("GET", "/b/ghost/latest")
        self.assertEqual(resp.status, 404)

    def test_bad_key_401(self):
        resp, _ = self.req("GET", "/b/board1/latest", key="wrong")
        self.assertEqual(resp.status, 401)

    def test_missing_key_401(self):
        resp, _ = self.req("GET", "/b/board1/latest", key=None)
        self.assertEqual(resp.status, 401)

    def test_path_traversal_id_400(self):
        resp, _ = self.req("PUT", "/b/..%2Fetc", json.dumps({"x": 1}))
        self.assertIn(resp.status, (400, 404))

    def test_invalid_json_400(self):
        resp, _ = self.req("PUT", "/b/board1", "{not json")
        self.assertEqual(resp.status, 400)

    def test_body_too_large_413(self):
        big = json.dumps({"x": "a" * 1_000_001})
        resp, _ = self.req("PUT", "/b/board1", big)
        self.assertEqual(resp.status, 413)

    def test_options_preflight_cors(self):
        resp, _ = self.req("OPTIONS", "/b/board1", key=None)
        self.assertEqual(resp.status, 204)
        self.assertEqual(
            resp.getheader("Access-Control-Allow-Origin"),
            "https://matanbanner1.github.io",
        )
        self.assertIn("X-Master-Key", resp.getheader("Access-Control-Allow-Headers"))

    def test_post_creates_bin(self):
        resp, data = self.req("POST", "/b", json.dumps({"todo": []}))
        self.assertEqual(resp.status, 200)
        new_id = json.loads(data)["metadata"]["id"]
        self.assertTrue(server.BIN_ID_RE.match(new_id))
        resp, data = self.req("GET", f"/b/{new_id}/latest")
        self.assertEqual(json.loads(data)["record"], {"todo": []})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && python3 -m unittest test_server -v`
Expected: FAIL — `AttributeError: module 'server' has no attribute 'Handler'`.

- [ ] **Step 3: Write minimal implementation**

Append to `server/server.py`:

```python
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
```

Note: the `%2F` in the traversal test is URL-encoded `/`; `http.server` decodes it so the path becomes `/b/../etc`, which fails the `^/b/([^/]+)$` match → 404 (also acceptable per the test's `(400, 404)`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd server && python3 -m unittest test_server -v`
Expected: PASS — all Task 1 + Task 2 tests (13 total).

- [ ] **Step 5: Commit**

```bash
git add server/server.py server/test_server.py
git commit -m "feat(server): JSONBin-compatible HTTP API with auth and CORS"
```

---

### Task 3: Deploy to `spark-8cae` under systemd

Ship the server to the box, run it as a hardened systemd service on `127.0.0.1:18790`, and verify a local round-trip on the server.

**Files:**
- Create: `server/kanban-store.service` (systemd unit, committed to repo)
- Create: `server/DEPLOY.md` (deploy runbook)

**Interfaces:**
- Consumes: `server/server.py` from Task 2.
- Produces: a running service reachable at `http://127.0.0.1:18790` on `spark-8cae`.

- [ ] **Step 1: Create the systemd unit**

Create `server/kanban-store.service`:

```ini
[Unit]
Description=Kanban JSON blob store
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 /opt/kanban-store/server.py
EnvironmentFile=/etc/kanban-store.env
Environment=KANBAN_DATA_DIR=/var/lib/kanban-store
Restart=always
RestartSec=2

# Sandboxing
DynamicUser=yes
StateDirectory=kanban-store
ProtectSystem=strict
ProtectHome=yes
NoNewPrivileges=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
```

Note: `StateDirectory=kanban-store` makes systemd create `/var/lib/kanban-store` owned by the dynamic user; `KANBAN_DATA_DIR` points the app there.

- [ ] **Step 2: Generate the master key and secret file (on the server)**

Run (records the key locally too — you'll need it for the frontend hash in Task 5):

```bash
KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
echo "MASTER KEY (save this): $KEY"
ssh mbanner@spark-8cae "echo 'KANBAN_MASTER_KEY=$KEY' | sudo tee /etc/kanban-store.env >/dev/null && sudo chmod 600 /etc/kanban-store.env"
```

Expected: prints the key; `/etc/kanban-store.env` created with mode 600.

- [ ] **Step 3: Copy the server code and install the unit**

```bash
ssh mbanner@spark-8cae "sudo mkdir -p /opt/kanban-store"
scp server/server.py mbanner@spark-8cae:/tmp/server.py
ssh mbanner@spark-8cae "sudo mv /tmp/server.py /opt/kanban-store/server.py"
scp server/kanban-store.service mbanner@spark-8cae:/tmp/kanban-store.service
ssh mbanner@spark-8cae "sudo mv /tmp/kanban-store.service /etc/systemd/system/kanban-store.service"
```

Expected: files in place; no errors.

- [ ] **Step 4: Enable and start the service**

```bash
ssh mbanner@spark-8cae "sudo systemctl daemon-reload && sudo systemctl enable --now kanban-store && sleep 1 && systemctl is-active kanban-store"
```

Expected: prints `active`.

- [ ] **Step 5: Verify a local round-trip on the server**

```bash
ssh mbanner@spark-8cae 'K=$(sudo grep -oP "(?<=KANBAN_MASTER_KEY=).*" /etc/kanban-store.env); \
  curl -s -X PUT -H "X-Master-Key: $K" -H "Content-Type: application/json" \
    -d "{\"todo\":[\"hi\"]}" http://127.0.0.1:18790/b/board1; echo; \
  curl -s -H "X-Master-Key: $K" http://127.0.0.1:18790/b/board1/latest; echo'
```

Expected: first line `{"metadata": {"id": "board1"}}`, second line `{"record": {"todo": ["hi"]}}`.

- [ ] **Step 6: Write the deploy runbook and commit**

Create `server/DEPLOY.md` capturing Steps 2–5 as a repeatable runbook (redeploy = re-`scp` `server.py` then `sudo systemctl restart kanban-store`). Then:

```bash
git add server/kanban-store.service server/DEPLOY.md
git commit -m "feat(server): systemd unit and deploy runbook"
```

---

### Task 4: Expose publicly via Tailscale Funnel on :8443

Make the service reachable at `https://spark-8cae.tailb9692d.ts.net:8443` without disturbing the existing `:443` service.

**Files:** none (Tailscale state changes only; commands recorded in `server/DEPLOY.md`).

**Interfaces:**
- Consumes: running service on `127.0.0.1:18790` from Task 3.
- Produces: public HTTPS endpoint `https://spark-8cae.tailb9692d.ts.net:8443`.

- [ ] **Step 1: Confirm the exact Funnel syntax**

```bash
ssh mbanner@spark-8cae "tailscale funnel --help 2>&1 | head -40"
```

Expected: usage text confirming the `--https=8443` + target form. Use it to finalize Step 2's command if the syntax differs.

- [ ] **Step 2: Enable Funnel on :8443 → :18790**

```bash
ssh mbanner@spark-8cae "sudo tailscale funnel --bg --https=8443 http://127.0.0.1:18790"
```

Expected: confirmation that `https://spark-8cae.tailb9692d.ts.net:8443` is now served publicly.

**Prerequisite gate:** if this errors with a message about Funnel not being permitted, the `funnel` node attribute must be enabled in the tailnet policy at https://login.tailscale.com/admin/acls (web action — user does this once), then re-run Step 2.

- [ ] **Step 3: Verify existing :443 service is untouched**

```bash
ssh mbanner@spark-8cae "tailscale serve status"
```

Expected: still shows `https://spark-8cae.tailb9692d.ts.net (tailnet only) |-- / proxy http://localhost:18789`, plus the new `:8443` funnel entry.

- [ ] **Step 4: Verify the public endpoint from off-server (run on the Mac)**

```bash
K="<paste the master key from Task 3 Step 2>"
curl -s -H "X-Master-Key: $K" "https://spark-8cae.tailb9692d.ts.net:8443/b/board1/latest"; echo
```

Expected: `{"record": {"todo": ["hi"]}}` (the value written in Task 3 Step 5).

- [ ] **Step 5: Record commands in the runbook and commit**

```bash
git add server/DEPLOY.md
git commit -m "docs(server): record Tailscale Funnel exposure steps"
```

---

### Task 5: Repoint the frontend and redeploy

Point `kanban.html` at the new server and verify the live board end-to-end.

**Files:**
- Modify: `kanban.html:633`

**Interfaces:**
- Consumes: public endpoint from Task 4.
- Produces: a working board reading/writing to `spark-8cae`.

- [ ] **Step 1: Change the API constant**

In `kanban.html`, line 633, change:

```js
const API     = 'https://api.jsonbin.io/v3';
```

to:

```js
const API     = 'https://spark-8cae.tailb9692d.ts.net:8443';
```

(Our server serves `/b/...` at the root — no `/v3` prefix.)

- [ ] **Step 2: Commit and deploy to GitHub Pages**

```bash
git add kanban.html
git commit -m "feat: point storage at self-hosted spark-8cae server"
git push
```

Expected: push succeeds; GitHub Actions `deploy.yml` publishes to `gh-pages` within ~1 min.

- [ ] **Step 3: Verify the live board in a browser**

Open (substituting the new master key from Task 3 Step 2 and the bin id `board1`, or the migrated bin id from Task 6):

```
https://matanbanner1.github.io/kanban/kanban.html#<NEW_MASTER_KEY>/board1
```

Using Playwright MCP: navigate to that URL, take a snapshot, and check the console has **no** 403/CORS errors and the status reads "Synced"/"Saved". Add a card, reload, confirm it persists.

Expected: board loads, shows the stored data, and saves without console errors.

- [ ] **Step 4: (No separate commit — code committed in Step 2.)**

---

### Task 6: Migrate existing data from JSONBin (gated on quota reset)

Copy the real board out of JSONBin into the new store. **Blocked while JSONBin returns 403 (quota exhausted); run when the quota resets.**

**Files:** none (data-only operation).

**Interfaces:**
- Consumes: running store from Task 3; old JSONBin credentials (old key `<OLD_JSONBIN_KEY>`, old bin `<OLD_JSONBIN_BIN_ID>`).
- Produces: the real board data under the chosen bin id on `spark-8cae`.

- [ ] **Step 1: Check whether JSONBin quota has reset**

```bash
curl -s -w "\n[HTTP %{http_code}]\n" \
  'https://api.jsonbin.io/v3/b/<OLD_JSONBIN_BIN_ID>/latest' \
  -H 'X-Master-Key: <OLD_JSONBIN_KEY>'
```

Expected when ready: `[HTTP 200]` with a JSON body containing `"record"`. If still `[HTTP 403]` ("Requests exhausted"), stop and retry later.

- [ ] **Step 2: Export the record to a file**

```bash
curl -s 'https://api.jsonbin.io/v3/b/<OLD_JSONBIN_BIN_ID>/latest' \
  -H 'X-Master-Key: <OLD_JSONBIN_KEY>' \
  | python3 -c "import sys, json; print(json.dumps(json.load(sys.stdin)['record']))" \
  > /tmp/board-export.json
cat /tmp/board-export.json
```

Expected: the board JSON (`{"todo":[...],"inprogress":[...],"done":[...]}`, possibly with a `todos` key). Verify it looks like your real board.

- [ ] **Step 3: Seed the new store via the public API**

Use whichever bin id the frontend uses (this plan standardized on `board1`):

```bash
K="<new master key from Task 3 Step 2>"
curl -s -X PUT -H "X-Master-Key: $K" -H "Content-Type: application/json" \
  --data-binary @/tmp/board-export.json \
  "https://spark-8cae.tailb9692d.ts.net:8443/b/board1"; echo
```

Expected: `{"metadata": {"id": "board1"}}`.

- [ ] **Step 4: Verify in the browser**

Reload the board URL from Task 5 Step 3. Confirm the migrated cards appear.

Expected: real board data is present and editable.

---

## Follow-ups (out of scope for this plan)

- Repoint the companion **todos app** (`/Users/mbanner/apps/todos/`) — same one-line `API` change; it shares the same bin.
- Optionally reduce frontend polling frequency / pause on hidden tab (unrelated efficiency improvement).
- Once migrated and verified, delete the old JSONBin bin and revoke the old key.
