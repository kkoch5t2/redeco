"""Protocol and security smoke tests against the local stdio server."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "hello.txt").write_text("original", encoding="utf-8")
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(Path(__file__).with_name("server.py"))],
            env={**os.environ, "REDECO_ROOTS": tmp, "REDECO_AUDIT": str(root / "audit.jsonl")},
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert {"host_status", "read_file", "write_file", "git_status", "run_command", "screenshot"} <= names
                read = await session.call_tool("read_file", {"path": str(root / "hello.txt")})
                read_data = read.structuredContent or json.loads(read.content[0].text)
                assert not read.isError and read_data["content"] == "original"
                denied = await session.call_tool("read_file", {"path": "/etc/passwd"})
                assert denied.isError
                (root / ".env").write_text("SECRET=dummy", encoding="utf-8")
                secret = await session.call_tool("read_file", {"path": str(root / ".env")})
                assert secret.isError
                stale = await session.call_tool("write_file", {"path": str(root / "hello.txt"), "content": "new", "expected_sha256": "bad"})
                assert stale.isError
                written = await session.call_tool("write_file", {"path": str(root / "hello.txt"), "content": "new", "expected_sha256": read_data["sha256"]})
                assert not written.isError and (root / "hello.txt").read_text() == "new"
                command = await session.call_tool("run_command", {"argv": ["python3", "-c", "print(41 + 1)"], "cwd": tmp})
                command_data = command.structuredContent or json.loads(command.content[0].text)
                assert not command.isError and command_data["stdout"].strip() == "42"
                sudo = await session.call_tool("run_command", {"argv": ["sudo", "id"], "cwd": tmp})
                assert sudo.isError
                print("PASS: MCP initialize/list/call, path boundary, secret path, stale write, command, sudo refusal")


if __name__ == "__main__":
    asyncio.run(main())
