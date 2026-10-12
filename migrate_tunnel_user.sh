#!/usr/bin/env bash
# Move the running tunnel to its own OS user without displaying or replacing the API key.
set -euo pipefail
umask 077

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo on the Ubuntu host" >&2
  exit 1
fi
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
unit=/etc/systemd/system/redeco-tunnel-isolated.service
old_profile=/var/lib/redeco/.config/tunnel-client/redeco-isolated.yaml
new_home=/var/lib/redeco-tunnel
new_profile="$new_home/.config/tunnel-client/redeco-isolated.yaml"
key_file=/etc/redeco/tunnel.env
if [[ ! -f "$unit" || ! -f "$source_dir/redeco-tunnel-isolated.service" ||
      ! -x /opt/redeco/tunnel-client || ! -f "$key_file" || -L "$key_file" ]]; then
  echo "Existing tunnel files are missing; no changes made" >&2
  exit 1
fi
if [[ "$(stat -c '%U:%G:%a' "$key_file")" != root:root:600 ]]; then
  echo "The runtime key file must be root:root mode 600; no changes made" >&2
  exit 1
fi
if [[ ! -f "$old_profile" && ! -f "$new_profile" ]]; then
  echo "Tunnel profile is missing; no changes made" >&2
  exit 1
fi
current_user="$(systemctl show -p User --value redeco-tunnel-isolated.service)"
if [[ "$current_user" != redeco && "$current_user" != redeco-tunnel ]]; then
  echo "Unexpected tunnel service user; no changes made" >&2
  exit 1
fi
if id redeco-tunnel >/dev/null 2>&1; then
  if [[ "$(getent passwd redeco-tunnel | cut -d: -f6)" != "$new_home" ]]; then
    echo "Existing redeco-tunnel account has an unexpected home" >&2
    exit 1
  fi
else
  useradd --system --user-group --home-dir "$new_home" --create-home --shell /usr/sbin/nologin redeco-tunnel
fi
install -d -o redeco-tunnel -g redeco-tunnel -m 0700 "$new_home" "$new_home/.config" "$new_home/.config/tunnel-client"
if [[ -f "$old_profile" ]]; then
  install -o redeco-tunnel -g redeco-tunnel -m 0600 "$old_profile" "$new_profile"
fi

# Stop the old process before replacing the unit. On failure it stays stopped,
# so the old same-user key exposure cannot return through Restart=always.
systemctl stop redeco-tunnel-isolated.service
install -o root -g root -m 0644 "$source_dir/redeco-tunnel-isolated.service" "$unit"
systemctl daemon-reload
systemctl start redeco-tunnel-isolated.service
ready=false
for _ in {1..20}; do
  if systemctl is-active --quiet redeco-tunnel-isolated.service &&
     curl --fail --silent --max-time 2 http://127.0.0.1:8780/readyz >/dev/null; then
    ready=true
    break
  fi
  sleep 1
done
if [[ "$ready" != true ]]; then
  systemctl stop redeco-tunnel-isolated.service
  echo "New tunnel failed readiness; it is stopped. Inspect journalctl -u redeco-tunnel-isolated.service" >&2
  exit 1
fi
if [[ "$(systemctl show -p User --value redeco-tunnel-isolated.service)" != redeco-tunnel ]]; then
  systemctl stop redeco-tunnel-isolated.service
  echo "Unexpected service user; tunnel stopped" >&2
  exit 1
fi
rm -f -- "$old_profile"
echo "Tunnel ready under redeco-tunnel. Autostart state: $(systemctl is-enabled redeco-tunnel-isolated.service)."
