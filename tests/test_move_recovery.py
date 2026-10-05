import pathlib,sys,pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import Common,Failure
from helper.moves import MoveManager,tree_stats

def test_successful_resume_does_not_keep_previous_error(tmp_path):
    c=Common(tmp_path/'state');m=MoveManager(c)
    j={'id':'e'*32,'state':'paused','error':'previous failure','interruptedState':'switching'}
    m.stage(j,'switched')
    assert c.read('moves',j['id'])['error']==''

def test_planned_crash_can_explicitly_allocate_missing_destination(tmp_path,monkeypatch):
    c=Common(tmp_path/'state');m=MoveManager(c);source=tmp_path/'source';source.mkdir();disk=tmp_path/'disk';disk.mkdir()
    j={'id':'a'*32,'state':'planned','source':str(source),'backup':str(source)+'.pre-move','destMount':str(disk),'dest':str(disk)+'/drives-'+('a'*32),'sourceIdentity':[1,2],'sourceUUID':'SRC','mountIdentity':[3,4],'destUUID':'DST','diskSerial':'TESTDATA0001'}
    c.journal('moves',j['id'],j)
    monkeypatch.setattr(m,'changed',lambda *args:None)
    monkeypatch.setattr(m,'preflight',lambda *args:({}, {'disk':{'serial':'TESTDATA0001'}},1000))
    monkeypatch.setattr(m,'execute',lambda record:record)
    result=m.resume(j['id'])
    assert pathlib.Path(result['dest']).is_dir()
    assert result['destIdentity']==c.read('moves',j['id'])['destIdentity']


def test_smoke_does_not_change_directory_mtime(tmp_path,monkeypatch):
    import os
    c=Common(tmp_path/'state');m=MoveManager(c);source=tmp_path/'folder';source.mkdir()
    os.utime(source,ns=(1000000000,1000000000));before=source.stat().st_mtime_ns
    monkeypatch.setattr(m,'bound',lambda j:True)
    m.smoke({'source':str(source)})
    assert source.stat().st_mtime_ns==before


def test_destination_creation_is_durable_before_execution(tmp_path,monkeypatch):
    import os
    from helper import moves
    c=Common(tmp_path/'state');m=MoveManager(c);source=tmp_path/'source';source.mkdir();disk=tmp_path/'disk';disk.mkdir();seen=[];actual=os.fsync
    def sync(fd):seen.append(os.readlink('/proc/self/fd/'+str(fd)));actual(fd)
    monkeypatch.setattr(moves.os,'fsync',sync)
    monkeypatch.setattr(m,'preflight',lambda *args:({}, {'chain':[{'uuid':'DST'}],'disk':{'serial':'TESTDATA0001'},'mount':{'fsroot':'/'}},1000))
    monkeypatch.setattr(moves,'probe',lambda *args,**kwargs:{'chain':[{'uuid':'SRC'}]})
    monkeypatch.setattr(m,'offline_layout',lambda *args:('/','data'))
    def execute(j):
        assert j['dest'] in seen and str(disk) in seen
        return j
    monkeypatch.setattr(m,'execute',execute)
    m.start(str(source),str(disk))


def test_sensitive_profile_inside_tree_is_refused(tmp_path):
    p=tmp_path/'Videos';(p/'.hermes').mkdir(parents=True);(p/'.hermes'/'state').write_text('fixture')
    with pytest.raises(Failure,match='protected subtree'):tree_stats(str(p))

def test_walk_error_is_not_a_silent_skip(tmp_path,monkeypatch):
    from helper import moves
    def broken(path,**kwargs):
        if kwargs.get('onerror'):kwargs['onerror'](PermissionError('fixture denied'))
        return iter(())
    monkeypatch.setattr(moves.os,'walk',broken)
    with pytest.raises(Failure,match='unreadable subtree'):tree_stats(str(tmp_path))


def test_hardlink_to_outside_the_folder_is_refused(tmp_path):
    import os
    outside=tmp_path/'outside.txt';outside.write_text('shared')
    folder=tmp_path/'Docs';folder.mkdir();os.link(outside,folder/'inside.txt')
    with pytest.raises(Failure,match='hardlink'):tree_stats(str(folder))


def test_hardlinks_wholly_inside_the_folder_are_fine(tmp_path):
    import os
    folder=tmp_path/'Docs';folder.mkdir();(folder/'a').write_text('x');os.link(folder/'a',folder/'b')
    assert tree_stats(str(folder))['files']==2
