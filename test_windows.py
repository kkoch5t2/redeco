"""Windows native integration checks using real child processes and the MCP protocol."""
import asyncio
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.name == 'nt', 'Windows integration tests')
class WindowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from windows_process_sessions import ProcessSessions
        from windows_runtime import elevated
        cls.ProcessSessions = ProcessSessions
        cls.tmp = tempfile.TemporaryDirectory(prefix='redeco-test-')
        cls.root = Path(cls.tmp.name)
        cls.env = {**os.environ, 'REDECO_ROOTS': cls.tmp.name, 'REDECO_MODE': 'user',
                   'REDECO_ALLOW_SUDO': '0', 'REDECO_ALLOW_ADMIN': '1' if elevated() else '0',
                   'REDECO_AUDIT': str(cls.root / 'audit.jsonl'), 'REDECO_TRANSPORT': 'stdio'}
        with patch.dict(os.environ, cls.env):
            cls.server = importlib.import_module('server')

    @classmethod
    def tearDownClass(cls):
        cls.server.SESSIONS.close()
        cls.tmp.cleanup()

    def setUp(self):
        self.manager = self.ProcessSessions()
        self.addCleanup(self.manager.close)

    def start(self, code, interactive=False):
        return self.manager.start([sys.executable, '-u', '-c', code], self.root,
                                  self.server._environment(), interactive)

    def until(self, session, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = session.read(wait_ms=50)
            if predicate(result):
                return result
        self.fail(str(result))

    def test_line_input_and_incremental_output(self):
        s = self.start("print('READY'); print('RESULT',input())", True)
        first = self.until(s, lambda r: 'READY' in r['output'])
        s.write('value\n')
        self.until(s, lambda r: r['status'] == 'exited')
        self.assertIn('RESULT value', s.read(first['next_offset'])['output'])
        self.assertEqual(s.read()['terminal'], 'pipe')
        with self.assertRaises(ValueError):
            s.write('after exit')

    def test_assignment_failure_never_executes_user_code(self):
        from windows_process_sessions import spawn_in_job
        marker = self.root / 'must-not-run'
        command = [sys.executable, '-c', f"from pathlib import Path;Path({str(marker)!r}).touch()"]
        created = []
        popen = subprocess.Popen

        def launch_real(*args, **kwargs):
            process = popen(*args, **kwargs)
            created.append(process)
            return process

        with patch('windows_process_sessions.Job.assign', side_effect=OSError('assignment failed')), \
             patch('windows_process_sessions.subprocess.Popen', side_effect=launch_real):
            with self.assertRaises(OSError):
                spawn_in_job(command, self.root, self.server._environment(), stderr=subprocess.PIPE)
            # The gate must never open, and even the unassigned helper is reaped.
            self.assertEqual(len(created), 1)
            self.assertIsNotNone(created[0].returncode)
            self.assertTrue(all(stream.closed for stream in
                                (created[0].stdin, created[0].stdout, created[0].stderr)))
        self.assertFalse(marker.exists())

    def test_command_bounds_and_timeout(self):
        r = self.server._exec([sys.executable, '-c', "import os; os.write(1,b'x'*2000000); os.write(2,b'y'*2000000)"], self.root)
        self.assertEqual(r['exit_code'], 0)
        self.assertEqual(r['stdout'], 'x' * 16000)
        self.assertEqual(r['stderr'], 'y' * 16000)
        self.assertTrue(r['stdout_truncated'] and r['stderr_truncated'])
        before = time.monotonic()
        r = self.server._exec([sys.executable, '-c', 'import time;time.sleep(300)'], self.root, 1)
        self.assertIsNone(r['exit_code'])
        self.assertLess(time.monotonic() - before, 6)

    def test_stop_removes_descendants_after_parent_exit(self):
        import psutil
        marker = self.root / 'background'
        code = "import subprocess,sys,pathlib; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(300)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); pathlib.Path('background').write_text(str(p.pid))"
        s = self.start(code)
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        pid = int(marker.read_text())
        self.assertEqual(s.read()['status'], 'running')
        self.assertEqual(s.stop()['status'], 'exited')
        self.assertFalse(psutil.pid_exists(pid))
        marker.unlink()

    def test_timeout_removes_descendants(self):
        import psutil
        marker = self.root / 'timeout-child'
        code = "import subprocess,sys,pathlib,time; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(300)']); pathlib.Path('timeout-child').write_text(str(p.pid)); time.sleep(300)"
        r = self.server._exec([sys.executable, '-c', code], self.root, 2)
        self.assertIsNone(r['exit_code'])
        self.assertFalse(psutil.pid_exists(int(marker.read_text())))
        marker.unlink()

    def test_unicode_bounded_output(self):
        s = self.start("print('あ'*150000)")
        page = self.until(s, lambda r: r['status'] == 'exited')
        self.assertTrue(page['truncated'])
        self.assertLessEqual(len(s.buffer), 256 * 1024)
        while True:
            self.assertNotIn('\ufffd', page['output'])
            if page['next_offset'] == page['output_end']:
                break
            page = s.read(page['next_offset'])

    def test_split_utf8(self):
        s = self.start("import os,time;os.write(1,bytes([227]));time.sleep(.4);os.write(1,bytes([129,130]))")
        self.assertEqual(self.until(s, lambda r: r['status'] == 'exited')['output'], 'あ')

    def test_file_guards_and_atomic_write(self):
        p = self.root / 'hello.txt'
        self.server.write_file(str(p), '日本語')
        read = self.server.read_file(str(p))
        with self.assertRaises(ValueError):
            self.server.write_file(str(p), 'stale')
        self.server.write_file(str(p), 'new', read['sha256'])
        self.assertEqual(p.read_text(), 'new')
        for path in (str(self.root / '.ENV'), str(p) + ':secret', str(self.root.parent / 'outside')):
            with self.assertRaises(ValueError):
                self.server._path(path)
        p.unlink()

    def test_environment_and_powershell(self):
        with patch.dict(os.environ, {'CONTROL_PLANE_API_KEY': 'DUMMY', 'GITHUB_TOKEN': 'DUMMY'}):
            env = self.server._environment()
        self.assertNotIn('CONTROL_PLANE_API_KEY', env)
        self.assertNotIn('GITHUB_TOKEN', env)
        self.assertIn('SYSTEMROOT', {k.upper() for k in env})
        r = self.server._exec(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
            '[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); Write-Output "日本語"'], self.root)
        self.assertEqual(r['exit_code'], 0)
        self.assertEqual(r['stdout'].strip(), '日本語')

    def test_limits_offsets_and_unknown_sessions(self):
        with patch('windows_process_sessions.MAX_ACTIVE', 1):
            s = self.start('import time;time.sleep(300)')
            with self.assertRaises(ValueError):
                self.start('print("must not run")')
            self.assertEqual(s.stop()['status'], 'exited')
        with self.assertRaises(ValueError):
            self.manager.get('unrelated-pid')
        for offset in (-1, 999999):
            with self.assertRaises(ValueError):
                s.read(offset)

    def test_server_crash_kills_its_jobs(self):
        import psutil
        marker = self.root / 'crash-child'
        module_path = str(Path(__file__).parent)
        code = (
            f"import sys;sys.path.insert(0,{module_path!r});"
            "from windows_process_sessions import ProcessSessions;"
            "import os,pathlib,time; m=ProcessSessions();"
            "s=m.start([sys.executable,'-c','import time;time.sleep(300)'],os.getcwd(),dict(os.environ));"
            "pathlib.Path('crash-child').write_text(str(s.process.pid));time.sleep(300)"
        )
        parent = subprocess.Popen([sys.executable, '-c', code], cwd=self.root,
                                  env=self.server._environment(), creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            child = psutil.Process(int(marker.read_text()))
            descendants = child.children(recursive=True)
            parent.kill()
            parent.wait(timeout=5)
            _, alive = psutil.wait_procs([child, *descendants], timeout=5)
            self.assertFalse(alive)
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=5)
            marker.unlink(missing_ok=True)

    @unittest.skipUnless(os.environ.get('REDECO_TEST_LONG') == '1', 'opt-in 125-second process check')
    def test_long_session_survives_120_seconds(self):
        s = self.start("import time;print('START');time.sleep(125);print('DONE')")
        self.until(s, lambda r: 'START' in r['output'])
        time.sleep(121)
        self.assertEqual(s.read()['status'], 'running')
        result = self.until(s, lambda r: r['status'] == 'exited', timeout=15)
        self.assertEqual(result['exit_code'], 0)
        self.assertIn('DONE', result['output'])

    def test_mcp_protocol(self):
        async def probe():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            def data(r):
                self.assertFalse(r.isError, str(r.content))
                return r.structuredContent or json.loads(r.content[0].text)
            params = StdioServerParameters(command=sys.executable,
                args=[str(Path(__file__).with_name('server.py'))], env=self.env)
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    names = {t.name for t in (await session.list_tools()).tools}
                    self.assertEqual(len(names), 12)
                    status = data(await session.call_tool('host_status', {}))
                    self.assertEqual(status['platform'], 'Windows')
                    result = data(await session.call_tool('run_command', {'argv': [sys.executable, '-c', 'print(42)'], 'cwd': str(self.root)}))
                    self.assertEqual(result['stdout'].strip(), '42')
                    started = data(await session.call_tool('start_process', {'argv': [sys.executable, '-u', '-c', "print('READY');print(input())"], 'cwd': str(self.root), 'interactive': True}))
                    sid = started['session_id']
                    data(await session.call_tool('write_process_input', {'session_id': sid, 'text': 'DUMMY_INPUT_NO_LOG\n'}))
                    offset = started['next_offset']
                    output = started['output']
                    for _ in range(40):
                        page = data(await session.call_tool('read_process_output', {'session_id': sid, 'offset': offset, 'wait_ms': 200}))
                        offset = page['next_offset']
                        output += page['output']
                        if page['status'] == 'exited':
                            break
                    self.assertEqual(page['exit_code'], 0)
                    self.assertIn('DUMMY_INPUT_NO_LOG', output)
                    self.assertNotIn('DUMMY_INPUT_NO_LOG', (self.root / 'audit.jsonl').read_text(encoding='utf-8'))
                    invalid = await session.call_tool('stop_process', {'session_id': 'unrelated-pid'})
                    self.assertTrue(invalid.isError)
        asyncio.run(probe())


if __name__ == '__main__':
    unittest.main()
