#!/usr/bin/env bash
# Switch an existing MCP to a development account, keeping the separate tunnel.
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then echo 'Run with sudo from your normal Ubuntu account' >&2; exit 1; fi
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
target="${SUDO_USER:-}"
args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) target="${2:?Missing user}"; shift 2 ;;
    --root|--path|--env) args+=("$1" "${2:?Missing value}"); shift 2 ;;
    --print-config) args+=("$1"); shift ;;
    --help|-h)
      echo 'Usage: sudo bash migrate_mcp_user.sh --root DIR [--user USER] [--path PATH] [--env KEY=VALUE]'
      echo 'Default user: the account that invoked sudo. At least one --root is required.'
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$target" && "$target" != root ]] || { echo 'Specify a non-root --user' >&2; exit 2; }
exec bash "$source_dir/install_isolated.sh" --user "$target" --migrate "${args[@]}"
