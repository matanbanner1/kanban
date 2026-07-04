# Deploying the kanban storage server to `spark-8cae`

The server runs as a **user-level systemd service** (no root/sudo required).
Everything lives under `~/kanban-store/` on the server.

- Code: `~/kanban-store/server.py`
- Data: `~/kanban-store/data/<binid>.json`
- Secret: `~/kanban-store/kanban-store.env` (mode 600, holds `KANBAN_MASTER_KEY`)
- Unit: `~/.config/systemd/user/kanban-store.service`
- Listens on `127.0.0.1:18821` (`KANBAN_PORT`; 18790 was already taken on this host)
- Exposed publicly via Tailscale Funnel on `:8443` (see below)

## First-time setup

```bash
# 1. Generate a master key (save it — needed for the frontend URL hash)
KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
echo "$KEY"

# 2. Copy code to the server
scp server/server.py mbanner@spark-8cae:/tmp/kanban-server.py

# 3. Install under $HOME (no sudo), write secret, create user unit, start it
ssh mbanner@spark-8cae bash -s <<REMOTE
set -e
export XDG_RUNTIME_DIR=/run/user/\$(id -u)
mkdir -p ~/kanban-store/data ~/.config/systemd/user
cp /tmp/kanban-server.py ~/kanban-store/server.py
umask 077; printf 'KANBAN_MASTER_KEY=%s\n' '$KEY' > ~/kanban-store/kanban-store.env; umask 022
cat > ~/.config/systemd/user/kanban-store.service <<'UNIT'
[Unit]
Description=Kanban JSON blob store
After=network-online.target
[Service]
ExecStart=/usr/bin/python3 %h/kanban-store/server.py
EnvironmentFile=%h/kanban-store/kanban-store.env
Environment=KANBAN_DATA_DIR=%h/kanban-store/data
Environment=KANBAN_PORT=18821
Restart=always
RestartSec=2
[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now kanban-store
loginctl enable-linger            # survive logout + reboot
systemctl --user is-active kanban-store
REMOTE
```

## Redeploy (after changing server.py)

```bash
scp server/server.py mbanner@spark-8cae:/tmp/kanban-server.py
ssh mbanner@spark-8cae 'export XDG_RUNTIME_DIR=/run/user/$(id -u); \
  cp /tmp/kanban-server.py ~/kanban-store/server.py && \
  systemctl --user restart kanban-store && \
  systemctl --user is-active kanban-store'
```

## Verify locally on the server

```bash
ssh mbanner@spark-8cae 'K=$(grep -oP "(?<=KANBAN_MASTER_KEY=).*" ~/kanban-store/kanban-store.env); \
  curl -s -X PUT -H "X-Master-Key: $K" -H "Content-Type: application/json" \
    -d "{\"todo\":[\"hi\"]}" http://127.0.0.1:18821/b/board1; echo; \
  curl -s -H "X-Master-Key: $K" http://127.0.0.1:18821/b/board1/latest; echo'
```

## Tailscale Funnel (public HTTPS on :8443)

```bash
ssh mbanner@spark-8cae 'tailscale funnel --bg --https=8443 http://127.0.0.1:18821'
```

If it errors that Funnel isn't permitted, enable the `funnel` node attribute
in the tailnet policy at https://login.tailscale.com/admin/acls, then retry.

Public URL: `https://spark-8cae.tailb9692d.ts.net:8443`

## Logs

```bash
ssh mbanner@spark-8cae 'export XDG_RUNTIME_DIR=/run/user/$(id -u); \
  journalctl --user -u kanban-store -n 50 --no-pager'
```
