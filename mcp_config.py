"""Render MCP service configuration without tunnel credentials."""
from __future__ import annotations
import argparse
import re
from pathlib import Path

DEV_ENV = {"JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT", "GRADLE_USER_HOME", "NVM_DIR", "LANG", "LC_ALL", "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"}
DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin"


def clean(value: str) -> str:
    if any(c in value for c in "\x00\n\r"):
        raise ValueError("Configuration values must be single-line strings")
    return value


def env_line(key: str, value: str) -> str:
    value = clean(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'{key}="{value}"\n'


def render(mode: str, user: str, group: str, home: str, roots: list[str], path: str = "", extra: dict[str, str] | None = None) -> tuple[str, str]:
    if mode not in {"isolated", "user"}:
        raise ValueError("Unsupported mode")
    for name in (user, group):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*[$]?", name):
            raise ValueError("Invalid OS account name")
    if user in {"root", "redeco-tunnel"} or (mode == "user" and user == "redeco"):
        raise ValueError("Choose a non-root development account distinct from tunnel users")
    if not clean(home).startswith("/") or not roots:
        raise ValueError("An absolute home and at least one root are required")
    for root in roots:
        if not clean(root).startswith("/") or ":" in root or root == "/":
            raise ValueError("Roots must be absolute directories without ':'; '/' is not allowed")
    path = clean(path or (f"{home}/.local/bin:{home}/bin:{DEFAULT_PATH}" if mode == "user" else DEFAULT_PATH))
    if any(not part.startswith("/") for part in path.split(":")):
        raise ValueError("PATH must contain only absolute, nonempty entries")
    extra = extra or {}
    if set(extra) - DEV_ENV:
        raise ValueError("Only documented development/desktop environment variables are accepted")
    state = "/var/lib/redeco" if mode == "isolated" else "/var/lib/redeco-mcp/" + user
    values = {"HOME": home, "USER": user, "LOGNAME": user, "PATH": path, "REDECO_MODE": mode, "REDECO_TRANSPORT": "streamable-http", "REDECO_ROOTS": ":".join(roots), "REDECO_AUDIT": state + "/audit.jsonl", "REDECO_PORT": "8770", **extra}
    environment = "".join(env_line(k, v) for k, v in values.items())
    isolation = "ProtectSystem=strict\nProtectHome=true\nReadWritePaths=/var/lib/redeco\nPrivateTmp=true\n" if mode == "isolated" else "ProtectSystem=false\nProtectHome=false\nPrivateTmp=false\n"
    unit = f"""[Unit]
Description=redeco MCP ({mode} mode, local only)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
Group={group}
WorkingDirectory=/opt/redeco
EnvironmentFile=/etc/redeco/mcp.env
ExecStart=/opt/redeco/venv/bin/python /opt/redeco/server.py
Restart=always
RestartSec=5
NoNewPrivileges=true
CapabilityBoundingSet=
AmbientCapabilities=
{isolation}RestrictSUIDSGID=true
UMask=0077

[Install]
WantedBy=multi-user.target
"""
    return unit, environment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["isolated", "user"], required=True)
    for name in ("user", "group", "home"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--root", action="append", required=True)
    parser.add_argument("--path", default="")
    parser.add_argument("--env", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    extra = {}
    for item in args.env:
        key, sep, value = item.partition("=")
        if not sep or key in extra:
            parser.error("Each --env must be a unique KEY=VALUE")
        extra[key] = value
    try:
        unit, environment = render(args.mode, args.user, args.group, args.home, args.root, args.path, extra)
    except ValueError as exc:
        parser.error(str(exc))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "mcp.service").write_text(unit, encoding="utf-8")
    (args.output / "mcp.env").write_text(environment, encoding="utf-8")


if __name__ == "__main__":
    main()
