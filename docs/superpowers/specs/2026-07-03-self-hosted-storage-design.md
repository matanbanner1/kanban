# Self-hosted storage server to replace JSONBin

**Date:** 2026-07-03
**Status:** Approved design — ready for implementation plan

## Problem

The kanban board (`kanban.html`, served from GitHub Pages) stores its entire
state as one JSON blob in **JSONBin.io**. JSONBin's free-tier request quota is
exhausted, so the API now returns `403 {"message":"Requests exhausted"}` and the
board can no longer load or save. The 15-second polling loop (per open tab)
burns ~172k requests/month against a ~10k/month free limit, so this will keep
recurring.

**Goal:** move the storage off JSONBin and onto the user's own server
(`spark-8cae`), with no ongoing quota limits and no third-party dependency.

## Constraints & environment (verified)

- **Server `spark-8cae`:** Ubuntu 24.04 LTS, ARM64 (`aarch64`), Python 3.12
  present, Docker present but **not needed** (see Deployment), Tailscale 1.98.4.
- **Tailscale:** MagicDNS name `spark-8cae.tailb9692d.ts.net`, tailnet IP
  `100.77.58.49`. Provides valid Let's Encrypt HTTPS certs automatically.
- **Existing service:** `tailscale serve` already proxies
  `https://spark-8cae.tailb9692d.ts.net/` (port 443, tailnet-only) to
  `localhost:18789`. **Must not be disturbed.**
- **Frontend:** stays on GitHub Pages (`https://matanbanner1.github.io`). This
  makes calls to the storage server **cross-origin**, so CORS is required.
- **Browser rules:** an HTTPS page cannot call an HTTP endpoint (mixed content),
  and cross-origin calls with a custom header (`X-Master-Key`) trigger a CORS
  **preflight** (`OPTIONS`) that the server must answer.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Server implementation | **Tiny purpose-built Python stdlib server** | Need is one JSON blob (GET/PUT, one secret). Off-the-shelf products (PocketBase, etc.) would force a larger frontend rewrite for no gain. |
| API shape | **JSONBin-compatible subset** | Frontend change reduces to one line (the `API` constant). |
| Access model | **Public HTTPS via Tailscale Funnel** | User opens the board from Mac and occasionally phone; Funnel avoids requiring the Tailscale app on every device. |
| Funnel port | **`:8443`** | Leaves the existing tailnet-only service on `:443` untouched (Funnel is per-port, not per-path). |
| Process manager | **systemd (not Docker)** | Zero dependencies + runtime already present ⇒ Docker adds a layer with no benefit. systemd gives restart-on-crash/boot + sandboxing. |
| Frontend host | **GitHub Pages (unchanged)** | Keep the existing auto-deploy pipeline; handle CORS server-side. |
| Data migration | **Migrate when possible** | Existing bin is behind the 403 quota; stand up server now, export from JSONBin once quota resets, then seed. |

## Architecture

```
Browser (any device)                    spark-8cae (Ubuntu 24.04 ARM64)
┌──────────────────────┐                ┌─────────────────────────────────┐
│ kanban.html          │                │  Tailscale Funnel :8443 (public) │
│ served by GitHub     │  HTTPS PUT/GET │        │ reverse-proxy            │
│ Pages (github.io)    │───────────────▶│        ▼                         │
│                      │   X-Master-Key │  systemd: python server :18790   │
│ API=...ts.net:8443   │◀───────────────│        │ (127.0.0.1 only)         │
└──────────────────────┘   JSON {record}│        ▼                         │
                                         │  /var/lib/kanban-store/<id>.json │
                            (existing :443 tailnet serve → :18789 untouched)│
                                         └─────────────────────────────────┘
```

## Component 1 — Storage server (`/opt/kanban-store/server.py`)

Single Python file, standard library only (`http.server`, `json`, `os`, `hmac`).
Runs a `ThreadingHTTPServer` bound to `127.0.0.1:18790`.

### API contract

| Method | Path | Behavior |
|--------|------|----------|
| `GET` | `/b/<id>/latest` | `200 {"record": <blob>}`; `404` if the bin file doesn't exist; `401` on bad key |
| `PUT` | `/b/<id>` | Persist the request body (verbatim JSON) as the blob → `200 {"metadata":{"id":<id>}}` |
| `POST` | `/b` | Create a new bin with a random id, seed `{}` → `200 {"metadata":{"id":<id>}}` *(only used by the frontend when no bin id is in the URL; included for parity)* |
| `OPTIONS` | any | `204` + CORS headers (preflight response) |
| anything else | | `405` |

This mirrors what `fetchState()` (reads `.record`), `pushState()`, and
`createBin()` (reads `.metadata.id`) already expect in `kanban.html`.

### Behavior details

- **Dumb blob store:** persists whatever JSON is PUT, verbatim. This naturally
  preserves the shared `todos` key that the companion todos app writes into the
  same blob.
- **Atomic writes:** write to a temp file then `os.replace()` so an interrupted
  save can never corrupt the existing board file.
- **Write lock:** a `threading.Lock` around writes, since polling and saving can
  overlap.

## Component 2 — Security (endpoint is public)

- **New master key:** a fresh 32-byte URL-safe random token (`secrets.token_urlsafe`),
  replacing the old JSONBin key. Sent by the browser in the `X-Master-Key`
  header; compared server-side with `hmac.compare_digest` (constant-time).
- **Path-traversal guard:** reject any `<id>` not matching `^[A-Za-z0-9_-]{1,64}$`
  before touching the filesystem → `400`.
- **Body size cap:** reject bodies over 1 MB → `413`.
- **CORS:** `Access-Control-Allow-Origin: https://matanbanner1.github.io`
  (specific origin, not `*`); `Access-Control-Allow-Methods: GET, PUT, POST, OPTIONS`;
  `Access-Control-Allow-Headers: X-Master-Key, Content-Type`.
- **Generic errors:** no internal detail in error bodies.

## Component 3 — Deployment (systemd + Tailscale Funnel)

### systemd unit `kanban-store.service`

- `ExecStart=/usr/bin/python3 /opt/kanban-store/server.py` (listens `127.0.0.1:18790`)
- `Restart=always`, unit `enable`d (survives crash and reboot)
- Secret via `EnvironmentFile=` pointing at a mode-`600` file containing
  `KANBAN_MASTER_KEY=…` (never committed to git)
- **Sandboxing:** `DynamicUser=yes`, `StateDirectory=kanban-store`
  (creates `/var/lib/kanban-store` owned by the dynamic user),
  `ProtectSystem=strict`, `ProtectHome=yes`, `NoNewPrivileges=yes`,
  `PrivateTmp=yes`.

### Tailscale Funnel

- `tailscale funnel --bg --https=8443 http://127.0.0.1:18790`
- **Prerequisite gate:** Funnel must be permitted for this node in the tailnet
  policy (the `funnel` node attribute). If the command errors, the user enables
  it once at the Tailscale admin console (web action, user-only). Exact CLI
  syntax to be confirmed against `tailscale funnel --help` at implementation.

Public board storage URL becomes:
`https://spark-8cae.tailb9692d.ts.net:8443`

## Component 4 — Frontend change (`kanban.html`)

- Change the single line:
  `const API = 'https://spark-8cae.tailb9692d.ts.net:8443';` (drop the `/v3`
  path segment JSONBin used; our server serves `/b/...` at the root).
- Redeploy via the existing GitHub Pages pipeline (push to `main`).
- Update the URL hash with the new master key: `#<NEW_KEY>/<binId>`.
- **Out of scope but flagged:** the companion todos app at
  `/Users/mbanner/apps/todos/` shares this bin and needs the *same* one-line
  `API` change to keep working.

## Component 5 — Data migration

1. Stand up the server (empty board works immediately).
2. When JSONBin quota resets: `GET` the current bin from JSONBin (old key) and
   write the `record` to `/var/lib/kanban-store/<binId>.json`.
3. Keep the **same bin id string**, so only the master key and API host change
   for the user.
4. If quota never resets in time, user can start fresh or supply a backup blob.

## Testing

- **Server-level (curl, local on server):**
  - GET/PUT round-trip returns the stored blob
  - `OPTIONS` returns the CORS headers
  - missing / wrong `X-Master-Key` → `401`
  - bin id containing `../` → `400`
  - oversized body → `413`
- **End-to-end:**
  - After Funnel is up, `curl` the public `:8443` URL from off-server
  - Load the board in a real browser (Playwright), confirm it loads, edits, and
    saves — the same method used to diagnose the original 403.

## Error handling summary

| Condition | Response |
|-----------|----------|
| Missing/invalid key | `401` |
| Unknown bin on GET | `404` (frontend treats as "bin not found") |
| Invalid bin id / malformed JSON | `400` |
| Body too large | `413` |
| Wrong method | `405` |
| Server/write failure | `500`, existing file left intact (atomic write) |

## Out of scope

- Rate limiting (master key + unguessable bin id deemed sufficient for now).
- Multi-user auth / accounts.
- Repointing the companion todos app (flagged; separate change).
- Reducing the frontend polling frequency (separate optional improvement).
