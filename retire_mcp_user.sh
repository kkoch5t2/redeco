#!/usr/bin/env bash
# Retire only the old MCP account, preserving its home in a root-only archive.
set -euo pipefail
umask 077
if [[ ${EUID} -ne 0 ]]; then echo 'Run with sudo on Ubuntu' >&2; exit 1; fi
if ! id redeco >/dev/null 2>&1; then echo 'Old MCP account already absent'; exit 0; fi
current_user="$(systemctl show -p User --value redeco-mcp-isolated.service)"
[[ -n "$current_user" && "$current_user" != root && "$current_user" != redeco && "$current_user" != redeco-tunnel ]] || {
  echo 'Migrate and verify the MCP development user first' >&2; exit 1;
}
systemctl is-active --quiet redeco-mcp-isolated.service
[[ "$(systemctl show -p User --value redeco-tunnel-isolated.service)" == redeco-tunnel ]]
/opt/redeco/venv/bin/python /opt/redeco/check_runtime.py --mode user --expected-user "$current_user"
uid="$(id -u redeco)"
if pgrep -u "$uid" >/dev/null; then echo 'Old redeco processes still exist; inspect them before retiring the account' >&2; exit 1; fi
old_home="$(getent passwd redeco | cut -d: -f6)"
[[ "$old_home" == /var/lib/redeco && -d "$old_home" && ! -L "$old_home" ]] || {
  echo 'Unexpected old home; no changes made' >&2; exit 1;
}
archive="$(mktemp -d /var/lib/redeco-retired.XXXXXXXX)"
chmod 0700 "$archive"
mv -- "$old_home" "$archive/home"
if ! userdel redeco; then mv -- "$archive/home" "$old_home"; rmdir "$archive"; exit 1; fi
echo "Old redeco account removed. Its files remain in root-only archive: $archive/home"
