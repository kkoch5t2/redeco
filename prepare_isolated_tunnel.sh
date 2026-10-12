#!/usr/bin/env bash
# Configure a private outbound tunnel with a separate OS user. Does not start it.
set -euo pipefail
umask 077

if [[ ${EUID} -ne 0 || ! -t 0 ]]; then
  echo "Run interactively with sudo on the Ubuntu host" >&2
  exit 1
fi
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! systemctl is-active --quiet redeco-mcp-isolated.service; then
  echo "Start redeco-mcp-isolated.service first" >&2
  exit 1
fi
if systemctl is-active --quiet redeco-tunnel-isolated.service; then
  echo "Tunnel already running; use migrate_tunnel_user.sh for an existing installation" >&2
  exit 1
fi
client_bin="${TUNNEL_CLIENT_BIN:-/home/${SUDO_USER:-}/.local/bin/tunnel-client}"
if [[ ! -x "$client_bin" ]]; then
  echo "Set TUNNEL_CLIENT_BIN to the official tunnel-client binary path" >&2
  exit 1
fi
read -r -p 'Tunnel ID (tunnel_...): ' tunnel_id
if [[ ! "$tunnel_id" =~ ^tunnel_[A-Za-z0-9_-]+$ ]]; then
  echo "Invalid tunnel ID" >&2
  exit 2
fi
read -r -s -p 'OpenAI runtime API key (hidden): ' runtime_key
printf '\n'
if [[ ! "$runtime_key" =~ ^sk-[A-Za-z0-9_-]+$ ]]; then
  echo "Invalid runtime API key format" >&2
  exit 2
fi

tunnel_home=/var/lib/redeco-tunnel
if id redeco-tunnel >/dev/null 2>&1; then
  if [[ "$(getent passwd redeco-tunnel | cut -d: -f6)" != "$tunnel_home" ]]; then
    echo "Existing redeco-tunnel account has an unexpected home" >&2
    exit 1
  fi
else
  useradd --system --user-group --home-dir "$tunnel_home" --create-home --shell /usr/sbin/nologin redeco-tunnel
fi
install -d -o redeco-tunnel -g redeco-tunnel -m 0700 "$tunnel_home" "$tunnel_home/.config" "$tunnel_home/.config/tunnel-client"
install -d -o root -g root -m 0700 /etc/redeco
install -o root -g root -m 0755 "$client_bin" /opt/redeco/tunnel-client
printf 'CONTROL_PLANE_API_KEY=%s\n' "$runtime_key" > /etc/redeco/tunnel.env
chmod 0600 /etc/redeco/tunnel.env
unset runtime_key

runuser -u redeco-tunnel -- env HOME="$tunnel_home" \
  /opt/redeco/tunnel-client init \
  --force \
  --health-listen-addr 127.0.0.1:8780 \
  --sample sample_mcp_remote_no_auth \
  --profile redeco-isolated \
  --tunnel-id "$tunnel_id" \
  --mcp-server-url http://127.0.0.1:8770/mcp
set -a
source /etc/redeco/tunnel.env
set +a
runuser -u redeco-tunnel -- env HOME="$tunnel_home" \
  /opt/redeco/tunnel-client doctor --profile redeco-isolated --explain
unset CONTROL_PLANE_API_KEY

install -o root -g root -m 0644 "$source_dir/redeco-tunnel-isolated.service" \
  /etc/systemd/system/redeco-tunnel-isolated.service
systemctl daemon-reload
echo "Separate tunnel user and service prepared; the tunnel remains stopped."
