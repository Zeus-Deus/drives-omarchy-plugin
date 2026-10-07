"""Return components: no host mounts, privilege or system configuration writes."""
import json,os,pathlib,sys
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper import moves,offline,maintenance,configwriter
from helper.common import Common,Failure
REAL_CONFIG_WRITE=configwriter.write_owned


@pytest.fixture(autouse=True)
def system_guards(tmp_path,monkeypatch):
    """Even failed-worker recovery must not escape the isolated fixture."""
    monkeypatch.setattr(maintenance,'LATCH',tmp_path/'request.json')
    monkeypatch.setattr(maintenance,'RUNTIME',tmp_path/'runtime')
    monkeypatch.setattr(offline,'LOG',tmp_path/'maintenance.log')
    monkeypatch.setattr(offline,'QA_CRASH',tmp_path/'crash-marker')
    monkeypatch.setattr(offline,'qa_crash',lambda *a:None)
    monkeypatch.setattr(configwriter,'write_owned',lambda *a,**k:pytest.fail('unguarded configuration write'))
    for module in (moves,offline):
        monkeypatch.setattr(module,'run',lambda *a,**k:pytest.fail('unguarded subprocess'))
        monkeypatch.setattr(module,'mount_rows',lambda:[])
    monkeypatch.setattr(moves,'blocks',lambda:{})
    monkeypatch.setattr(moves,'probe',lambda *a,**k:pytest.fail('unguarded topology probe'))


def test_move_back_bridge_uses_authorized_service_contract(monkeypatch):
    import bridge
    from helper import client,service
    called=[]
    monkeypatch.setattr(client,'call',lambda method,args:called.append((method,args)) or {'ok':True,'jobId':'fixture'})
    assert bridge.handle({'op':'move_back','id':'a'*32})=={'ok':True,'jobId':'fixture'}
    assert called==[('MoveBack',('a'*32,))]
    assert service.SIGNATURES['MoveBack']=='s'
    assert service.ACTIONS['MoveBack']=='move'
    assert '<method name="MoveBack">' in service.XML
    for request in ({'op':'move_back','id':42},{'op':'move_back','id':'a'*32,'source':'/foreign'}):
        with pytest.raises(Failure,match='fields'):bridge.handle(request)


def test_return_request_validates_action_protocol_and_journal_state(tmp_path,monkeypatch):
    value={'version':1,'moveId':'a'*32,'action':'return','armedBootId':'11111111-1111-1111-1111-111111111111'}
    j={'id':'a'*32,'state':'cleaned','maintenanceProtocol':2,'returnRequest':{'action':'return','armedBootId':value['armedBootId']}}
    monkeypatch.setattr(maintenance,'private_directory',lambda p,**k:pathlib.Path(p))
    monkeypatch.setattr(maintenance,'control_json',lambda p,*a:value if pathlib.Path(p)==maintenance.LATCH else j)
    assert maintenance.latch_request()==value
    assert maintenance.request(tmp_path)==value
    for state in ('switched','return-preparing','return-copying','return-verifying','return-switching','return-finishing'):
        j['state']=state;assert maintenance.request(tmp_path)==value
    for state in ('planned','paused','rolling-back','returned','rolled-back'):
        j['state']=state
        with pytest.raises(Failure,match='return|finished'):maintenance.request(tmp_path)
    j.update(state='switched',maintenanceProtocol=1)
    with pytest.raises(Failure,match='protocol'):maintenance.request(tmp_path)
    value['action']='erase'
    with pytest.raises(Failure,match='request'):maintenance.latch_request()


def return_plan(tmp_path,state='switched'):
    source=tmp_path/'home'/'folder';source.parent.mkdir();source.mkdir(mode=0o700)
    disk=tmp_path/'disk';disk.mkdir();wrapper=disk/('drives-'+'a'*32);wrapper.mkdir(mode=0o700)
    dest=wrapper/'content';dest.mkdir();(dest/'latest').write_bytes(b'latest SSD data')
    c=Common(tmp_path/'state');manager=moves.MoveManager(c,uid=os.getuid())
    j={'id':'a'*32,'state':state,'source':str(source),'sourceMount':'/','sourceUUID':'SRC',
       'sourceIdentity':[0,100,None],'parentIdentity':moves.identity(str(source.parent)),
       'placeholderIdentity':moves.identity(str(source)),'destMount':str(disk),'dest':str(dest),
       'destContainer':str(wrapper),'containerIdentity':moves.identity(str(wrapper)),
       'destIdentity':moves.identity(str(dest)),'mountIdentity':moves.identity(str(disk)),
       'destUUID':'DST','diskSerial':'FIXTURE','destFSRoot':'/drives-'+ 'a'*32+'/content',
       'backup':str(tmp_path/'absent-original'),'uid':os.getuid(),'verified':True,'maintenanceProtocol':2,'boot_id':'old'}
    c.journal('moves',j['id'],j)
    return manager,j


def test_move_back_schedules_return_with_durable_intent(tmp_path,monkeypatch):
    manager,j=return_plan(tmp_path,'cleaned');calls=[]
    monkeypatch.setattr(manager,'return_admission',lambda plan,**k:calls.append('admit'),raising=False)
    def arm(*args):
        assert manager.c.read('moves',j['id'])['returnRequest']['action']=='return'
        calls.append(args)
    monkeypatch.setattr(manager,'latch',arm)
    assert manager.move_back(j['id'])=={'ok':True,'id':j['id'],'state':'restart-required','action':'return'}
    assert calls==['admit',('arm',j['id'],'return')]
    with pytest.raises(Failure,match='action'):manager.request(j['id'],'foreign')
    monkeypatch.setattr(manager,'latch',lambda *a:(_ for _ in ()).throw(Failure('arm failed')))
    with pytest.raises(moves.ScheduleFailure) as error:manager.move_back(j['id'])
    assert error.value.result['restartRequestMayBeArmed'] is True
    assert manager.c.read('moves',j['id'])['state']=='cleaned'
    assert pathlib.Path(j['dest'],'latest').read_bytes()==b'latest SSD data'


def admission_fixture(tmp_path,monkeypatch,state='switched'):
    manager,j=return_plan(tmp_path,state)
    tab=tmp_path/'fstab';line=offline.fstab_line(j)
    tab.write_text('# unrelated\nUUID=OTHER /other ext4 defaults 0 0\n# drives-helper '+j['id']+'\n'+line+'\n')
    ownership=manager.c.state_dir/('config-fstab-'+j['id']+'.json');ownership.write_text(json.dumps({'line':line}))
    real=moves.read_regular
    monkeypatch.setattr(moves,'read_regular',lambda p,*a:real(tab if str(p)=='/etc/fstab' else p,*a))
    monkeypatch.setattr(manager,'destination',lambda *a,**k:None)
    monkeypatch.setattr(manager,'bound',lambda *a,**k:True)
    monkeypatch.setattr(manager,'changed',lambda p,want,fsuuid=None:None if moves.identity(p)[1:]==want[1:] else (_ for _ in ()).throw(Failure('parent identity changed')))
    topology={'supported':True,'encrypted':False,'disk':{'serial':'ROOT'},'chain':[{'uuid':'SRC'}],
              'mount':{'target':'/','options':'rw','fstype':'ext4'}}
    monkeypatch.setattr(moves,'probe',lambda *a,**k:topology)
    actual_identity=moves.identity
    monkeypatch.setattr(moves,'identity',lambda p:actual_identity(str(pathlib.Path(j['source']).parent)) if str(p)=='/' else actual_identity(p))
    rows=[{'target':j['source'],'fstype':'btrfs'}]
    monkeypatch.setattr(moves,'mount_rows',lambda:rows)
    monkeypatch.setattr(moves,'open_users',lambda p:[])
    return manager,j,tab,ownership,rows,topology


def test_return_admission_accepts_current_copy_without_previous_original(tmp_path,monkeypatch):
    manager,j,tab,ownership,rows,topology=admission_fixture(tmp_path,monkeypatch,'cleaned')
    (pathlib.Path(j['dest'])/'latest').write_bytes(b'changed after move')
    assert manager.return_admission(j)['files']==1
    assert not pathlib.Path(j['backup']).exists()
    manager.c.journal('moves',j['id'],j)
    assert manager.inspect()[0]['canMoveBack'] is True
    j['state']='returned';manager.c.journal('moves',j['id'],j)
    assert manager.inspect()[0]['canMoveBack'] is False

@pytest.mark.parametrize('busy_path',['source','dest'])
def test_return_active_use_is_only_refused_inside_maintenance(tmp_path,monkeypatch,busy_path):
    manager,j,_,_,_,_=admission_fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(moves,'open_users',lambda path:[{'pid':42,'name':'writer'}] if path==j[busy_path] else [])
    # In the normal session the restart closes these users first.
    assert manager.return_admission(j)['files']==1
    with pytest.raises(Failure,match='writer.*42'):manager.return_admission(j,offline=True)
    assert pathlib.Path(j['dest'],'latest').read_bytes()==b'latest SSD data'


@pytest.mark.parametrize('unsafe',['protocol','state','foreign-bind','nested-source','nested-dest','stacked-bind','parent',
    'source-mount','uuid','root-readonly','external-link','space','foreign-fstab','manual-fstab','ownership','no-private'])
def test_return_admission_refuses_unsafe_inputs_without_arming(tmp_path,monkeypatch,unsafe):
    manager,j,tab,ownership,rows,topology=admission_fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(manager,'latch',lambda *a:pytest.fail('unsafe return armed'))
    if unsafe=='protocol':j['maintenanceProtocol']=1
    elif unsafe=='state':j['state']='rolled-back'
    elif unsafe=='foreign-bind':monkeypatch.setattr(manager,'bound',lambda *a,**k:False)
    elif unsafe in ('nested-source','nested-dest'):rows.append({'target':j['source' if unsafe=='nested-source' else 'dest']+'/nested','fstype':'ext4'})
    elif unsafe=='stacked-bind':rows.append(dict(rows[0]))
    elif unsafe=='parent':j['parentIdentity'][1]+=1
    elif unsafe=='source-mount':j['sourceMount']='/data'
    elif unsafe=='uuid':topology['chain'][0]['uuid']='FOREIGN'
    elif unsafe=='root-readonly':topology['mount']['options']='ro'
    elif unsafe=='external-link':os.link(pathlib.Path(j['dest'])/'latest',tmp_path/'outside-hardlink')
    elif unsafe=='space':monkeypatch.setattr(os,'statvfs',lambda p:type('V',(),{'f_bavail':0,'f_frsize':4096})())
    elif unsafe=='foreign-fstab':tab.write_text(tab.read_text().replace(offline.fstab_line(j),'FOREIGN '+j['source']+' none bind 0 0'))
    elif unsafe=='manual-fstab':tab.write_text(tab.read_text()+'/manual '+j['source']+' none bind 0 0\n')
    elif unsafe=='ownership':ownership.unlink()
    else:j.pop('destContainer')
    manager.c.journal('moves',j['id'],j)
    with pytest.raises((Failure,OSError)):manager.move_back(j['id'])
    assert pathlib.Path(j['dest'],'latest').read_bytes()==b'latest SSD data'


def worker_fixture(tmp_path,monkeypatch,state='switched',real_root=False):
    manager,j,tab,ownership,rows,topology=admission_fixture(tmp_path,monkeypatch,state)
    rows.clear()  # production maintenance boot never activates the saved bind
    root=tmp_path/'return-root'
    monkeypatch.setattr(offline,'RETURN_ROOT',root,raising=False)
    if real_root:
        pathlib.Path(j['source']).chmod(0)
    else:
        monkeypatch.setattr(maintenance,'private_directory',lambda p,**k:pathlib.Path(p))
        monkeypatch.setattr(os,'geteuid',lambda:0)  # metadata model only, no host privilege
        actual=os.fstat;placeholder_inode=j['placeholderIdentity'][1]
        def root_metadata(fd):
            info=actual(fd);values=list(info);values[4]=0
            if info.st_ino==placeholder_inode:values[0]=info.st_mode&~0o777
            return os.stat_result(values)
        monkeypatch.setattr(os,'fstat',root_metadata)
    worker=offline.Offline(manager.c,'22222222-2222-2222-2222-222222222222');worker.m=manager
    calls=[]
    monkeypatch.setattr(worker,'mount_destination',lambda *a:None)
    monkeypatch.setattr(worker,'mount_return_origin',lambda plan:worker.m.return_origin(plan))
    monkeypatch.setattr(worker,'admitted',lambda plan,action:calls.append(('audit',action)))
    monkeypatch.setattr(offline,'mount_rows',lambda:rows)
    from helper.common import run as real_run
    def isolated_run(argv,**kwargs):
        if argv[0] in ('rsync','/usr/bin/rsync','/usr/bin/sync'):
            assert all(str(tmp_path) in p for p in argv[-2:] if p.startswith('/'))
            return real_run(argv,**kwargs)
        if argv[0]=='/usr/bin/chattr':
            assert argv[-1]==j['source'];calls.append('chattr');return b''
        pytest.fail('unapproved worker subprocess: '+repr(argv))
    monkeypatch.setattr(offline,'run',isolated_run);monkeypatch.setattr(moves,'run',isolated_run)
    monkeypatch.setattr(configwriter,'write_owned',lambda c,k,id,line:REAL_CONFIG_WRITE(c,k,id,line,etc=str(tmp_path)))
    return worker,j,tab,ownership,calls


def test_return_copies_latest_ssd_not_original_and_preserves_retained_copies(tmp_path,monkeypatch):
    worker,j,tab,ownership,calls=worker_fixture(tmp_path,monkeypatch)
    old=pathlib.Path(j['backup']);old.mkdir();(old/'deleted').write_text('frozen original')
    dest=pathlib.Path(j['dest']);(dest/'latest').write_bytes(b'edited after move');(dest/'new').write_bytes(b'new file')
    os.link(dest/'latest',dest/'alias');os.setxattr(dest/'latest','user.fixture',b'metadata')
    (dest/'link').symlink_to('../literal-outside')
    assert worker.return_move(j)=='returned'
    saved=worker.c.read('moves',j['id']);source=pathlib.Path(j['source'])
    assert saved['state']=='returned' and source.joinpath('latest').read_bytes()==b'edited after move'
    assert (source/'new').read_bytes()==b'new file' and not (source/'deleted').exists()
    assert (old/'deleted').read_text()=='frozen original' and (dest/'latest').read_bytes()==b'edited after move'
    assert os.stat(source/'latest').st_nlink==2 and os.getxattr(source/'latest','user.fixture')==b'metadata'
    assert os.readlink(source/'link')=='../literal-outside'
    assert tab.read_text()=='# unrelated\nUUID=OTHER /other ext4 defaults 0 0\n'
    assert json.loads(ownership.read_text())=={'line':None}
    assert ('audit','return') in calls and saved['returnedIdentity'][1:]==moves.identity(str(source))[1:]
    assert pathlib.Path(saved['returnStore']['path']).stat().st_mode&0o777==0o700


class PowerCut(BaseException):pass


@pytest.mark.parametrize('boundary',['return-preparing','return-store','return-copying','return-verifying','return-switching','return-exchanged','return-finishing','return-fstab'])
def test_return_interruption_keeps_newest_copy_and_recovers_without_partial_publication(tmp_path,monkeypatch,boundary):
    worker,j,tab,ownership,calls=worker_fixture(tmp_path,monkeypatch,'cleaned')
    dest=pathlib.Path(j['dest']);source=pathlib.Path(j['source']);(dest/'latest').write_bytes(b'newest before power cut')
    def cut(stage):
        if stage==boundary:raise PowerCut(stage)
    monkeypatch.setattr(offline,'qa_crash',cut)
    with pytest.raises((PowerCut,offline.Unsafe)):worker.return_move(j)
    saved=worker.c.read('moves',j['id'])
    assert (dest/'latest').read_bytes()==b'newest before power cut'
    if (source/'latest').exists():assert (source/'latest').read_bytes()==b'newest before power cut'
    else:assert not any(source.iterdir())
    monkeypatch.setattr(offline,'qa_crash',lambda *a:None)
    before_switch=boundary in ('return-preparing','return-store','return-copying','return-verifying')
    result=worker.return_move(saved)
    recovered=worker.c.read('moves',j['id'])
    if before_switch:
        assert result=='return paused; SSD remains active' and recovered['state']=='cleaned'
        assert not any(source.iterdir()) and '# drives-helper '+j['id'] in tab.read_text()
        # Explicit retry takes the CURRENT SSD, not the private partial seed.
        (dest/'latest').write_bytes(b'edited after interrupted return')
        assert worker.return_move(recovered)=='returned'
        assert (source/'latest').read_bytes()==b'edited after interrupted return'
    else:
        assert result=='returned' and recovered['state']=='returned'
        assert (source/'latest').read_bytes()==b'newest before power cut'
    assert (dest/'latest').exists()


def test_return_accepts_os_home_subvolume_not_foreign_home_filesystem(tmp_path,monkeypatch):
    manager,j,tab,ownership,rows,root=admission_fixture(tmp_path,monkeypatch)
    j['sourceMount']='/home';home=dict(root,mount=dict(root['mount'],target='/home',fstype='btrfs'))
    monkeypatch.setattr(moves,'probe',lambda p,**k:root if p=='/' else home)
    real=moves.identity
    monkeypatch.setattr(moves,'identity',lambda p:real(str(pathlib.Path(j['source']).parent)) if p=='/home' else real(p))
    assert manager.return_admission(j)['files']==1
    worker=offline.Offline(manager.c)
    assert worker.return_root(j)==pathlib.Path('/home/.drives-return')
    home['chain']=[{'uuid':'FOREIGN'}]
    with pytest.raises(Failure,match='topology'):manager.return_admission(j)


def test_offline_mounts_only_recorded_os_home_before_revalidating(tmp_path,monkeypatch):
    manager,j=return_plan(tmp_path);j['sourceMount']='/home';calls=[]
    worker=offline.Offline(manager.c)
    monkeypatch.setattr(offline,'mount_rows',lambda:[{'target':'/'}])
    monkeypatch.setattr(offline,'mount_for',lambda *a:{'target':'/'})
    monkeypatch.setattr(offline,'run',lambda argv,**k:calls.append(argv))
    monkeypatch.setattr(worker.m,'return_origin',lambda *a:calls.append('revalidated'))
    worker.mount_return_origin(j)
    assert calls==[['/usr/bin/mount','/home'],'revalidated']
    j['sourceMount']='/foreign';calls.clear()
    with pytest.raises(Failure,match='original'):worker.mount_return_origin(j)
    assert not calls


def test_service_dispatches_move_back_under_storage_lease(tmp_path,monkeypatch):
    import contextlib,threading
    from helper import service
    server=service.Server.__new__(service.Server);server.c=Common(tmp_path/'state')
    server.jobs=[];server.worker_lock=threading.Lock();server.draining=False
    held=[];called=[]
    @contextlib.contextmanager
    def lease():
        held.append(True)
        try:yield
        finally:held.clear()
    monkeypatch.setattr(server.c,'storage_lock',lease)
    def move_back(manager,id):
        assert held and server.worker_lock.locked() and manager.uid==(None if os.getuid()==0 else os.getuid())
        called.append(id);return {'ok':True,'action':'return','state':'restart-required'}
    monkeypatch.setattr(moves.MoveManager,'move_back',move_back)
    class InlineThread:
        def __init__(self,target,**k):self.target=target
        def start(self):self.target()
    monkeypatch.setattr(service.threading,'Thread',InlineThread)
    result=server.schedule('MoveBack',('a'*32,),os.getuid())
    job=server.c.read('jobs',result['jobId'])
    assert called==['a'*32] and job['state']=='done' and job['result']['action']=='return'
    assert not held and not server.worker_lock.locked()


def test_worker_main_routes_return_without_using_undo(tmp_path,monkeypatch):
    import contextlib
    manager,j=return_plan(tmp_path);selected={'moveId':j['id'],'action':'return','armedBootId':'11111111-1111-1111-1111-111111111111'};calls=[]
    monkeypatch.setattr(os,'geteuid',lambda:0)
    monkeypatch.setattr(offline,'boot_id',lambda:'new-boot')
    monkeypatch.setattr(maintenance,'request',lambda:selected)
    monkeypatch.setattr(maintenance,'control_json',lambda *a:{'bootId':'new-boot','valid':True})
    monkeypatch.setattr(maintenance,'clear_latch',lambda id:calls.append(('clear',id)))
    monkeypatch.setattr(offline,'Common',lambda:manager.c)
    monkeypatch.setattr(manager.c,'storage_lock',contextlib.nullcontext)
    worker=offline.Offline(manager.c)
    monkeypatch.setattr(worker,'settle',lambda:None)
    monkeypatch.setattr(worker,'rollback_move',lambda *a:pytest.fail('return routed through destructive Undo'))
    monkeypatch.setattr(worker,'continue_move',lambda *a:pytest.fail('return routed through Continue'))
    monkeypatch.setattr(worker,'return_move',lambda plan:calls.append(('return',plan['id'])) or 'returned')
    monkeypatch.setattr(offline,'Offline',lambda *a:worker)
    monkeypatch.setattr(offline,'run',lambda argv,**k:calls.append(argv))
    assert offline.main()==0
    assert calls==[('return',j['id']),('clear',j['id']),['/usr/bin/systemctl','--no-block','reboot']]


def test_return_latch_writer_requires_matching_journal_intent(tmp_path,monkeypatch):
    from helper import latch
    boot='11111111-1111-1111-1111-111111111111'
    j={'id':'a'*32,'state':'switched','maintenanceProtocol':2,'returnRequest':{'action':'return','armedBootId':boot}}
    monkeypatch.setattr(maintenance,'control_json',lambda *a:j)
    monkeypatch.setattr(maintenance,'boot_id',lambda:boot)
    calls=[];monkeypatch.setattr(maintenance,'write_latch',lambda *a:calls.append(a))
    latch.arm('a'*32,'return');assert calls==[('a'*32,'return',boot)]
    j['returnRequest']['armedBootId']='22222222-2222-2222-2222-222222222222';calls.clear()
    with pytest.raises(Failure,match='intent'):latch.arm('a'*32,'return')
    assert not calls
    monkeypatch.setattr(maintenance,'private_directory',lambda p:pathlib.Path(p))
    value={'version':1,'moveId':'a'*32,'action':'return','armedBootId':boot}
    monkeypatch.setattr(maintenance,'control_json',lambda p,*a:value if pathlib.Path(p)==maintenance.LATCH else j)
    with pytest.raises(Failure,match='intent'):maintenance.request(tmp_path)


def test_return_refuses_replaced_staging_before_rsync_can_delete_foreign_data(tmp_path,monkeypatch):
    worker,j,tab,ownership,calls=worker_fixture(tmp_path,monkeypatch);victims=[]
    def replace(stage):
        if stage!='return-copying':return
        content=pathlib.Path(j['returnStore']['path'])/'content'
        content.rename(content.with_name('content-held'));content.mkdir()
        victim=content/'foreign';victim.write_bytes(b'never overwrite');victims.append(victim)
    monkeypatch.setattr(offline,'qa_crash',replace)
    with pytest.raises(offline.Unsafe):worker.return_move(j)
    assert victims[0].read_bytes()==b'never overwrite'
    assert pathlib.Path(j['dest'],'latest').read_bytes()==b'latest SSD data'
    assert '# drives-helper '+j['id'] in tab.read_text()


def test_return_refuses_placeholder_name_replacement_after_immutable_clear(tmp_path,monkeypatch):
    worker,j,tab,ownership,calls=worker_fixture(tmp_path,monkeypatch)
    real=offline.run;source=pathlib.Path(j['source'])
    def replace(argv,**kwargs):
        if argv[0]=='/usr/bin/chattr':
            source.rename(source.with_name('placeholder-held'));source.mkdir()
            (source/'foreign').write_bytes(b'foreign occupant');return b''
        return real(argv,**kwargs)
    monkeypatch.setattr(offline,'run',replace)
    with pytest.raises(offline.Unsafe,match='changed|placeholder'):worker.return_move(j)
    assert (source/'foreign').read_bytes()==b'foreign occupant'
    assert not (source/'latest').exists()
    assert pathlib.Path(j['dest'],'latest').read_bytes()==b'latest SSD data'


def test_finish_retry_needs_no_second_copy_space_and_keeps_new_local_edits(tmp_path,monkeypatch):
    worker,j,tab,ownership,calls=worker_fixture(tmp_path,monkeypatch)
    def cut(stage):
        if stage=='return-fstab':raise PowerCut(stage)
    monkeypatch.setattr(offline,'qa_crash',cut)
    with pytest.raises(offline.Unsafe):worker.return_move(j)
    saved=worker.c.read('moves',j['id']);assert saved['state']=='return-finishing'
    source=pathlib.Path(j['source']);(source/'latest').write_bytes(b'new local edits after return')
    (source/'new-local').write_bytes(b'new local file')
    monkeypatch.setattr(os,'statvfs',lambda p:type('V',(),{'f_bavail':0,'f_frsize':4096})())
    monkeypatch.setattr(worker.m,'latch',lambda *a:None)
    assert worker.m.move_back(j['id'])['action']=='return'
    monkeypatch.setattr(offline,'qa_crash',lambda *a:None)
    assert worker.return_move(worker.c.read('moves',j['id']))=='returned'
    assert (source/'latest').read_bytes()==b'new local edits after return' and (source/'new-local').exists()
    assert pathlib.Path(j['dest'],'latest').read_bytes()==b'latest SSD data'


def test_return_refuses_manual_fstab_removal_at_switch_boundary(tmp_path,monkeypatch):
    worker,j,tab,ownership,calls=worker_fixture(tmp_path,monkeypatch)
    def changed(stage):
        if stage=='return-switching':
            tab.write_text('# foreign replacement\n');ownership.write_text(json.dumps({'line':None}))
    monkeypatch.setattr(offline,'qa_crash',changed)
    with pytest.raises(offline.Unsafe):worker.return_move(j)
    assert not any(pathlib.Path(j['source']).iterdir())
    assert pathlib.Path(j['dest'],'latest').read_bytes()==b'latest SSD data'


@pytest.mark.parametrize('kind',['staging-root-bind','ssd-alias'])
def test_return_refuses_foreign_mounts_exposing_staging_or_ssd(tmp_path,monkeypatch,kind):
    manager,j,tab,ownership,rows,topology=admission_fixture(tmp_path,monkeypatch)
    if kind=='staging-root-bind':rows.append({'target':'/.drives-return','fstype':'ext4','fsroot':'/foreign-private'})
    else:rows.append({'target':'/foreign-alias','fstype':'btrfs','fsroot':j['destFSRoot']})
    with pytest.raises(Failure,match='mount|bind'):manager.return_admission(j)


@pytest.mark.parametrize('state',['return-preparing','return-copying','return-verifying','return-switching','return-finishing','returned'])
@pytest.mark.parametrize('saved_first',[False,True])
def test_return_journal_failure_boundaries_keep_complete_authoritative_copy(tmp_path,monkeypatch,state,saved_first):
    worker,j,tab,ownership,calls=worker_fixture(tmp_path,monkeypatch)
    real=worker.c.journal;failed=[]
    def fail(kind,id,value):
        if value.get('state')==state and not failed:
            failed.append(True)
            if saved_first:real(kind,id,value)
            raise Failure('injected durable journal boundary')
        return real(kind,id,value)
    monkeypatch.setattr(worker.c,'journal',fail)
    with pytest.raises(offline.Unsafe):worker.return_move(j)
    assert failed and pathlib.Path(j['dest'],'latest').read_bytes()==b'latest SSD data'
    source=pathlib.Path(j['source'])
    if any(source.iterdir()):assert (source/'latest').read_bytes()==b'latest SSD data'
    monkeypatch.setattr(worker.c,'journal',real)
    result=worker.return_move(worker.c.read('moves',j['id']))
    assert result in ('returned','already returned','return paused; SSD remains active')
    assert pathlib.Path(j['dest'],'latest').read_bytes()==b'latest SSD data'


@pytest.mark.parametrize('write_number',[1,2,3,4])
@pytest.mark.parametrize('saved_first',[False,True])
def test_fstab_atomic_removal_boundaries_recover_with_both_complete_copies(tmp_path,monkeypatch,write_number,saved_first):
    worker,j,tab,ownership,calls=worker_fixture(tmp_path,monkeypatch)
    real=configwriter.atomic;count=[]
    def fail(path,data,*args):
        assert pathlib.Path(path).is_relative_to(tmp_path)
        count.append(path)
        if len(count)==write_number:
            if saved_first:real(path,data,*args)
            raise Failure('injected fstab boundary')
        return real(path,data,*args)
    monkeypatch.setattr(configwriter,'atomic',fail)
    with pytest.raises(offline.Unsafe):worker.return_move(j)
    assert pathlib.Path(j['source'],'latest').read_bytes()==b'latest SSD data'
    assert pathlib.Path(j['dest'],'latest').read_bytes()==b'latest SSD data'
    monkeypatch.setattr(configwriter,'atomic',real)
    assert worker.return_move(worker.c.read('moves',j['id']))=='returned'
    assert tab.read_text()=='# unrelated\nUUID=OTHER /other ext4 defaults 0 0\n'


def test_request_prearm_journal_failure_does_not_launch_latch(tmp_path,monkeypatch):
    manager,j=return_plan(tmp_path)
    monkeypatch.setattr(manager,'return_admission',lambda *a,**k:None)
    monkeypatch.setattr(manager,'latch',lambda *a:pytest.fail('prearm journal failure launched latch'))
    monkeypatch.setattr(manager.c,'journal',lambda *a:(_ for _ in ()).throw(Failure('prearm journal boundary')))
    with pytest.raises(Failure,match='prearm'):manager.move_back(j['id'])
    assert pathlib.Path(j['dest'],'latest').read_bytes()==b'latest SSD data'


def test_foreign_source_automount_is_refused_before_path_observation(tmp_path,monkeypatch):
    manager,j,tab,ownership,rows,topology=admission_fixture(tmp_path,monkeypatch)
    rows[0]['fstype']='autofs'
    monkeypatch.setattr(manager,'return_origin',lambda *a:pytest.fail('foreign automount path observed'))
    monkeypatch.setattr(manager,'destination',lambda *a:pytest.fail('foreign automount reached destination observation'))
    with pytest.raises(Failure,match='bind|mount'):manager.return_admission(j)


@pytest.mark.parametrize('action',['continue','rollback'])
def test_return_stage_cannot_be_selected_as_undo_or_continue(action):
    value={'moveId':'a'*32,'action':action,'armedBootId':'11111111-1111-1111-1111-111111111111'}
    j={'id':'a'*32,'state':'return-copying','maintenanceProtocol':2}
    with pytest.raises(Failure,match='return'):maintenance.validate_move(value,j)


@pytest.mark.parametrize('kind',['store','root'])
def test_return_rechecks_staging_parent_identity_at_exchange(tmp_path,monkeypatch,kind):
    worker,j,tab,ownership,calls=worker_fixture(tmp_path,monkeypatch)
    def swap(stage):
        if stage!='return-switching':return
        store=pathlib.Path(j['returnStore']['path']);path=store if kind=='store' else store.parent
        held=path.with_name(path.name+'-held');path.rename(held);path.mkdir(mode=0o700)
        # Keep the verified content inode but replace its private ancestor.
        child='content' if kind=='store' else store.name
        (held/child).rename(path/child)
    monkeypatch.setattr(offline,'qa_crash',swap)
    with pytest.raises(offline.Unsafe,match='identity'):worker.return_move(j)
    assert not any(pathlib.Path(j['source']).iterdir())
    assert pathlib.Path(j['dest'],'latest').read_bytes()==b'latest SSD data'


def test_return_inspect_reports_retry_not_undo_and_preserves_attention_detail(tmp_path,monkeypatch):
    manager,j,tab,ownership,rows,topology=admission_fixture(tmp_path,monkeypatch,'return-copying')
    j['error']='Latest SSD retained; original placeholder untouched.';j['needsAttention']=True
    manager.c.journal('moves',j['id'],j)
    row=manager.inspect()[0]
    assert row['state']=='paused' and row['interruptedState']=='return-copying' and row['canMoveBack'] is True
    assert row['error']==j['error']
    j['error']='';manager.c.journal('moves',j['id'],j)
    row=manager.inspect()[0]
    assert 'Move back' in row['error'] and 'Undo' not in row['error']


def test_post_exchange_ssd_edits_never_get_replaced_by_stale_local_copy(tmp_path,monkeypatch):
    worker,j,tab,ownership,calls=worker_fixture(tmp_path,monkeypatch)
    def cut(stage):
        if stage=='return-exchanged':raise PowerCut(stage)
    monkeypatch.setattr(offline,'qa_crash',cut)
    with pytest.raises(offline.Unsafe):worker.return_move(j)
    source=pathlib.Path(j['source']);dest=pathlib.Path(j['dest'])
    (dest/'latest').write_bytes(b'newer SSD edits after interrupted exchange')
    (dest/'new').write_bytes(b'newest file')
    monkeypatch.setattr(offline,'qa_crash',lambda *a:None)
    with pytest.raises(offline.Unsafe,match='differs'):worker.return_move(worker.c.read('moves',j['id']))
    assert (source/'latest').read_bytes()==b'latest SSD data'
    assert (dest/'latest').read_bytes()==b'newer SSD edits after interrupted exchange' and (dest/'new').exists()
    assert '# drives-helper '+j['id'] in tab.read_text()


def test_foreign_fstab_lexical_alias_is_not_left_to_rebind_after_return(tmp_path,monkeypatch):
    manager,j,tab,ownership,rows,topology=admission_fixture(tmp_path,monkeypatch)
    alias=str(pathlib.Path(j['source']).parent)+'/./'+pathlib.Path(j['source']).name
    tab.write_text(tab.read_text()+'/manual '+alias+' none bind 0 0\n')
    with pytest.raises(Failure,match='foreign fstab'):manager.return_admission(j)
