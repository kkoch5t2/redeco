"""Validate account separation and systemd configuration for both modes."""
import tempfile
import unittest
from pathlib import Path
from mcp_config import render


class ServiceConfigTest(unittest.TestCase):
    def build(self, **kwargs):
        args = dict(mode='user', user='developer', group='developers', home='/home/developer', roots=['/home/developer/Sites'])
        args.update(kwargs)
        return render(**args)

    def test_user_mode_uses_home_and_normal_filesystem(self):
        unit, env = self.build()
        self.assertIn('User=developer\nGroup=developers\n', unit)
        self.assertIn('ProtectHome=false', unit)
        self.assertIn('ProtectSystem=false', unit)
        self.assertIn('PrivateTmp=false', unit)
        self.assertNotIn('ReadWritePaths=', unit)
        self.assertIn('NoNewPrivileges=true', unit)
        self.assertIn('HOME="/home/developer"', env)
        self.assertIn('REDECO_MODE="user"', env)
        self.assertIn('/home/developer/.local/bin:', env)
        self.assertIn('REDECO_AUDIT="/var/lib/redeco-mcp/developer/audit.jsonl"', env)
        self.assertNotIn('tunnel.env', unit + env)
        self.assertNotIn('CONTROL_PLANE_API_KEY', unit + env)

    def test_isolated_mode_retains_original_boundary(self):
        unit, env = self.build(mode='isolated', user='redeco', group='redeco', home='/var/lib/redeco', roots=['/var/lib/redeco/workspaces'])
        for setting in ('ProtectHome=true', 'ProtectSystem=strict', 'ReadWritePaths=/var/lib/redeco', 'PrivateTmp=true'):
            self.assertIn(setting, unit)
        self.assertIn('REDECO_MODE="isolated"', env)

    def test_reject_root_tunnel_and_invalid_account_names(self):
        for user in ('root', 'redeco-tunnel', 'redeco', 'developer\nUser=root', '../developer'):
            with self.subTest(user=user), self.assertRaises(ValueError):
                self.build(user=user)

    def test_reject_invalid_root_and_relative_path(self):
        for root in ('/', 'relative', '/work:other', '/work\nUser=root'):
            with self.subTest(root=root), self.assertRaises(ValueError):
                self.build(roots=[root])
        for path in ('/usr/bin:', '/usr/bin:.', 'bin:/usr/bin'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.build(path=path)

    def test_environment_quoting_does_not_create_new_settings(self):
        unit, env = self.build(roots=['/srv/work space'], path='/usr/bin:/opt/dev tools', extra={'JAVA_HOME': '/opt/java "special"', 'DISPLAY': ':0'})
        self.assertIn('REDECO_ROOTS="/srv/work space"\n', env)
        self.assertIn('JAVA_HOME="/opt/java \\"special\\""\n', env)
        for extra in ({'CONTROL_PLANE_API_KEY': 'dummy'}, {'GITHUB_TOKEN': 'dummy'}, {'JAVA_HOME': '/jdk\nINJECT=yes'}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.build(extra=extra)

    def test_only_loopback_port_is_configured(self):
        _, env = self.build()
        self.assertIn('REDECO_PORT="8770"', env)
        source = Path('server.py').read_text()
        self.assertIn('host="127.0.0.1"', source)

    def test_sudo_is_opt_in_and_user_mode_only(self):
        unit, env = self.build()
        self.assertIn('REDECO_ALLOW_SUDO="0"', env)
        unit, env = self.build(allow_sudo=True)
        self.assertIn('REDECO_ALLOW_SUDO="1"', env)
        self.assertIn('NoNewPrivileges=false', unit)
        self.assertIn('RestrictSUIDSGID=false', unit)
        self.assertNotIn('CapabilityBoundingSet=', unit)
        self.assertIn('KillMode=control-group', unit)
        self.assertNotIn('NOPASSWD', unit + env)
        with self.assertRaises(ValueError):
            self.build(mode='isolated', user='redeco', allow_sudo=True)


if __name__ == '__main__':
    unittest.main()
