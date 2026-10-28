"""Windows host probes and bounded short commands; imported only on Windows."""
from __future__ import annotations
import ctypes
import getpass
import os
import platform
import subprocess
import threading
import time
from windows_process_sessions import spawn_in_job


def elevated():
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def environment():
    names = {'HOME', 'USERPROFILE', 'USERNAME', 'USERDOMAIN', 'HOMEDRIVE', 'HOMEPATH',
             'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'PATH', 'PATHEXT',
             'TEMP', 'TMP', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)',
             'PROGRAMDATA', 'JAVA_HOME', 'ANDROID_HOME', 'ANDROID_SDK_ROOT', 'GRADLE_USER_HOME',
             'NVM_HOME', 'NVM_SYMLINK'}
    result = {k: v for k, v in os.environ.items() if k.upper() in names}
    result.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    return result


def host_status():
    import psutil
    memory = psutil.virtual_memory()
    return {'host': platform.node(), 'platform': 'Windows', 'os_version': platform.version(),
            'uptime_seconds': int(time.time() - psutil.boot_time()),
            'cpu_percent': psutil.cpu_percent(interval=.1),
            'memory_bytes': {'total': memory.total, 'available': memory.available},
            'user': getpass.getuser(), 'elevated': elevated()}


def execute(argv, cwd, env, timeout, max_output):
    """Use a gated helper and a Job for short commands too; drain both pipes."""
    job = None
    process = None
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    truncated = {'stdout': False, 'stderr': False}
    threads = []
    timed_out = False

    def collect(name, stream):
        while chunk := stream.read(8192):
            room = max_output * 4 - len(buffers[name])
            buffers[name].extend(chunk[:room])
            truncated[name] |= len(chunk) > room

    try:
        job, process = spawn_in_job(argv, cwd, env, stderr=subprocess.PIPE)
        process.stdin.close()
        for name in buffers:
            thread = threading.Thread(target=collect, args=(name, getattr(process, name)), daemon=True)
            thread.start()
            threads.append(thread)
        deadline = time.monotonic() + timeout
        while process.poll() is None or job.active() or any(t.is_alive() for t in threads):
            if time.monotonic() >= deadline:
                timed_out = True
                job.terminate()
                # TerminateJobObject is asynchronous: wait for descendants, not
                # just the helper process, before reporting timeout cleanup.
                job.wait_empty(timeout=5)
                break
            time.sleep(.01)
        process.wait(timeout=5)
    finally:
        if job is not None:
            job.close()
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if not process.stdin.closed:
                process.stdin.close()
            for thread in threads:
                thread.join(timeout=5)
            process.stdout.close()
            process.stderr.close()
    decoded = {k: v.decode('utf-8', 'replace') for k, v in buffers.items()}
    result = {'exit_code': None if timed_out else process.returncode,
              'stdout': decoded['stdout'], 'stderr': decoded['stderr'],
              **{k + '_truncated': truncated[k] or len(decoded[k]) > max_output for k in buffers}}
    if timed_out:
        result['stderr'] = 'Timed out; process group stopped (Windows Job). Use start_process for long work.'
    return result
