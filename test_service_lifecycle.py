"""Exercise service startup with fake commands; never change real services."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


MOCKS = r'''
mcp_calls=0
ready_calls=0
systemctl() { printf 'systemctl %s\n' "$*" >> "$EVENT_LOG"; }
sleep() { :; }
timeout() {
  printf 'check %s\n' "$*" >> "$EVENT_LOG"
  if [[ "$*" == *--skip-tunnel-check* ]]; then
    mcp_calls=$((mcp_calls + 1))
    [[ "$mcp_calls" -gt "$MCP_FAILURES" ]]
  else
    return "$BOUNDARY_CODE"
  fi
}
curl() {
  printf 'curl %s\n' "$*" >> "$EVENT_LOG"
  if [[ "$*" == *8770/mcp* ]]; then
    printf '%s' "$LISTENER_CODE"
  else
    ready_calls=$((ready_calls + 1))
    [[ "$ready_calls" -gt "$TUNNEL_FAILURES" ]]
  fi
}
'''


class LifecycleTests(unittest.TestCase):
    def run_mock(self, action=None, **settings):
        action = action or 'redeco_restart_services /fake/python /fake/check.py user developer true'
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'events'
            log.touch()
            env = dict(os.environ, EVENT_LOG=str(log), MCP_FAILURES='0',
                       TUNNEL_FAILURES='0', BOUNDARY_CODE='0', LISTENER_CODE='406')
            env.update({key: str(value) for key, value in settings.items()})
            helper = Path(__file__).with_name('service_lifecycle.sh')
            result = subprocess.run(
                ['bash', '-eu', '-c', f'source {shlex.quote(str(helper))}\n{MOCKS}\n{action}'],
                env=env, text=True, capture_output=True, timeout=5)
            return result, log.read_text().splitlines()

    def test_mcp_ready_before_tunnel_starts(self):
        result, events = self.run_mock(MCP_FAILURES=2, TUNNEL_FAILURES=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        start = events.index('systemctl start redeco-tunnel-isolated.service')
        stop = events.index('systemctl stop redeco-tunnel-isolated.service')
        restart = events.index('systemctl restart redeco-mcp-isolated.service')
        local = [i for i, value in enumerate(events) if value.startswith('check ') and '--skip-tunnel-check' in value]
        boundary = [i for i, value in enumerate(events) if value.startswith('check ') and '--skip-tunnel-check' not in value]
        self.assertLess(stop, restart)
        self.assertEqual(len(local), 3)
        self.assertTrue(all(restart < i < start for i in local))
        self.assertEqual(len(boundary), 1)
        self.assertGreater(boundary[0], start)

    def test_mcp_failure_does_not_start_tunnel(self):
        result, events = self.run_mock(MCP_FAILURES=100)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('systemctl start redeco-tunnel-isolated.service', events)
        self.assertEqual(sum(value.startswith('check ') for value in events), 15)

    def test_previously_stopped_tunnel_stays_stopped(self):
        result, events = self.run_mock(
            'redeco_restart_services /fake/python /fake/check.py isolated redeco false')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any('tunnel-isolated.service' in value for value in events))
        self.assertEqual(sum(value.startswith('check ') for value in events), 1)

    def test_tunnel_failure_is_not_reported_as_success(self):
        result, events = self.run_mock(TUNNEL_FAILURES=100)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sum('8780/readyz' in value for value in events), 20)
        self.assertFalse(any(value.startswith('check ') and '--skip-tunnel-check' not in value for value in events))

    def test_key_boundary_failure_is_not_reported_as_success(self):
        result, events = self.run_mock(BOUNDARY_CODE=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sum(value.startswith('check ') and '--skip-tunnel-check' not in value for value in events), 15)

    def test_rollback_waits_for_streamable_http_listener(self):
        result, events = self.run_mock('redeco_wait_mcp_listener')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(events), 1)
        result, events = self.run_mock('redeco_wait_mcp_listener', LISTENER_CODE=200)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(events), 20)


if __name__ == '__main__':
    unittest.main()
