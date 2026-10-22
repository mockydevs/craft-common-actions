"""Exercise opt-in runtime metadata without executing archive payloads."""
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class LibdbusPackagingTests(unittest.TestCase):
    def test_release_opt_in_reaches_arch_and_appimage_tools(self):
        release = yaml.load((ROOT / '.github/workflows/release.yml').read_text(), Loader=yaml.BaseLoader)
        option = release['on']['workflow_call']['inputs']['needs-libdbus']
        self.assertEqual((option['type'], option['default']), ('boolean', 'false'))
        arch = next(s for s in release['jobs']['arch']['steps'] if s.get('uses', '').startswith('storytold/craft-common-actions/actions/arch-package@'))
        self.assertEqual(arch['with']['needs-libdbus'], '${{ inputs.needs-libdbus }}')
        tools = next(s for s in release['jobs']['linux']['steps'] if s.get('name') == 'Install private libdbus AppImage build tools')
        self.assertEqual(tools['if'], 'inputs.needs-libdbus')
        self.assertIn('meson ninja-build', tools['run'])

    def test_container_and_both_user_switches_preserve_opt_in(self):
        action = yaml.safe_load((ROOT / 'actions/arch-package/action.yml').read_text())
        step = action['runs']['steps'][0]
        self.assertEqual(step['env']['NEEDS_LIBDBUS'], '${{ inputs.needs-libdbus }}')
        self.assertIn('-e NEEDS_LIBDBUS="$NEEDS_LIBDBUS"', step['run'])
        entry = (ROOT / 'actions/arch-package/docker-entrypoint.sh').read_text()
        self.assertEqual(entry.count('NEEDS_LIBDBUS="${NEEDS_LIBDBUS:-false}"'), 2)

    def arch_metadata(self, setting):
        with tempfile.TemporaryDirectory(prefix='craft-dbus-package-') as directory:
            root = Path(directory)
            stage = root / 'fixture'
            (stage / 'bin').mkdir(parents=True)
            # A sentinel payload must only be copied; never run by the packager.
            (stage / 'bin/fixture').write_text('#!/bin/sh\nexit 99\n')
            archive = root / 'fixture-1.0.0-linux-x86_64.tar.gz'
            with tarfile.open(archive, 'w:gz') as bundle:
                bundle.add(stage, arcname='fixture')
            tools = root / 'tools'
            tools.mkdir()
            for name, script in {
                'bsdtar': 'exit 0', 'zstd': 'exit 0',
                'makepkg': '. ./PKGBUILD\nprintf "%s\\n" "${depends[@]}" > "$DEPENDENCY_LOG"\ntouch fixture.pkg.tar.zst',
            }.items():
                tool = tools / name
                tool.write_text('#!/bin/bash\nset -eu\n' + script + '\n')
                tool.chmod(0o755)
            log = root / 'dependencies'
            env = {**os.environ, 'PATH': str(tools) + ':' + os.environ['PATH'],
                   'DEPENDENCY_LOG': str(log), 'SOURCE_DATE_EPOCH': '1'}
            env.pop('NEEDS_LIBDBUS', None)
            if setting is not None:
                env['NEEDS_LIBDBUS'] = setting
            result = subprocess.run(['bash', str(ROOT / 'actions/arch-package/arch-package.sh'),
                                     str(archive), 'x86_64', str(root / 'out')],
                                    env=env, capture_output=True, text=True, timeout=20)
            return result, log.read_text().splitlines() if log.exists() else []

    def test_only_opted_in_apps_require_dbus(self):
        for setting in (None, 'false', 'true'):
            with self.subTest(setting=setting):
                result, dependencies = self.arch_metadata(setting)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual('dbus' in dependencies, setting == 'true')
                self.assertIn('glibc', dependencies)
                self.assertIn('gcc-libs', dependencies)

    def test_invalid_opt_in_is_rejected_before_packaging(self):
        for setting in ('', 'yes', 'true;exit 0', '$(exit 0)'):
            with self.subTest(setting=setting):
                result, dependencies = self.arch_metadata(setting)
                # Empty env has the documented default-false shell semantics.
                if not setting:
                    self.assertEqual(result.returncode, 0, result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('must be true or false', result.stderr)
                    self.assertEqual(dependencies, [])


if __name__ == '__main__':
    unittest.main()
