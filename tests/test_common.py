import importlib.util, pathlib, sys, pytest
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def test_bounded_runner_refuses_stream_flood():
    assert (ROOT/'helper/common.py').exists(), 'bounded helper runner is not implemented'
    from helper.common import run, Failure
    with pytest.raises(Failure,match='output limit'):
        run([sys.executable,'-c','import sys;sys.stdout.write("x"*1000000)'],cap=1024)

def test_journal_is_atomic_private_and_rejects_symlinks(tmp_path):
    assert (ROOT/'helper/common.py').exists(), 'safe journal is not implemented'
    from helper.common import Common,Failure
    c=Common(tmp_path)
    c.journal('moves', 'a'*32, {'id':'a'*32,'state':'planned'})
    path=tmp_path/'moves'/('a'*32+'.json')
    assert path.stat().st_mode & 0o777==0o600
    assert c.read('moves','a'*32)['state']=='planned'
    path.unlink();path.symlink_to(tmp_path/'victim')
    with pytest.raises(Failure):c.read('moves','a'*32)
