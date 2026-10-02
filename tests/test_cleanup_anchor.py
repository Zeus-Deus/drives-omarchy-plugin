import pathlib,sys,os
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import Common
from helper import moves

def test_cleanup_cannot_be_redirected_by_ancestor_swap(tmp_path,monkeypatch):
    parent=tmp_path/'user';parent.mkdir();backup=parent/'Videos.pre-move';backup.mkdir();(backup/'original').write_bytes(b'old fixture')
    victim=tmp_path/'victim';victim.mkdir();valuable=victim/backup.name;valuable.mkdir();(valuable/'important').write_bytes(b'keep me')
    c=Common(tmp_path/'state');m=moves.MoveManager(c);id='b'*32
    # This unit isolates descriptor-relative deletion after admission. It does
    # not prove a maintenance boot, quarantine or migration qualification.
    c.journal('moves',id,{'id':id,'state':'switched','backup':str(backup),'sourceIdentity':moves.identity(backup),'verified':True,'maintenanceProtocol':2,'boot_id':'previous'})
    monkeypatch.setattr(m,'destination',lambda j:None);monkeypatch.setattr(m,'bound',lambda j:True)
    monkeypatch.setattr(moves,'open_users',lambda p:[]);monkeypatch.setattr(moves,'mount_rows',lambda:[])
    stage=m.stage
    def swap(j,state):
        stage(j,state)
        if state=='cleaning':parent.rename(tmp_path/'original-parent');parent.symlink_to(victim,target_is_directory=True)
    monkeypatch.setattr(m,'stage',swap)
    m.delete_old(id)
    assert (valuable/'important').read_bytes()==b'keep me'
    assert not (tmp_path/'original-parent'/backup.name).exists()
