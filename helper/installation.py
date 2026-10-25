"""Explicit setup/removal transaction. No environment-based system-root override.

All ownership/type checks precede mutation. Replacement code is staged in full
before quiescing the old daemon; rollback keeps the original code tree available.
Only root may invoke this module from an explicitly authorized checkout.
"""
import ast,contextlib,fcntl,json,os,pathlib,shutil,subprocess,sys,uuid
from helper.common import Failure,atomic
from helper.ownership import directory,read,digest,refuse

ROOT=pathlib.Path('/')
CODE='/usr/lib/drives-helper'
MANIFEST=CODE+'/.ownership.json'
SPECIAL={
 'drives-sysinit-guard.conf':'/etc/systemd/system/sysinit.target.d/drives-normal-boot-guard.conf',
 'drives-maintenance-generator':'/etc/systemd/system-generators/drives-maintenance-generator',
 'io.github.zeus-deus.drives.policy':'/usr/share/polkit-1/actions/io.github.zeus-deus.drives.policy',
 'io.github.zeus_deus.Drives.conf':'/etc/dbus-1/system.d/io.github.zeus_deus.Drives.conf',
}
SHARED=('/etc/cryptsetup-keys.d','/mnt/drives','/etc/udev/rules.d')
GATES=('/drives-maintenance-request.json','/run/drives-maintenance')

def target(name):return ROOT/name.lstrip('/')

def ctl(*args):
    p=subprocess.run(['systemctl',*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60,check=False)
    if p.returncode:raise Failure('systemctl '+ ' '.join(args)+': '+p.stderr.decode(errors='replace')[:1000])
    return p.stdout.decode().strip()


def candidates(source):
    files={CODE+'/topology.py':((source/'topology.py').read_bytes(),0o644)}
    for p in sorted((source/'helper').iterdir()):
        if p.suffix in ('.py','.json'):
            if p.is_symlink() or not p.is_file():refuse(p,'unsafe source module')
            data=p.read_bytes()
            if p.suffix=='.py':ast.parse(data,filename=str(p))
            files[CODE+'/helper/'+p.name]=(data,0o644)
    files[CODE+'/normal_boot_guard.py']=files[CODE+'/helper/normal_boot_guard.py']
    for p in sorted((source/'packaging').iterdir()):
        if not p.is_file() or p.is_symlink():refuse(p,'unsafe packaging source')
        files[SPECIAL.get(p.name,'/etc/systemd/system/'+p.name)]=(p.read_bytes(),0o755 if p.name.endswith('-generator') else 0o644)
    return files


def receipt():
    data=read(target(MANIFEST))
    if data is None:return {}
    try:
        obj=json.loads(data)
        if obj.get('owner')!='io.github.zeus-deus.drives' or not isinstance(obj['files'],dict):raise ValueError()
        return obj['files']
    except (ValueError,KeyError,TypeError):refuse(target(MANIFEST),'invalid ownership receipt')


def preflight(files,legacy,record):
    """Do not adopt current source bytes as evidence of an existing owner."""
    paths=set(files)|set(record)
    code=target(CODE)
    directory(code,missing=True)
    if code.exists():
        for p in code.rglob('*'):
            if p.is_dir() and not p.is_symlink():directory(p);continue
            key='/'+str(p.relative_to(ROOT))
            # Python caches have no authority and are retained in the backup;
            # never read their contents or execute them during staging.
            if '__pycache__' in p.parts:
                read(p);continue
            if key!=MANIFEST:paths.add(key)
    for name in sorted(paths):
        p=target(name);data=read(p)
        if data is None:continue
        if digest(data)!=record.get(name) and digest(data) not in legacy.get(name,[]):refuse(p,'foreign or modified installation file')
    for name in SHARED:directory(target(name),missing=True)
    crypttab=target('/etc/crypttab');read(crypttab)
    state=target('/var/lib/drives-helper');directory(state,missing=True)
    read(state/'storage.lock')
    # systemctl enable must not replace an administrator's alias/link.
    link=target('/etc/systemd/system/multi-user.target.wants/drives-helper.service')
    directory(link.parent,missing=True)
    if os.path.lexists(link):
        if not link.is_symlink() or os.readlink(link) not in ('/etc/systemd/system/drives-helper.service','../drives-helper.service',str(target('/etc/systemd/system/drives-helper.service'))):refuse(link,'foreign service enable link')
    return paths


def gates():
    for name in GATES:
        p=target(name)
        if os.path.lexists(p):refuse(p,'maintenance recovery is armed; refuse setup/removal')
    state=ctl('show','drives-maintenance.target','--property=ActiveState','--value')
    if state!='inactive':raise Failure('Refusing setup/removal: maintenance is active or uncertain ('+state+').')
    # Legacy workers may predate parent-lifetime bindings. Never stop/remove
    # their implementation while PID1 still owns a possibly mutating worker.
    active=ctl('list-units','drives-*.service','--all','--state=active,activating,deactivating','--no-legend','--plain')
    for line in active.splitlines():
        unit=line.split()[0] if line.split() else ''
        if unit.startswith('drives-') and unit not in ('drives-helper.service','drives-normal-boot-guard.service'):
            raise Failure('Refusing setup/removal while worker is active: '+unit)


def effective_units(files):
    for name in sorted(files):
        if not name.startswith('/etc/systemd/system/') or not name.endswith(('.service','.target')):continue
        unit=pathlib.Path(name).name
        fragment=ctl('show',unit,'--property=FragmentPath','--value')
        if fragment and fragment!=str(target(name)):refuse(fragment,'foreign effective systemd unit')
        drops=ctl('show',unit,'--property=DropInPaths','--value')
        if drops:refuse(drops,'unexpected effective unit drop-in')
    # sysinit's deliberate guard is removed without ever stopping sysinit or
    # the guard. Other sysinit overrides are outside this helper's ownership.


@contextlib.contextmanager
def lease():
    state=target('/var/lib/drives-helper');directory(state,missing=True)
    state.mkdir(mode=0o700,parents=True,exist_ok=True)
    path=state/'storage.lock';read(path)
    fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise Failure('Refusing setup/removal: another storage operation is running.') from None
        yield
    finally:os.close(fd)


def ready_rules():
    from helper import udevhide
    rules=target('/etc/udev/rules.d');ready=[]
    state=target('/var/lib/drives-helper/drives');directory(state,missing=True)
    for p in sorted(state.glob('*.json')):
        try:j=json.loads(read(p,1024*1024))
        except (ValueError,TypeError):refuse(p,'invalid drive journal')
        if j.get('state')!='ready' or not j.get('luksUUID') or not j.get('name'):continue
        name=j['name'];uid=j['luksUUID'];udevhide.rule_text(name,uid)
        udevhide.owned_rule(udevhide.rule_path(name,rules),name)
        udevhide.owned_rule(udevhide.setup_path(name,rules),name,True)
        ready.append((name,uid))
    return ready


def make_dirs(path,mode=0o755):
    directory(path,missing=True)
    path.mkdir(mode=mode,parents=True,exist_ok=True)


def execute(action,source):
    files=candidates(source)
    legacy=json.loads((source/'helper/legacy_install_hashes.json').read_bytes())['files']
    record=receipt();preflight(files,legacy,record)
    desired={name:digest(value[0]) for name,value in files.items()}
    code_current=record==desired and all(read(target(name))==data for name,(data,mode) in files.items() if name.startswith(CODE+'/'))
    enable_link=target('/etc/systemd/system/multi-user.target.wants/drives-helper.service')
    enable_before=os.readlink(enable_link) if enable_link.is_symlink() else None
    # Every runtime target is included in preflight before staging or stopping.
    ready=ready_rules() if action=='install' else []
    gates();effective_units(files)
    base=target('/usr/lib');make_dirs(base)
    transaction=base/('.drives-transaction-'+uuid.uuid4().hex);transaction.mkdir(mode=0o700)
    staged=transaction/'replacement';staged.mkdir(mode=0o755)
    previous=transaction/'previous'
    changed=[];swapped=False;had_code=False;stopped=False;previous_active_state=None
    held=contextlib.ExitStack()
    external={name:value for name,value in files.items() if not name.startswith(CODE+'/')}
    try:
        if action=='install':
            for name,(data,mode) in files.items():
                p=staged/name[len(CODE)+1:] if name.startswith(CODE+'/') else transaction/'external'/name.lstrip('/')
                make_dirs(p.parent);atomic(p,data,mode)
            manifest=json.dumps({'owner':'io.github.zeus-deus.drives','files':{k:digest(v[0]) for k,v in files.items()}},sort_keys=True).encode()
            atomic(staged/'.ownership.json',manifest,0o644)
            # Verify the complete staged tree before the old producer stops.
            for name,(data,mode) in files.items():
                p=staged/name[len(CODE)+1:] if name.startswith(CODE+'/') else transaction/'external'/name.lstrip('/')
                if read(p)!=data:raise Failure('staged replacement verification failed: '+str(p))
        with contextlib.nullcontext():
            held.enter_context(lease())
            gates();preflight(files,legacy,record);effective_units(files)
            if target('/etc/systemd/system/drives-helper.service').exists():
                # Stopping an existing unit does not imply it was running.
                # Failed setup/removal must not activate an intentionally
                # inactive (or failed/stopping) helper during rollback.
                previous_active_state=ctl('show','drives-helper.service','--property=ActiveState','--value')
                ctl('stop','drives-helper.service');stopped=True
                if ctl('show','drives-helper.service','--property=ActiveState','--value')!='inactive':
                    raise Failure('Helper did not quiesce; replacement was not activated.')
            gates()
            if action=='install':
                for name in SHARED:make_dirs(target(name))
                crypttab=target('/etc/crypttab')
                if read(crypttab) is None:
                    make_dirs(crypttab.parent);atomic(crypttab,b'',0o600);changed.append(('/etc/crypttab',None,0o600))
                if not code_current:
                    code=target(CODE);had_code=code.exists()
                    if had_code:os.rename(code,previous)
                    try:os.rename(staged,code)
                    except BaseException:
                        if had_code:os.rename(previous,code)
                        raise
                    swapped=True
                for name,(data,mode) in external.items():
                    p=target(name);old=read(p);oldmode=p.stat().st_mode&0o777 if old is not None else mode
                    make_dirs(p.parent);atomic(p,data,mode);changed.append((name,old,oldmode))
                # Same bounded ownership rules as helper.udevhide --all, but all
                # collisions have already been checked before the transaction.
                from helper import udevhide
                for name,uid in ready:
                    p=udevhide.rule_path(name,target('/etc/udev/rules.d'));old=read(p,8192)
                    udevhide.write(name,uid,p.parent);changed.append(('/'+str(p.relative_to(ROOT)),old,0o644))
                    setup=udevhide.setup_path(name,p.parent);old=read(setup,8192)
                    if old is not None:setup.unlink();changed.append(('/'+str(setup.relative_to(ROOT)),old,0o644))
                if ready:subprocess.run(['udevadm','control','--reload'],check=True,timeout=30)
                ctl('daemon-reload');ctl('reload','dbus.service');ctl('enable','drives-helper.service');ctl('restart','drives-helper.service')
            else:
                ctl('disable','drives-helper.service')
                # Retain code, rules, keys, journals and binds for recovery.
                # Only the independently installed guard is removed from code.
                removals=list(external)+[CODE+'/normal_boot_guard.py']
                for name in removals:
                    p=target(name);old=read(p)
                    if old is not None:changed.append((name,old,p.stat().st_mode&0o777));p.unlink()
                ctl('daemon-reload');ctl('reload','dbus.service')
    except BaseException:
        for name,data,mode in reversed(changed):
            p=target(name)
            if data is None:p.unlink(missing_ok=True)
            else:atomic(p,data,mode)
        if swapped:
            failed=transaction/'failed';os.rename(target(CODE),failed)
            if had_code:os.rename(previous,target(CODE))
        if os.path.lexists(enable_link):
            if not enable_link.is_symlink() or os.readlink(enable_link) not in ('/etc/systemd/system/drives-helper.service','../drives-helper.service',str(target('/etc/systemd/system/drives-helper.service'))):
                refuse(enable_link,'enable link changed unexpectedly during rollback')
            enable_link.unlink()
        if enable_before is not None:
            os.symlink(enable_before,enable_link)
        if swapped or changed:
            # Retain the first error. Rollback restart is best effort; the
            # exact old tree remains available even if PID1 itself is failing.
            try:
                ctl('daemon-reload')
                if stopped and previous_active_state=='active':ctl('start','drives-helper.service')
            except Exception:pass
        raise
    finally:
        held.close()
        # A previous version is deliberately retained, not recursively removed
        # on upgrade. It is root-private and does not participate in imports.
        if not previous.exists():shutil.rmtree(transaction)
    print('Helper installed. No disk was provisioned or folder moved.' if action=='install' else 'Helper disabled and policies removed. Keys, journals, crypttab, fstab, code and data retained for recovery.')


if __name__=='__main__':
    try:
        if os.geteuid()!=0:raise Failure('Run setup/removal explicitly with sudo bash helper/install.sh or helper/uninstall.sh')
        if len(sys.argv)!=3 or sys.argv[1] not in ('install','uninstall'):raise Failure('invalid installation invocation')
        execute(sys.argv[1],pathlib.Path(sys.argv[2]))
    except (Failure,OSError,subprocess.SubprocessError) as e:raise SystemExit(str(e))
