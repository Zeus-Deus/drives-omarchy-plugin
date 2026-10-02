import pathlib,sys,pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))

def test_unknown_format_stage_cannot_resume_into_format(tmp_path):
    from helper import provisioning
    from helper.common import Common,Failure
    assert callable(getattr(provisioning,'resume',None)), 'explicit inspect-only provisioning continuation is not implemented'
    c=Common(tmp_path);id='d'*32
    c.journal('drives',id,{'id':id,'state':'unfinished','interruptedState':'encrypting'})
    with pytest.raises(Failure,match='unsafe stage'):
        provisioning.resume(id,c)
