"""Private destination regression components; privileged proofs run in the VM."""
import os,pathlib,sys
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper import moves,destination
from helper.common import Common,Failure


def plan(tmp_path):
    source=tmp_path/'source';source.mkdir(mode=0o755)
    (source/'synthetic-token').write_bytes(b'nonsecret regression fixture')
    (source/'synthetic-token').chmod(0o644)
    disk=tmp_path/'disk';disk.mkdir()
    manager=moves.MoveManager(Common(tmp_path/'state'))
    facts={'topology':{'chain':[{'uuid':'DST'}],'disk':{'serial':'TESTDATA'},'mount':{'fsroot':'/'}},
        'sourceIdentity':moves.identity(str(source)),'sourceUUID':'SRC','parentIdentity':moves.identity(str(tmp_path)),
        'mountIdentity':moves.identity(str(disk)),'uid':os.getuid(),'stats':moves.tree_stats(str(source)),
        'sourceMount':'/','destMapper':'fixture'}
    return manager,manager.new_plan(str(source),str(disk),facts)


def test_new_plan_binds_content_not_private_wrapper(tmp_path):
    manager,j=plan(tmp_path)
    assert j['destContainer']==j['destMount']+'/drives-'+j['id']
    assert j['dest']==j['destContainer']+'/content'
    assert j['destFSRoot']=='/drives-'+j['id']+'/content'
    from helper.offline import fstab_line
    assert fstab_line(j).split()[0]==j['dest']


@pytest.fixture
def root_metadata(monkeypatch):
    """Only model UID for components; no privilege/capability changes."""
    actual=os.fstat
    def root(fd):
        info=actual(fd);values=list(info);values[4]=0
        return os.stat_result(values)
    monkeypatch.setattr(os,'fstat',root)


def test_private_creation_journals_both_durable_identities(tmp_path,monkeypatch,root_metadata):
    manager,j=plan(tmp_path);synced=[];actual=os.fsync
    def sync(fd):synced.append(os.readlink('/proc/self/fd/'+str(fd)));actual(fd)
    monkeypatch.setattr(os,'fsync',sync)
    manager.make_destination(j)
    saved=manager.c.read('moves',j['id'])
    assert saved['containerIdentity']==moves.identity(j['destContainer'])
    assert saved['destIdentity']==moves.identity(j['dest'])
    assert pathlib.Path(j['destContainer']).stat().st_mode&0o777==0o700
    assert set((j['destMount'],j['destContainer'],j['dest'])).issubset(synced)
    # rsync may restore 0755 on content; it must never chmod its wrapper.
    pathlib.Path(j['dest']).chmod(0o755)
    assert pathlib.Path(j['destContainer']).stat().st_mode&0o777==0o700


@pytest.mark.parametrize('kind',['mode','uid','acl'])
def test_private_creation_rejects_nonprivate_wrapper(tmp_path,monkeypatch,root_metadata,kind):
    manager,j=plan(tmp_path)
    if kind=='mode':
        real=os.mkdir
        def mkdir(path,mode=0o777,**kw):
            real(path,mode,**kw)
            if str(path)==pathlib.Path(j['destContainer']).name:os.chmod(path,0o755,dir_fd=kw.get('dir_fd'))
        monkeypatch.setattr(os,'mkdir',mkdir)
    elif kind=='uid':
        actual=os.fstat
        def caller(fd):
            values=list(actual(fd));values[4]=1000;return os.stat_result(values)
        monkeypatch.setattr(os,'fstat',caller)
    else:
        import struct
        acl=struct.pack('<I',2)+b''.join(struct.pack('<HHI',tag,perms,uid) for tag,perms,uid in
            [(1,7,0xffffffff),(2,5,2000),(4,0,0xffffffff),(16,5,0xffffffff),(32,0,0xffffffff)])
        monkeypatch.setattr(os,'getxattr',lambda *a:acl)
    with pytest.raises(Failure,match='private|ACL'):manager.make_destination(j)
    assert not manager.c.records('moves')


@pytest.mark.parametrize('kind',['mode','uid','container-replaced','content-replaced','layout'])
def test_destination_and_bound_refuse_changed_private_boundary(tmp_path,monkeypatch,root_metadata,kind):
    manager,j=plan(tmp_path);manager.make_destination(j)
    monkeypatch.setattr(moves,'probe',lambda *a,**k:{'supported':True,'encrypted':True,'disk':{'serial':'TESTDATA'},
        'chain':[{'uuid':'DST'}],'mount':{'fsroot':'/','fstype':'btrfs','majorMinor':'0:55'}})
    monkeypatch.setattr(manager,'changed',lambda *a:None)
    monkeypatch.setattr(moves,'mount_for',lambda *a:{'target':j['source'],'fstype':'btrfs','majorMinor':'0:55','fsroot':j['destFSRoot']})
    manager.destination(j)
    assert manager.bound(j,block={},rows=[]) is True
    if kind=='mode':pathlib.Path(j['destContainer']).chmod(0o755)
    elif kind=='uid':
        actual=os.fstat
        def caller(fd):
            values=list(actual(fd));values[4]=1000;return os.stat_result(values)
        monkeypatch.setattr(os,'fstat',caller)
    elif kind in ('container-replaced','content-replaced'):
        path=pathlib.Path(j['destContainer'] if kind=='container-replaced' else j['dest'])
        path.rename(path.with_name(path.name+'-held'));path.mkdir(mode=0o700)
        if kind=='container-replaced':pathlib.Path(j['dest']).mkdir()
    else:j['dest']=j['destContainer'];j['destFSRoot']='/drives-'+j['id']
    with pytest.raises(Failure,match='private|identity|changed'):manager.destination(j)
    assert manager.bound(j,block={},rows=[]) is False


@pytest.mark.parametrize('operation',['cancel','restart'])
def test_private_cancel_or_restart_cleans_only_content_and_wrapper(tmp_path,monkeypatch,root_metadata,operation):
    manager,j=plan(tmp_path);manager.make_destination(j)
    j['state']='paused';j['interruptedState']='awaiting-maintenance';manager.c.journal('moves',j['id'],j)
    content=pathlib.Path(j['dest']);wrapper=pathlib.Path(j['destContainer'])
    content.chmod(0o755);(content/'partial-token').write_bytes(b'synthetic fixture')
    (content/'outside').symlink_to(j['source'])
    monkeypatch.setattr(manager,'changed',lambda *a:None)
    monkeypatch.setattr(moves,'probe',lambda *a,**k:{'supported':True,'encrypted':True,'disk':{'serial':'TESTDATA'}})
    monkeypatch.setattr(moves,'mount_rows',lambda:[])
    monkeypatch.setattr(manager,'bound',lambda *a:False)
    from helper import maintenance
    monkeypatch.setattr(maintenance,'LATCH',tmp_path/'no-latch')
    monkeypatch.setattr(maintenance,'RUNTIME',tmp_path/'no-runtime')
    result=getattr(manager,operation)(j['id'])
    assert pathlib.Path(j['source'],'synthetic-token').read_bytes()==b'nonsecret regression fixture'
    if operation=='cancel':
        assert result['state']=='rolled-back' and not wrapper.exists()
    else:
        saved=manager.c.read('moves',j['id'])
        assert saved['containerIdentity']==j['containerIdentity']==moves.identity(str(wrapper))
        assert saved['destIdentity']==moves.identity(str(content))
        assert not any(content.iterdir()) and wrapper.stat().st_mode&0o777==0o700


@pytest.mark.parametrize('kind',['container','content','extra-entry','mode'])
def test_private_discard_worker_refuses_unknown_data(tmp_path,root_metadata,kind):
    manager,j=plan(tmp_path);manager.make_destination(j)
    path=pathlib.Path(j['destContainer'] if kind=='container' else j['dest'])
    if kind in ('container','content'):
        path.rename(path.with_name(path.name+'-held'));path.mkdir(mode=0o700)
        if kind=='container':pathlib.Path(j['dest']).mkdir()
    elif kind=='mode':pathlib.Path(j['destContainer']).chmod(0o755)
    else:path=pathlib.Path(j['destContainer'])/'extra';path.mkdir()
    (path/'victim').write_bytes(b'keep unknown data')
    import json
    with pytest.raises(Failure,match='identity|private|unexpected'):destination.main(['discard-private',j['destContainer'],json.dumps(
        {'identity':j['destIdentity'],'containerIdentity':j['containerIdentity']})])
    assert (path/'victim').read_bytes()==b'keep unknown data'


def test_offline_rechecks_private_boundary_before_quarantining(tmp_path,monkeypatch,root_metadata):
    from helper import offline
    manager,j=plan(tmp_path);manager.make_destination(j);j['state']='awaiting-maintenance'
    pathlib.Path(j['destContainer']).chmod(0o755)
    worker=offline.Offline(manager.c)
    monkeypatch.setattr(worker,'mount_source',lambda *a:None)
    monkeypatch.setattr(worker,'mount_destination',lambda *a:None)
    monkeypatch.setattr(worker,'admitted',lambda *a:None)
    monkeypatch.setattr(worker.m,'changed',lambda *a:None)
    monkeypatch.setattr(offline,'mount_rows',lambda:[])
    monkeypatch.setattr(offline,'prepare_store',lambda *a:pytest.fail('nonprivate destination reached quarantine'))
    monkeypatch.setattr(worker,'restore',lambda *a:pytest.fail('nonprivate destination reached restoration'))
    monkeypatch.setattr(offline,'say',lambda *a:None)
    with pytest.raises(Failure,match='private'):worker.continue_move(j)
    assert pathlib.Path(j['source'],'synthetic-token').exists()


def test_offline_undo_removes_private_wrapper(tmp_path,monkeypatch,root_metadata):
    from helper import offline,configwriter
    manager,j=plan(tmp_path);manager.make_destination(j);j['state']='rolling-back'
    (pathlib.Path(j['dest'])/'copied-token').write_bytes(b'synthetic fixture')
    worker=offline.Offline(manager.c)
    monkeypatch.setattr(worker,'mount_source',lambda *a:None)
    monkeypatch.setattr(worker,'mount_destination',lambda *a:None)
    monkeypatch.setattr(worker,'unbind',lambda *a:None)
    monkeypatch.setattr(worker,'remove_placeholder',lambda *a:None)
    monkeypatch.setattr(worker,'put_back',lambda *a:None)
    monkeypatch.setattr(worker,'drop_store',lambda *a:None)
    monkeypatch.setattr(configwriter,'write_owned',lambda *a:None)
    monkeypatch.setattr(offline,'say',lambda *a:None)
    assert worker.rollback_move(j)=='undone'
    assert not pathlib.Path(j['destContainer']).exists()
    assert pathlib.Path(j['source'],'synthetic-token').read_bytes()==b'nonsecret regression fixture'


def test_offline_rechecks_privacy_after_rsync_before_verifying(tmp_path,monkeypatch,root_metadata):
    from helper import offline,configwriter
    manager,j=plan(tmp_path);manager.make_destination(j);j['state']='awaiting-maintenance'
    worker=offline.Offline(manager.c);reasons=[]
    monkeypatch.setattr(worker,'mount_source',lambda *a:None)
    monkeypatch.setattr(worker,'mount_destination',lambda *a:None)
    monkeypatch.setattr(worker,'admitted',lambda *a:None)
    monkeypatch.setattr(worker.m,'changed',lambda *a:None)
    monkeypatch.setattr(worker.m,'verify',lambda *a:pytest.fail('copy reached verify without post-copy privacy check'))
    monkeypatch.setattr(worker,'restore',lambda j,reason:reasons.append(reason))
    monkeypatch.setattr(offline,'prepare_store',lambda *a:{'path':str(tmp_path/'quarantine')})
    monkeypatch.setattr(offline,'move_original',lambda *a:{'path':j['source']})
    monkeypatch.setattr(offline,'mount_rows',lambda:[])
    monkeypatch.setattr(offline,'qa_crash',lambda *a:None)
    monkeypatch.setattr(offline,'say',lambda *a:None)
    monkeypatch.setattr(configwriter,'write_owned',lambda *a:pytest.fail('unexpected config write'))
    def copied(argv,**kw):
        assert argv[0]=='/usr/bin/rsync'
        pathlib.Path(j['destContainer']).chmod(0o755)
        return b''
    monkeypatch.setattr(offline,'run',copied)
    assert worker.continue_move(j)=='restored after failure'
    assert reasons and 'root-private' in reasons[0]


@pytest.mark.parametrize('removed',['content','container'])
def test_interrupted_undo_can_validate_destination_after_partial_cleanup(tmp_path,monkeypatch,root_metadata,removed):
    from helper import offline
    manager,j=plan(tmp_path);manager.make_destination(j);j['state']='rolling-back'
    pathlib.Path(j['dest']).rmdir()
    if removed=='container':pathlib.Path(j['destContainer']).rmdir()
    worker=offline.Offline(manager.c)
    monkeypatch.setattr(worker.m,'changed',lambda *a:None)
    monkeypatch.setattr(moves,'probe',lambda *a,**k:{'supported':True,'encrypted':True,'disk':{'serial':'TESTDATA'}})
    monkeypatch.setattr(offline,'mount_rows',lambda:[{'target':j['destMount'],'fstype':'btrfs'}])
    worker.mount_destination(j)
    j['state']='awaiting-maintenance'
    with pytest.raises((Failure,OSError)):worker.mount_destination(j)


def test_inherited_named_acl_is_masked_on_new_private_wrapper(tmp_path,root_metadata):
    import struct,subprocess
    manager,j=plan(tmp_path)
    subprocess.run(['/usr/bin/setfacl','-m','d:u::rwx,d:u:65534:r-x,d:g::r-x,d:m::r-x,d:o::r-x',j['destMount']],check=True)
    manager.make_destination(j)
    wrapper=pathlib.Path(j['destContainer'])
    assert wrapper.stat().st_mode&0o777==0o700
    with moves.anchored_tree(str(wrapper)) as (_,fd,_):
        moves.private_container_fd(fd)
        acl=os.getxattr(fd,'system.posix_acl_access')
        entries=[struct.unpack_from('<HHI',acl,i) for i in range(4,len(acl),8)]
        assert any(tag==2 and uid==65534 for tag,perms,uid in entries)
        assert next(perms for tag,perms,uid in entries if tag==16)==0


def test_private_wrapper_clears_inherited_setgid_bit(tmp_path,root_metadata):
    manager,j=plan(tmp_path)
    pathlib.Path(j['destMount']).chmod(0o2755)
    manager.make_destination(j)
    assert pathlib.Path(j['destContainer']).stat().st_mode&0o7777==0o700


def test_missing_drive_status_does_not_touch_private_paths_or_trigger_automount(tmp_path,monkeypatch):
    manager,j=plan(tmp_path)
    monkeypatch.setattr(moves,'probe',lambda *a,**k:{'supported':False})
    monkeypatch.setattr(manager,'private_destination',lambda *a,**k:pytest.fail('missing-drive status touched the automount'))
    monkeypatch.setattr(manager,'changed',lambda *a,**k:pytest.fail('missing-drive admission touched the automount'))
    assert manager.bound(j,block={},rows=[]) is False
    with pytest.raises(Failure,match='missing|changed'):manager.destination(j)


def test_actual_rsync_preserves_source_compatibility_inside_wrapper(tmp_path,root_metadata):
    from helper.common import run
    manager,j=plan(tmp_path);source=pathlib.Path(j['source'])
    (source/'nested').mkdir();(source/'nested'/'state.sqlite').write_bytes(b'synthetic database fixture')
    os.link(source/'synthetic-token',source/'nested'/'hardlink')
    (source/'literal-link').symlink_to('../outside')
    os.setxattr(source/'synthetic-token','user.fixture',b'metadata')
    manager.make_destination(j)
    run(['/usr/bin/rsync','-aHAXS','--numeric-ids','--delete','--',j['source']+'/',j['dest']+'/'])
    manager.verify(j['source'],j['dest'])
    assert pathlib.Path(j['dest']).stat().st_mode&0o777==0o755
    assert pathlib.Path(j['destContainer']).stat().st_mode&0o777==0o700
    assert os.stat(pathlib.Path(j['dest'])/'synthetic-token').st_nlink==2
    assert os.readlink(pathlib.Path(j['dest'])/'literal-link')=='../outside'
    assert os.getxattr(pathlib.Path(j['dest'])/'synthetic-token','user.fixture')==b'metadata'
