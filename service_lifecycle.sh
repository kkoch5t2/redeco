#!/usr/bin/env bash
# Wait for the local MCP before starting the tunnel's discovery process.
redeco_wait_mcp() {
  local python="$1" check="$2" mode="$3" user="$4" skip_tunnel="$5"
  local args=(--mode "$mode" --expected-user "$user")
  [[ "$skip_tunnel" != true ]] || args+=(--skip-tunnel-check)
  local attempt
  for attempt in {1..15}; do
    if timeout 12 "$python" "$check" "${args[@]}" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  echo 'MCP runtime checks did not pass; inspect the MCP service locally.' >&2
  return 1
}

redeco_wait_mcp_listener() {
  # GET without an Accept header returns 406 when Streamable HTTP is serving.
  local status attempt
  for attempt in {1..20}; do
    status="$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 2 http://127.0.0.1:8770/mcp)" || status=
    [[ "$status" != 406 ]] || return 0
    sleep 1
  done
  echo 'Previous MCP listener did not become available.' >&2
  return 1
}

redeco_wait_tunnel() {
  local attempt
  for attempt in {1..20}; do
    if systemctl is-active --quiet redeco-tunnel-isolated.service &&
       curl --fail --silent --output /dev/null --max-time 2 http://127.0.0.1:8780/readyz; then
      return 0
    fi
    sleep 1
  done
  echo 'Tunnel readiness checks did not pass; inspect the tunnel service locally.' >&2
  return 1
}

redeco_restart_services() {
  local python="$1" check="$2" mode="$3" user="$4" tunnel_active="$5"
  if [[ "$tunnel_active" == true ]]; then
    systemctl stop redeco-tunnel-isolated.service || return
  fi
  systemctl restart redeco-mcp-isolated.service || return
  redeco_wait_mcp "$python" "$check" "$mode" "$user" true || return
  if [[ "$tunnel_active" == true ]]; then
    systemctl start redeco-tunnel-isolated.service || return
    redeco_wait_tunnel || return
    redeco_wait_mcp "$python" "$check" "$mode" "$user" false || return
  fi
}
