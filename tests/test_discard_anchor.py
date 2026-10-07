"""Discard must never re-resolve deletion through a replaced ancestor."""
import pathlib,sys
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import Common,Failure
from helper import moves


@pytest.mark.parametrize('operation',['cancel','restart'])
def test_discard_ancestor_swap_never_deletes_replacement(tmp_path,monkeypatch,operation):
    parent=tmp_path/'target';parent.mkdir();dest=parent/'partial';dest.mkdir();(dest/'seed').write_bytes(b'disposable seed')
    source=tmp_path/'source';source.mkdir();(source/'original').write_bytes(b'live original')
    c=Common(tmp_path/'state');manager=moves.MoveManager(c);move_id='3'*32
    j={'id':move_id,'state':'awaiting-maintenance','verified':False,'source':str(source),'sourceIdentity':moves.identity(source),'dest':str(dest),'destIdentity':moves.identity(dest),'destMount':str(parent),'mountIdentity':moves.identity(parent),'backup':str(tmp_path/'absent')}
    c.journal('moves',move_id,j)
    monkeypatch.setattr(manager,'destination',lambda *a:None)
    monkeypatch.setattr(manager,'bound',lambda *a:False)
    admission=manager.discard_admission
    def replace_ancestor(record,**kwargs):
        admission(record,**kwargs);parent.rename(tmp_path/'held-parent');parent.mkdir();replacement=parent/'partial';replacement.mkdir();(replacement/'victim').write_bytes(b'keep unrelated data')
    monkeypatch.setattr(manager,'discard_admission',replace_ancestor)
    try:getattr(manager,operation)(move_id)
    except Failure:pass # Refusal is also safe; redirection is not.
    assert (parent/'partial'/'victim').read_bytes()==b'keep unrelated data'
    assert (source/'original').read_bytes()==b'live original'
