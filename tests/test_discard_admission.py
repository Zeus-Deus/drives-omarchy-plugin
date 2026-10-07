"""A missing legacy backup is never proof that a destination is disposable."""
import pathlib,sys
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import Common,Failure
from helper import moves


@pytest.mark.parametrize('operation',['cancel','restart'])
@pytest.mark.parametrize('state',['switched','planned'])
def test_discard_refuses_active_bind_when_backup_missing(tmp_path,monkeypatch,operation,state):
    source=tmp_path/'source';source.mkdir()
    dest=tmp_path/'dest';dest.mkdir();(dest/'new-work').write_bytes(b'preserve active work')
    c=Common(tmp_path/'state');manager=moves.MoveManager(c);move_id='1'*32
    j={'id':move_id,'state':state,'verified':False,'source':str(source),'sourceIdentity':moves.identity(source),'dest':str(dest),'destMount':str(tmp_path),'backup':str(tmp_path/'missing-backup')}
    j['destIdentity']=moves.identity(dest)
    c.journal('moves',move_id,j)
    monkeypatch.setattr(manager,'destination',lambda *a:None)
    monkeypatch.setattr(manager,'bound',lambda *a:True)
    monkeypatch.setattr(manager,'changed',lambda *a,**k:None)
    with pytest.raises(Failure,match='discard|switch|active bind'):
        getattr(manager,operation)(move_id)
    assert (dest/'new-work').read_bytes()==b'preserve active work'
    assert c.read('moves',move_id)==j


def test_legacy_rollback_requires_maintenance_before_any_mutation(tmp_path,monkeypatch):
    c=Common(tmp_path/'state');manager=moves.MoveManager(c);move_id='2'*32
    source=tmp_path/'source';source.mkdir()
    backup=tmp_path/'old';backup.mkdir();(backup/'work').write_bytes(b'old copy')
    dest=tmp_path/'dest';dest.mkdir();(dest/'work').write_bytes(b'latest active work')
    j={'id':move_id,'state':'switched','verified':True,'source':str(source),'sourceIdentity':moves.identity(backup),'sourceUUID':'fixture','dest':str(dest),'backup':str(backup)}
    c.journal('moves',move_id,j)
    monkeypatch.setattr(manager,'destination',lambda *a:None)
    monkeypatch.setattr(manager,'bound',lambda *a:True)
    monkeypatch.setattr(manager,'changed',lambda *a,**k:None)
    monkeypatch.setattr(manager,'verify',lambda *a:{})
    monkeypatch.setattr(c,'config',lambda *a:None)
    monkeypatch.setattr(moves,'open_users',lambda *a:[])
    monkeypatch.setattr(moves,'run',lambda *a,**k:b'')
    with pytest.raises(Failure,match='maintenance|legacy'):
        manager.rollback(move_id)
    assert backup.is_dir() and (dest/'work').read_bytes()==b'latest active work'
    assert c.read('moves',move_id)==j
