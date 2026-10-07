"""Nonprivileged policy/admission tests; no mounts, root workers or host writes."""
import os,pathlib,pwd,sys,types
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper import moves
from helper.common import Common,Failure
CALLER=os.getuid() or 1000

def folder(home,name):
    p=home/name;p.mkdir()
    if os.geteuid()==0:os.chown(p,CALLER,CALLER)
    return p

@pytest.fixture
def home(tmp_path,monkeypatch):
    home=tmp_path/'home';home.mkdir()
    real=pwd.getpwuid(CALLER)
    if os.geteuid()==0:os.chown(home,CALLER,CALLER)
    monkeypatch.setattr(moves.pwd,'getpwuid',lambda uid:types.SimpleNamespace(pw_dir=str(home),pw_name=real.pw_name,pw_gid=real.pw_gid))
    return home

def test_dormant_hidden_profile_and_sqlite_are_not_name_refusals(home):
    profile=folder(home,'.config');(profile/'.ssh').mkdir()
    (profile/'.ssh'/'credentials').write_bytes(b'fixture only')
    (profile/'state.sqlite').write_bytes(b'fixture database')
    moves.protected(str(profile),CALLER)
    assert moves.tree_stats(str(profile),CALLER)['files']==2


def test_policy_uses_passwd_home_not_literal_home_prefix(home,monkeypatch):
    base='/srv/users/demo';source=base+'/.config';real_stat=home.stat();real=pwd.getpwuid(CALLER)
    monkeypatch.setattr(moves.pwd,'getpwuid',lambda uid:types.SimpleNamespace(pw_dir=base,pw_name=real.pw_name,pw_gid=real.pw_gid))
    monkeypatch.setattr(moves,'safe_path',lambda p:pathlib.Path(p))
    monkeypatch.setattr(pathlib.Path,'stat',lambda *a,**k:real_stat)
    assert moves.protected(source,CALLER)==CALLER

@pytest.mark.parametrize('which',['whole-home','other-home','system','file','noncanonical','symlink'])
def test_source_policy_is_actual_caller_home_and_real_directory(home,tmp_path,which):
    source=folder(home,'data')
    paths={'whole-home':str(home),'other-home':str(tmp_path/'other'),'system':'/etc','file':str(home/'file'),'noncanonical':str(home)+'/./data','symlink':str(home/'alias')}
    (home/'file').write_bytes(b'fixture');(tmp_path/'other').mkdir();(home/'alias').symlink_to(source,target_is_directory=True)
    with pytest.raises(Failure):moves.protected(paths[which],CALLER)

@pytest.mark.parametrize('target',['../outside','/etc/passwd'])
def test_symlink_leaving_tree_is_preserved_without_reading_target(home,target):
    source=folder(home,'.hermes');(source/'link').symlink_to(target)
    assert moves.tree_stats(str(source),CALLER)['files']==0
    assert os.readlink(source/'link')==target

def test_internal_dangling_and_real_symlinks_remain_supported(home):
    source=folder(home,'.cache');(source/'item').write_bytes(b'fixture')
    (source/'real').symlink_to('item');(source/'dangling').symlink_to('missing')
    assert moves.tree_stats(str(source),CALLER)['files']==1

def test_permissions_never_decide_admission(home):
    # The helper copies as root; a mode-000 folder is only unreadable to a
    # non-root test run, where the error must still name the path.
    source=folder(home,'.config');(source/'inner').mkdir();(source/'inner'/'f').write_text('x');(source/'inner').chmod(0o000)
    try:
        if os.geteuid()==0:assert moves.tree_stats(str(source),CALLER)['files']==1
        else:
            with pytest.raises(Failure,match='could not read .*inner'):moves.tree_stats(str(source),CALLER)
    finally:(source/'inner').chmod(0o700)

@pytest.mark.parametrize('kind',['fd','cwd','root','exe','mmap','deleted-mmap','escaped-mmap'])
def test_actual_use_including_mmap_and_executable_is_refused(home,tmp_path,kind):
    source=folder(home,'data');file=source/('line\nname' if kind=='escaped-mmap' else 'file');file.write_bytes(b'fixture')
    proc=tmp_path/'proc';pid=proc/'123';(pid/'fd').mkdir(parents=True)
    (pid/'comm').write_text('fixture-app\n');(pid/'maps').write_text('')
    if 'mmap' in kind:
        target=str(file).replace('\n','\\012')+(' (deleted)' if kind=='deleted-mmap' else '')
        (pid/'maps').write_text('7f00-7fff rw-s 00000000 00:01 42 '+target+'\n')
    else:(pid/'fd'/'5' if kind=='fd' else pid/kind).symlink_to(source if kind in ('cwd','root') else file)
    assert moves.open_users(str(source),proc_root=proc)==[{'pid':123,'name':'fixture-app'}]


def test_open_users_names_the_systemd_unit(home,tmp_path):
    source=folder(home,'data');proc=tmp_path/'proc';pid=proc/'123';(pid/'fd').mkdir(parents=True)
    (pid/'comm').write_text('python3\n');(pid/'maps').write_text('');(pid/'cwd').symlink_to(source)
    (pid/'cgroup').write_text('0::/user.slice/user-1000.slice/user@1000.service/app.slice/hermes-serve.service\n')
    assert moves.open_users(str(source),proc_root=proc)==[{'pid':123,'name':'python3','unit':'hermes-serve.service'}]


def test_sockets_and_fifos_move_like_any_file(home):
    import socket
    source=folder(home,'.hermes');(source/'sub').mkdir()
    # Bind relative to the folder: pytest temp paths exceed the AF_UNIX limit.
    cwd=os.getcwd();os.chdir(source/'sub')
    try:s=socket.socket(socket.AF_UNIX);s.bind('rpc.sock');s.close()
    finally:os.chdir(cwd)
    assert moves.tree_stats(str(source),CALLER)['files']==0
    os.mkfifo(source/'sub'/'pipe')
    assert moves.tree_stats(str(source),CALLER)['files']==0


def test_outside_hardlink_refusal_names_an_example_file(home,tmp_path):
    source=folder(home,'data');(source/'a.bin').write_bytes(b'x')
    os.link(source/'a.bin',home/'outside.bin')
    with pytest.raises(Failure,match=r'1 file\(s\).*a\.bin'):moves.tree_stats(str(source),CALLER)


def test_tree_stats_reports_progress(home):
    source=folder(home,'data')
    for i in range(12):(source/str(i)).write_bytes(b'x')
    seen=[];moves.tree_stats(str(source),CALLER,seen.append)
    assert seen and seen[-1]==12


def test_nonmatching_process_paths_do_not_false_match(home,tmp_path):
    source=folder(home,'data');proc=tmp_path/'proc';pid=proc/'123';(pid/'fd').mkdir(parents=True)
    (pid/'comm').write_text('fixture-app');(pid/'maps').write_text('7f00-7fff rw-s 00000000 00:01 42 '+str(source)+'-other/file\n')
    (pid/'cwd').symlink_to(str(source)+'-other')
    assert moves.open_users(str(source),proc_root=proc)==[]


def test_proc_visibility_unknown_is_not_no_holders(home,tmp_path,monkeypatch):
    source=folder(home,'data');proc=tmp_path/'proc';pid=proc/'123';(pid/'fd').mkdir(parents=True)
    (pid/'comm').write_text('fixture-app');(pid/'maps').write_text('')
    def denied(*a,**k):raise PermissionError('fixture denied proc maps')
    monkeypatch.setattr(moves,'read_regular',denied)
    with pytest.raises(Failure,match='visibility'):moves.open_users(str(source),proc_root=proc)

@pytest.fixture
def admission(home,tmp_path,monkeypatch):
    from helper import preparedrive,maintenance,offline
    import topology
    source=folder(home,'.config');(source/'state.sqlite').write_bytes(b'fixture database')
    dest=tmp_path/'drive';dest.mkdir();(dest/'existing').write_bytes(b'untouched fixture')
    c=Common(tmp_path/'state');m=moves.MoveManager(c,uid=CALLER)
    mount={'target':str(dest),'fsroot':'/','fstype':'btrfs','source':'/dev/mapper/fixture','options':'rw','majorMinor':'1:1'}
    source_mount={**mount,'target':'/','source':'/dev/source','fstype':'ext4'}
    source_disk={'name':'/dev/source','type':'disk','serial':'TESTSOURCE'}
    data_disk={'name':'/dev/data','type':'disk','serial':'TESTDEST'}
    src_top={'supported':True,'encrypted':False,'mount':source_mount,'disk':source_disk,'chain':[source_disk,{'uuid':'source-uuid','type':'part'}]}
    dst_top={'supported':True,'encrypted':True,'mount':mount,'disk':data_disk,'chain':[data_disk,{'type':'crypt','name':'/dev/mapper/fixture','uuid':'dest-uuid'}]}
    rows=[source_mount,mount]
    def probe(path,*a,**k):return src_top if path in (str(source),str(source.parent)) else dst_top
    monkeypatch.setattr(moves,'probe',probe);monkeypatch.setattr(moves,'blocks',lambda:{})
    monkeypatch.setattr(moves,'mount_rows',lambda:rows);monkeypatch.setattr(moves,'open_users',lambda p:[])
    monkeypatch.setattr(topology,'probe',probe);monkeypatch.setattr(topology,'blocks',lambda:{})
    monkeypatch.setattr(topology,'classify',lambda *a:[{'name':'/dev/data','system':False}])
    monkeypatch.setattr(preparedrive,'REFUSED',());monkeypatch.setattr(offline,'crypttab_key',lambda mapper:{'mapper':mapper})
    monkeypatch.setattr(maintenance,'LATCH',tmp_path/'no-latch');monkeypatch.setattr(maintenance,'RUNTIME',tmp_path/'no-runtime')
    # Pretend the isolated data-drive top is caller-owned even in root guest
    # suites; no mount/chown worker is exercised by these component fixtures.
    state={'secure':False};real_stat=pathlib.Path.stat
    def metadata(p,*a,**k):
        actual=real_stat(p,*a,**k)
        if str(p)==str(dest):
            values=list(actual);values[4]=0 if state['secure'] else CALLER;values[0]=(values[0]&~0o777)|0o755
            return os.stat_result(values)
        return actual
    monkeypatch.setattr(pathlib.Path,'stat',metadata)
    return types.SimpleNamespace(m=m,c=c,src=str(source),dest=str(dest),state=state,rows=rows,top=dst_top,source_top=src_top)


def test_assessment_is_readonly_and_reports_required_preparation(admission,monkeypatch):
    from helper import preparedrive
    a=admission
    monkeypatch.setattr(preparedrive,'run_isolated',lambda *a,**k:pytest.fail('assessment mutated destination'))
    result=a.m.assess(a.src,a.dest)
    assert result['ok'] is True and result['source']==a.src and result['destMount']==a.dest
    assert result['needsPreparation'] is True and result['stats']['files']==1
    assert a.c.records('moves')==[] and a.c.records('drives')==[]
    assert (pathlib.Path(a.dest)/'existing').read_bytes()==b'untouched fixture'

@pytest.mark.parametrize('kind',['nested','hardlink','space','keyfile','system-destination','journal','pending','readonly','unsupported'])
def test_assessment_checks_real_refusals_before_any_preparation(admission,monkeypatch,kind):
    from helper import preparedrive,offline,maintenance
    import topology
    a=admission
    monkeypatch.setattr(preparedrive,'run_isolated',lambda *a,**k:pytest.fail('refusal prepared destination'))
    if kind=='nested':a.rows.append({**a.rows[0],'target':a.src+'/nested'})
    elif kind=='hardlink':os.link(pathlib.Path(a.src)/'state.sqlite',pathlib.Path(a.dest)/'alias')
    elif kind=='space':monkeypatch.setattr(moves.os,'statvfs',lambda p:types.SimpleNamespace(f_bavail=0,f_frsize=4096))
    elif kind=='keyfile':monkeypatch.setattr(offline,'crypttab_key',lambda m:(_ for _ in ()).throw(Failure('fixture missing keyfile')))
    elif kind=='system-destination':monkeypatch.setattr(topology,'classify',lambda *a:[{'name':'/dev/data','system':True}])
    elif kind=='journal':a.c.journal('moves','a'*32,{'id':'a'*32,'state':'awaiting-maintenance','source':a.src+'/nested'})
    elif kind=='pending':maintenance.LATCH.write_bytes(b'malformed fixture latch')
    elif kind=='readonly':a.top['mount']['options']='ro'
    else:a.source_top['supported']=False
    with pytest.raises(Failure):a.m.assess(a.src,a.dest)
    assert len(a.c.records('moves'))==(1 if kind=='journal' else 0)


def test_assessment_secure_destination_does_not_need_preparation(admission):
    a=admission;a.state['secure']=True
    assert a.m.assess(a.src,a.dest)['needsPreparation'] is False

@pytest.fixture
def scheduling(admission,monkeypatch):
    from helper import preparedrive
    a=admission;a.calls=[];make=a.m.make_destination;actual=os.fstat
    def metadata(fd):
        info=actual(fd)
        if os.readlink('/proc/self/fd/'+str(fd)).startswith(a.dest+'/drives-'):
            values=list(info);values[4]=0;return os.stat_result(values)
        return info
    monkeypatch.setattr(os,'fstat',metadata)
    def prepare(path,expected=None):
        assert path==a.dest and expected==moves.identity(a.dest)
        a.calls.append('prepare');a.state['secure']=True
        return {'ok':True,'mountpoint':path,'after':{'uid':0,'gid':0,'mode':'0o755'}}
    def destination(j):
        a.calls.append('destination');make(j)
    monkeypatch.setattr(preparedrive,'run_isolated',prepare);monkeypatch.setattr(a.m,'make_destination',destination)
    monkeypatch.setattr(a.m,'latch',lambda *args:a.calls.append(tuple(args)))
    return a


def test_schedule_requires_explicit_preparation_consent_before_any_write(scheduling):
    a=scheduling
    with pytest.raises(Failure,match='consent'):a.m.schedule_move(a.src,a.dest,False)
    assert a.calls==[] and a.c.records('moves')==[]


def test_schedule_prepares_plans_and_arms_one_continue_without_live_copy(scheduling):
    a=scheduling
    result=a.m.schedule_move(a.src,a.dest,True)
    assert result['ok'] is True and result['state']=='restart-required' and result['action']=='continue'
    assert a.calls==['prepare','destination',('arm',result['id'],'continue')]
    j=a.c.read('moves',result['id'])
    assert j['state']=='awaiting-maintenance' and j['verified'] is False and j['maintenanceProtocol']==2
    assert j['preparation']['completed'] is True
    assert pathlib.Path(a.src).is_dir() and list(pathlib.Path(j['dest']).iterdir())==[]
    assert not pathlib.Path(j['backup']).exists()
    assert (pathlib.Path(a.dest)/'existing').read_bytes()==b'untouched fixture'


def test_schedule_on_secure_destination_does_not_prepare(scheduling):
    a=scheduling;a.state['secure']=True
    result=a.m.schedule_move(a.src,a.dest,False)
    assert a.calls==['destination',('arm',result['id'],'continue')]


@pytest.mark.parametrize('unknown',[False,True])
def test_early_schedule_failure_can_cancel_and_reassess_without_deleting_unknown(scheduling,monkeypatch,unknown):
    a=scheduling;retained=[]
    def failed(j):
        if unknown:
            p=pathlib.Path(j['destContainer']);p.mkdir();(p/'unknown-data').write_bytes(b'retain synthetic data');retained.append(p)
        raise Failure('simulated destination creation failure before identity was saved')
    monkeypatch.setattr(a.m,'make_destination',failed)
    with pytest.raises(moves.ScheduleFailure) as caught:a.m.schedule_move(a.src,a.dest,True)
    id=caught.value.result['id'];saved=a.c.read('moves',id)
    assert 'destIdentity' not in saved and saved['preparation']['completed'] is True
    before=a.c.path('moves',id).read_bytes()
    live=a.m.inspect()[0]
    assert live['canCancelIncomplete'] is True
    assert a.c.path('moves',id).read_bytes()==before, 'inspection must not mutate the plan'
    monkeypatch.setattr(a.m,'destination_op',lambda *a:pytest.fail('unknown destination deletion was attempted'))
    result=a.m.cancel(id)
    assert result['state']=='rolled-back' and a.c.read('moves',id)['state']=='rolled-back'
    assert a.state['secure'] is True and a.c.read('moves',id)['preparation']['completed'] is True
    assert a.m.assess(a.src,a.dest)['needsPreparation'] is False, 'cancel releases conflict; preparation is permanent'
    assert pathlib.Path(a.src,'state.sqlite').read_bytes()==b'fixture database'
    if unknown:
        assert (retained[0]/'unknown-data').read_bytes()==b'retain synthetic data'
        assert result['retainedDestination']==str(retained[0]) and 'retained' in result['message'].lower()


def test_cancel_safe_partial_destination_after_post_creation_schedule_failure(scheduling,monkeypatch):
    a=scheduling;admit=a.m.admit;calls=0
    def admission(*args,**kw):
        nonlocal calls
        calls+=1
        if calls==3:raise Failure('simulated post-creation admission refusal')
        return admit(*args,**kw)
    monkeypatch.setattr(a.m,'admit',admission)
    with pytest.raises(moves.ScheduleFailure) as caught:a.m.schedule_move(a.src,a.dest,True)
    id=caught.value.result['id'];j=a.c.read('moves',id)
    assert j['destIdentity'] and a.m.inspect()[0]['canCancelIncomplete'] is True
    (pathlib.Path(j['dest'])/'partial').write_bytes(b'synthetic partial data')
    assert a.m.cancel(id)['state']=='rolled-back'
    assert not pathlib.Path(j['destContainer']).exists()
    assert a.m.assess(a.src,a.dest)['ok'] is True


@pytest.mark.parametrize('barrier',['cutover','quarantine','placeholderIdentity','verification','verified','backup','source-mount','destination-mount','latch','runtime','source-replaced','fstab','legacy'])
def test_incomplete_cancel_refuses_every_possible_switch_or_identity_barrier(scheduling,monkeypatch,barrier):
    from helper import maintenance
    a=scheduling
    monkeypatch.setattr(a.m,'make_destination',lambda *a:(_ for _ in ()).throw(Failure('early fixture failure')))
    with pytest.raises(moves.ScheduleFailure) as caught:a.m.schedule_move(a.src,a.dest,True)
    id=caught.value.result['id'];j=a.c.read('moves',id)
    if barrier in ('cutover','quarantine','placeholderIdentity','verification'):j[barrier]={}
    elif barrier=='verified':j['verified']=True
    elif barrier=='backup':pathlib.Path(j['backup']).mkdir()
    elif barrier in ('source-mount','destination-mount'):
        a.rows.append({**a.rows[0],'target':j['source']+'/nested' if barrier=='source-mount' else j['destContainer']})
    elif barrier=='latch':maintenance.LATCH.write_bytes(b'malformed synthetic latch')
    elif barrier=='runtime':maintenance.RUNTIME.mkdir()
    elif barrier=='source-replaced':
        source=pathlib.Path(j['source']);source.rename(source.with_name('held-original'));source.mkdir()
    elif barrier=='fstab':
        read=moves.read_regular
        monkeypatch.setattr(moves,'read_regular',lambda path,cap:('# drives-helper '+id+'\nfixture bind entry\n').encode() if path=='/etc/fstab' else read(path,cap))
    else:j.pop('maintenanceProtocol')
    a.c.journal('moves',id,j)
    assert a.m.inspect()[0]['canCancelIncomplete'] is False
    before=a.c.path('moves',id).read_bytes()
    with pytest.raises((Failure,OSError)):a.m.cancel(id)
    assert a.c.path('moves',id).read_bytes()==before


def test_missing_destination_cancellation_does_not_create_a_live_retry(scheduling,monkeypatch):
    a=scheduling
    monkeypatch.setattr(a.m,'make_destination',lambda *a:(_ for _ in ()).throw(Failure('early fixture failure')))
    with pytest.raises(moves.ScheduleFailure) as caught:a.m.schedule_move(a.src,a.dest,True)
    id=caught.value.result['id'];j=a.c.read('moves',id)
    monkeypatch.setattr(a.m,'destination_op',lambda *a:pytest.fail('missing destination was silently allocated'))
    with pytest.raises(Failure):a.m.resume(id)
    with pytest.raises(Failure):a.m.restart(id)
    assert not pathlib.Path(j['destContainer']).exists()
    assert a.m.cancel(id)['state']=='rolled-back'


def test_cancel_parent_identity_survives_reboot_device_renumbering(scheduling,monkeypatch):
    a=scheduling
    monkeypatch.setattr(a.m,'make_destination',lambda *args:(_ for _ in ()).throw(Failure('early fixture failure')))
    with pytest.raises(moves.ScheduleFailure) as caught:a.m.schedule_move(a.src,a.dest,True)
    id=caught.value.result['id'];j=a.c.read('moves',id)
    j['parentIdentity'][0]+=12345
    a.c.journal('moves',id,j)
    assert a.m.cancel(id)['state']=='rolled-back','parent UUID/inode/subvolume, not reboot-unstable st_dev, is authoritative'


def test_schedule_repeats_admission_never_trusts_assessment(scheduling,monkeypatch):
    a=scheduling;assert a.m.assess(a.src,a.dest)['ok']
    a.rows.append({**a.rows[0],'target':a.src+'/late-mount'})
    with pytest.raises(Failure,match='nested'):a.m.schedule_move(a.src,a.dest,True)
    assert a.calls==[] and a.c.records('moves')==[]

def test_apps_using_the_folder_are_reported_not_refused(admission,monkeypatch):
    # The maintenance boot runs after a normal shutdown closed every app and
    # service, and independently refuses any non-root process; assessment only
    # tells the user what the restart will close.
    a=admission;users=[{'pid':123,'name':'hermes','unit':'hermes-serve.service'}]
    monkeypatch.setattr(moves,'open_users',lambda p:users)
    result=a.m.assess(a.src,a.dest)
    assert result['ok'] is True and result['inUse']==users
    assert a.c.records('moves')==[]

def test_maintenance_boot_still_refuses_any_user_process():
    # The guarantee the in-session heads-up relies on, pinned here.
    from helper import maintenance_audit as audit
    import inspect
    src=inspect.getsource(audit.validate_snapshot)
    assert "if process['uid']!=[0,0,0,0]:raise Failure('non-root process blocks maintenance')" in src
    assert "if snapshot['sessions'] or snapshot['jobs']:raise Failure" in src

@pytest.mark.parametrize('kind',['nested','keyfile','journal','pending'])
def test_schedule_all_checks_before_preparation(scheduling,monkeypatch,kind):
    from helper import maintenance,offline
    a=scheduling
    if kind=='nested':a.rows.append({**a.rows[0],'target':a.src+'/nested'})
    elif kind=='keyfile':monkeypatch.setattr(offline,'crypttab_key',lambda m:(_ for _ in ()).throw(Failure('fixture no key')))
    elif kind=='journal':a.c.journal('moves','a'*32,{'id':'a'*32,'state':'awaiting-maintenance','source':a.src})
    else:maintenance.LATCH.write_bytes(b'fixture malformed')
    with pytest.raises(Failure):a.m.schedule_move(a.src,a.dest,True)
    assert a.calls==[]

@pytest.mark.parametrize('kind',['nested','source-replaced','dest-replaced','topology-changed','prepare-failed','create-failed','latch-failed','journal-failed'])
def test_partial_schedule_failure_retains_journal_and_discloses_preparation(scheduling,monkeypatch,kind):
    from helper import preparedrive
    a=scheduling;prepare=preparedrive.run_isolated
    def raced(path,expected=None):
        result=prepare(path,expected)
        if kind=='nested':a.rows.append({**a.rows[0],'target':a.src+'/late-mount'})
        elif kind in ('source-replaced','dest-replaced'):
            p=pathlib.Path(a.src if kind=='source-replaced' else a.dest);p.rename(p.with_name(p.name+'-original'));p.mkdir()
            if os.geteuid()==0 and kind=='source-replaced':os.chown(p,CALLER,CALLER)
        elif kind=='topology-changed':a.top['chain'][-1]['uuid']='replacement-uuid'
        elif kind=='prepare-failed':raise Failure('injected worker failure after possible mutation')
        elif kind=='journal-failed':
            monkeypatch.setattr(a.c,'journal',lambda *args:(_ for _ in ()).throw(OSError('injected journal failure')))
        return result
    monkeypatch.setattr(preparedrive,'run_isolated',raced)
    if kind=='create-failed':monkeypatch.setattr(a.m,'make_destination',lambda j:(_ for _ in ()).throw(Failure('injected destination failure')))
    if kind=='latch-failed':monkeypatch.setattr(a.m,'latch',lambda *args:(_ for _ in ()).throw(Failure('injected latch failure')))
    with pytest.raises(Failure,match='permanent|permanently') as caught:a.m.schedule_move(a.src,a.dest,True)
    outcome=caught.value.result
    assert outcome['ok'] is False and outcome['id']
    assert outcome['preparation']['attempted'] is True
    assert outcome['preparation']['completed'] is (kind!='prepare-failed')
    assert a.c.records('moves'), 'inspectable plan must survive failed scheduling'
    assert 'destination' not in a.calls if kind not in ('latch-failed',) else True

@pytest.mark.parametrize('kind',['outside-hardlink','nested-mount'])
def test_maintenance_rechecks_tree_before_quarantine(admission,monkeypatch,kind):
    from helper import offline
    a=admission;w=offline.Offline(a.c)
    j={'id':'a'*32,'state':'awaiting-maintenance','source':a.src,'destMount':a.dest,'destContainer':a.dest+'/drives-'+'a'*32,'uid':CALLER,'sourceIdentity':moves.identity(a.src)}
    monkeypatch.setattr(w,'mount_source',lambda j:None);monkeypatch.setattr(w,'mount_destination',lambda j:None)
    monkeypatch.setattr(w.m,'changed',lambda *a,**k:None);monkeypatch.setattr(w,'admitted',lambda *a:None)
    # Reach the late-tree check without creating privileged privacy fixtures.
    monkeypatch.setattr(w.m,'private_destination',lambda *a:None)
    monkeypatch.setattr(offline,'prepare_store',lambda *a:pytest.fail('invalid changed source reached quarantine'))
    monkeypatch.setattr(w,'restore',lambda *a:pytest.fail('invalid source reached restoration after quarantine'))
    monkeypatch.setattr(offline,'say',lambda *a:None)
    monkeypatch.setattr(offline,'mount_rows',lambda:a.rows)
    if kind=='outside-hardlink':os.link(pathlib.Path(a.src)/'state.sqlite',pathlib.Path(a.dest)/'late-alias')
    else:a.rows.append({**a.rows[0],'target':a.src+'/nested'})
    with pytest.raises(Failure,match='hard link|hardlink|nested'):w.continue_move(j)


def test_source_replaced_during_assessment_is_refused_before_preparation(admission,monkeypatch):
    a=admission;stats=moves.tree_stats
    def replaced(path,*args,**kw):
        result=stats(path,*args,**kw)
        p=pathlib.Path(path);p.rename(p.with_name(p.name+'-original'));p.mkdir()
        if os.geteuid()==0:os.chown(p,CALLER,CALLER)
        return result
    monkeypatch.setattr(moves,'tree_stats',replaced)
    with pytest.raises(Failure,match='changed'):a.m.assess(a.src,a.dest)
    assert a.c.records('moves')==[]


def test_directory_named_deleted_suffix_still_detects_use(home,tmp_path):
    source=folder(home,'data (deleted)');proc=tmp_path/'proc';pid=proc/'123';(pid/'fd').mkdir(parents=True)
    (pid/'comm').write_text('fixture-app');(pid/'maps').write_text('');(pid/'cwd').symlink_to(source)
    assert moves.open_users(str(source),proc_root=proc)


def test_literal_proc_escape_in_directory_name_still_detects_mapping(home,tmp_path):
    source=folder(home,'data\\012');proc=tmp_path/'proc';pid=proc/'123';(pid/'fd').mkdir(parents=True)
    (pid/'comm').write_text('fixture-app');(pid/'maps').write_text('7f00-7fff rw-s 0 00:01 42 '+str(source)+'/fixture\n')
    assert moves.open_users(str(source),proc_root=proc)


def test_root_private_parent_inside_home_is_accepted(home,monkeypatch):
    parent=home/'private';parent.mkdir();source=folder(parent,'data');actual=pathlib.Path.stat
    def private(p,*args,**kwargs):
        s=actual(p,*args,**kwargs)
        if p==parent:
            values=list(s);values[4]=0;values[0]=(int(values[0])&~0o777)|0o700
            return os.stat_result(values)
        return s
    monkeypatch.setattr(pathlib.Path,'stat',private)
    # Authorization comes from the administrator prompt, not from who owns a
    # parent folder: a root-private parent inside your home no longer refuses.
    assert moves.protected(str(source),CALLER)==CALLER


def test_wake_starts_only_a_configured_idle_automount(tmp_path,monkeypatch):
    c=Common(tmp_path/'state');m=moves.MoveManager(c)
    calls=[];monkeypatch.setattr(moves,'run',lambda argv,**k:calls.append(argv) or (b'data.mount\n' if argv[0]=='systemd-escape' else b''))
    monkeypatch.setattr(moves,'safe_path',lambda p:pathlib.Path(p))
    monkeypatch.setattr(moves,'read_regular',lambda *a,**k:b'')
    autofs=[{'target':'/data','fstype':'autofs'}]
    monkeypatch.setattr(moves,'mount_rows',lambda:autofs)
    m.wake('/data');assert calls==[],'unknown mountpoint must not be started'
    c.journal('drives','a'*32,{'id':'a'*32,'state':'ready','mountpoint':'/data'})
    m.wake('/data');assert calls[-1]==['systemctl','start','data.mount']
    calls.clear();monkeypatch.setattr(moves,'mount_rows',lambda:autofs+[{'target':'/data','fstype':'btrfs'}])
    m.wake('/data');assert calls==[],'already mounted: nothing to start'
    calls.clear();monkeypatch.setattr(moves,'mount_rows',lambda:[])
    m.wake('/data');assert calls==[],'no automount at all: missing drive, nothing to start'
    for bad in (None,'',"data",123):m.wake(bad)
    assert calls==[]


def test_wake_accepts_a_hand_made_fstab_drive(tmp_path,monkeypatch):
    c=Common(tmp_path/'state');m=moves.MoveManager(c)
    calls=[];monkeypatch.setattr(moves,'run',lambda argv,**k:calls.append(argv) or b'mnt-big.mount\n')
    monkeypatch.setattr(moves,'safe_path',lambda p:pathlib.Path(p))
    monkeypatch.setattr(moves,'mount_rows',lambda:[{'target':'/mnt/big','fstype':'autofs'}])
    monkeypatch.setattr(moves,'read_regular',lambda *a,**k:b'/dev/mapper/big /mnt/big btrfs nofail,x-systemd.automount 0 0\n')
    m.wake('/mnt/big');assert calls[-1][:2]==['systemctl','start']


def test_user_operations_wake_the_drive_but_status_never_does():
    import inspect
    for name in ('assess','schedule_move','request','delete_old','discard_destination'):
        assert 'self.wake(' in inspect.getsource(getattr(moves.MoveManager,name)),name
    for name in ('inspect','bound','destination'):
        assert 'self.wake(' not in inspect.getsource(getattr(moves.MoveManager,name)),name


def test_files_owned_by_others_or_unreadable_still_move(home):
    source=home/'named';(source/'inner').mkdir(parents=True);bad=source/'inner'/'root-file.json';bad.write_text('x')
    os.chmod(bad,0)
    if os.geteuid()==0:os.chown(bad,0,0)
    try:assert moves.tree_stats(str(source),CALLER)['files']==1
    finally:os.chmod(bad,0o600)


def test_source_folder_owned_by_another_user_is_accepted(home,monkeypatch):
    source=folder(home,'shared');actual=pathlib.Path.stat
    def foreign(p,*args,**kwargs):
        s=actual(p,*args,**kwargs)
        if p==source:values=list(s);values[4]=0;return os.stat_result(values)
        return s
    monkeypatch.setattr(pathlib.Path,'stat',foreign)
    assert moves.protected(str(source),CALLER)==CALLER
