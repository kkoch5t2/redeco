"""Check a running Windows MCP over loopback without printing machine identity."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import uuid
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def data(result):
    if result.isError:
        raise RuntimeError(str(result.content))
    return result.structuredContent or json.loads(result.content[0].text)


async def verify(port, screenshot):
    async with streamable_http_client(f'http://127.0.0.1:{port}/mcp') as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            assert len((await session.list_tools()).tools) == 12
            info = data(await session.call_tool('host_status', {}))
            assert info['platform'] == 'Windows'
            cwd = info['roots'][0]
            command = data(await session.call_tool('run_command', {
                'argv': [sys.executable, '-c', 'print(42)'], 'cwd': cwd}))
            assert command['exit_code'] == 0 and command['stdout'].strip() == '42'
            path = str(Path(cwd) / ('.redeco-check-' + uuid.uuid4().hex + '.txt'))
            try:
                written = data(await session.call_tool('write_file', {'path': path, 'content': '日本語'}))
                read = data(await session.call_tool('read_file', {'path': path}))
                assert read['content'] == '日本語' and read['sha256'] == written['sha256']
            finally:
                result = data(await session.call_tool('run_command', {
                    'argv': [sys.executable, '-c', 'import pathlib,sys;pathlib.Path(sys.argv[1]).unlink(missing_ok=True)', path], 'cwd': cwd}))
                assert result['exit_code'] == 0
            if screenshot:
                result = await session.call_tool('screenshot', {})
                assert not result.isError and any(c.type == 'image' for c in result.content)
            print('PASS: Windows HTTP MCP, 12 tools, host, command, UTF-8 file roundtrip' +
                  (', screenshot' if screenshot else '') + f", administrator={info['runtime']['allow_admin']}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8770)
    parser.add_argument('--screenshot', action='store_true')
    args = parser.parse_args()
    asyncio.run(verify(args.port, args.screenshot))
