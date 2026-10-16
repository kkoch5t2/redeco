"""Bounded command output and timeout behavior against real subprocesses."""
import importlib
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


class CommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'REDECO_ROOTS': cls.root.name,
                                    'REDECO_AUDIT': str(Path(cls.root.name) / 'audit.jsonl'),
                                    'REDECO_MODE': 'user', 'REDECO_ALLOW_SUDO': '0'}):
            cls.server = importlib.import_module('server')

    @classmethod
    def tearDownClass(cls):
        cls.server.SESSIONS.close()
        cls.root.cleanup()

    def test_large_stdout_and_stderr_are_capped(self):
        result = self.server._exec([sys.executable, '-c',
                                   "import os; os.write(1,b'x'*2000000); os.write(2,b'y'*2000000)"],
                                  Path(self.root.name))
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['stdout'], 'x' * self.server.MAX_OUTPUT)
        self.assertEqual(result['stderr'], 'y' * self.server.MAX_OUTPUT)
        self.assertTrue(result['stdout_truncated'])
        self.assertTrue(result['stderr_truncated'])

    def test_detached_writer_cannot_block_timeout_return(self):
        marker = Path(self.root.name) / 'detached-child'
        code = "import subprocess,sys,pathlib,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True); pathlib.Path('detached-child').write_text(str(p.pid)); time.sleep(30)"
        start = time.monotonic()
        try:
            result = self.server._exec([sys.executable, '-c', code], Path(self.root.name), 1)
            self.assertLess(time.monotonic() - start, 5)
            self.assertIsNone(result['exit_code'])
            self.assertIn('Timed out', result['stderr'])
        finally:
            if marker.exists():
                pid = int(marker.read_text())
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


if __name__ == '__main__':
    unittest.main()
