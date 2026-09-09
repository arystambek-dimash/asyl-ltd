import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class CollectorLifecycleTests(unittest.TestCase):
    def run_install(self, action, busy=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(ROOT / 'deploy/weighbridge', root / 'deploy/weighbridge')
            (root / '.env').touch()
            binary = root / 'bin'
            binary.mkdir()
            docker = binary / 'docker'
            docker.write_text('''#!/usr/bin/env python3
import os,sys
args=sys.argv[1:]
with open(os.environ['COMMAND_LOG'],'a') as f:f.write(' '.join(args)+'\\n')
if args[:2]==['ps','-q']:print('collector-current')
if args[:2]==['exec','-i']:
 sys.stdin.read()
 sys.exit(int(os.environ['BUSY']))
if args[-3:]==['ps','-q','backend']:print('backend-current')
if args[:1]==['inspect']:print('ghcr.io/example/backend@sha256:'+'a'*64)
''')
            docker.chmod(0o755)
            log = root / 'commands.log'
            env = {**os.environ, 'PATH':str(binary)+os.pathsep+os.environ['PATH'],
                   'APP_DIR':str(root), 'COMMAND_LOG':str(log), 'BUSY':str(int(busy))}
            env.pop('WEIGHBRIDGE_IMAGE_REF', None)
            result = subprocess.run(['sh', str(root / 'deploy/weighbridge/install.sh'), action],
                                    env=env, capture_output=True, text=True)
            return result, log.read_text()

    def test_application_deploy_keeps_running_collector_unchanged(self):
        result, commands = self.run_install('prepare')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('python -m weighbridge.healthcheck', commands)
        self.assertNotIn(' up ', commands)
        self.assertNotIn('stop', commands)

    def test_explicit_upgrade_defers_without_recreating_busy_collector(self):
        result, commands = self.run_install('upgrade', busy=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(' up ', commands)
        self.assertNotIn('stop', commands)

    def test_explicit_empty_scale_upgrade_retains_outbox_and_pins_running_release(self):
        result, commands = self.run_install('upgrade')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('inspect --format {{.Config.Image}} backend-current', commands)
        self.assertIn('--force-recreate', commands)
        self.assertNotIn('down', commands)
        self.assertNotIn('volume rm', commands)
