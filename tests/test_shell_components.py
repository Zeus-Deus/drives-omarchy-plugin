"""Unprivileged, no storage/system writes; run explicitly in owned VM copy."""
import importlib.util
import pathlib
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import bridge
import paste
import os
import signal
import supervisor
from unittest.mock import Mock

class SessionSupervisorRegressions(unittest.TestCase):
    def test_restart_transport_retains_only_session_context(self):
        context = {'PATH':'/usr/bin:/bin', 'HOME':'/fixture/home',
                   'XDG_RUNTIME_DIR':'/fixture/runtime',
                   'DBUS_SESSION_BUS_ADDRESS':'unix:path=/fixture/bus',
                   'HYPRLAND_INSTANCE_SIGNATURE':'fixture-instance',
                   'OMARCHY_PATH':'/fixture/omarchy', 'WAYLAND_DISPLAY':'wayland-fixture',
                   'LD_PRELOAD':'/must/not/inherit', 'PYTHONPATH':'/must/not/inherit',
                   'DRIVES_STORAGE_LEASE_FD':'123'}
        class Intercepted(Exception): pass
        launch = Mock(side_effect=Intercepted)
        # Intercept the first Popen: no session/desktop command is executed.
        with patch.dict(os.environ, context, clear=True), patch('os.geteuid',return_value=1000), patch('os.getuid',return_value=1000), patch('subprocess.Popen',launch), patch.object(bridge,'reboot_argv',return_value=['omarchy-system','reboot']):
            with self.assertRaises(Intercepted):bridge.handle({'op':'reboot'})
        env = launch.call_args.kwargs['env']
        for name in ('XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS','HYPRLAND_INSTANCE_SIGNATURE','OMARCHY_PATH','WAYLAND_DISPLAY'):
            self.assertEqual(env.get(name),context[name],name)
        for name in ('LD_PRELOAD','PYTHONPATH','DRIVES_STORAGE_LEASE_FD'):
            self.assertNotIn(name,env)

    def test_cleanup_signals_only_pinned_direct_children(self):
        raw, pinned = Mock(), Mock()
        # All identities and signal boundaries are synthetic. Never signal a
        # real process; specifically preserve the native fixture's safe receipts.
        with patch.object(supervisor,'children',return_value={42}), patch('os.scandir',side_effect=FileNotFoundError), patch('os.kill',raw), patch('os.pidfd_open',return_value=900), patch('signal.pidfd_send_signal',pinned), patch('os.close') as close, patch('os.waitpid',side_effect=[(42,9),ChildProcessError()]):
            supervisor.cleanup()
        self.assertEqual(raw.call_args_list,[], 'numerical PID must never be signalled')
        pinned.assert_called_once_with(900,signal.SIGKILL)
        close.assert_called_once_with(900)

    def test_cleanup_rechecks_direct_ownership_after_pinning(self):
        raw, pinned = Mock(), Mock()
        with patch.object(supervisor,'children',side_effect=[{42},set()]), patch('os.scandir',side_effect=FileNotFoundError), patch('os.kill',raw), patch('os.pidfd_open',return_value=900), patch('signal.pidfd_send_signal',pinned), patch('os.close') as close, patch('os.waitpid',side_effect=ChildProcessError()):
            supervisor.cleanup()
        self.assertEqual(raw.call_args_list,[])
        pinned.assert_not_called()
        close.assert_called_once_with(900)

class ShellComponents(unittest.TestCase):
    def test_fixed_reboot_variants_and_unavailability(self):
        for available, expected in [({'omarchy-system'},['omarchy-system','reboot']),({'omarchy-system-reboot'},['omarchy-system-reboot']),({'omarchy-system','omarchy-system-reboot'},['omarchy-system','reboot'])]:
            self.assertEqual(bridge.reboot_argv(lambda n:n if n in available else None),expected)
        with self.assertRaisesRegex(Exception,'Restart is unavailable'):
            bridge.reboot_argv(lambda n:None)
        with patch.object(bridge,'run') as run, patch.object(bridge,'reboot_argv',return_value=['omarchy-system','reboot']):
            self.assertTrue(bridge.handle({'op':'reboot'})['ok'])
            run.assert_called_once_with(['omarchy-system','reboot'],timeout=5)
    def test_clipboard_unicode_and_byte_bound(self):
        self.assertEqual(paste.read_paste([sys.executable,'-c',"print('é漢',end='')"]),'é漢')
        self.assertEqual(len(paste.read_paste([sys.executable,'-c',"print('a'*65536,end='')"])),65536)
        with self.assertRaisesRegex(ValueError,'64 KiB'):
            paste.read_paste([sys.executable,'-c',"print('a'*65537,end='')"])
        with self.assertRaises(UnicodeDecodeError):
            paste.read_paste([sys.executable,'-c',"import os;os.write(1,b'\\xff')"])
    def test_supervisor_deadline_and_output_bound(self):
        for body, seconds, cap, code in [("import time;time.sleep(5)",.1,4096,124),("print('a'*4097,end='')",2,4096,90),("print('a'*4096,end='')",2,4096,0)]:
            p=subprocess.run([sys.executable,'-B',str(ROOT/'supervisor.py'),str(seconds),str(cap),sys.executable,'-c',body],capture_output=True,timeout=4)
            self.assertEqual(p.returncode,code,p.stderr)
            self.assertLessEqual(len(p.stdout),cap)
if __name__=='__main__':unittest.main(verbosity=2)
