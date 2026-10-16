"""Protocol and security smoke tests against the local stdio server."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def verify(allow_sudo=False, long=False):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "hello.txt").write_text("original", encoding="utf-8")
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(Path(__file__).with_name("server.py"))],
            env={**os.environ, "REDECO_ROOTS": tmp, "REDECO_AUDIT": str(root / "audit.jsonl"), "REDECO_MODE": "user", "REDECO_ALLOW_SUDO": "1" if allow_sudo else "0", "JAVA_HOME": "/opt/test-jdk", "CONTROL_PLANE_API_KEY": "dummy-test-only", "GITHUB_TOKEN": "dummy-test-only"},
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert {"host_status", "read_file", "write_file", "git_status", "run_command", "screenshot", "start_process", "read_process_output", "write_process_input", "stop_process", "list_processes"} <= names
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
                fake_sudo = root / 'sudo'
                fake_sudo.write_text('#!' + sys.executable + '\nimport sys,json\nprint(json.dumps(sys.argv[1:]))\n')
                fake_sudo.chmod(0o700)
                sudo = await session.call_tool("run_command", {"argv": [str(fake_sudo), "id"], "cwd": tmp})
                if allow_sudo:
                    assert not sudo.isError
                    assert json.loads(data(sudo)['stdout']) == ['-n', 'id']
                else:
                    assert sudo.isError
                started = await session.call_tool('start_process', {
                    'argv': [sys.executable, '-u', '-c', "import os; print('TTY',os.isatty(0)); print('READY'); value=input(); print('RESULT',value)"],
                    'cwd': tmp, 'interactive': True, 'wait_ms': 1000})
                assert not started.isError
                process = data(started)
                sid = process['session_id']
                output = process['output']
                offset = process['next_offset']
                for _ in range(10):
                    if 'READY' in output:
                        break
                    page = data(await session.call_tool('read_process_output', {'session_id': sid, 'offset': offset}))
                    output += page['output']; offset = page['next_offset']
                assert 'TTY True' in output and 'READY' in output
                written = await session.call_tool('write_process_input', {'session_id': sid, 'text': 'DUMMY_INPUT_NO_LOG\n'})
                assert not written.isError
                for _ in range(10):
                    page = data(await session.call_tool('read_process_output', {'session_id': sid, 'offset': offset}))
                    output += page['output']; offset = page['next_offset']
                    if page['status'] == 'exited':
                        break
                assert page['status'] == 'exited' and page['exit_code'] == 0
                assert 'RESULT DUMMY_INPUT_NO_LOG' in output
                assert 'DUMMY_INPUT_NO_LOG' not in (root / 'audit.jsonl').read_text()
                unknown = await session.call_tool('stop_process', {'session_id': 'unrelated-host-pid'})
                assert unknown.isError
                waiting = data(await session.call_tool('start_process', {
                    'argv': [sys.executable, '-u', '-c', "import time; print('RUNNING'); time.sleep(300)"], 'cwd': tmp}))
                stopped = data(await session.call_tool('stop_process', {'session_id': waiting['session_id']}))
                assert stopped['status'] == 'exited' and stopped['exit_code'] != 0
                # The existing short-command timeout must not leave ordinary children running.
                marker = root / 'timeout-child'
                child = "import os,time,pathlib; pathlib.Path('timeout-child').write_text(str(os.getpid())); time.sleep(300)"
                timeout = data(await session.call_tool('run_command', {
                    'argv': [sys.executable, '-c', f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(300)"],
                    'cwd': tmp, 'timeout_seconds': 1}))
                assert timeout['exit_code'] is None and 'process group stopped' in timeout['stderr']
                proc = Path('/proc') / marker.read_text() / 'stat'
                assert not proc.exists() or proc.read_text().rsplit(')', 1)[1].split()[0] == 'Z'
                if long:
                    job = data(await session.call_tool('start_process', {
                        'argv': [sys.executable, '-u', '-c', "import time; print('LONG_START'); time.sleep(125); print('LONG_DONE')"], 'cwd': tmp}))
                    await asyncio.sleep(121)
                    alive = data(await session.call_tool('read_process_output', {'session_id': job['session_id']}))
                    assert alive['status'] == 'running', 'Long session died at the old 120s limit'
                    long_output = alive['output']
                    long_offset = alive['next_offset']
                    for _ in range(15):
                        done = data(await session.call_tool('read_process_output', {'session_id': job['session_id'], 'wait_ms': 1000, 'offset': long_offset}))
                        long_output += done['output']; long_offset = done['next_offset']
                        if done['status'] == 'exited':
                            break
                    assert done['status'] == 'exited' and done['exit_code'] == 0 and 'LONG_DONE' in long_output
                    print('PASS: real session survived 120 seconds and completed after 125 seconds', flush=True)
                print(f"PASS: MCP protocol, files, environment exclusion, sudo opt-in={allow_sudo}, PTY/input/output, stop, timeout descendant cleanup, audit input exclusion", flush=True)


def data(result):
    return result.structuredContent or json.loads(result.content[0].text)


async def main():
    await verify(long='--long' in sys.argv)
    await verify(allow_sudo=True)


if __name__ == "__main__":
    asyncio.run(main())
