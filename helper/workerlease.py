"""Duplicate the held flock description, never reacquire it in a worker.

The descriptor (including one queued in SCM_RIGHTS) pins the lease through
parent death and uninterruptible worker I/O. No shared PID file is authority.
"""
import contextlib,os,pathlib,stat,threading

ENV='_DRIVES_STORAGE_LEASE_FD'
_guard=threading.RLock()
_active=set()

def validate(fd):
    from helper.common import Failure
    info=os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o077
            or info.st_nlink!=1 or pathlib.Path(os.readlink('/proc/self/fd/'+str(fd))).name!='storage.lock'):
        raise Failure('unsafe inherited storage lease')
    return fd

def register(fd):
    with _guard:_active.add(fd)

def unregister(fd):
    with _guard:_active.discard(fd)

@contextlib.contextmanager
def borrow():
    """A private duplicate usable from the scheduler's separate worker thread."""
    from helper.common import Failure
    fd=None
    with _guard:
        sources=list(_active)
        if not sources and ENV in os.environ:
            try:sources=[validate(int(os.environ[ENV]))]
            except (ValueError,OSError) as exc:raise Failure('invalid inherited storage lease') from exc
        if len(sources)>1:raise Failure('ambiguous storage transaction lease')
        if sources:fd=os.dup(validate(sources[0]))
    try:yield fd
    finally:
        if fd is not None:os.close(fd)
