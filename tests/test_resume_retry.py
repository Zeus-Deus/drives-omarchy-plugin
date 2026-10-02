import pathlib,sys,pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import Common,Failure

def test_retry_keeps_eligible_stage_if_drive_temporarily_missing(tmp_path,monkeypatch):
    from helper import provisioning
    c=Common(tmp_path);id='a'*32
    c.journal('drives',id,{'id':id,'state':'unfinished','interruptedState':'configuring'})
    calls=[]
    def missing(j,common):calls.append(1);raise Failure('drive disconnected')
    monkeypatch.setattr(provisioning,'finish',missing)
    for _ in range(2):
        with pytest.raises(Failure,match='drive disconnected'):provisioning.resume(id,c)
        assert c.read('drives',id)['interruptedState']=='configuring'
    assert len(calls)==2
