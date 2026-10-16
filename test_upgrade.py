"""Upgrade options retain local development settings without executing environment text."""
import unittest
from upgrade import installer_arguments, read_environment


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.values = dict(REDECO_MODE='user', USER='developer', REDECO_ROOTS='/srv/work:/srv/other',
                           PATH='/opt/sdk/bin:/usr/bin', JAVA_HOME='/opt/java', REDECO_ALLOW_SUDO='0')

    def test_environment_is_parsed_without_expansion(self):
        values = read_environment('USER="developer"\nJAVA_HOME="/opt/a \\"b\\""\nVALUE="$(false)"\n')
        self.assertEqual(values['JAVA_HOME'], '/opt/a "b"')
        self.assertEqual(values['VALUE'], '$(false)')
        for text in ('USER=a\nUSER=b\n', 'BAD', 'USER="unterminated'):
            with self.assertRaises(ValueError):
                read_environment(text)

    def test_keeps_roots_path_and_development_environment(self):
        self.values['CONTROL_PLANE_API_KEY'] = 'dummy-never-forward'
        args = installer_arguments(self.values)
        self.assertEqual(args.count('--root'), 2)
        self.assertIn('/opt/sdk/bin:/usr/bin', args)
        self.assertIn('JAVA_HOME=/opt/java', args)
        self.assertNotIn('CONTROL_PLANE_API_KEY=dummy-never-forward', args)
        self.assertNotIn('--allow-sudo', args)

    def test_opt_in_preserve_and_revoke(self):
        args = installer_arguments(self.values, allow_sudo=True, sudo_nopasswd=True)
        self.assertIn('--allow-sudo', args)
        self.assertIn('--sudo-nopasswd', args)
        self.values['REDECO_ALLOW_SUDO'] = '1'
        self.assertIn('--sudo-nopasswd', installer_arguments(self.values, managed_nopasswd=True))
        args = installer_arguments(self.values, disable_sudo=True, managed_nopasswd=True)
        self.assertNotIn('--allow-sudo', args)
        self.assertNotIn('--sudo-nopasswd', args)
        with self.assertRaises(ValueError):
            installer_arguments(dict(self.values, REDECO_MODE='isolated'))


if __name__ == '__main__':
    unittest.main()
