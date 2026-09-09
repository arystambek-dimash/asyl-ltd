import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class CollectorLifecycleTests(unittest.TestCase):
    def run_install(self, action, busy=False, arrival_during_prep=False, video_failure=False, pending_writes=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(ROOT / 'deploy/weighbridge', root / 'deploy/weighbridge')
            (root / '.env').touch()
            binary = root / 'bin'
            binary.mkdir()
            docker = binary / 'docker'
            docker.write_text('''#!/usr/bin/env python3
import os,sys,time,types
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['COMMAND_LOG'],'a') as f:f.write(' '.join(args)+'\\n')
if args[:2]==['ps','-q']:print('collector-current')
if args[:2]==['exec','-i']:
 script=sys.stdin.read()
 if 'HTTPConnection' in script:
  with open(os.environ['COMMAND_LOG'],'a') as f:f.write('video-probe\\n')
  if os.environ['VIDEO_FAILURE']=='1':
   print('Weighbridge video unavailable: collector upgrade is degraded', file=sys.stderr)
   sys.exit(1)
 else:
  with open(os.environ['COMMAND_LOG'],'a') as f:f.write('clear-guard\\n')
  arriving=os.environ['ARRIVAL_DURING_PREP']=='1' and Path(os.environ['PREP_MARKER']).exists()
  heartbeat={'updated_at':time.time(), 'clear':not (os.environ['BUSY']=='1' or arriving),
             'armed':True, 'pending_writes':int(os.environ['PENDING_WRITES'])}
  outbox=types.ModuleType('weighbridge.outbox')
  outbox.Outbox=lambda _:types.SimpleNamespace(state=lambda _:heartbeat, counts=lambda:{'pending':0})
  sys.modules['weighbridge']=types.ModuleType('weighbridge')
  sys.modules['weighbridge.outbox']=outbox
  exec(compile(script, '<collector-upgrade-guard>', 'exec'), {})
if args[-1:] == ['chown app:app /var/lib/weighbridge']:
 if os.environ.get('BACKEND_IMAGE_REF') != 'ghcr.io/example/backend@sha256:'+'a'*64:
  sys.exit('Permission helper did not use the running backend image')
 Path(os.environ['PREP_MARKER']).touch()
if args[-3:]==['ps','-q','backend']:print('backend-current')
if args[:1]==['inspect']:print('ghcr.io/example/backend@sha256:'+'a'*64)
''')
            docker.chmod(0o755)
            log = root / 'commands.log'
            env = {**os.environ, 'PATH':str(binary)+os.pathsep+os.environ['PATH'],
                   'APP_DIR':str(root), 'COMMAND_LOG':str(log), 'BUSY':str(int(busy)),
                   'ARRIVAL_DURING_PREP':str(int(arrival_during_prep)),
                   'PREP_MARKER':str(root / 'prepared'), 'VIDEO_FAILURE':str(int(video_failure)),
                   'PENDING_WRITES':str(int(pending_writes))}
            env.pop('WEIGHBRIDGE_IMAGE_REF', None)
            env.pop('BACKEND_IMAGE_REF', None)
            result = subprocess.run(['sh', str(root / 'deploy/weighbridge/install.sh'), action],
                                    env=env, capture_output=True, text=True)
            return result, log.read_text()

    def test_application_deploy_keeps_running_collector_unchanged(self):
        result, commands = self.run_install('prepare')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('python -m weighbridge.healthcheck', commands)
        self.assertNotIn(' up ', commands)
        self.assertNotIn('stop', commands)
        self.assertNotIn('video-probe', commands)

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
        self.assertEqual(commands.count('clear-guard\n'), 2)
        self.assertLess(commands.rindex('clear-guard'), commands.index(' up '))
        self.assertGreater(commands.index('video-probe'), commands.index(' up '))
        self.assertNotIn('down', commands)
        self.assertNotIn('volume rm', commands)

    def test_new_arrival_during_preparation_prevents_replacement(self):
        result, commands = self.run_install('upgrade', arrival_during_prep=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('chown app:app /var/lib/weighbridge', commands)
        self.assertEqual(commands.count('clear-guard\n'), 2)
        self.assertNotIn(' up ', commands)
        self.assertNotIn('video-probe', commands)
        self.assertNotIn('activate_weighbridge_collector', commands)

    def test_empty_scale_with_pending_memory_writes_cannot_replace_collector(self):
        result, commands = self.run_install('upgrade', pending_writes=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Collector storage writes are pending', result.stderr)
        self.assertNotIn(' up ', commands)
        self.assertNotIn('chown app:app /var/lib/weighbridge', commands)

    def test_video_not_ready_fails_upgrade_even_when_containers_are_healthy(self):
        result, commands = self.run_install('upgrade', video_failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--force-recreate', commands)
        self.assertIn('video-probe', commands)
        self.assertIn('upgrade is degraded', result.stderr)
        self.assertNotIn('activate_weighbridge_collector', commands)
