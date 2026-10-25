"""Unprivileged bounded producer. Relay death closes guardian's owner pipe.

The guardian is a Linux child subreaper: even setsid/double-fork descendants
remain owned. Only it writes stdout; its exit follows tree cleanup and reap.
"""
import ctypes
import os
import selectors
import signal
import subprocess
import sys
import time


def prctl(option, value):
    if ctypes.CDLL(None, use_errno=True).prctl(option, value, 0, 0, 0):
        raise OSError(ctypes.get_errno(), 'prctl')


def children():
    """Current direct children, including subreaper-adopted orphans."""
    result = []
    for task in os.scandir('/proc/self/task'):
        try:
            with open(task.path + '/children') as stream:
                result.extend(map(int, stream.read().split()))
        except FileNotFoundError:
            pass
    return set(result)


def cleanup():
    # Never walk a running parent's numerical grandchild list. Only direct
    # children are admitted; this sole reaper does not reap between discovery,
    # pidfd_open and ownership recheck, so their PIDs cannot be recycled there.
    # pidfds reserve signal identity, not PID numbers. KILL then reap adopts the
    # next generation (including setsid/double forks and all task children).
    # No SIGSTOP traversal remains: signal acceptance is NOT stop acknowledgment.
    end = time.monotonic() + 2
    while True:
        for pid in children():
            if time.monotonic() >= end:
                raise RuntimeError('producer cleanup deadline exceeded')
            if type(pid) is not int or pid <= 1:
                raise RuntimeError('invalid direct child identity')
            fd = None
            try:
                fd = os.pidfd_open(pid)
                if pid in children():
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
            except ProcessLookupError:
                pass
            finally:
                if fd is not None:
                    os.close(fd)
        try:
            # Nonblocking reap: a stuck kernel task must not hang the guardian.
            # ECHILD, not a transient empty /proc snapshot, proves completion.
            while os.waitpid(-1, os.WNOHANG)[0]:
                if time.monotonic() >= end:
                    raise RuntimeError('producer cleanup deadline exceeded')
        except ChildProcessError:
            return
        if time.monotonic() >= end:
            raise RuntimeError('producer cleanup deadline exceeded')
        time.sleep(.005)


def guard(owner, argv, seconds, cap):
    prctl(36, 1)  # PR_SET_CHILD_SUBREAPER, before any producer exists
    child = None
    sel = selectors.DefaultSelector()
    code = 1
    try:
        child = subprocess.Popen(argv, stdin=sys.stdin.buffer, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        assert child.stdout is not None
        sel.register(owner, selectors.EVENT_READ, 'owner')
        sel.register(child.stdout, selectors.EVENT_READ, 'data')
        os.set_blocking(1, False)
        end = time.monotonic() + seconds
        used = 0
        eof = False
        while True:
            left = end - time.monotonic()
            if left <= 0:
                code = 124
                break
            events = sel.select(min(left, .05))
            if any(k.data == 'owner' for k, _ in events):
                code = 125
                break
            if eof:
                if child.poll() is not None:
                    code = child.returncode
                    break
                continue
            if not events:
                continue
            data = os.read(child.stdout.fileno(), min(65536, cap - used + 1))
            if not data:
                eof = True
                sel.unregister(child.stdout)
                continue
            if used + len(data) > cap:
                code = 90
                break
            used += len(data)
            while data:
                if time.monotonic() >= end:
                    code = 124
                    return code
                if any(k.data == 'owner' for k, _ in sel.select(0)):
                    code = 125
                    return code
                try:
                    n = os.write(1, data)
                    data = data[n:]
                except BlockingIOError:
                    time.sleep(.005)
    except (BrokenPipeError, OSError):
        code = 125
    finally:
        cleanup()
        if child and child.stdout:
            child.stdout.close()
        sel.close()
        os.close(owner)
    return code


def main():
    seconds = min(180.0, max(.05, float(sys.argv[1])))
    cap = min(2097152, max(1, int(sys.argv[2])))
    argv = sys.argv[3:]
    # Install cancellation before forking: a TERM in the fork/handler window
    # must not publish relay exit while its guardian is still cleaning up.
    write = None
    cancelled = False
    def cancel(*_):
        nonlocal write, cancelled
        cancelled = True
        if write is not None:
            os.close(write)
            write = None
    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    parent = os.getppid()
    if parent == 1:
        return 125
    prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    if os.getppid() != parent or cancelled:
        return 125
    read, write = os.pipe2(os.O_CLOEXEC)
    pid = os.fork()
    if pid == 0:
        os.close(write)
        # Guardian is intentionally independent of relay death signals.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        os._exit(guard(read, argv, seconds, cap))
    os.close(read)
    os.close(1)  # guardian alone owns stdout
    if cancelled:
        cancel()
    try:
        _, status = os.waitpid(pid, 0)
        return os.waitstatus_to_exitcode(status)
    finally:
        cancel()


if __name__ == '__main__':
    sys.exit(main())
