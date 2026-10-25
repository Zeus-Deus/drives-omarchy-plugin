"""Private FD handoff and exact-unit hard cancellation for systemd-run callers."""
import array,contextlib,os,pathlib,socket,uuid


def run(argv,data,timeout,cap,accepted,lease):
    from helper.common import _run,Failure
    if '--pipe' not in argv or '--wait' not in argv:raise Failure('supervised worker requires --pipe and --wait')
    # Existing internal callers use only --option=value or flag options. Keep
    # the worker argv intact and refuse an ambiguous options/command boundary.
    boundary=next((i for i,x in enumerate(argv[1:],1) if not x.startswith('-')),None)
    if boundary is None:raise Failure('missing transient worker command')
    if data is not None and len(data)>4096:raise Failure('input limit exceeded')
    unit='drives-worker-'+uuid.uuid4().hex+'.service'
    options=[x for x in argv[1:boundary] if not x.startswith('--unit=')]
    options+=['--unit='+unit,'--property=Type=exec','--property=ExitType=cgroup',
              '--property=KillMode=control-group','--property=SendSIGKILL=yes','--property=TimeoutStopSec=2s']
    command=[argv[0],*options,'/usr/bin/python3','-B','-m','helper.transient_worker',unit,'--',*argv[boundary:]]
    parent,worker=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET|socket.SOCK_CLOEXEC)
    try:
        # Queue the actual locked open-file-description before submitting the
        # unit. Even parent death between submission and exec pins the lease.
        parent.sendmsg([b'L'+(data or b'')],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[lease]))])
        try:return _run(command,timeout=timeout,cap=cap,accepted=accepted,stdin=worker)
        finally:
            parent.close();worker.close()
            # The client has already been killed-before-reap. Cancellation is
            # not merely RuntimeMaxSec: kill every member of our random unit,
            # then wait for systemd's stop job. A manager failure cannot free a
            # live writer's inherited lease, including a process stuck in D.
            for args in (['kill','--kill-whom=all','--signal=KILL',unit],['stop',unit]):
                with contextlib.suppress(Failure,OSError):_run(['/usr/bin/systemctl',*args],timeout=5,cap=16384)
    finally:
        parent.close();worker.close()
