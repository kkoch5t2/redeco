"""Bounded, in-memory process sessions with optional Linux controlling terminals."""
from __future__ import annotations

import atexit
import errno
import fcntl
import json
import os
import pty
import select
import signal
import subprocess
import sys
import termios
import threading
import time
import uuid
from pathlib import Path

BUFFER_BYTES = 256 * 1024
MAX_ACTIVE = 16
MAX_RETAINED = 64


def signal_group(process, sig):
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


def signal_session(process, sig, allow_sudo=False):
    """Also cover foreground job groups created by an interactive shell."""
    groups = {process.pid}
    privileged = set()
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
            if os.getsid(pid) == process.pid:
                group = os.getpgid(pid)
                groups.add(group)
                if entry.stat().st_uid != os.geteuid():
                    privileged.add(group)
        except (OSError, ValueError):
            continue
    for group in groups:
        try:
            os.killpg(group, sig)
        except ProcessLookupError:
            continue
        except PermissionError:
            privileged.add(group)
        if group in privileged:
            if not allow_sudo:
                raise PermissionError('Cannot stop elevated children without sudo enabled')
            result = subprocess.run(['sudo', '-n', '/bin/kill', '-' + str(int(sig)), '--', '-' + str(group)],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=2, check=False)
            if result.returncode:
                # A group may have exited between the unprivileged and privileged signals.
                try:
                    os.killpg(group, 0)
                except ProcessLookupError:
                    continue
                except PermissionError:
                    pass
                raise PermissionError('Stopping elevated children requires working noninteractive sudo')


class ProcessSession:
    def __init__(self, argv, cwd, env, interactive, allow_sudo=False):
        self.id = uuid.uuid4().hex
        self.interactive = interactive
        self.allow_sudo = allow_sudo
        self.condition = threading.Condition()
        self.input_lock = threading.Lock()
        self.buffer = bytearray()
        self.base = 0
        self.finished = False
        self.created = time.time()
        self.closed = False
        self.master = None
        slave = None
        try:
            if interactive:
                self.master, slave = pty.openpty()
                attributes = termios.tcgetattr(slave)
                attributes[3] &= ~(termios.ECHO | termios.ECHONL)
                termios.tcsetattr(slave, termios.TCSANOW, attributes)
                command = [sys.executable, str(Path(__file__).resolve()), '--pty-child', json.dumps(argv)]
                self.process = subprocess.Popen(command, cwd=cwd, env=env, stdin=slave,
                                                stdout=slave, stderr=slave, start_new_session=True)
                self.fd = self.master
            else:
                self.process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                start_new_session=True)
                self.fd = self.process.stdout.fileno()
            os.set_blocking(self.fd, False)
        except BaseException:
            if hasattr(self, 'process'):
                signal_group(self.process, signal.SIGKILL)
                self.process.wait()
            if self.master is not None:
                os.close(self.master)
            raise
        finally:
            if slave is not None:
                os.close(slave)
        self.thread = threading.Thread(target=self._collect, daemon=True)
        self.thread.start()

    def _collect(self):
        try:
            while True:
                if not select.select([self.fd], [], [], .1)[0]:
                    continue
                try:
                    chunk = os.read(self.fd, 8192)
                except BlockingIOError:
                    continue
                except OSError as exc:
                    if exc.errno == errno.EIO:  # PTY slave has closed.
                        break
                    raise
                if not chunk:
                    break
                with self.condition:
                    self.buffer.extend(chunk)
                    excess = max(0, len(self.buffer) - BUFFER_BYTES)
                    if excess:
                        del self.buffer[:excess]
                        self.base += excess
                    self.condition.notify_all()
            self.process.wait()
        finally:
            with self.condition:
                self.finished = True
                self.condition.notify_all()
            self.close_descriptors()

    def close_descriptors(self):
        with self.input_lock:
            if self.closed:
                return
            self.closed = True
            if self.master is not None:
                os.close(self.master)
            else:
                self.process.stdin.close()
                self.process.stdout.close()

    def read(self, offset=0, wait_ms=0, max_bytes=12000):
        if offset < 0 or not 0 <= wait_ms <= 3000 or not 1 <= max_bytes <= 12000:
            raise ValueError('Invalid offset, wait_ms (0..3000), or max_bytes (1..12000)')
        with self.condition:
            end = self.base + len(self.buffer)
            if offset > end:
                raise ValueError('Offset is beyond available output')
            if offset == end and not self.finished:
                self.condition.wait_for(lambda: self.finished or self.base + len(self.buffer) > offset,
                                        timeout=wait_ms / 1000)
            start = max(offset, self.base)
            data = bytes(self.buffer[start - self.base:start - self.base + max_bytes])
            # Keep UTF-8 characters together across normal page boundaries.
            if start + len(data) < self.base + len(self.buffer):
                for count in range(1, min(4, len(data) + 1)):
                    try:
                        data.decode('utf-8')
                        break
                    except UnicodeDecodeError as exc:
                        if exc.reason == 'unexpected end of data' and exc.start > 0:
                            data = data[:exc.start]
                        else:
                            break
            return {'session_id': self.id, 'pid': self.process.pid,
                    'status': 'exited' if self.finished else 'running',
                    'exit_code': self.process.returncode if self.finished else None,
                    'interactive': self.interactive, 'output': data.decode('utf-8', 'replace'),
                    'offset': start, 'next_offset': start + len(data),
                    'output_end': self.base + len(self.buffer), 'truncated': offset < self.base}

    def write(self, text):
        raw = text.encode('utf-8')
        if len(raw) > 16000:
            raise ValueError('Input exceeds 16000 bytes')
        with self.input_lock:
            if self.closed or self.process.poll() is not None:
                raise ValueError('Process has exited')
            fd = self.master if self.interactive else self.process.stdin.fileno()
            os.set_blocking(fd, False)
            deadline = time.monotonic() + 1
            sent = 0
            while sent < len(raw):
                try:
                    sent += os.write(fd, raw[sent:])
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ValueError(f'Input blocked after {sent} bytes; do not blindly resend')
                    select.select([], [fd], [], .05)
            return sent

    def stop(self):
        if self.finished:
            return self.read()
        # Signal the whole group, including children after their parent has exited.
        signal_session(self.process, signal.SIGTERM, self.allow_sudo)
        self.thread.join(timeout=1)
        signal_session(self.process, signal.SIGKILL, self.allow_sudo)
        self.thread.join(timeout=1)
        return self.read()


class ProcessSessions:
    def __init__(self, allow_sudo=False):
        self.sessions = {}
        self.lock = threading.Lock()
        self.allow_sudo = allow_sudo
        atexit.register(self.close)

    def start(self, argv, cwd, env, interactive=False):
        with self.lock:
            active = sum(not s.finished for s in self.sessions.values())
            if active >= MAX_ACTIVE:
                raise ValueError('Too many running sessions; stop an existing session first')
            if len(self.sessions) >= MAX_RETAINED:
                oldest = next((key for key, s in self.sessions.items() if s.finished), None)
                if oldest is not None:
                    del self.sessions[oldest]
            session = ProcessSession(argv, cwd, env, interactive, self.allow_sudo)
            self.sessions[session.id] = session
            return session

    def get(self, session_id):
        with self.lock:
            if session_id not in self.sessions:
                raise ValueError('Unknown session (sessions expire when the MCP server restarts)')
            return self.sessions[session_id]

    def listing(self):
        with self.lock:
            return [{key: value for key, value in s.read().items()
                     if key in {'session_id', 'pid', 'status', 'exit_code', 'interactive'}}
                    for s in self.sessions.values()]

    def close(self):
        with self.lock:
            for session in self.sessions.values():
                if not session.finished:
                    session.stop()


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != '--pty-child':
        raise SystemExit('Internal PTY child entry point')
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    command = json.loads(sys.argv[2])
    os.execvpe(command[0], command, os.environ)
