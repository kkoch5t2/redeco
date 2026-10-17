"""Windows process sessions, contained in kill-on-close Job Objects.

The helper waits for a gate byte before launching user code. Assigning it to the
job before opening that gate avoids the usual Popen/AssignProcess race.
"""
from __future__ import annotations

import atexit
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

BUFFER_BYTES = 256 * 1024
MAX_ACTIVE = 16
MAX_RETAINED = 64


class BasicLimits(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64),
                ('PerJobUserTimeLimit', ctypes.c_int64), ('LimitFlags', wintypes.DWORD),
                ('MinimumWorkingSetSize', ctypes.c_size_t), ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', wintypes.DWORD), ('Affinity', ctypes.c_size_t),
                ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]


class IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', BasicLimits), ('IoInfo', IOCounters),
                ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]


class Accounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in
                ('TotalUserTime', 'TotalKernelTime', 'ThisPeriodTotalUserTime', 'ThisPeriodTotalKernelTime')]
    _fields_ += [(name, wintypes.DWORD) for name in
                 ('TotalPageFaultCount', 'TotalProcesses', 'ActiveProcesses', 'TotalTerminatedProcesses')]


def kernel():
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreateJobObjectW': ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        'SetInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        'QueryInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
        'AssignProcessToJobObject': ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        'TerminateJobObject': ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
    }
    for name, (args, result) in signatures.items():
        getattr(api, name).argtypes = args
        getattr(api, name).restype = result
    return api


def check(value):
    if not value:
        raise ctypes.WinError(ctypes.get_last_error())
    return value


class Job:
    def __init__(self):
        self.api = kernel()
        self.handle = check(self.api.CreateJobObjectW(None, None))
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        try:
            check(self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        except BaseException:
            self.close()
            raise

    def assign(self, process):
        check(self.api.AssignProcessToJobObject(self.handle, int(process._handle)))

    def active(self):
        info = Accounting()
        check(self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None))
        return info.ActiveProcesses

    def terminate(self):
        check(self.api.TerminateJobObject(self.handle, 1))

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class ProcessSession:
    def __init__(self, argv, cwd, env, interactive=False):
        self.id = uuid.uuid4().hex
        self.interactive = interactive
        self.condition = threading.Condition()
        self.input_lock = threading.Lock()
        self.job_lock = threading.Lock()
        self.buffer = bytearray()
        self.base = 0
        self.finished = False
        self.closed = False
        self.input_pending = None
        self.job = Job()
        try:
            self.process = subprocess.Popen(
                [sys.executable, '-u', str(Path(__file__).resolve()), '--child', json.dumps(argv)],
                cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW, bufsize=0)
            self.job.assign(self.process)
            self.process.stdin.write(b'\x00')
            self.thread = threading.Thread(target=self._collect, daemon=True)
            self.thread.start()
        except BaseException:
            self.job.close()
            if hasattr(self, 'process'):
                if self.process.poll() is None:
                    self.process.kill()
                self.process.wait(timeout=5)
                self.process.stdin.close()
                self.process.stdout.close()
            raise

    def _collect(self):
        try:
            while chunk := self.process.stdout.read(8192):
                with self.condition:
                    self.buffer.extend(chunk)
                    excess = max(0, len(self.buffer) - BUFFER_BYTES)
                    if excess:
                        del self.buffer[:excess]
                        self.base += excess
                        while self.buffer and self.buffer[0] & 0xc0 == 0x80:
                            del self.buffer[:1]
                            self.base += 1
                    self.condition.notify_all()
            self.process.wait()
            # A parent can exit after starting children with closed stdio.
            while True:
                with self.job_lock:
                    if self.job.active() == 0:
                        self.job.close()
                        break
                time.sleep(.05)
        finally:
            with self.condition:
                self.finished = True
                self.condition.notify_all()
            self.process.stdout.close()
            with self.input_lock:
                self.closed = True
                self.process.stdin.close()

    def read(self, offset=0, wait_ms=0, max_bytes=12000):
        if offset < 0 or not 0 <= wait_ms <= 3000 or not 1 <= max_bytes <= 12000:
            raise ValueError('Invalid offset, wait_ms (0..3000), or max_bytes (1..12000)')
        with self.condition:
            if offset > self.base + len(self.buffer):
                raise ValueError('Offset is beyond available output')
            deadline = time.monotonic() + wait_ms / 1000
            while True:
                start = max(offset, self.base)
                data = bytes(self.buffer[start - self.base:start - self.base + max_bytes])
                try:
                    data.decode('utf-8')
                except UnicodeDecodeError as exc:
                    if exc.reason == 'unexpected end of data' and (
                            not self.finished or start + len(data) < self.base + len(self.buffer)):
                        data = data[:exc.start]
                remaining = deadline - time.monotonic()
                if data or self.finished or remaining <= 0:
                    break
                self.condition.wait(remaining)
            return {'session_id': self.id, 'pid': self.process.pid,
                    'status': 'exited' if self.finished else 'running',
                    'exit_code': self.process.returncode if self.finished else None,
                    'interactive': self.interactive, 'terminal': 'pipe',
                    'output': data.decode('utf-8', 'replace'), 'offset': start,
                    'next_offset': start + len(data), 'output_end': self.base + len(self.buffer),
                    'truncated': offset < self.base}

    def write(self, text):
        raw = text.encode('utf-8')
        if len(raw) > 16000:
            raise ValueError('Input exceeds 16000 bytes')
        # A pipe write can block on Windows. At most one writer is allowed;
        # timeout means delivery is uncertain, so retries must not duplicate it.
        with self.input_lock:
            if self.closed or self.finished or self.process.poll() is not None:
                raise ValueError('Process has exited')
            if self.input_pending and self.input_pending.is_alive():
                raise ValueError('Previous input is still pending; do not resend')
            outcome = {}

            def send():
                try:
                    sent = 0
                    while sent < len(raw):
                        count = self.process.stdin.write(raw[sent:])
                        if not count:
                            raise BrokenPipeError('Input pipe closed')
                        sent += count
                    outcome['count'] = sent
                except OSError as exc:
                    outcome['error'] = exc

            self.input_pending = threading.Thread(target=send, daemon=True)
            self.input_pending.start()
            self.input_pending.join(1)
            if self.input_pending.is_alive():
                raise ValueError('Input blocked; delivery uncertain, do not resend. Stop session if needed')
            if 'error' in outcome:
                raise ValueError('Input pipe closed') from outcome['error']
            return outcome['count']

    def stop(self):
        with self.job_lock:
            if self.job.handle:
                self.job.terminate()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError('Job terminated but output collection has not finished')
        return self.read()


class ProcessSessions:
    def __init__(self, allow_sudo=False):
        self.sessions = {}
        self.lock = threading.Lock()
        atexit.register(self.close)

    def start(self, argv, cwd, env, interactive=False):
        with self.lock:
            if sum(not s.finished for s in self.sessions.values()) >= MAX_ACTIVE:
                raise ValueError('Too many running sessions; stop an existing session first')
            if len(self.sessions) >= MAX_RETAINED:
                oldest = next((key for key, s in self.sessions.items() if s.finished), None)
                if oldest is not None:
                    del self.sessions[oldest]
            session = ProcessSession(argv, cwd, env, interactive)
            self.sessions[session.id] = session
            return session

    def get(self, session_id):
        with self.lock:
            if session_id not in self.sessions:
                raise ValueError('Unknown session (sessions expire when the MCP server restarts)')
            return self.sessions[session_id]

    def listing(self):
        with self.lock:
            return [{k: v for k, v in s.read().items() if k in
                     {'session_id', 'pid', 'status', 'exit_code', 'interactive', 'terminal'}}
                    for s in self.sessions.values()]

    def close(self):
        with self.lock:
            for session in self.sessions.values():
                if not session.finished:
                    session.stop()


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != '--child':
        raise SystemExit('Internal Windows child entry point')
    # Binary read: do not buffer or consume the user's following stdin bytes.
    import msvcrt
    msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    if os.read(sys.stdin.fileno(), 1) != b'\x00':
        raise SystemExit(1)
    try:
        child = subprocess.Popen(json.loads(sys.argv[2]))
        raise SystemExit(child.wait())
    except OSError as exc:
        print(f'Cannot start command: {exc}', file=sys.stderr, flush=True)
        raise SystemExit(127)
