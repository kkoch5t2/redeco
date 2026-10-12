"""Read-only checks for isolated and development-user MCP modes."""
from __future__ import annotations
import argparse
import asyncio
import json
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def data(result):
    return result.structuredContent or json.loads(result.content[0].text)


async def verify(mode=None, expected_user=None, skip_tunnel=False):
    async with streamable_http_client("http://127.0.0.1:8770/mcp") as (reader, writer, _):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {"host_status", "read_file", "run_command"} <= names, "Required MCP tools are missing"
            status = await session.call_tool("host_status", {})
            assert not status.isError, "host_status failed"
            info = data(status)
            actual_mode = info.get("runtime", {}).get("mode", "isolated")
            mode = mode or actual_mode
            assert mode == actual_mode, "Runtime mode does not match requested mode"
            roots = info["roots"]
            cwd = roots[0]

            async def command(argv):
                result = await session.call_tool("run_command", {"argv": argv, "cwd": cwd})
                assert not result.isError, "Command tool failed"
                payload = data(result)
                assert payload["exit_code"] == 0, "Probe failed"
                return payload["stdout"].strip()

            probe = (
                "import os,pwd,json\n"
                "a=pwd.getpwuid(os.getuid())\n"
                "print(json.dumps({'user':a.pw_name,'home_ok':os.environ.get('HOME')==a.pw_dir,"
                "'home_readable':os.access(a.pw_dir,os.R_OK|os.X_OK),"
                "'cwd_writable':os.access(os.getcwd(),os.W_OK|os.X_OK),"
                "'path_has_local_bin':a.pw_dir+'/.local/bin' in os.environ.get('PATH','').split(':')}))\n"
            )
            identity = json.loads(await command(["/usr/bin/python3", "-c", probe]))
            assert identity["user"] not in {"root", "redeco-tunnel"}, "Unsafe MCP account"
            if expected_user:
                assert identity["user"] == expected_user, "Unexpected MCP user"
            if mode == "isolated":
                assert identity["user"] == "redeco", "Isolated mode must run as redeco"
                assert await command(["sh", "-c", "test -r /home && echo readable || echo blocked"]) == "blocked", "Home isolation is missing"
            else:
                assert mode == "user" and identity["user"] != "redeco", "User mode must use a development account"
                assert identity["home_ok"] and identity["home_readable"], "Development home is unavailable"
            assert identity["cwd_writable"], "Work root is not writable"
            denied = await session.call_tool("read_file", {"path": "/etc/passwd"})
            assert denied.isError, "File tool accepted a path outside work roots"
            if not skip_tunnel:
                boundary_probe = (
                    "import os,pathlib\n"
                    "found=False; exposed=False\n"
                    "for p in pathlib.Path('/proc').iterdir():\n"
                    " if not p.name.isdigit(): continue\n"
                    " try:\n"
                    "  if (p/'comm').read_text().strip()!='tunnel-client': continue\n"
                    "  found=True\n"
                    "  if p.stat().st_uid==os.getuid(): exposed=True\n"
                    "  try:\n"
                    "   fd=os.open(p/'environ',os.O_RDONLY); os.close(fd); exposed=True\n"
                    "  except OSError: pass\n"
                    " except OSError: pass\n"
                    "try:\n"
                    " fd=os.open('/etc/redeco/tunnel.env',os.O_RDONLY);os.close(fd);exposed=True\n"
                    "except OSError: pass\n"
                    "print('isolated' if found and not exposed else 'exposed_or_missing')\n"
                )
                assert await command(["/usr/bin/python3", "-c", boundary_probe]) == "isolated", "Tunnel key boundary failed"
            print(f"PASS: MCP tools, mode={mode}, user={identity['user']}, work root, file boundary" + (", tunnel key boundary" if not skip_tunnel else ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["isolated", "user"])
    parser.add_argument("--expected-user")
    parser.add_argument("--skip-tunnel-check", action="store_true", help="Only for initial MCP setup before a tunnel is configured")
    args = parser.parse_args()
    asyncio.run(verify(args.mode, args.expected_user, args.skip_tunnel_check))


if __name__ == "__main__":
    main()
