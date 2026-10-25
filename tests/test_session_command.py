"""Inert-only session runner and synthetic process-identity boundary tests."""
import os
import pathlib
import signal
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import session_command
import supervisor


class InertSessionCommand(unittest.TestCase):
    @unittest.skipIf(os.geteuid()==0, 'real session runner is qualified separately as the desktop user')
    def test_real_inert_producer_receives_session_context(self):
        context = {'PATH':'/usr/bin:/bin', 'HOME':'/fixture/home',
                   'XDG_RUNTIME_DIR':'/fixture/runtime',
                   'DBUS_SESSION_BUS_ADDRESS':'unix:path=/fixture/bus',
                   'HYPRLAND_INSTANCE_SIGNATURE':'fixture-instance',
                   'OMARCHY_PATH':'/fixture/omarchy', 'LD_PRELOAD':'/no-loader'}
        with patch.dict(os.environ, context, clear=True):
            output = session_command.run([sys.executable, '-I', '-c',
                'import json,os;print(json.dumps(dict(os.environ)))'])
        import json
        actual = json.loads(output)
        for key in ('XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS',
                    'HYPRLAND_INSTANCE_SIGNATURE','OMARCHY_PATH'):
            self.assertEqual(actual[key], context[key])
        self.assertNotIn('LD_PRELOAD', actual)

    @unittest.skipIf(os.geteuid()==0, 'real session runner is qualified separately as the desktop user')
    def test_inert_deadline_overflow_exit_and_exact_cap(self):
        for body, timeout, cap, code in [
                ('import time;time.sleep(10)', .1, 1024, 124),
                ("print('x'*1025,end='')", 2, 1024, 90),
                ('raise SystemExit(7)', 2, 1024, 7)]:
            with self.subTest(code=code), self.assertRaisesRegex(Exception, 'exit '+str(code)):
                session_command.run([sys.executable, '-I', '-c', body], timeout, cap)
        self.assertEqual(session_command.run([sys.executable, '-I', '-c',
                         "print('x'*1024,end='')"],2,1024), b'x'*1024)

    def test_root_and_malformed_inputs_never_launch(self):
        with patch('subprocess.Popen') as launch, patch('os.geteuid',return_value=0):
            with self.assertRaisesRegex(Exception,'unprivileged'):
                session_command.run(['inert'])
        launch.assert_not_called()
        for argv, timeout, cap in [([],1,1), (['bad\0arg'],1,1), (['inert'],0,1), (['inert'],1,0)]:
            with patch('subprocess.Popen') as launch, self.assertRaises(Exception):
                session_command.run(argv,timeout,cap)
            launch.assert_not_called()


class SyntheticCleanupBoundaries(unittest.TestCase):
    def test_disappeared_identity_closes_pin_without_numeric_fallback(self):
        with patch.object(supervisor,'children',return_value={42}), patch('os.pidfd_open',return_value=900), patch('signal.pidfd_send_signal',side_effect=ProcessLookupError), patch('os.close') as close, patch('os.kill') as raw, patch('os.waitpid',side_effect=ChildProcessError):
            supervisor.cleanup()
        close.assert_called_once_with(900)
        raw.assert_not_called()

    def test_invalid_synthetic_identity_never_reaches_signal(self):
        for pid in (0,1,-1,True):
            with patch.object(supervisor,'children',return_value={pid}), patch('os.pidfd_open') as pin, patch('os.kill') as raw, self.assertRaisesRegex(RuntimeError,'identity'):
                supervisor.cleanup()
            pin.assert_not_called()
            raw.assert_not_called()

    def test_unreaped_task_exhausts_budget_not_false_success(self):
        with patch.object(supervisor,'children',return_value=set()), patch('os.waitpid',return_value=(0,0)) as reap, patch('time.monotonic',side_effect=[0,3]), self.assertRaisesRegex(RuntimeError,'cleanup deadline'):
            supervisor.cleanup()
        reap.assert_called_once_with(-1,os.WNOHANG)


if __name__ == '__main__':
    unittest.main(verbosity=2)
