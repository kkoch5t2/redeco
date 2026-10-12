#!/usr/bin/env bash
# One-time local setup. Run on the Ubuntu host with sudo; does not enable a tunnel.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script with sudo from its checkout" >&2
  exit 1
fi
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
app_dir=/opt/redeco
state_dir=/var/lib/redeco
unit=/etc/systemd/system/redeco-mcp-isolated.service
if [[ ! -f "$source_dir/server.py" || ! -f "$source_dir/requirements.txt" ]]; then
  echo "redeco source files are missing" >&2
  exit 1
fi
if id redeco >/dev/null 2>&1; then
  if [[ "$(getent passwd redeco | cut -d: -f6)" != "$state_dir" ]]; then
    echo "Existing redeco account has an unexpected home; aborting" >&2
    exit 1
  fi
else
  useradd --system --user-group --home-dir "$state_dir" --create-home --shell /usr/sbin/nologin redeco
fi
install -d -o redeco -g redeco -m 0700 "$state_dir" "$state_dir/workspaces"
install -d -o root -g root -m 0755 "$app_dir"
install -o root -g root -m 0644 "$source_dir/server.py" "$app_dir/server.py"
install -o root -g root -m 0644 "$source_dir/requirements.txt" "$app_dir/requirements.txt"
if [[ ! -x "$app_dir/venv/bin/python" ]]; then
  python3 -m venv "$app_dir/venv"
fi
"$app_dir/venv/bin/python" -m pip install -r "$app_dir/requirements.txt"
if ss -H -ltn 'sport = :8770' | grep -q . && ! systemctl is-active --quiet redeco-mcp-isolated.service; then
  echo "Port 8770 is already in use; aborting" >&2
  exit 1
fi
cat > "$unit" <<'UNIT'
[Unit]
Description=redeco isolated MCP (local only)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=redeco
Group=redeco
WorkingDirectory=/opt/redeco
Environment=HOME=/var/lib/redeco
Environment=REDECO_TRANSPORT=streamable-http
Environment=REDECO_ROOTS=/var/lib/redeco/workspaces
Environment=REDECO_AUDIT=/var/lib/redeco/audit.jsonl
Environment=REDECO_PORT=8770
ExecStart=/opt/redeco/venv/bin/python /opt/redeco/server.py
Restart=always
RestartSec=5
NoNewPrivileges=true
CapabilityBoundingSet=
AmbientCapabilities=
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/redeco
PrivateTmp=true
RestrictSUIDSGID=true
UMask=0077

[Install]
WantedBy=multi-user.target
UNIT
chmod 0644 "$unit"
systemctl daemon-reload
systemctl enable redeco-mcp-isolated.service
systemctl restart redeco-mcp-isolated.service
systemctl --no-pager --lines=8 status redeco-mcp-isolated.service
echo "Isolated server installed on 127.0.0.1:8770. Tunnel remains disabled."
