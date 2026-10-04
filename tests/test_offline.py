"""Protocol-2 worker logic on real directories (root, VM). Mounts, fstab and
system audit are stubbed here; the installed maintenance boot is exercised by
tests/vm_maintenance_e2e.py."""
import json,os,pathlib,sys
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import Common,Failure
from helper import moves,offline,configwriter

pytestmark=pytest.mark.skipif(os.geteuid()!=0,reason='quarantine needs root-owned ancestry')


def fixture(tmp_path,monkeypatch,state='awaiting-maintenance'):
    os.chmod(tmp_path,0o755)
    home=tmp_path/'home';home.mkdir(mode=0o755)
    source=home/'Videos';source.mkdir();(source/'clip.mkv').write_bytes(b'x'*4096);(source/'sub').mkdir();(source/'sub'/'note').write_text('keep')
    os.link(source/'clip.mkv',source/'sub'/'clip-link')
    disk=tmp_path/'data';disk.mkdir(mode=0o755);dest=disk/('drives-'+'a'*32);dest.mkdir(mode=0o700)
    c=Common(tmp_path/'state')
    j={'id':'a'*32,'state':state,'source':str(source),'backup':str(source)+'.pre-move','dest':str(dest),'destMount':str(disk),
       'sourceIdentity':moves.identity(str(source)),'destIdentity':moves.identity(str(dest)),'uid':os.getuid(),'verified':False,
       'maintenanceProtocol':2,'sourceMount':str(tmp_path),'destMapper':'data','boot_id':'old'}
    c.journal('moves',j['id'],j)
    w=offline.Offline(c,'22222222-2222-2222-2222-222222222222')
    calls=[]
    monkeypatch.setattr(w,'mount_source',lambda j:None)
    monkeypatch.setattr(w,'mount_destination',lambda j:None)
    monkeypatch.setattr(w,'admitted',lambda j,a:calls.append(('admitted',a)))
    monkeypatch.setattr(w.m,'changed',lambda *a,**k:None)
    monkeypatch.setattr(w.m,'placeholder',lambda j:(os.mkdir(j['source'],0) if not os.path.lexists(j['source']) else None))
    monkeypatch.setattr(w.m,'smoke',lambda j:calls.append('smoke'))
    monkeypatch.setattr(w.m,'bound',lambda j:False)
    monkeypatch.setattr(configwriter,'write_owned',lambda c,kind,id,line:calls.append(('fstab',line)))
    real=offline.run
    def fake_run(argv,**k):
        if argv[0] in ('/usr/bin/mount','/usr/bin/umount','/usr/bin/chattr'):calls.append(argv[0]);return b''
        return real(argv,**k)
    monkeypatch.setattr(offline,'run',fake_run)
    monkeypatch.setattr(offline,'qa_crash',lambda stage:None)
    monkeypatch.setattr(offline,'LOG',tmp_path/'maintenance.log')
    return c,w,j,source,dest,calls


def test_continue_quarantines_copies_verifies_and_binds(tmp_path,monkeypatch):
    c,w,j,source,dest,calls=fixture(tmp_path,monkeypatch)
    assert w.continue_move(j)=='moved'
    saved=c.read('moves',j['id'])
    assert saved['state']=='switched' and saved['verified'] is True and saved['cutover']['bootId']==w.boot
    original=pathlib.Path(saved['backup'])
    assert original.name=='original' and original.parent.parent.name=='.drives-quarantine'
    assert (original.parent.stat().st_mode&0o777)==0o700 and original.parent.stat().st_uid==0
    assert (dest/'sub'/'note').read_text()=='keep' and os.stat(dest/'clip.mkv').st_nlink==2
    assert moves.identity(str(original))[1:]==j['sourceIdentity'][1:]
    assert calls.count(('admitted','continue'))==2 and 'smoke' in calls
    line=[x for x in calls if isinstance(x,tuple) and x[0]=='fstab'][0][1]
    assert ' bind,nofail,' in line and str(source) in line


def test_failed_copy_restores_the_untouched_original(tmp_path,monkeypatch):
    c,w,j,source,dest,calls=fixture(tmp_path,monkeypatch)
    real=offline.run
    def broken(argv,**k):
        if argv[0]=='/usr/bin/rsync':raise Failure('rsync failed (exit 23)')
        return real(argv,**k) if argv[0] not in ('/usr/bin/mount','/usr/bin/umount','/usr/bin/chattr') else b''
    monkeypatch.setattr(offline,'run',broken)
    assert w.continue_move(j)=='restored after failure'
    saved=c.read('moves',j['id'])
    assert saved['state']=='paused' and saved['interruptedState']=='awaiting-maintenance' and 'quarantine' not in saved
    assert moves.identity(str(source))[1:]==j['sourceIdentity'][1:] and (source/'sub'/'note').read_text()=='keep'
    assert not (tmp_path/'.drives-quarantine'/j['id']).exists()


@pytest.mark.parametrize('crashed',['quarantining','copying','verifying'])
def test_interrupted_continue_puts_original_back_on_next_boot(tmp_path,monkeypatch,crashed):
    c,w,j,source,dest,calls=fixture(tmp_path,monkeypatch)
    # Reproduce the durable state left by a power cut after the rename.
    with moves.anchored_tree(str(source)) as (_,fd,_):store=offline.prepare_store(fd,str(tmp_path),j['id'])
    j['quarantine']=store;j['backup']=store['path']+'/original'
    offline.move_original(str(source),j['sourceIdentity'],store);j['state']=crashed;c.journal('moves',j['id'],j)
    assert not source.exists()
    assert w.continue_move(c.read('moves',j['id']))=='restored after interruption'
    saved=c.read('moves',j['id'])
    assert saved['state']=='paused' and saved['restoredFrom']==crashed and (source/'sub'/'note').read_text()=='keep'


def test_crash_before_rename_leaves_original_and_drops_empty_store(tmp_path,monkeypatch):
    c,w,j,source,dest,calls=fixture(tmp_path,monkeypatch)
    with moves.anchored_tree(str(source)) as (_,fd,_):offline.prepare_store(fd,str(tmp_path),j['id'])
    j['state']='quarantining';c.journal('moves',j['id'],j)
    assert w.continue_move(c.read('moves',j['id']))=='restored after interruption'
    assert source.is_dir() and not (tmp_path/'.drives-quarantine'/j['id']).exists()


def test_undo_restores_original_and_removes_copy(tmp_path,monkeypatch):
    c,w,j,source,dest,calls=fixture(tmp_path,monkeypatch)
    w.continue_move(j);j=c.read('moves',j['id'])
    os.rmdir(source)  # stub placeholder (real one is root-owned 000 + immutable)
    monkeypatch.setattr(w,'remove_placeholder',lambda j:None)
    assert w.rollback_move(j)=='undone'
    saved=c.read('moves',j['id'])
    assert saved['state']=='rolled-back' and not dest.exists()
    assert moves.identity(str(source))[1:]==j['sourceIdentity'][1:] and (source/'sub'/'note').read_text()=='keep'


def test_undo_ignores_directory_timestamp_only_changes(tmp_path,monkeypatch):
    c,w,j,source,dest,calls=fixture(tmp_path,monkeypatch)
    w.continue_move(j);j=c.read('moves',j['id'])
    (dest/'sub'/'scratch').write_text('x');(dest/'sub'/'scratch').unlink()  # bumps only the dir mtime
    os.rmdir(source);monkeypatch.setattr(w,'remove_placeholder',lambda j:None)
    assert w.rollback_move(j)=='undone'


def test_undo_refuses_when_new_work_would_be_lost(tmp_path,monkeypatch):
    c,w,j,source,dest,calls=fixture(tmp_path,monkeypatch)
    w.continue_move(j);j=c.read('moves',j['id'])
    (dest/'new-after-move.txt').write_text('work done on the new drive')
    assert 'refused' in w.rollback_move(j)
    saved=c.read('moves',j['id'])
    assert saved['state']=='switched' and (dest/'new-after-move.txt').exists() and pathlib.Path(saved['backup']).is_dir()


def test_latch_arms_next_boot_only_and_clears_exactly(tmp_path,monkeypatch):
    from helper import maintenance
    latch=tmp_path/'latch.json';monkeypatch.setattr(maintenance,'LATCH',latch)
    value=maintenance.write_latch('b'*32,'continue','11111111-1111-1111-1111-111111111111')
    assert json.loads(latch.read_text())==value and latch.stat().st_mode&0o777==0o600
    with pytest.raises(Failure,match='already waiting'):maintenance.write_latch('c'*32,'continue')
    with pytest.raises(Failure,match='another move'):maintenance.clear_latch('c'*32)
    maintenance.clear_latch('b'*32,'11111111-1111-1111-1111-111111111111')
    assert not latch.exists()


def test_worker_is_the_only_extra_unit_admitted():
    from helper import maintenance_audit as audit
    boot='22222222-2222-2222-2222-222222222222'
    request={'version':1,'moveId':'a'*32,'action':'continue','armedBootId':'11111111-1111-1111-1111-111111111111'}
    snap={'bootId':boot,'latch':request,'receipt':dict(request,bootId=boot,valid=True,error=''),'targetActive':True,
          'defaultTarget':audit.TARGET,'masksComplete':True,'targetRequires':sorted(audit.REQUIRES),'targetWants':[audit.WORKER_UNIT],
          'sessions':[],'jobs':[],'units':[{'unit':audit.WORKER_UNIT,'active':'activating'}],'processes':[],'callerUnit':audit.WORKER_UNIT}
    assert audit.validate_snapshot(snap,'a'*32,'continue',caller=audit.WORKER_UNIT)['ok']
    snap['targetWants']=[audit.WORKER_UNIT,'sshd.service']
    with pytest.raises(Failure):audit.validate_snapshot(snap,'a'*32,'continue',caller=audit.WORKER_UNIT)


def test_missing_keyfile_is_a_plain_refusal(tmp_path,monkeypatch):
    from helper import offline
    from helper.common import Failure
    tab=tmp_path/'crypttab';tab.write_text('data UUID=x '+str(tmp_path/'gone.key')+' luks,nofail\n')
    real=offline.read_regular
    monkeypatch.setattr(offline,'read_regular',lambda p,cap=0:real(str(tab),cap) if p=='/etc/crypttab' else real(p,cap))
    with pytest.raises(Failure,match='unlock key is missing'):offline.crypttab_key('data')
