"""Demonstrate that live verification is not an exclusion mechanism."""
import pathlib,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import Common,run as real_run
from helper import moves

def test_closed_write_after_verification_cannot_be_certified(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir();(source/'work').write_bytes(b'before')
    dest=tmp_path/'dest';dest.mkdir();backup=tmp_path/'source.pre-move'
    c=Common(tmp_path/'state');m=moves.MoveManager(c)
    j={'id':'f'*32,'state':'planned','source':str(source),'backup':str(backup),'dest':str(dest),'destMount':str(tmp_path),'sourceIdentity':moves.identity(source),'verified':False}
    monkeypatch.setattr(m,'changed',lambda *a,**k:None)
    monkeypatch.setattr(m,'destination',lambda j:None)
    monkeypatch.setattr(m,'preflight',lambda *a:None)
    monkeypatch.setattr(m,'placeholder',lambda j:None)
    monkeypatch.setattr(m,'bound',lambda j:True)
    monkeypatch.setattr(m,'smoke',lambda j:None)
    monkeypatch.setattr(c,'config',lambda *a:None)
    monkeypatch.setattr(moves,'open_users',lambda path:[])
    monkeypatch.setattr(moves,'run',lambda argv,**k:real_run(argv,**k) if argv[0]=='rsync' else b'')
    stage=m.stage
    def closed_write(j,state):
        stage(j,state)
        if state=='switching' and source.exists():(source/'work').write_bytes(b'closed intervening update')
    monkeypatch.setattr(m,'stage',closed_write)
    result=m.execute(j)
    assert not (result['state']=='switched' and j['verified'] and (dest/'work').read_bytes()!=(backup/'work').read_bytes()),'stale copy certified for reboot-gated cleanup'


def test_normal_session_defers_without_copy_or_switch(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir();(source/'work').write_bytes(b'before')
    dest=tmp_path/'dest';dest.mkdir();backup=tmp_path/'source.pre-move'
    c=Common(tmp_path/'state');m=moves.MoveManager(c)
    j={'id':'e'*32,'state':'planned','source':str(source),'backup':str(backup),'dest':str(dest),'destMount':str(tmp_path),'sourceIdentity':moves.identity(source),'verified':False}
    commands=[]
    monkeypatch.setattr(m,'changed',lambda *a,**k:None)
    monkeypatch.setattr(m,'destination',lambda *a:None)
    monkeypatch.setattr(m,'preflight',lambda *a:None)
    monkeypatch.setattr(m,'placeholder',lambda *a:None)
    monkeypatch.setattr(m,'bound',lambda *a:True)
    monkeypatch.setattr(m,'smoke',lambda *a:None)
    monkeypatch.setattr(c,'config',lambda *a:None)
    monkeypatch.setattr(moves,'open_users',lambda *a:[])
    def command(argv,**kwargs):
        commands.append(argv)
        return real_run(argv,**kwargs) if argv[0]=='rsync' else b''
    monkeypatch.setattr(moves,'run',command)
    result=m.execute(j)
    assert result['state']=='awaiting-maintenance', 'normal desktop executed migration instead of deferring'
    assert commands==[] and not backup.exists() and list(dest.iterdir())==[]
    (source/'work').write_bytes(b'latest closed update')
    assert (source/'work').read_bytes()==b'latest closed update'
    assert c.read('moves',j['id'])['verified'] is False


def test_legacy_live_verification_never_authorizes_cleanup(tmp_path,monkeypatch):
    import pytest
    from helper.common import Failure
    c=Common(tmp_path/'state');move_id='d'*32
    backup=tmp_path/'old-copy';backup.mkdir();(backup/'latest').write_bytes(b'keep updated original')
    c.journal('moves',move_id,{'id':move_id,'state':'switched','verified':True,'boot_id':'earlier-boot','backup':str(backup),'sourceIdentity':moves.identity(backup)})
    manager=moves.MoveManager(c)
    monkeypatch.setattr(manager,'destination',lambda *a:None)
    monkeypatch.setattr(manager,'bound',lambda *a:True)
    monkeypatch.setattr(moves,'open_users',lambda *a:[])
    monkeypatch.setattr(moves,'mount_rows',lambda:[])
    with pytest.raises(Failure,match='legacy live verification'):
        manager.delete_old(move_id)
    assert (backup/'latest').read_bytes()==b'keep updated original'

