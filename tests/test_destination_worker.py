"""The helper never writes to a drive from its own (read-only) namespace."""
import json,pathlib,sys
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from helper.common import Common,Failure
from helper import destination,moves


def test_helper_unit_does_not_list_drive_mountpoints():
    text=(ROOT/'packaging/drives-helper.service').read_text()
    line=next(l for l in text.splitlines() if l.startswith('ReadWritePaths='))
    # A locked drive's automount listed here makes the helper fail to start
    # (status=226/NAMESPACE), which is exactly when recovery unlock is needed.
    assert not any(p.lstrip('-').startswith(('/data','/mnt/drives')) for p in line.split('=',1)[1].split())


def test_isolated_manager_routes_destination_writes_through_a_transient_unit(tmp_path,monkeypatch):
    c=Common(tmp_path/'state');m=moves.MoveManager(c,isolated=True);calls=[]
    def fake_run(argv,**kwargs):
        calls.append(argv);return json.dumps({'identity':[1,2,3]}).encode()
    monkeypatch.setattr(moves,'run',fake_run)
    j={'id':'d'*32,'destMount':'/data','dest':'/data/drives-'+'d'*32}
    m.make_destination(j)
    argv=calls[0]
    assert argv[0]=='systemd-run' and '--property=ReadWritePaths=/data' in argv
    assert argv[-3:]==['helper.destination','create',j['dest']]
    assert c.read('moves',j['id'])['destIdentity']==[1,2,3]


def test_discard_refuses_a_replaced_destination(tmp_path):
    dest=tmp_path/'drives-x';dest.mkdir();recorded=moves.identity(str(dest))
    dest.rename(tmp_path/'held');dest.mkdir();(dest/'victim').write_bytes(b'keep')
    with pytest.raises(Failure,match='destination changed'):destination.main(['discard',str(dest),json.dumps(recorded)])
    assert (dest/'victim').read_bytes()==b'keep'


def test_create_then_discard_and_recreate(tmp_path):
    dest=tmp_path/'drives-y'
    first=destination.main(['create',str(dest)])['identity']
    (dest/'partial').write_bytes(b'partial copy')
    second=destination.main(['discard',str(dest),json.dumps(first),'--recreate'])['identity']
    assert dest.is_dir() and not any(dest.iterdir())
    assert destination.main(['discard',str(dest),json.dumps(second)])=={'identity':None}
    assert not dest.exists()
    (tmp_path/'exists').mkdir()
    with pytest.raises(Failure,match='unrecorded destination'):destination.main(['create',str(tmp_path/'exists')])
