"""Detached-manager simulation: real processes and SCM_RIGHTS, no systemd writes."""
import contextlib,os,pathlib,signal,subprocess,sys,time
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def test_detached_stopped_writer_retains_lease_after_timeout(tmp_path,monkeypatch):
    from helper import common
    c=common.Common(tmp_path/'state');ready=tmp_path/'ready';late=tmp_path/'LATE';workers=[];calls=[]
    original=common.subprocess.Popen;payload_fds=[]
    # A paused writer is a safe stand-in for uninterruptible kernel I/O: its
    # inherited lease must survive caller error, even if manager RPCs fail.
    payload=[sys.executable,'-c','import os,signal,pathlib,sys;pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));os.kill(os.getpid(),signal.SIGSTOP);pathlib.Path(sys.argv[2]).touch()',str(ready),str(late)]
    def launch(argv,**kw):
        if pathlib.Path(argv[0]).name=='systemd-run':
            args=list(argv);start=next(i for i,x in enumerate(args[1:],1) if not x.startswith('-'))
            worker=original(args[start:],stdin=kw['stdin'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,env=kw['env'],pass_fds=kw.get('pass_fds',()))
            workers.append(worker)
            end=time.monotonic()+5
            while not ready.exists() and time.monotonic()<end:time.sleep(.01)
            assert ready.exists(),'detached writer never reached test barrier'
            payload_fds.append(os.pidfd_open(int(ready.read_text())))
            os.kill(worker.pid,signal.SIGSTOP)
            return original([sys.executable,'-c','import time;time.sleep(60)'],**kw)
        if pathlib.Path(argv[0]).name=='systemctl':
            calls.append(argv);return original([sys.executable,'-c','raise SystemExit(1)'],**kw)
        return original(argv,**kw)
    monkeypatch.setattr(common.subprocess,'Popen',launch)
    try:
        with c.storage_lock():
            with pytest.raises(common.Failure):common.run(['systemd-run','--quiet','--wait','--collect','--pipe','--unit=drives-fixture',*payload],timeout=.05)
        with pytest.raises(common.Failure,match='another storage operation'):
            with common.Common(c.state_dir).storage_lock():pass
        assert any('KILL' in ' '.join(a) for a in calls),'deadline did not request a hard kill of the exact worker unit'
        assert not late.exists()
    finally:
        for worker in workers:
            with contextlib.suppress(ProcessLookupError):os.killpg(worker.pid,signal.SIGKILL)
            worker.wait()
        for fd in payload_fds:
            with contextlib.suppress(ProcessLookupError):signal.pidfd_send_signal(fd,signal.SIGKILL)
            os.close(fd)


def test_worker_eof_cancels_before_releasing_shared_lease(tmp_path):
    import array,socket
    from helper.common import Common,Failure
    c=Common(tmp_path/'state');ready=tmp_path/'ready';late=tmp_path/'late'
    # External manager calls are replaced ONLY inside this component child;
    # this test never contacts the guest's real service manager.
    worker_code='''import sys
from helper import common
from helper.transient_worker import serve
common._run=lambda *a,**k: (_ for _ in ()).throw(common.Failure('fixture manager unreachable'))
try:serve(sys.argv[1],sys.argv[2:])
except common.Failure:raise SystemExit(23)
'''
    unit='drives-worker-'+'a'*32+'.service'
    payload=[sys.executable,'-c','import os,pathlib,signal,sys;pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));os.kill(os.getpid(),signal.SIGSTOP);pathlib.Path(sys.argv[2]).touch()',str(ready),str(late)]
    parent,remote=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    lease=c.storage_lock();lease.__enter__();p=None
    try:
        from helper.workerlease import borrow
        with borrow() as fd:parent.sendmsg([b'L'],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[fd]))])
        p=subprocess.Popen([sys.executable,'-B','-c',worker_code,unit,*payload],cwd=ROOT,stdin=remote,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,start_new_session=True)
        remote.close();end=time.monotonic()+5
        while not ready.exists() and time.monotonic()<end:time.sleep(.01)
        assert ready.exists()
        leaf=os.pidfd_open(int(ready.read_text()))
        try:
            lease.__exit__(None,None,None);lease=None
            with pytest.raises(Failure,match='another storage operation'):
                with Common(c.state_dir).storage_lock():pass
            parent.close()
            assert p.wait(timeout=8)==23,p.stderr.read()
            # Exact child is reaped by its owner before the shared flock drops.
            assert select_pidfd(leaf)
            assert not late.exists(),'writer ran after its parent socket disappeared'
            with Common(c.state_dir).storage_lock():pass
        finally:os.close(leaf)
    finally:
        parent.close();remote.close()
        if p is not None:
            with contextlib.suppress(ProcessLookupError):os.killpg(p.pid,signal.SIGKILL)
            p.wait();p.stderr.close()
        if lease is not None:lease.__exit__(None,None,None)


def select_pidfd(fd):
    import select
    return bool(select.select([fd],[],[],0)[0])


def test_regular_subprocess_keeps_lease_without_reacquiring(tmp_path):
    from helper.common import Common,run
    c=Common(tmp_path)
    probe='''import fcntl,os,sys
fd=int(os.environ['_DRIVES_STORAGE_LEASE_FD'])
fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
try:
 other=os.open(sys.argv[1],os.O_RDWR)
 try:fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:print('shared-description')
 else:raise SystemExit('lease was not held')
 finally:os.close(other)
finally:pass
'''
    with c.storage_lock():assert run([sys.executable,'-c',probe,str(tmp_path/'storage.lock')])==b'shared-description\n'
    with c.storage_lock():pass


def test_failed_transient_spawn_releases_queued_lease(tmp_path,monkeypatch):
    from helper import common
    c=common.Common(tmp_path);original=common.subprocess.Popen
    def spawn(argv,**kwargs):
        if pathlib.Path(argv[0]).name=='systemd-run':raise OSError('fixture launch failed')
        if pathlib.Path(argv[0]).name=='systemctl':return original([sys.executable,'-c','raise SystemExit(1)'],**kwargs)
        return original(argv,**kwargs)
    monkeypatch.setattr(common.subprocess,'Popen',spawn)
    with c.storage_lock():
        with pytest.raises(OSError,match='fixture launch failed'):common.run(['systemd-run','--wait','--pipe','/bin/true'])
    with c.storage_lock():pass
