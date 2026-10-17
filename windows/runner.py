"""Per-user Windows MCP supervisor. No tunnel credentials belong in this config."""
from __future__ import annotations
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import msvcrt
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from windows_process_sessions import spawn_in_job
from windows_runtime import environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    state = args.config.resolve().parent
    with (state / 'runner.lock').open('a+b') as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit('Redeco supervisor is already running')
        logger = logging.getLogger('redeco')
        logger.setLevel(logging.INFO)
        handler = RotatingFileHandler(state / 'server.log', maxBytes=1024 * 1024, backupCount=3, encoding='utf-8')
        logger.addHandler(handler)
        while True:
            config = json.loads(args.config.read_text(encoding='utf-8-sig'))
            roots = config['roots']
            if not roots or not all(Path(p).is_dir() for p in roots):
                raise ValueError('Configured roots must exist')
            repo = Path(config['repository']).resolve()
            env = environment()
            env.update(REDECO_ROOTS=os.pathsep.join(roots), REDECO_MODE='user',
                       REDECO_TRANSPORT='streamable-http', REDECO_PORT=str(config['port']),
                       REDECO_ALLOW_ADMIN='1' if config.get('allow_admin') else '0',
                       REDECO_AUDIT=str(state / 'audit.jsonl'))
            python = str(Path(sys.executable).with_name('python.exe'))
            job = None
            child = None
            try:
                # Job handles close automatically if Task Scheduler kills this supervisor.
                job, child = spawn_in_job([python, '-u', str(repo / 'server.py')], repo, env)
                child.stdin.close()
                for line in child.stdout:
                    logger.info(line.decode('utf-8', 'replace').rstrip())
                logger.warning('MCP exited: %s; restarting in 5s', child.wait())
            finally:
                if job is not None:
                    job.close()
                if child is not None:
                    child.wait(timeout=5)
                    child.stdout.close()
                    if not child.stdin.closed:
                        child.stdin.close()
            time.sleep(5)


if __name__ == '__main__':
    main()
