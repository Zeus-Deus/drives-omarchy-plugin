import pathlib,sys,pytest
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def test_move_refuses_protected_root_before_journalling(tmp_path):
    assert (ROOT/'helper/moves.py').exists(),'move engine is not implemented'
    from helper.common import Common,Failure
    from helper.moves import MoveManager
    m=MoveManager(Common(tmp_path))
    with pytest.raises(Failure,match='protected'):
        m.start('/etc','/data')
    assert not list((tmp_path/'moves').glob('*.json'))

def test_inspection_pauses_interrupted_copy_without_action(tmp_path):
    assert (ROOT/'helper/moves.py').exists(),'move inspection is not implemented'
    from helper.common import Common
    from helper.moves import MoveManager
    c=Common(tmp_path);id='a'*32
    c.journal('moves',id,{'id':id,'state':'copying','source':'/nonexistent','dest':'/nonexistent2','backup':'/nonexistent3','boot_id':'old'})
    r=MoveManager(c).inspect()[0]
    assert r['state']=='paused' and r['interruptedState']=='copying'
    assert c.read('moves',id)['state']=='copying', 'startup must only inspect'

def test_cleanup_requires_saved_verification_and_reboot(tmp_path):
    assert (ROOT/'helper/moves.py').exists(),'cleanup gate is not implemented'
    from helper.common import Common,Failure
    from helper.moves import MoveManager
    c=Common(tmp_path);id='b'*32
    c.journal('moves',id,{'id':id,'state':'switched','verified':False,'source':'/none','dest':'/none2','backup':'/none3','boot_id':'old'})
    with pytest.raises(Failure,match='reboot'):
        MoveManager(c).delete_old(id)
