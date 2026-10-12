"""Read-only live check of the isolated redeco MCP service."""
import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def data(result):
    return result.structuredContent or json.loads(result.content[0].text)


async def main():
    async with streamable_http_client("http://127.0.0.1:8770/mcp") as (reader, writer, _):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {"host_status", "read_file", "run_command"} <= names
            status = await session.call_tool("host_status", {})
            assert not status.isError
            who = await session.call_tool("run_command", {
                "argv": ["id", "-un"],
                "cwd": "/var/lib/redeco/workspaces",
            })
            assert not who.isError and data(who)["stdout"].strip() == "redeco"
            home = await session.call_tool("run_command", {
                "argv": ["sh", "-c", "test -r /home && echo readable || echo blocked"],
                "cwd": "/var/lib/redeco/workspaces",
            })
            assert not home.isError and data(home)["stdout"].strip() == "blocked"
            denied = await session.call_tool("read_file", {
                "path": "/etc/passwd",
            })
            assert denied.isError
            probe_source = (
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
                "print('isolated' if found and not exposed else 'exposed_or_missing')\n"
            )
            boundary = await session.call_tool("run_command", {
                "argv": ["python3", "-c", probe_source],
                "cwd": "/var/lib/redeco/workspaces",
            })
            assert not boundary.isError and data(boundary)["stdout"].strip() == "isolated"
            print("PASS: live MCP tools, redeco UID, home isolation, path denial, tunnel key boundary")


if __name__ == "__main__":
    asyncio.run(main())
