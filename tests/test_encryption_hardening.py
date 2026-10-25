"""Mock-bound root components only; never invoke storage or system services."""
import copy,os,pathlib,sys,types
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import Common,Failure

@pytest.fixture
def setup_drive(tmp_path,monkeypatch):
    from helper import provisioning as p
    req=dict(byId='/dev/disk/by-id/unit-selected',serial='UNIT0001',name='unit',mountpoint='/data17',erase=False,confirmation='0001',autoUnlock=False)
    device='/dev/unit-selected';part='/dev/unexpected-part7';obj=p.block_object(device);part_obj='/org/fake/returned_partition'
    disk=dict(name=device,serial=req['serial'],system=False,mounts=[],selectable=True)
    state=dict(disks=[disk],rootEncrypted=False)
    events=[];keys=tmp_path/'keys';keys.mkdir(mode=0o700)
    Path=pathlib.Path;realpath=os.path.realpath;orig_stat=os.stat
    monkeypatch.setattr(p.pathlib,'Path',lambda path:Path(keys) if str(path)=='/etc/cryptsetup-keys.d' else Path(path))
    monkeypatch.setattr(p.os.path,'realpath',lambda path,**kw:device if path==req['byId'] else (part if path in (part,req['byId']+'-part1','/dev/disk/by-partuuid/partition-uuid') else realpath(path,**kw)))
    monkeypatch.setattr(p.os,'stat',lambda path,*a,**kw:types.SimpleNamespace(st_mode=0o60600,st_rdev=42 if str(path)==device else 43) if str(path) in (device,part) else orig_stat(path,*a,**kw))
    monkeypatch.setattr(p,'snapshot',lambda:copy.deepcopy(state))
    monkeypatch.setattr(p,'blocks',lambda:{'blockdevices':[{'name':device}]})
    monkeypatch.setattr(p,'chains',lambda _: {})
    monkeypatch.setattr(p,'mount_rows',lambda:[])
    monkeypatch.setattr(p,'prepare_mountpoint',lambda path:None)
    monkeypatch.setattr(p,'hide_from_automount',lambda *a,**kw:events.append(('hide',kw)))
    def run(argv,**kw):
        events.append(('run',argv,kw))
        if argv[:2]==['cryptsetup','luksUUID']:return b'fixture-luks-uuid'
        if argv[0]=='wipefs':return b'{"signatures":[]}'
        if argv[0]=='blkid':return b'btrfs' if argv[2]=='TYPE' else b'fixture-luks-uuid'
        if 'luksHeaderBackup' in argv:Path(argv[-1]).write_bytes(b'header fixture')
        return b''
    monkeypatch.setattr(p,'run',run)
    B='org.freedesktop.UDisks2.'
    def call(path,interface,method,signature,args,**kw):
        events.append(('udisks',method,path,args))
        if method=='CreatePartition':return (part_obj,)
        if method=='GetManagedObjects':return ({obj:{B+'Block':dict(Device=(device+'\0').encode(),DeviceNumber=42,Drive='/org/fake/drive')},'/org/fake/drive':{B+'Drive':dict(Serial=req['serial'])},part_obj:{B+'Block':dict(Device=(part+'\0').encode(),DeviceNumber=43,Drive='/org/fake/drive',Symlinks=[(req['byId']+'-part1\0').encode()]),B+'Partition':dict(Table=obj,Number=1,UUID='partition-uuid')}},)
        return ()
    monkeypatch.setattr(p,'udisks_call',call)
    c=Common(tmp_path/'state')
    monkeypatch.setattr(p,'finish',lambda j,common:dict(ok=True,id=j['id']))
    return types.SimpleNamespace(p=p,req=req,device=device,part=part,part_obj=part_obj,state=state,events=events,keys=keys,c=c)

def test_new_manual_never_persists_unlock_key(setup_drive):
    f=setup_drive
    f.p.provision(f.req,b'fixture-recovery-passphrase',f.c)
    assert list(f.keys.iterdir())==[], 'manual setup wrote a durable unlock key'
    assert not any(e[0]=='run' and 'luksAddKey' in e[1] for e in f.events), 'manual setup created a bypass keyslot'
    assert f.c.records('drives')[0]['keyfile'] is None

def test_manual_interruption_keeps_no_key(setup_drive,monkeypatch):
    f=setup_drive
    monkeypatch.setattr(f.p,'finish',lambda *a:(_ for _ in ()).throw(Failure('proof interruption')))
    with pytest.raises(Failure,match='proof interruption'):f.p.provision(f.req,b'fixture-recovery-passphrase',f.c)
    assert not list(f.keys.iterdir()), 'interrupted manual setup left an unlock key'

def test_auto_still_creates_tested_private_key(setup_drive):
    f=setup_drive;f.req['autoUnlock']=True;f.state['rootEncrypted']=True
    f.p.provision(f.req,b'fixture-recovery-passphrase',f.c)
    key=f.keys/'unit.key'
    assert key.stat().st_mode&0o777==0o400 and len(key.read_bytes())==64
    assert any(e[0]=='run' and 'luksAddKey' in e[1] for e in f.events)

def test_plain_root_still_refuses_auto_before_format(setup_drive):
    f=setup_drive;f.req['autoUnlock']=True
    with pytest.raises(Failure,match='encrypted OS root'):f.p.provision(f.req,b'fixture-recovery-passphrase',f.c)
    assert not any(e[:2]==('udisks','Format') for e in f.events)

def test_hotplug_after_preparation_never_formats_replacement(setup_drive,monkeypatch):
    f=setup_drive
    monkeypatch.setattr(f.p,'hide_from_automount',lambda *a,**k:f.state['disks'][0].update(serial='OTHER0002'))
    try:f.p.provision(f.req,b'fixture-recovery-passphrase',f.c)
    except Failure:pass
    assert not any(e[:2]==('udisks','Format') for e in f.events), 'stale selected name reached Format'

def test_hotplug_after_partition_creation_never_formats_replacement(setup_drive,monkeypatch):
    f=setup_drive;call=f.p.udisks_call
    def replace(*args,**kwargs):
        result=call(*args,**kwargs)
        if args[2]=='CreatePartition':f.state['disks'][0]['serial']='OTHER0002'
        return result
    monkeypatch.setattr(f.p,'udisks_call',replace)
    try:f.p.provision(f.req,b'fixture-recovery-passphrase',f.c)
    except Failure:pass
    assert len([e for e in f.events if e[:2]==('udisks','Format')])==1, 'replacement partition reached encrypted Format'

def test_returned_partition_device_not_kernel_name_arithmetic(setup_drive):
    f=setup_drive
    f.p.provision(f.req,b'fixture-recovery-passphrase',f.c)
    assert f.c.records('drives')[0]['partition']==f.part, 'guessed partition instead of returned object Device'

@pytest.fixture
def finish_drive(setup_drive,monkeypatch):
    from helper import driveconfig as d,common
    f=setup_drive;name='/dev/mapper/unit';identifier='a'*32
    tree={name:[[{'name':f.device,'type':'disk'},{'name':f.part,'type':'part'},{'name':name,'type':'crypt'}]]}
    monkeypatch.setattr(d,'snapshot',f.p.snapshot);monkeypatch.setattr(d,'blocks',f.p.blocks)
    monkeypatch.setattr(d,'chains',lambda _:tree);monkeypatch.setattr(d,'mount_rows',lambda:[])
    monkeypatch.setattr(d,'prepare_mountpoint',lambda *a:None)
    monkeypatch.setattr(common,'host_mount',lambda *a:None);monkeypatch.setattr(common,'hide_from_automount',lambda *a:None)
    exists=os.path.exists
    monkeypatch.setattr(os.path,'exists',lambda path:True if path==name else exists(path))
    base_run=f.p.run;active={'0':{},'1':{}}
    def run(argv,**kw):
        if 'luksDump' in argv:return __import__('json').dumps({'keyslots':active}).encode()
        if '--key-slot' in argv:
            expected='0' if argv[argv.index('--key-file')+1]=='-' else '1'
            if argv[argv.index('--key-slot')+1]!=expected:raise Failure('cryptsetup failed (exit 2)')
            return b''
        if 'luksKillSlot' in argv:active.pop(argv[-1]);return b''
        if argv[0]=='systemd-run':return b'{"readWrite":true}'
        return base_run(argv,**kw)
    monkeypatch.setattr(d,'run',run)
    j={**f.req,'id':identifier,'state':'unfinished','interruptedState':'formatted','manualSecretOnly':True,'keyfile':None,'partition':f.part,'header':str(f.c.state_dir/'drives'/(identifier+'.header'))}
    f.c.journal('drives',identifier,j)
    f.d=d;f.j=j;f.active=active
    return f

def test_manual_secret_only_finish_needs_no_durable_key(finish_drive):
    f=finish_drive
    try:result=f.d.finish(f.j,f.c)
    except Failure as exc:result={'state':str(exc)}
    assert result['state']=='ready' and not list(f.keys.iterdir())
    assert f.c.read('drives',f.j['id'])['keyfile'] is None

@pytest.mark.parametrize('stage',['formatted','finalizing'])
def test_legacy_manual_journals_still_finish(finish_drive,stage):
    f=finish_drive;f.j.pop('manualSecretOnly')
    f.j['interruptedState']=stage
    key=f.keys/'unit.key';key.write_bytes(b'legacy fixture');key.chmod(0o400);f.j['keyfile']=str(key)
    if stage=='finalizing':f.j['mountProof']={'readWrite':True}
    f.c.journal('drives',f.j['id'],f.j)
    assert f.d.finish(f.j,f.c)['state']=='ready'
    assert not list(f.keys.iterdir())

def test_recovery_never_reformats_partial_filesystem(finish_drive,monkeypatch):
    f=finish_drive;run=f.d.run
    monkeypatch.setattr(f.d,'run',lambda argv,**kw:b'ext4' if argv[:3]==['blkid','-s','TYPE'] else run(argv,**kw))
    with pytest.raises(Failure,match='never reformat'):f.d.finish(f.j,f.c)

def test_secret_only_missing_mapper_requires_recovery_not_keyfile(finish_drive,monkeypatch):
    f=finish_drive;exists=os.path.exists
    monkeypatch.setattr(os.path,'exists',lambda path:False if path=='/dev/mapper/unit' else exists(path))
    with pytest.raises(Failure,match='recovery unlock required'):f.d.finish(f.j,f.c)
    assert not list(f.keys.iterdir())

def test_legacy_manual_resume_retires_keyslot_before_key_deletion(finish_drive,monkeypatch):
    f=finish_drive;f.j.pop('manualSecretOnly');key=f.keys/'unit.key'
    key.write_bytes(b'legacy fixture');key.chmod(0o400);f.j['keyfile']=str(key)
    active={'0':{},'1':{}};events=[];execute=f.d.run
    def run(argv,**kw):
        if 'luksDump' in argv:return __import__('json').dumps({'keyslots':active}).encode()
        if '--test-passphrase' in argv and '--key-slot' in argv:
            if argv[argv.index('--key-slot')+1]!='1':raise Failure('cryptsetup failed (exit 2)')
            return b''
        if 'luksKillSlot' in argv:
            events.append('retire');assert key.exists();active.pop(argv[-1]);return b''
        if 'luksHeaderBackup' in argv:events.append('backup-with-'+','.join(active))
        return execute(argv,**kw)
    monkeypatch.setattr(f.d,'run',run)
    assert f.d.finish(f.j,f.c)['state']=='ready'
    assert set(active)=={'0'}, 'legacy random unlock keyslot remains active'
    assert events[-1]=='backup-with-0', 'recovery header still contains retired bypass slot'
    assert not key.exists()


def test_legacy_retirement_resumes_after_header_refresh_interruption(finish_drive,monkeypatch):
    f=finish_drive;f.j.pop('manualSecretOnly');key=f.keys/'unit.key'
    key.write_bytes(b'legacy fixture');key.chmod(0o400);f.j['keyfile']=str(key)
    execute=f.d.run;cuts=[];kills=[]
    def run(argv,**kw):
        if 'luksKillSlot' in argv:kills.append(argv[-1])
        if 'luksHeaderBackup' in argv and '1' not in f.active and not cuts:
            cuts.append(True);assert key.exists();raise Failure('header refresh cut')
        return execute(argv,**kw)
    monkeypatch.setattr(f.d,'run',run)
    with pytest.raises(Failure,match='header refresh cut'):f.d.finish(f.j,f.c)
    durable=f.c.read('drives',f.j['id'])
    assert durable['state']=='finalizing' and durable['manualKeySlot']=='1' and key.exists()
    assert f.d.finish(durable,f.c)['state']=='ready'
    assert kills==['1'] and not key.exists() and set(f.active)=={'0'}

def test_legacy_retirement_never_removes_last_slot(finish_drive):
    f=finish_drive;f.j.pop('manualSecretOnly');key=f.keys/'unit.key'
    key.write_bytes(b'legacy fixture');key.chmod(0o400);f.j['keyfile']=str(key);f.active.pop('0')
    with pytest.raises(Failure,match='last recovery keyslot'):f.d.finish(f.j,f.c)
    assert key.exists() and set(f.active)=={'1'}

def test_legacy_missing_key_two_slots_requires_administrator_inspection(finish_drive):
    f=finish_drive;f.j.pop('manualSecretOnly');f.j['interruptedState']='finalizing';f.j['mountProof']={'readWrite':True}
    with pytest.raises(Failure,match='recovery unlock required'):f.d.finish(f.j,f.c)
    with pytest.raises(Failure,match='administrator inspection'):
        f.d.finish(f.c.read('drives',f.j['id']),f.c,recovery_secret=b'fixture-recovery-passphrase')
    assert set(f.active)=={'0','1'}
    assert f.c.read('drives',f.j['id'])['state']!='ready'


@pytest.mark.parametrize('administrator_slots',[('1',),('7',),('1','7')])
def test_missing_key_preserves_preexisting_administrator_replacement(finish_drive,monkeypatch,administrator_slots):
    f=finish_drive;f.j.pop('manualSecretOnly')
    f.j.update(interruptedState='finalizing',mountProof={'readWrite':True})
    # The historical random key has already been removed by an administrator.
    # Its former slot number is not authority to revoke the replacement key.
    f.active.clear();f.active['0']={'kdf':{'salt':'original-recovery'}}
    for slot in administrator_slots:f.active[slot]={'kdf':{'salt':'administrator-'+slot}}
    before=copy.deepcopy(f.active)
    header=pathlib.Path(f.j['header']);header.write_bytes(b'preexisting current header');header.chmod(0o600)
    f.c.journal('drives',f.j['id'],f.j)
    calls=[];execute=f.d.run
    def run(argv,**kw):
        calls.append(argv)
        return execute(argv,**kw)
    monkeypatch.setattr(f.d,'run',run)
    error=None
    try:f.d.finish(f.j,f.c,recovery_secret=b'fixture-recovery-passphrase')
    except Failure as exc:error=exc
    assert f.active==before, 'unproven administrator keyslot was revoked'
    assert not any('luksKillSlot' in argv for argv in calls)
    assert not any('luksHeaderBackup' in argv for argv in calls), 'ambiguous retirement refreshed the header'
    assert header.read_bytes()==b'preexisting current header'
    durable=f.c.read('drives',f.j['id'])
    assert durable['state']!='ready' and not durable.get('manualSecretOnly')
    assert 'manualKeySlot' not in durable and 'manualRetirementUUID' not in durable
    assert error is not None and 'administrator inspection' in str(error)


def test_missing_key_single_authenticated_recovery_slot_finishes_without_removal(finish_drive,monkeypatch):
    f=finish_drive;f.j.pop('manualSecretOnly');f.j.update(interruptedState='finalizing',mountProof={'readWrite':True})
    f.active.pop('1');calls=[];execute=f.d.run
    def run(argv,**kw):
        calls.append(argv)
        return execute(argv,**kw)
    monkeypatch.setattr(f.d,'run',run)
    assert f.d.finish(f.j,f.c,recovery_secret=b'fixture-recovery-passphrase')['state']=='ready'
    assert set(f.active)=={'0'} and not any('luksKillSlot' in argv for argv in calls)
    durable=f.c.read('drives',f.j['id'])
    assert durable['manualKeySlot'] is None and durable['manualSecretOnly']


def test_missing_key_single_unmatched_slot_is_not_adopted(finish_drive):
    f=finish_drive;f.j.pop('manualSecretOnly');f.j.update(interruptedState='finalizing',mountProof={'readWrite':True})
    f.active.pop('0')
    with pytest.raises(Failure,match='ambiguous'):
        f.d.finish(f.j,f.c,recovery_secret=b'fixture-recovery-passphrase')
    assert set(f.active)=={'1'} and f.c.read('drives',f.j['id'])['state']!='ready'

def test_legacy_retry_refuses_reused_keyslot_identity(finish_drive,monkeypatch):
    f=finish_drive;f.j.pop('manualSecretOnly');key=f.keys/'unit.key'
    key.write_bytes(b'legacy fixture');key.chmod(0o400);f.j['keyfile']=str(key)
    execute=f.d.run
    def run(argv,**kw):
        if 'luksKillSlot' in argv:raise Failure('kill cut')
        return execute(argv,**kw)
    monkeypatch.setattr(f.d,'run',run)
    with pytest.raises(Failure,match='kill cut'):f.d.finish(f.j,f.c)
    f.active['1']={'kdf':{'salt':'replacement'}}
    with pytest.raises(Failure,match='keyslot changed'):f.d.finish(f.c.read('drives',f.j['id']),f.c)
    assert key.exists() and len(f.active)==2


@pytest.mark.parametrize('secret_only,stage',[(True,'encrypted'),(False,'finalizing')])
def test_unfinished_manual_can_recover_with_passphrase(setup_drive,monkeypatch,secret_only,stage):
    f=setup_drive;identifier='a'*32
    f.c.journal('drives',identifier,{**f.req,'id':identifier,'state':'unfinished','interruptedState':stage,'manualSecretOnly':secret_only,'keyfile':None,'partition':f.part})
    monkeypatch.setattr(f.p,'configured_partition',lambda j:f.part)
    called=[]
    monkeypatch.setattr(f.p,'finish',lambda j,c,**kw:called.append(j['id']) or {'ok':True,'state':'ready'})
    try:result=f.p.unlock(identifier,b'fixture-recovery-passphrase',f.c)
    except Failure as exc:result={'state':str(exc)}
    assert result['state']=='ready' and called==[identifier]
    assert any(e[0]=='run' and e[1][:4]==['cryptsetup','open','--key-file','-'] and e[2]['data']==b'fixture-recovery-passphrase' for e in f.events)
