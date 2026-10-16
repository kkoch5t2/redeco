#!/usr/bin/env bash
# Isolated by default; --user selects an existing development account.
set -euo pipefail
umask 077
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
target= mode=isolated selected_path= migrate=false print_config=false
allow_sudo=false sudo_nopasswd=false
roots=() extra_env=()
usage() {
  echo 'Usage: sudo bash install_isolated.sh [--user USER --root DIR ...] [--path PATH] [--env KEY=VALUE]'
  echo 'No --user: isolated service. --user: existing development account; --root is required.'
  echo '--print-config renders configuration without changing the host.'
  echo '--allow-sudo permits sudo in user mode, subject to the OS sudo policy.'
  echo '--sudo-nopasswd additionally grants the selected user passwordless sudo (ALL commands). Requires --allow-sudo.'
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) target="${2:?Missing user}"; mode=user; shift 2 ;;
    --root) roots+=("${2:?Missing root}"); shift 2 ;;
    --path) selected_path="${2:?Missing PATH}"; shift 2 ;;
    --env) extra_env+=("${2:?Missing KEY=VALUE}"); shift 2 ;;
    --migrate) migrate=true; shift ;;
    --print-config) print_config=true; shift ;;
    --allow-sudo) allow_sudo=true; shift ;;
    --sudo-nopasswd) sudo_nopasswd=true; shift ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ "$allow_sudo" != true || "$mode" == user ]] || { echo '--allow-sudo requires --user' >&2; exit 2; }
[[ "$sudo_nopasswd" != true || "$allow_sudo" == true ]] || { echo '--sudo-nopasswd requires --allow-sudo' >&2; exit 2; }
if [[ "$print_config" != true && ${EUID} -ne 0 ]]; then
  echo 'Run with sudo on the Ubuntu host' >&2; exit 1
fi
for file in server.py process_sessions.py requirements.txt mcp_config.py check_runtime.py service_lifecycle.sh; do
  [[ -f "$source_dir/$file" ]] || { echo "Missing source: $file" >&2; exit 1; }
done
source "$source_dir/service_lifecycle.sh"
if [[ "$mode" == user ]]; then
  account="$(getent passwd "$target")" || { echo 'Development account does not exist' >&2; exit 1; }
  IFS=: read -r target _ target_uid _ _ target_home target_shell <<< "$account"
  [[ "$target_uid" != 0 && "$target" != redeco && "$target" != redeco-tunnel ]] || {
    echo 'Choose a normal non-root development account' >&2; exit 1;
  }
  [[ "$target_shell" != */nologin && "$target_shell" != */false ]] || {
    echo 'Development account must have a login shell' >&2; exit 1;
  }
  [[ ${#roots[@]} -gt 0 ]] || { echo '--root is required with --user' >&2; exit 2; }
  target_group="$(id -gn "$target")"
  state_dir="/var/lib/redeco-mcp/$target"
  for i in "${!roots[@]}"; do
    roots[$i]="$(realpath -e -- "${roots[$i]}")"
    [[ -d "${roots[$i]}" ]] || { echo 'Root is not a directory' >&2; exit 1; }
    if [[ "$print_config" != true ]]; then
      for access in -r -w -x; do runuser -u "$target" -- test "$access" "${roots[$i]}"; done
    fi
  done
else
  [[ ${#roots[@]} -eq 0 ]] || { echo '--root requires --user' >&2; exit 2; }
  target=redeco target_group=redeco target_home=/var/lib/redeco state_dir=/var/lib/redeco
  roots=(/var/lib/redeco/workspaces)
fi
app_dir=/opt/redeco
config_dir=/etc/redeco
unit=/etc/systemd/system/redeco-mcp-isolated.service
sudoers_file="/etc/sudoers.d/redeco-mcp-$target"
if [[ "$mode" == user && ( -e "$sudoers_file" || -L "$sudoers_file" ) ]]; then
  [[ ! -L "$sudoers_file" && "$(head -n 1 "$sudoers_file")" == '# Managed by redeco --sudo-nopasswd' ]] || {
    echo 'Refusing to replace an unmanaged sudoers file' >&2; exit 1;
  }
fi
if [[ "$sudo_nopasswd" == true ]]; then
  command -v visudo >/dev/null || { echo 'Install sudo (visudo required) first' >&2; exit 1; }
fi
if [[ "$migrate" == true ]]; then
  [[ -f "$unit" ]] || { echo 'Existing MCP service is missing' >&2; exit 1; }
  [[ "$(systemctl show -p User --value redeco-tunnel-isolated.service)" == redeco-tunnel ]] || {
    echo 'Separate the tunnel user with migrate_tunnel_user.sh first' >&2; exit 1;
  }
  [[ -f "$config_dir/tunnel.env" && ! -L "$config_dir/tunnel.env" ]] || {
    echo 'Tunnel key file is missing or is a symlink' >&2; exit 1;
  }
  [[ "$(stat -c '%U:%G:%a' "$config_dir/tunnel.env")" == root:root:600 ]] || {
    echo 'Tunnel key file must be root:root mode 600' >&2; exit 1;
  }
  systemctl is-active --quiet redeco-tunnel-isolated.service || {
    echo 'Start the separate tunnel before migrating' >&2; exit 1;
  }
fi
if [[ "$print_config" != true ]]; then
  [[ -z "$(systemctl show -p DropInPaths --value redeco-mcp-isolated.service)" ]] || {
    echo 'Existing MCP service overrides need review before installation' >&2; exit 1;
  }
  if ss -H -ltn 'sport = :8770' | grep -q . && ! systemctl is-active --quiet redeco-mcp-isolated.service; then
    echo 'Port 8770 is already in use' >&2; exit 1
  fi
fi
staging="$(mktemp -d)"
trap 'rm -rf -- "$staging"' EXIT
render_args=(--mode "$mode" --user "$target" --group "$target_group" --home "$target_home" --output "$staging")
for root in "${roots[@]}"; do render_args+=(--root "$root"); done
[[ -z "$selected_path" ]] || render_args+=(--path "$selected_path")
[[ "$allow_sudo" != true ]] || render_args+=(--allow-sudo)
for item in "${extra_env[@]}"; do render_args+=(--env "$item"); done
python3 "$source_dir/mcp_config.py" "${render_args[@]}"
if [[ "$sudo_nopasswd" == true ]]; then
  printf '# Managed by redeco --sudo-nopasswd\n%s ALL=(ALL:ALL) NOPASSWD: ALL\n' "$target" > "$staging/sudoers"
  visudo -cf "$staging/sudoers" >/dev/null
fi
if [[ "$print_config" == true ]]; then
  cat "$staging/mcp.service" "$staging/mcp.env"
  [[ "$sudo_nopasswd" != true ]] || cat "$staging/sudoers"
  exit 0
fi
if [[ "$mode" == isolated ]]; then
  if id redeco >/dev/null 2>&1; then
    [[ "$(getent passwd redeco | cut -d: -f6)" == "$state_dir" ]] || {
      echo 'Existing redeco account has an unexpected home' >&2; exit 1;
    }
  else
    useradd --system --user-group --home-dir "$state_dir" --create-home --shell /usr/sbin/nologin redeco
  fi
  install -d -o redeco -g redeco -m 0700 "$state_dir/workspaces"
fi
if [[ "$mode" == user ]]; then install -d -o root -g root -m 0755 /var/lib/redeco-mcp; fi
install -d -o "$target" -g "$target_group" -m 0700 "$state_dir"
install -d -o root -g root -m 0755 "$app_dir"
install -d -o root -g root -m 0700 "$config_dir"
if [[ "$migrate" == true ]]; then
  [[ -x "$app_dir/venv/bin/python" ]] || { echo "Existing MCP virtual environment is missing" >&2; exit 1; }
  "$app_dir/venv/bin/python" -c "import mcp"
else
  if [[ ! -x "$app_dir/venv/bin/python" ]]; then python3 -m venv "$app_dir/venv"; fi
  "$app_dir/venv/bin/python" -m pip install -r "$source_dir/requirements.txt"
fi
backup="$(mktemp -d "$config_dir/mcp-backup.XXXXXXXX")"
was_active=false was_enabled=false was_tunnel_active=false
systemctl is-active --quiet redeco-mcp-isolated.service && was_active=true
systemctl is-enabled --quiet redeco-mcp-isolated.service && was_enabled=true
systemctl is-active --quiet redeco-tunnel-isolated.service && was_tunnel_active=true
files=("$unit" "$config_dir/mcp.env" "$app_dir/server.py" "$app_dir/requirements.txt" "$app_dir/check_runtime.py" "$app_dir/process_sessions.py")
[[ "$mode" != user ]] || files+=("$sudoers_file")
for i in "${!files[@]}"; do [[ ! -e "${files[$i]}" ]] || cp -p -- "${files[$i]}" "$backup/$i"; done
rollback() {
  code=$?; trap - ERR; set +e
  if [[ "$was_tunnel_active" == true ]]; then systemctl stop redeco-tunnel-isolated.service; fi
  for i in "${!files[@]}"; do
    if [[ -f "$backup/$i" ]]; then cp -p -- "$backup/$i" "${files[$i]}"; else rm -f -- "${files[$i]}"; fi
  done
  systemctl daemon-reload
  if [[ "$was_enabled" != true ]]; then systemctl disable redeco-mcp-isolated.service; fi
  if [[ "$was_active" == true ]]; then systemctl restart redeco-mcp-isolated.service; else systemctl stop redeco-mcp-isolated.service; fi
  if [[ "$was_tunnel_active" == true && "$was_active" == true ]]; then
    if redeco_wait_mcp_listener; then
      systemctl start redeco-tunnel-isolated.service
      redeco_wait_tunnel || echo 'Previous files restored, but tunnel readiness still needs inspection.' >&2
    else
      echo 'Previous files restored, but the tunnel remains stopped because MCP is unavailable.' >&2
    fi
  fi
  echo "MCP migration failed; previous files restored. Backup: $backup" >&2
  exit "$code"
}
trap rollback ERR
install -o root -g root -m 0644 "$source_dir/server.py" "$app_dir/server.py"
install -o root -g root -m 0644 "$source_dir/process_sessions.py" "$app_dir/process_sessions.py"
install -o root -g root -m 0644 "$source_dir/requirements.txt" "$app_dir/requirements.txt"
install -o root -g root -m 0644 "$source_dir/check_runtime.py" "$app_dir/check_runtime.py"
install -o root -g root -m 0600 "$staging/mcp.env" "$config_dir/mcp.env"
install -o root -g root -m 0644 "$staging/mcp.service" "$unit"
if [[ "$mode" == user ]]; then
  if [[ "$sudo_nopasswd" == true ]]; then
    install -o root -g root -m 0440 "$staging/sudoers" "$sudoers_file"
    visudo -c >/dev/null
  elif [[ -e "$sudoers_file" ]]; then
    rm -- "$sudoers_file"
  fi
fi
systemctl daemon-reload
systemctl enable redeco-mcp-isolated.service
# ActiveState=active does not mean Python has opened its listener yet.
# Discovery failures can persist until the tunnel process is restarted.
redeco_restart_services "$app_dir/venv/bin/python" "$app_dir/check_runtime.py" "$mode" "$target" "$was_tunnel_active"
trap - ERR
echo "MCP ready: mode=$mode user=$target. Service name remains redeco-mcp-isolated.service."
echo "Previous MCP files saved at $backup. Tunnel settings and key were not replaced."
echo "sudo enabled=$allow_sudo passwordless grant=$sudo_nopasswd (OS policy still applies)."
