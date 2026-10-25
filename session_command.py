"""Bounded desktop-user commands; deliberately separate from root common.run.

Only the bridge's fixed command variants enter this path. Never import this
runner into the privileged helper or forward arbitrary request argv/env.
"""
import os
import pathlib
import subprocess
import sys

from helper.common import Failure

# Session/bus transport and desktop context, not the caller's arbitrary loader,
# Python, shell, credential or privileged storage-lease environment.
SESSION_ENV = ('PATH', 'HOME', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS',
               'HYPRLAND_INSTANCE_SIGNATURE', 'OMARCHY_PATH', 'WAYLAND_DISPLAY',
               'DISPLAY', 'XDG_SESSION_TYPE', 'XDG_CURRENT_DESKTOP')


def run(argv, timeout=5, cap=65536):
    if os.geteuid() == 0 or os.geteuid() != os.getuid():
        raise Failure('session commands require the unprivileged desktop user')
    if (not isinstance(argv, (list, tuple)) or not argv
            or any(not isinstance(arg, str) or '\0' in arg for arg in argv)):
        raise Failure('invalid session command')
    if not 0 < timeout <= 180 or not 0 < cap <= 2097152:
        raise Failure('invalid session command bounds')
    env = {name: os.environ[name] for name in SESSION_ENV if name in os.environ}
    env.setdefault('PATH', '/usr/bin:/bin')
    env.setdefault('HOME', os.path.expanduser('~'))
    env['LC_ALL'] = 'C'
    command = [sys.executable, '-I', '-B',
               str(pathlib.Path(__file__).with_name('supervisor.py')),
               str(timeout), str(cap), *argv]
    # Supervisor caps bytes/time and owns descendant cleanup. If its relay
    # stalls, terminating the owned relay closes the guardian's owner pipe.
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               start_new_session=True, env=env)
    assert process.stdout is not None
    try:
        out, _ = process.communicate(timeout=timeout + 3)
    except subprocess.TimeoutExpired:
        raise Failure('session command deadline exceeded') from None
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        process.stdout.close()
    if process.returncode != 0:
        raise Failure(pathlib.Path(argv[0]).name + ' failed (exit ' + str(process.returncode) + ')')
    return out
