"""Fail-closed runtime maintenance observations; never authorizes a live cutover.

A successful observation is not a durable writer lease. The offline worker must
hold the boot gate and recheck before irreversible steps; no move calls this yet.
"""
import json,os,pathlib
from helper.common import Failure,boot_id,read_regular,run
from helper.maintenance import BOOT,MASKS,RUNTIME,TARGET,control_json,request

AUDIT_UNIT='drives-maintenance-audit.service'
WORKER_UNIT='drives-maintenance-worker.service'
SHUTDOWN_SERVICES={'systemd-reboot.service','systemd-poweroff.service','systemd-halt.service'}
CORE_SERVICES={'systemd-journald.service','systemd-udevd.service','systemd-remount-fs.service','systemd-udev-load-credentials.service','drives-maintenance-splash.service'}
CORE_SOCKETS={'systemd-journald.socket','systemd-journald-dev-log.socket','systemd-journald-audit.socket','systemd-udevd-control.socket','systemd-udevd-kernel.socket','systemd-udevd-varlink.socket'}
REQUIRES={'systemd-remount-fs.service','systemd-journald.service','systemd-udevd.service','drives-maintenance-splash.service'}


def process_unit(directory):
    lines=read_regular(directory/'cgroup',16384).decode().splitlines()
    unified=[line[3:] for line in lines if line.startswith('0::')]
    if len(unified)!=1:raise Failure('unified process cgroup is unavailable')
    return next((part for part in unified[0].split('/') if part.endswith('.service')),'')


def collect_runtime():
    """Observe PID1 and procfs directly; no caller-supplied runtime facts."""
    if os.geteuid()!=0:raise Failure('maintenance inspection requires root')
    props=run(['/usr/bin/systemctl','show','drives-maintenance.target','--property=ActiveState,Requires,Wants'],timeout=10,cap=16384).decode()
    props=dict(line.split('=',1) for line in props.splitlines())
    units=json.loads(run(['/usr/bin/systemctl','list-units','--all','--type=service,socket,timer,path','--output=json','--no-pager'],timeout=10))
    jobs=[]
    for line in run(['/usr/bin/systemctl','list-jobs','--no-legend','--plain','--no-pager'],timeout=10,cap=65536).decode().splitlines():
        columns=line.split()
        if len(columns)!=4 or not columns[0].isdecimal():raise Failure('cannot parse pending systemd jobs')
        if columns[1] not in (AUDIT_UNIT,WORKER_UNIT):jobs.append({'unit':columns[1],'type':columns[2],'state':columns[3]})
    processes=[]
    for directory in pathlib.Path('/proc').iterdir():
        if not directory.name.isdecimal():continue
        try:
            fields=read_regular(directory/'stat',16384).decode().rpartition(')')[2].split()
            # PF_KTHREAD identifies kernel tasks, not merely empty argv.
            if int(fields[6])&0x00200000 or fields[0]=='Z':continue
            status=read_regular(directory/'status',16384).decode().splitlines()
            uid=[int(value) for value in next(line for line in status if line.startswith('Uid:')).split()[1:]]
            processes.append({'pid':int(directory.name),'uid':uid,'exe':os.readlink(directory/'exe'),'unit':process_unit(directory)})
        except FileNotFoundError:
            if not directory.exists():continue # Exited process has no writers.
            raise Failure('cannot establish process identity')
    sessions=pathlib.Path('/run/systemd/sessions')
    early=pathlib.Path('/run/systemd/generator.early')
    default=early/'default.target'
    return {'bootId':boot_id(),'targetActive':props['ActiveState']=='active','targetRequires':props['Requires'].split(),'targetWants':props['Wants'].split(),'defaultTarget':os.readlink(default) if default.is_symlink() else '', 'masksComplete':all((early/name).is_symlink() and os.readlink(early/name)=='/dev/null' for name in MASKS),'sessions':sorted(path.name for path in sessions.iterdir()) if sessions.exists() else [],'jobs':jobs,'units':units,'processes':processes,'callerUnit':process_unit(pathlib.Path('/proc/self'))}


def validate_snapshot(snapshot,move_id,action,caller=AUDIT_UNIT):
    latch=snapshot['latch'];receipt=snapshot['receipt'];boot=snapshot['bootId']
    if not BOOT.fullmatch(boot) or receipt.get('bootId')!=boot or receipt.get('valid') is not True or receipt.get('error'):
        raise Failure('maintenance receipt does not prove this boot')
    if latch['armedBootId']==boot or any(latch.get(k)!=receipt.get(k) for k in ('version','moveId','action','armedBootId')):
        raise Failure('maintenance latch and boot receipt differ')
    if latch['moveId']!=move_id or latch['action']!=action:raise Failure('maintenance selection differs')
    if not snapshot['targetActive']:raise Failure('maintenance target is not active')
    if snapshot['defaultTarget']!=TARGET:raise Failure('maintenance target was not selected at boot')
    if not snapshot['masksComplete']:raise Failure('normal-boot masks are not established')
    extra=sorted((set(snapshot['targetRequires'])-REQUIRES)|(set(snapshot['targetWants'])-{WORKER_UNIT}))
    missing=sorted(REQUIRES-set(snapshot['targetRequires']))
    if extra or missing:
        raise Failure('unexpected maintenance dependency closure'+(': extra '+' '.join(extra) if extra else '')+(': missing '+' '.join(missing) if missing else ''))
    if caller not in (AUDIT_UNIT,WORKER_UNIT) or snapshot['callerUnit']!=caller:raise Failure('maintenance audit must run in its fixed root service')
    if snapshot['sessions'] or snapshot['jobs']:raise Failure('sessions or pending activation block maintenance')
    for unit in snapshot['units']:
        if unit['active'] in ('inactive','failed'):continue
        name=unit['unit']
        if name not in CORE_SERVICES|CORE_SOCKETS|{AUDIT_UNIT,WORKER_UNIT}:raise Failure('unexpected unit or activation: '+name)
        if unit['active'] not in ('active','activating'):raise Failure('unstable maintenance unit: '+name)
    executables={unit:os.path.realpath(path) for unit,path in {'systemd-journald.service':'/usr/lib/systemd/systemd-journald','systemd-udevd.service':'/usr/lib/systemd/systemd-udevd',AUDIT_UNIT:'/usr/bin/python3',WORKER_UNIT:'/usr/bin/python3'}.items()}
    for process in snapshot['processes']:
        if process['uid']!=[0,0,0,0]:raise Failure('non-root process blocks maintenance')
        if process['pid']==1 and process['exe']==os.path.realpath('/usr/lib/systemd/systemd'):continue
        if process['exe']!=executables.get(process['unit']):raise Failure('unexpected process blocks maintenance: '+process['exe']+' in '+process['unit'])
    return {'ok':True,'bootId':boot,'moveId':move_id,'action':action,'observationOnly':True}


if __name__=='__main__':
    try:
        if os.geteuid()!=0:raise Failure('maintenance inspection requires root')
        selected=request()
        receipt=control_json(RUNTIME/'boot.json')
        snapshot=collect_runtime();snapshot.update(latch=selected,receipt=receipt)
        result=validate_snapshot(snapshot,selected['moveId'],selected['action'])
    except Failure as error:result={'ok':False,'error':str(error)}
    except (OSError,ValueError,KeyError,IndexError,StopIteration):
        result={'ok':False,'error':'maintenance controls or runtime are unavailable'}
    print(json.dumps(result,ensure_ascii=True))
    raise SystemExit(0 if result['ok'] else 1)
