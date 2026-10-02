import pathlib,sys
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def test_restart_inspects_interrupted_jobs_without_replaying(tmp_path):
    from helper.common import Common
    from helper.service import Server
    c=Common(tmp_path)
    assert (tmp_path/'jobs').exists(),'job outcomes do not survive helper namespace refresh'
    id='c'*32;c.journal('jobs',id,{'id':id,'method':'StartMove','state':'running','started':1})
    server=Server(c)
    assert server.jobs[0]['state']=='paused'
    assert c.read('jobs',id)['state']=='running', 'startup must inspect, not mutate or resume'
