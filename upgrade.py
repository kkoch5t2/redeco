"""Upgrade an installed MCP while preserving its local account and development settings."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import subprocess

from mcp_config import DEV_ENV


def read_environment(text: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if not separator or key in result:
            raise ValueError('Invalid or duplicate environment setting')
        words = shlex.split(value)
        if len(words) != 1:
            raise ValueError('Expected one quoted environment value')
        result[key] = words[0]
    return result


def installer_arguments(values, allow_sudo=False, sudo_nopasswd=False, disable_sudo=False,
                        managed_nopasswd=False):
    args = ['--migrate']
    mode = values.get('REDECO_MODE', 'isolated')
    if mode not in {'user', 'isolated'}:
        raise ValueError('Unknown installed MCP mode')
    if mode == 'user':
        args += ['--user', values['USER']]
        for root in values['REDECO_ROOTS'].split(':'):
            args += ['--root', root]
        args += ['--path', values['PATH']]
        for key in sorted(DEV_ENV & values.keys()):
            args += ['--env', key + '=' + values[key]]
    use_sudo = not disable_sudo and (allow_sudo or values.get('REDECO_ALLOW_SUDO') == '1')
    use_nopasswd = not disable_sudo and (sudo_nopasswd or managed_nopasswd)
    if use_nopasswd and not use_sudo:
        raise ValueError('--sudo-nopasswd requires --allow-sudo')
    if use_sudo and mode != 'user':
        raise ValueError('sudo requires development-user mode')
    if use_sudo:
        args.append('--allow-sudo')
    if use_nopasswd:
        args.append('--sudo-nopasswd')
    return args


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--allow-sudo', action='store_true')
    choice.add_argument('--disable-sudo', action='store_true')
    parser.add_argument('--sudo-nopasswd', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run locally with sudo; existing configuration is root-readable only')
    if args.disable_sudo and args.sudo_nopasswd:
        parser.error('--disable-sudo conflicts with --sudo-nopasswd')
    try:
        values = read_environment(Path('/etc/redeco/mcp.env').read_text())
        user = values.get('USER', '')
        # Never use an unvalidated account name to construct a privileged file path.
        import re
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*[$]?', user):
            raise ValueError('Invalid installed account name')
        sudoers = Path('/etc/sudoers.d') / ('redeco-mcp-' + user)
        managed = (sudoers.is_file() and not sudoers.is_symlink()
                   and sudoers.read_text().startswith('# Managed by redeco --sudo-nopasswd\n'))
        options = installer_arguments(values, args.allow_sudo, args.sudo_nopasswd,
                                      args.disable_sudo, managed)
    except (OSError, KeyError, ValueError) as exc:
        parser.error(str(exc))
    subprocess.run(['bash', str(Path(__file__).with_name('install_isolated.sh')), *options], check=True)


if __name__ == '__main__':
    main()
