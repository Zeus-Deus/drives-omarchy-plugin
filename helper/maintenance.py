"""Root-private, inspect-only early boot gate for explicit folder maintenance.

Selecting a target is not proof of exclusion. Migration must independently audit
its boot receipt and the actual running services/processes before cutover.
"""
import json,os,pathlib,re,stat,sys
from helper.common import Failure,atomic,boot_id,read_regular

STATE=pathlib.Path('/var/lib/drives-helper')
# A separate /var is not mounted when system generators first run. Keep the
# boot latch directly on the root filesystem, independent of journal storage.
LATCH=pathlib.Path('/drives-maintenance-request.json')
RUNTIME=pathlib.Path('/run/drives-maintenance')
TARGET='/etc/systemd/system/drives-maintenance.target'
MASKS=('graphical.target','multi-user.target','user@.service','timers.target','paths.target','sockets.target','display-manager.service')
BOOT=re.compile('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
ACTIVATION_TYPES=('service','socket','timer','path','mount','automount','swap','scope')
ACTIVATION_DROPIN='zzzz-drives-maintenance.conf'


def private_directory(path,create=False):
    """Refuse user-controlled ancestors, including symlinks and writable dirs."""
    p=pathlib.Path(path)
    if not p.is_absolute() or '..' in p.parts:raise Failure('invalid control directory')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for index,part in enumerate(p.parts[1:]):
            if create and index==len(p.parts)-2:
                try:os.mkdir(part,0o700,dir_fd=fd);os.fsync(fd)
                except FileExistsError:pass
            nextfd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=nextfd
            s=os.fstat(fd)
            if s.st_uid!=0 or s.st_mode&0o022:raise Failure('unsafe control directory ownership or permissions')
        return p
    finally:os.close(fd)


def control_json(path,cap=8192):
    p=pathlib.Path(path);private_directory(p.parent)
    fd=os.open(p,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
    try:
        s=os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid!=0 or s.st_mode&0o077 or s.st_nlink!=1 or s.st_size>cap:
            raise Failure('unsafe maintenance control file')
        raw=os.read(fd,cap+1)
        if len(raw)>cap:raise Failure('maintenance control file limit exceeded')
        try:
            text=raw.decode('utf-8')
            depth=0;quoted=False;escaped=False
            for char in text:
                if quoted:
                    if escaped:escaped=False
                    elif char=='\\':escaped=True
                    elif char=='"':quoted=False
                elif char=='"':quoted=True
                elif char in '[{':
                    depth+=1
                    if depth>64:raise Failure('invalid maintenance control JSON: nesting limit exceeded')
                elif char in ']}':depth-=1
            data=json.loads(text)
        except (ValueError,RecursionError):raise Failure('invalid maintenance control JSON') from None
        if not isinstance(data,dict):raise Failure('invalid maintenance control record')
        return data
    finally:os.close(fd)


def latch_request():
    value=control_json(LATCH)
    if set(value)!={'version','moveId','action','armedBootId'} or type(value['version']) is not int or value['version']!=1 or value['action'] not in ('continue','rollback'):
        raise Failure('invalid maintenance request')
    if not isinstance(value['moveId'],str) or not re.fullmatch('[a-f0-9]{32}',value['moveId']):raise Failure('invalid maintenance move id')
    if not isinstance(value['armedBootId'],str) or not BOOT.fullmatch(value['armedBootId']):raise Failure('invalid maintenance boot id')
    return value


def request(state_dir=STATE):
    state=private_directory(state_dir)
    value=latch_request()
    move=control_json(state/'moves'/(value['moveId']+'.json'),1024*1024)
    if move.get('id')!=value['moveId'] or move.get('state') in ('cleaned','rolled-back'):raise Failure('maintenance move missing or already finished')
    return value


def link(path,target):
    p=pathlib.Path(path)
    if os.path.lexists(p):
        if p.is_symlink() and os.readlink(p)==target:return
        raise Failure('unexpected existing generated unit')
    os.symlink(target,p)


def activation_guards(early,runtime):
    """Deny later unit activation for the whole maintenance boot.

    Exact-name drop-ins shadow only our type-wide condition, preserving vendor
    conditions. This is prevention infrastructure, not migration admission:
    udev/direct process writers and boot/runtime provenance still need auditing.
    """
    from helper.maintenance_audit import CORE_SERVICES,CORE_SOCKETS,AUDIT_UNIT
    for kind in ACTIVATION_TYPES:
        directory=private_directory(early/(kind+'.d'),create=True)
        atomic(directory/ACTIVATION_DROPIN,('[Unit]\nConditionPathExists=!'+str(runtime)+'\n').encode(),0o644)
    for unit in CORE_SERVICES|CORE_SOCKETS|{AUDIT_UNIT}:
        directory=private_directory(early/(unit+'.d'),create=True)
        atomic(directory/ACTIVATION_DROPIN,b'[Unit]\n# Exempt only the owned type-wide activation condition.\n',0o644)


def generate(early_dir,state_dir=STATE,runtime=RUNTIME,current_boot=None):
    """Generate only runtime units/receipt; never touch source or move journal."""
    if os.geteuid()!=0:raise Failure('maintenance generator requires root')
    state=pathlib.Path(state_dir)
    runtime=pathlib.Path(runtime)
    current_boot=boot_id() if current_boot is None else current_boot
    if os.path.lexists(runtime):
        # Releasing the persistent latch permits the NEXT boot only. Keep this
        # boot's policy across daemon-reload, including a damaged runtime record.
        early=private_directory(early_dir)
        link(early/'default.target',TARGET)
        for unit in MASKS:link(early/unit,'/dev/null')
        private_directory(runtime)
        try:
            saved=control_json(runtime/'boot.json')
            if saved.get('bootId')!=current_boot:raise Failure('stale maintenance runtime')
        except (Failure,OSError,ValueError):
            atomic(runtime/'boot.json',json.dumps({'version':1,'bootId':current_boot,'valid':False,'error':'Invalid maintenance runtime; retain the boot gate and inspect.'}).encode())
        activation_guards(early,runtime)
        return True
    try:os.lstat(LATCH)
    except FileNotFoundError:return False
    except OSError:pass # An unreadable latch is not permission to boot normally.
    value=None
    try:
        value=latch_request()
        # daemon-reload after arming must not convert a running desktop.
        if value['armedBootId']==current_boot:return False
        request(state)
        error=''
    except (Failure,OSError,ValueError):error='Invalid maintenance request; retain both copies and inspect as administrator.'
    early=private_directory(early_dir)
    # Even a damaged request holds the boot gate closed. No auto-resume.
    link(early/'default.target',TARGET)
    for unit in MASKS:link(early/unit,'/dev/null')
    runtime=private_directory(runtime,create=True)
    receipt={'version':1,'bootId':current_boot,'valid':not error,'error':error}
    if value is not None:receipt.update(value)
    atomic(runtime/'boot.json',json.dumps(receipt,ensure_ascii=True).encode())
    activation_guards(early,runtime)
    return True


if __name__=='__main__':
    if len(sys.argv)!=4:raise SystemExit('This entrypoint is a systemd generator, not a migration command.')
    generate(sys.argv[2])
