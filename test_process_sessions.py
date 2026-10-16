"""Real subprocess tests: PTYs, bounded output, lifetime, and descendant cleanup."""
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from process_sessions import BUFFER_BYTES, ProcessSessions, signal_session


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.manager = ProcessSessions()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.manager.close)

    def start(self, code, interactive=False):
        return self.manager.start([sys.executable, '-u', '-c', code], self.tmp.name,
                                  dict(os.environ), interactive)

    def until(self, session, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = session.read(wait_ms=50)
            if condition(result):
                return result
        self.fail(f'Process did not reach expected state: {result}')

    def test_live_input_and_controlling_terminal(self):
        session = self.start("import os; print('TTY',os.isatty(0)); print('CONTROL',os.tcgetpgrp(0)==os.getpgrp()); print('READY'); line=input(); print('ANSWER',line)", True)
        self.until(session, lambda r: 'READY' in r['output'])
        session.write('hello\n')
        result = self.until(session, lambda r: r['status'] == 'exited')
        self.assertEqual(result['exit_code'], 0)
        self.assertIn('TTY True', result['output'])
        self.assertIn('CONTROL True', result['output'])
        self.assertIn('ANSWER hello', result['output'])

    def test_pipe_input_and_incremental_output(self):
        session = self.start("print('READY'); print('RESULT',input())")
        first = self.until(session, lambda r: 'READY' in r['output'])
        session.write('value\n')
        self.until(session, lambda r: r['status'] == 'exited')
        second = session.read(first['next_offset'])
        self.assertEqual(second['output'], 'RESULT value\n')
        self.assertEqual(session.read(second['next_offset'])['output'], '')
        with self.assertRaises(ValueError):
            session.write('after exit')

    def test_bounded_output_and_unicode_paging(self):
        session = self.start("print('あ'*150000)")
        self.until(session, lambda r: r['status'] == 'exited')
        self.assertLessEqual(len(session.buffer), BUFFER_BYTES)
        page = session.read()
        self.assertTrue(page['truncated'])
        self.assertGreater(page['offset'], 0)
        offset = page['next_offset']
        count = 0
        while offset < page['output_end']:
            page = session.read(offset)
            self.assertNotIn('\ufffd', page['output'])
            self.assertGreater(page['next_offset'], offset)
            offset = page['next_offset']
            count += 1
        self.assertGreater(count, 1)

    def test_stop_includes_child_ignoring_term(self):
        marker = Path(self.tmp.name) / 'child'
        child = "import os,signal,time,pathlib; os.setpgid(0,0); signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path('child').write_text(str(os.getpid())); time.sleep(300)"
        session = self.start(f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(300)")
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(marker.exists())
        child_pid = int(marker.read_text())
        result = session.stop()
        self.assertEqual(result['status'], 'exited')
        status = Path(f'/proc/{child_pid}/stat')
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            pass
        else:
            try:
                state = status.read_text().rsplit(')', 1)[1].split()[0]
            except FileNotFoundError:  # Reaped between kill(0) and reading procfs.
                pass
            else:
                self.assertEqual(state, 'Z')
        self.assertFalse(session.thread.is_alive())

    def test_pty_ctrl_c(self):
        session = self.start("import time; print('READY'); time.sleep(300)", True)
        self.until(session, lambda r: 'READY' in r['output'])
        session.write('\x03')
        result = self.until(session, lambda r: r['status'] == 'exited')
        self.assertNotEqual(result['exit_code'], 0)

    def test_independent_sessions_unknown_ids_and_invalid_offsets(self):
        first = self.start("print('FIRST')")
        second = self.start("print('SECOND')")
        self.until(first, lambda r: r['status'] == 'exited')
        self.until(second, lambda r: r['status'] == 'exited')
        self.assertNotIn('SECOND', first.read()['output'])
        self.assertEqual(len(self.manager.listing()), 2)
        with self.assertRaises(ValueError):
            self.manager.get('unrelated-pid')
        for offset in (-1, 999999):
            with self.assertRaises(ValueError):
                first.read(offset)
        self.assertEqual(first.stop()['status'], 'exited')

    def test_concurrency_limit_and_completed_session_eviction(self):
        with patch('process_sessions.MAX_ACTIVE', 2), patch('process_sessions.MAX_RETAINED', 3):
            first = self.start('import time; time.sleep(300)')
            second = self.start('import time; time.sleep(300)')
            with self.assertRaises(ValueError):
                self.start('print("must not start")')
            first.stop(); second.stop()
            third = self.start('print("third")')
            self.until(third, lambda r: r['status'] == 'exited')
            fourth = self.start('print("fourth")')
            self.until(fourth, lambda r: r['status'] == 'exited')
            self.assertEqual(len(self.manager.sessions), 3)
            with self.assertRaises(ValueError):
                self.manager.get(first.id)

    def test_elevated_child_signals_require_opt_in(self):
        entry = SimpleNamespace(name='42', stat=lambda: SimpleNamespace(st_uid=0))
        with patch('process_sessions.Path.iterdir', return_value=[entry]), \
             patch('process_sessions.os.getsid', return_value=42), \
             patch('process_sessions.os.getpgid', return_value=42), \
             patch('process_sessions.os.geteuid', return_value=1000), \
             patch('process_sessions.os.killpg', side_effect=PermissionError), \
             patch('process_sessions.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
            with self.assertRaises(PermissionError):
                signal_session(SimpleNamespace(pid=42), signal.SIGTERM)
            run.assert_not_called()
            signal_session(SimpleNamespace(pid=42), signal.SIGTERM, allow_sudo=True)
            self.assertEqual(run.call_args.args[0], ['sudo', '-n', '/bin/kill', '-15', '--', '-42'])


if __name__ == '__main__':
    unittest.main()
