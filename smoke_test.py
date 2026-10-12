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
            env={**os.environ, "REDECO_ROOTS": tmp, "REDECO_AUDIT": str(root / "audit.jsonl"), "REDECO_MODE": "user", "JAVA_HOME": "/opt/test-jdk", "CONTROL_PLANE_API_KEY": "dummy-test-only", "GITHUB_TOKEN": "dummy-test-only"},
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
                runtime = await session.call_tool("host_status", {})
                runtime_data = runtime.structuredContent or json.loads(runtime.content[0].text)
                assert runtime_data["runtime"]["mode"] == "user"
                env_probe = await session.call_tool("run_command", {
                    "argv": [sys.executable, "-c", "import os,json;print(json.dumps({'java':os.environ.get('JAVA_HOME')=='/opt/test-jdk','tunnel_absent':'CONTROL_PLANE_API_KEY' not in os.environ,'git_absent':'GITHUB_TOKEN' not in os.environ}))"],
                    "cwd": tmp,
                })
                env_data = env_probe.structuredContent or json.loads(env_probe.content[0].text)
                assert env_data["exit_code"] == 0 and all(json.loads(env_data["stdout"]).values())
                sudo = await session.call_tool("run_command", {"argv": ["sudo", "id"], "cwd": tmp})
                assert sudo.isError
                print("PASS: MCP initialize/list/call, path boundary, secret path, stale write, command, development environment, secret environment exclusion, sudo refusal")


if __name__ == "__main__":
    asyncio.run(main())
