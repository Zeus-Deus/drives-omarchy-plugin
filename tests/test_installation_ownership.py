"""Root-only, path-remapped guest fixtures; never invoke native systemctl."""
import hashlib,json,os,pathlib,shutil,subprocess,sys
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from helper import udevhide
from helper.common import Failure
UUID='64323122-325f-45d2-bd2b-a18b97576363'

@pytest.mark.parametrize('operation',['write','setup','apply'])
def test_foreign_runtime_rules_survive_before_any_mutation(tmp_path,monkeypatch,operation):
    monkeypatch.setattr(udevhide,'RULES',tmp_path)
    monkeypatch.setattr(udevhide,'run',lambda *a,**k:b'')
    final=tmp_path/'90-drives-data.rules'; setup=tmp_path/'90-drives-data-setup.rules'
    target=final if operation=='write' else setup
    target.write_bytes(b'# administrator rule\n')
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(Failure):
        if operation=='write':udevhide.write('data',UUID,tmp_path)
        elif operation=='setup':udevhide.apply_setup('data','TEST0001',tmp_path)
        else:udevhide.apply('data',UUID)
    assert {p.name:p.read_bytes() for p in tmp_path.iterdir()}==before

# The exact legacy body is a frozen shipped specimen, not reconstructed from
# whatever current rule_text() happens to accept.
LEGACY=('# Drives plugin: data unlocks with the OS or via the Drives panel, not desktop automount.\n'
'SUBSYSTEM=="block", ENV{ID_FS_TYPE}=="crypto_LUKS", ENV{ID_FS_UUID}=="'+UUID+'", ENV{UDISKS_IGNORE}="1"\n')

def test_exact_legacy_rule_upgrades_but_edited_body_refuses(tmp_path):
    path=tmp_path/'90-drives-data.rules';path.write_text(LEGACY)
    assert udevhide.write('data',UUID,tmp_path)
    assert not udevhide.write('data',UUID,tmp_path)
    path.write_bytes(path.read_bytes()+b'# customization\n')
    with pytest.raises(Failure):udevhide.write('data',UUID,tmp_path)


def snapshot(root):
    return {str(p.relative_to(root)):(p.lstat().st_mode,p.readlink().as_posix() if p.is_symlink() else hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else '') for p in root.rglob('*')}


def harness(tmp_path,action='install'):
    assert os.geteuid()==0,'only run in authorized guest'
    fs=tmp_path/'fs';fs.mkdir();source=tmp_path/'source';source.mkdir()
    for name in ('helper','packaging'):shutil.copytree(ROOT/name,source/name,ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(ROOT/'topology.py',source/'topology.py')
    # Test-only remapping, no environment-based root override in production.
    module=source/'helper/installation.py'
    if module.exists():module.write_text(module.read_text().replace("ROOT=pathlib.Path('/')",'ROOT=pathlib.Path('+repr(str(fs))+')'))
    # Map BOTH entrypoints: lifecycle tests deliberately switch scripts.
    # Mapping only the first runner lets a later uninstall escape the fixture.
    for entrypoint in ('install','uninstall'):
        script=source/'helper'/f'{entrypoint}.sh';text=script.read_text()
        if not module.exists():
            for prefix in ('/usr/lib/drives-helper','/usr/share/polkit-1','/etc/','/mnt/','/run/drives-maintenance','/drives-maintenance-request.json'):
                text=text.replace(prefix,str(fs)+prefix)
            text=text.replace('/usr/bin/python3 -B -m helper.udevhide --all','true')
            import re
            leaked=re.findall(r'(?<![\w.-])/(?:etc|usr/lib|usr/share|mnt|run|var)/[\w./@-]+',text)
            assert not leaked,('fixture path escape',entrypoint,leaked)
        script.write_text(text)
    runner=source/'helper'/f'{action}.sh'
    bin=tmp_path/'bin';bin.mkdir();log=tmp_path/'calls.jsonl'
    spy=bin/'systemctl';spy.write_text('#!/usr/bin/python3\nimport json,os,sys\na=sys.argv[1:]\nwith open(os.environ["SPY_LOG"],"a") as f:f.write(json.dumps(a)+"\\n")\nif a[0]=="show":\n p=next((v for v in a if v.startswith("--property=")),"")\n print(os.environ.get("TARGET_STATE","inactive") if "ActiveState" in p else (os.environ.get("FRAGMENT","") if "FragmentPath" in p else ""))\n')
    spy.chmod(0o755)
    for tool in ('udevadm',):shutil.copy2(spy,bin/tool)
    return fs,source,runner,log,{'PATH':str(bin)+':/usr/bin:/bin','SPY_LOG':str(log),'LC_ALL':'C','PYTHONDONTWRITEBYTECODE':'1'}


def invoke(runner,env):return subprocess.run(['bash',str(runner)],env=env,capture_output=True,timeout=30)


def test_install_foreign_last_target_has_zero_filesystem_or_service_mutation(tmp_path):
    fs,source,runner,log,env=harness(tmp_path)
    target=fs/'etc/dbus-1/system.d/io.github.zeus_deus.Drives.conf';target.parent.mkdir(parents=True);target.write_bytes(b'foreign policy')
    before=snapshot(fs);result=invoke(runner,env)
    assert result.returncode!=0,'installer adopted foreign policy'
    assert snapshot(fs)==before,'preflight refusal happened after filesystem mutation'
    assert not log.exists(),'preflight refusal changed service state'


def test_install_preserves_private_shared_directory_modes(tmp_path):
    fs,source,runner,log,env=harness(tmp_path)
    dirs=[fs/'mnt/drives',fs/'etc/cryptsetup-keys.d']
    for p in dirs:p.mkdir(parents=True,mode=0o700)
    result=invoke(runner,env)
    assert result.returncode==0,result.stderr
    assert all(p.stat().st_mode&0o777==0o700 for p in dirs)


def test_uninstall_foreign_unit_never_stops_service(tmp_path):
    fs,source,runner,log,env=harness(tmp_path,'uninstall')
    p=fs/'etc/systemd/system/drives-helper.service';p.parent.mkdir(parents=True);p.write_bytes(b'foreign unit')
    before=snapshot(fs);result=invoke(runner,env)
    assert result.returncode!=0,'uninstaller adopted foreign unit'
    assert snapshot(fs)==before
    assert not log.exists(),'must establish unit ownership before service contact'


# Every pre-existing named installation destination, not a differently named
# foreign-file control. This list is deliberately stable across RED/GREEN.
DESTINATIONS=['/usr/lib/drives-helper/helper/__init__.py', '/usr/lib/drives-helper/helper/agent.py', '/usr/lib/drives-helper/helper/bootscreen.py', '/usr/lib/drives-helper/helper/client.py', '/usr/lib/drives-helper/helper/common.py', '/usr/lib/drives-helper/helper/configwriter.py', '/usr/lib/drives-helper/helper/destination.py', '/usr/lib/drives-helper/helper/driveconfig.py', '/usr/lib/drives-helper/helper/health.py', '/usr/lib/drives-helper/helper/latch.py', '/usr/lib/drives-helper/helper/maintenance.py', '/usr/lib/drives-helper/helper/maintenance_audit.py', '/usr/lib/drives-helper/helper/mountpoint.py', '/usr/lib/drives-helper/helper/mountproof.py', '/usr/lib/drives-helper/helper/moves.py', '/usr/lib/drives-helper/helper/normal_boot_guard.py', '/usr/lib/drives-helper/helper/offline.py', '/usr/lib/drives-helper/helper/preparedrive.py', '/usr/lib/drives-helper/helper/provisioning.py', '/usr/lib/drives-helper/helper/quarantine.py', '/usr/lib/drives-helper/helper/service.py', '/usr/lib/drives-helper/helper/udevhide.py', '/usr/lib/drives-helper/topology.py', '/usr/lib/drives-helper/normal_boot_guard.py', '/etc/systemd/system/drives-helper.service', '/etc/systemd/system/drives-maintenance.target', '/etc/systemd/system/drives-maintenance-splash.service', '/etc/systemd/system/drives-maintenance-audit.service', '/etc/systemd/system/drives-maintenance-worker.service', '/etc/systemd/system/drives-normal-boot-guard.service', '/etc/systemd/system/sysinit.target.d/drives-normal-boot-guard.conf', '/etc/systemd/system-generators/drives-maintenance-generator', '/usr/share/polkit-1/actions/io.github.zeus-deus.drives.policy', '/etc/dbus-1/system.d/io.github.zeus_deus.Drives.conf']

@pytest.mark.parametrize('name',DESTINATIONS)
@pytest.mark.parametrize('action',['install','uninstall'])
def test_each_foreign_destination_refuses_before_mutation(tmp_path,name,action):
    fs,source,runner,log,env=harness(tmp_path,action)
    p=fs/name.lstrip('/');p.parent.mkdir(parents=True);p.write_bytes(b'administrator-owned replacement')
    before=snapshot(fs);result=invoke(runner,env)
    assert result.returncode!=0,'foreign target was adopted: '+name
    assert snapshot(fs)==before,'foreign target was touched: '+name
    assert not log.exists(),'ownership must precede even service inspection'

@pytest.mark.parametrize('kind',['symlink','fifo','directory','oversized'])
@pytest.mark.parametrize('operation',['write','setup','apply'])
def test_runtime_unsafe_leaf_is_bounded_and_preserved(tmp_path,kind,operation):
    p=tmp_path/('90-drives-data.rules' if operation=='write' else '90-drives-data-setup.rules')
    if kind=='symlink':p.symlink_to(tmp_path/'absent')
    elif kind=='fifo':os.mkfifo(p)
    elif kind=='directory':p.mkdir()
    else:p.write_bytes(b'x'*9000)
    before=snapshot(tmp_path)
    code="""import pathlib,sys
from helper import udevhide as u
from helper.common import Failure
u.RULES=pathlib.Path(sys.argv[1]);u.run=lambda *a,**k:b''
try:
 if sys.argv[2]=='write':u.write('data',sys.argv[3],u.RULES)
 elif sys.argv[2]=='setup':u.apply_setup('data','TEST0001',u.RULES)
 else:u.apply('data',sys.argv[3])
except Failure:raise SystemExit(17)
"""
    try:result=subprocess.run([sys.executable,'-B','-c',code,str(tmp_path),operation,UUID],cwd=ROOT,capture_output=True,timeout=2)
    except subprocess.TimeoutExpired:pytest.fail('unsafe runtime rule read blocked instead of refusing')
    assert result.returncode==17,result.stderr
    assert snapshot(tmp_path)==before

@pytest.mark.parametrize('kind',['symlink-parent','fifo','directory','symlink-leaf'])
def test_install_unsafe_target_chain_is_preflighted(tmp_path,kind):
    fs,source,runner,log,env=harness(tmp_path)
    p=fs/'usr/lib/drives-helper/helper/common.py';p.parent.mkdir(parents=True)
    if kind=='symlink-parent':
        p.parent.rmdir();outside=tmp_path/'outside';outside.mkdir();p.parent.symlink_to(outside)
    elif kind=='fifo':os.mkfifo(p)
    elif kind=='directory':p.mkdir()
    else:p.symlink_to(tmp_path/'missing')
    before=snapshot(fs);result=invoke(runner,env)
    assert result.returncode!=0
    assert snapshot(fs)==before
    assert not log.exists()


def test_foreign_effective_unit_is_not_stopped(tmp_path):
    fs,source,runner,log,env=harness(tmp_path,'uninstall')
    p=fs/'etc/systemd/system/drives-helper.service';p.parent.mkdir(parents=True)
    p.write_bytes(b''.join(line for line in (source/'packaging/drives-helper.service').read_bytes().splitlines(keepends=True) if b'Drives-owned:' not in line))
    env['FRAGMENT']=str(fs/'run/systemd/system/drives-helper.service')
    result=invoke(runner,env)
    assert result.returncode!=0
    calls=[json.loads(x) for x in log.read_text().splitlines()]
    assert all(c[0] in ('show','list-units') for c in calls)
    assert p.exists()


def test_repeated_install_upgrade_uninstall_retains_user_state(tmp_path):
    fs,source,runner,log,env=harness(tmp_path)
    retained={}
    for name in ('etc/fstab','etc/crypttab','etc/cryptsetup-keys.d/data.key','var/lib/drives-helper/drives/synthetic.json','mnt/drives/data/user-file'):
        p=fs/name;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(b'{"state":"unfinished"}' if name.endswith('.json') else b'retain exact live bytes')
        retained[name]=p.read_bytes()
    for action in ('install','install','uninstall','uninstall','install'):
        runner=source/'helper'/f'{action}.sh';result=invoke(runner,env)
        assert result.returncode==0,result.stderr
        assert {name:(fs/name).read_bytes() for name in retained}==retained
    manifest=json.loads((fs/'usr/lib/drives-helper/.ownership.json').read_text())
    assert manifest['owner']=='io.github.zeus-deus.drives'


def test_verified_staging_failure_keeps_previous_code_and_service(tmp_path,monkeypatch):
    installation=pytest.importorskip('helper.installation')
    fs,source,runner,log,env=harness(tmp_path)
    assert invoke(runner,env).returncode==0
    before=snapshot(fs/'usr/lib/drives-helper');log.unlink()
    monkeypatch.setattr(installation,'ROOT',fs)
    monkeypatch.setattr(installation,'ctl',lambda *args: 'inactive' if 'ActiveState' in ' '.join(args) else '')
    original=installation.atomic;count=0
    def fail_stage(path,*a,**kw):
        nonlocal count
        count+=1
        if count==4:raise OSError('injected stage ENOSPC')
        return original(path,*a,**kw)
    monkeypatch.setattr(installation,'atomic',fail_stage)
    with pytest.raises(OSError,match='stage ENOSPC'):installation.execute('install',source)
    assert snapshot(fs/'usr/lib/drives-helper')==before
    assert not log.exists()


def test_late_activation_failure_rolls_back_exact_tree_and_policies(tmp_path,monkeypatch):
    installation=pytest.importorskip('helper.installation')
    fs,source,runner,log,env=harness(tmp_path)
    assert invoke(runner,env).returncode==0
    before=snapshot(fs/'usr/lib/drives-helper')
    policy=fs/'etc/dbus-1/system.d/io.github.zeus_deus.Drives.conf';oldpolicy=policy.read_bytes()
    # A legitimate new version differs, so rollback cannot be faked by
    # re-copying identical source bytes.
    (source/'helper/health.py').write_bytes((source/'helper/health.py').read_bytes()+b'\n# new version\n')
    monkeypatch.setattr(installation,'ROOT',fs)
    monkeypatch.setattr(installation,'ctl',lambda *args:'inactive' if 'ActiveState' in ' '.join(args) else '')
    original=installation.atomic;failed=False
    def fail_once(path,*a,**kw):
        nonlocal failed
        if pathlib.Path(path)==policy and not failed:
            failed=True;raise OSError('injected activation ENOSPC')
        return original(path,*a,**kw)
    monkeypatch.setattr(installation,'atomic',fail_once)
    with pytest.raises(OSError,match='activation ENOSPC'):installation.execute('install',source)
    assert snapshot(fs/'usr/lib/drives-helper')==before
    assert policy.read_bytes()==oldpolicy



def test_failed_uninstall_restores_enable_link(tmp_path,monkeypatch):
    installation=pytest.importorskip('helper.installation')
    fs,source,runner,log,env=harness(tmp_path)
    assert invoke(runner,env).returncode==0
    link=fs/'etc/systemd/system/multi-user.target.wants/drives-helper.service'
    link.parent.mkdir(parents=True);link.symlink_to('../drives-helper.service')
    monkeypatch.setattr(installation,'ROOT',fs)
    failed=False
    def ctl(*args):
        nonlocal failed
        if args[0]=='disable':link.unlink()
        if args==('reload','dbus.service') and not failed:
            failed=True;raise Failure('injected reload failure')
        return 'inactive' if 'ActiveState' in ' '.join(args) else ''
    monkeypatch.setattr(installation,'ctl',ctl)
    with pytest.raises(Failure,match='injected reload'):installation.execute('uninstall',source)
    assert link.is_symlink() and os.readlink(link)=='../drives-helper.service'
    assert (fs/'etc/systemd/system/drives-helper.service').exists()


def test_identical_reinstall_does_not_accumulate_previous_trees(tmp_path):
    fs,source,runner,log,env=harness(tmp_path)
    assert invoke(runner,env).returncode==0
    before=sorted((fs/'usr/lib').glob('.drives-transaction-*'))
    assert invoke(runner,env).returncode==0
    assert sorted((fs/'usr/lib').glob('.drives-transaction-*'))==before


@pytest.fixture
def rollback_component(tmp_path, monkeypatch):
    """Execute only Python transaction logic; every privileged seam is inert.

    No harness()/invoke() or shell scripts are used. ROOT and all external
    authority are replaced before execute() can be called, including recovery.
    """
    import contextlib
    from unittest.mock import Mock
    from helper import installation

    fs=tmp_path/'fs';fs.mkdir()
    source=tmp_path/'source';(source/'helper').mkdir(parents=True)
    (source/'helper/legacy_install_hashes.json').write_text('{"files":{}}')
    files={
        installation.CODE+'/helper/health.py':(b'# old code\n',0o644),
        installation.CODE+'/normal_boot_guard.py':(b'# retained guard\n',0o644),
        '/etc/systemd/system/drives-helper.service':(b'# owned unit\n',0o644),
        '/etc/dbus-1/system.d/io.github.zeus_deus.Drives.conf':(b'owned policy\n',0o644),
    }
    def confined(path):
        path=pathlib.Path(path)
        assert path.is_relative_to(fs), ('filesystem escape',path)
        return path
    def read(path,*args):
        path=confined(path)
        return path.read_bytes() if path.exists() else None
    def atomic(path,data,mode):
        path=confined(path);path.write_bytes(data);path.chmod(mode)
    def directory(path,**kwargs):
        confined(path)
    def forbidden(*args,**kwargs):
        raise AssertionError(('external command forbidden',args))
    for name,(data,mode) in files.items():
        path=fs/name.lstrip('/');path.parent.mkdir(parents=True,exist_ok=True)
        atomic(path,data,mode)
    manifest=fs/installation.MANIFEST.lstrip('/')
    manifest.write_text(json.dumps({'owner':'io.github.zeus-deus.drives',
                                   'files':{k:installation.digest(v[0]) for k,v in files.items()}}))
    for name in (*installation.SHARED,'/var/lib/drives-helper'):
        (fs/name.lstrip('/')).mkdir(parents=True,exist_ok=True)
    (fs/'etc/crypttab').write_bytes(b'# retained crypttab\n')
    link=fs/'etc/systemd/system/multi-user.target.wants/drives-helper.service'
    link.parent.mkdir();link.symlink_to('../drives-helper.service')
    # Force a genuine code-tree swap on reinstall, not an identical-byte path.
    files[installation.CODE+'/helper/health.py']=(b'# candidate code\n',0o644)
    state={'active':'inactive','fail':False}
    calls=[]
    def ctl(*args):
        calls.append(args)
        if args[0]=='show':
            if args[1]=='drives-helper.service' and '--property=ActiveState' in args:
                return state['active']
            return 'inactive' if '--property=ActiveState' in args else ''
        if args[0]=='list-units':return ''
        if args[0]=='stop':state['active']='inactive'
        if args[0] in ('start','restart'):state['active']='active'
        if args[0]=='disable':link.unlink(missing_ok=True)
        if args==('reload','dbus.service') and state['fail']:
            state['fail']=False
            raise Failure('injected late D-Bus reload failure')
        return ''
    monkeypatch.setattr(installation,'ROOT',fs)
    monkeypatch.setattr(installation,'ctl',ctl)
    lease=Mock(side_effect=contextlib.nullcontext)
    monkeypatch.setattr(installation,'lease',lease)
    monkeypatch.setattr(installation,'ready_rules',Mock(return_value=[]))
    monkeypatch.setattr(installation,'candidates',Mock(return_value=files))
    monkeypatch.setattr(installation,'directory',directory)
    monkeypatch.setattr(installation,'read',read)
    monkeypatch.setattr(installation,'atomic',atomic)
    monkeypatch.setattr(installation.subprocess,'run',forbidden)
    monkeypatch.setattr(udevhide,'run',forbidden)
    monkeypatch.setattr(udevhide,'write',forbidden)
    return installation,source,fs,state,calls,lease


@pytest.mark.parametrize('action',['install','uninstall'])
@pytest.mark.parametrize('previous',['inactive','active','failed','deactivating'])
def test_component_failed_transaction_preserves_prior_running_state(rollback_component,action,previous):
    installation,source,fs,state,calls,lease=rollback_component
    state.update(active=previous,fail=True)
    code=fs/installation.CODE.lstrip('/')
    before=snapshot(code)
    unit=fs/'etc/systemd/system/drives-helper.service';unit_before=unit.read_bytes()
    policy=fs/'etc/dbus-1/system.d/io.github.zeus_deus.Drives.conf';policy_before=policy.read_bytes()
    link=fs/'etc/systemd/system/multi-user.target.wants/drives-helper.service'
    with pytest.raises(Failure,match='injected late D-Bus reload failure'):
        installation.execute(action,source)
    assert snapshot(code)==before
    assert unit.read_bytes()==unit_before and policy.read_bytes()==policy_before
    assert link.is_symlink() and os.readlink(link)=='../drives-helper.service'
    lease.assert_called_once_with()
    assert ('stop','drives-helper.service') in calls
    expected=[('start','drives-helper.service')] if previous=='active' else []
    assert [c for c in calls if c[0] in ('start','restart')]==expected
    assert state['active']==('active' if previous=='active' else 'inactive')


@pytest.mark.parametrize('previous',['inactive','active','failed','deactivating'])
def test_component_successful_install_explicitly_restarts_helper(rollback_component,previous):
    installation,source,fs,state,calls,lease=rollback_component
    state['active']=previous
    installation.execute('install',source)
    assert [c for c in calls if c[0] in ('start','restart')]==[('restart','drives-helper.service')]
    assert ('enable','drives-helper.service') in calls
    assert state['active']=='active'


@pytest.mark.parametrize('previous',['inactive','active'])
def test_component_successful_uninstall_leaves_helper_stopped(rollback_component,previous):
    installation,source,fs,state,calls,lease=rollback_component
    state['active']=previous
    installation.execute('uninstall',source)
    assert not [c for c in calls if c[0] in ('start','restart')]
    assert state['active']=='inactive'
