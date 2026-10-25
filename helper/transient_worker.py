"""Root transient entrypoint; no payload starts before receiving its lease."""
import array,contextlib,os,re,select,signal,socket,subprocess,sys,time
from helper.workerlease import ENV,validate


def serve(unit,argv,control_fd=0):
    from helper.common import Failure,_run
    if os.geteuid()!=0 or not re.fullmatch(r'drives-worker-[0-9a-f]{32}\.service',unit):
        raise Failure('invalid privileged worker identity')
    control=socket.socket(fileno=os.dup(control_fd));control.settimeout(10)
    lease=None;p=None
    def interrupted(signum,frame):raise Failure('worker interrupted')
    for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(sig,interrupted)
    try:
        message,ancillary,flags,_=control.recvmsg(4097,socket.CMSG_SPACE(array.array('i').itemsize))
        fds=[]
        for level,kind,value in ancillary:
            if level==socket.SOL_SOCKET and kind==socket.SCM_RIGHTS:
                received=array.array('i');received.frombytes(value);fds.extend(received)
        if len(fds)!=1 or flags&(socket.MSG_TRUNC|socket.MSG_CTRUNC) or not message.startswith(b'L'):
            for fd in fds:os.close(fd)
            raise Failure('invalid worker lease handoff')
        lease=validate(fds[0]);control.setblocking(False)
        # EOF before admission must not turn an already-failed request into a
        # late write. The peer is a private socket, not a mutable shared name.
        if select.select([control],[],[],0)[0] and control.recv(1,socket.MSG_PEEK)==b'':
            raise Failure('worker owner disappeared before admission')
        env={'PATH':'/usr/bin:/bin','LC_ALL':'C','HOME':'/root',ENV:str(lease)}
        payload=message[1:]
        p=subprocess.Popen(argv,stdin=subprocess.PIPE if payload else subprocess.DEVNULL,
                           pass_fds=(lease,),env=env,start_new_session=True)
        if payload:p.stdin.write(payload);p.stdin.close()
        while True:
            if select.select([control],[],[],.05)[0]:
                raise Failure('worker owner disappeared')
            result=os.waitid(os.P_PID,p.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
            if result is not None:
                return result.si_status if result.si_code==os.CLD_EXITED else 128+result.si_status
    except BaseException:
        # A stopped/blocked descendant can escape a local process group, but
        # not the exact systemd cgroup. Each live writer also owns the flock.
        with contextlib.suppress(Failure,OSError):
            _run(['/usr/bin/systemctl','kill','--kill-whom=all','--signal=KILL',unit],timeout=5,cap=16384)
        raise
    finally:
        if p is not None:
            with contextlib.suppress(ProcessLookupError):os.killpg(p.pid,signal.SIGKILL)
            p.wait()  # reservation retained through group signal
        if lease is not None:os.close(lease)
        control.close()


if __name__=='__main__':
    if len(sys.argv)<4 or sys.argv[2]!='--':raise SystemExit('invalid worker invocation')
    raise SystemExit(serve(sys.argv[1],sys.argv[3:]))
