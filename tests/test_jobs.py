import pathlib,sys,pytest
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

@pytest.fixture(autouse=True)
def private_maintenance_controls(tmp_path,monkeypatch):
    # Scheduler tests must not depend on whether the test guest itself has a
    # real restart request armed at /drives-maintenance-request.json.
    from helper import maintenance
    monkeypatch.setattr(maintenance,'LATCH',tmp_path/'no-latch')
    monkeypatch.setattr(maintenance,'RUNTIME',tmp_path/'no-runtime')

def test_scheduler_refuses_a_shared_lease_before_writing_jobs(tmp_path,monkeypatch):
    import pytest
    from helper.common import Common,Failure
    from helper.service import Server
    import helper.service as service
    c=Common(tmp_path);server=Server(c)
    class DeferredThread:
        def __init__(self,target,daemon):pass
        def start(self):pass
    monkeypatch.setattr(service.threading,'Thread',DeferredThread)
    with Common(tmp_path).storage_lock():
        with pytest.raises(Failure,match='another storage operation'):
            server.schedule('StartMove',('/unused','/unused'),0)
    assert not server.worker_lock.locked()
    assert server.jobs==[] and c.records('jobs')==[]

def test_scheduler_retains_shared_lease_until_worker_finishes(tmp_path,monkeypatch):
    import pytest
    from helper.common import Common,Failure
    from helper.service import Server,MoveManager
    import helper.service as service
    c=Common(tmp_path);server=Server(c);pending=[]
    class DeferredThread:
        def __init__(self,target,daemon):pending.append(target)
        def start(self):pass
    monkeypatch.setattr(service.threading,'Thread',DeferredThread)
    monkeypatch.setattr(MoveManager,'start',lambda *args:{'ok':True})
    server.schedule('StartMove',('/unused','/unused'),0)
    with pytest.raises(Failure,match='another storage operation'):
        with Common(tmp_path).storage_lock():pass
    pending[0]()
    assert server.jobs[0]['state']=='done'
    assert not server.worker_lock.locked()
    with Common(tmp_path).storage_lock():pass

def test_armed_maintenance_refuses_normal_jobs_before_persistence(tmp_path,monkeypatch):
    import pytest
    from helper.common import Common,Failure
    from helper.service import Server
    from helper import maintenance
    c=Common(tmp_path/'state');server=Server(c)
    latch=tmp_path/'latch';runtime=tmp_path/'runtime'
    monkeypatch.setattr(maintenance,'LATCH',latch)
    monkeypatch.setattr(maintenance,'RUNTIME',runtime)
    for kind in ('malformed','dangling','directory','runtime'):
        if kind=='malformed':latch.write_bytes(b'not a valid request')
        elif kind=='dangling':latch.symlink_to(tmp_path/'missing')
        elif kind=='directory':latch.mkdir()
        else:runtime.mkdir()
        with pytest.raises(Failure,match='maintenance.*inspection'):
            server.schedule('StartMove',('/unused','/unused'),0)
        assert server.jobs==[] and c.records('jobs')==[]
        assert not server.worker_lock.locked()
        with Common(c.state_dir).storage_lock():pass
        if kind=='directory':latch.rmdir()
        elif kind=='runtime':runtime.rmdir()
        else:latch.unlink()

def test_failed_initial_journal_does_not_wedge_worker(tmp_path,monkeypatch):
    import pytest
    from helper.common import Common
    from helper.service import Server
    c=Common(tmp_path);server=Server(c)
    def fail(*args):raise OSError('injected journal failure')
    monkeypatch.setattr(c,'journal',fail)
    with pytest.raises(OSError,match='injected'):
        server.schedule('StartMove',('/unused','/unused'),0)
    assert not server.worker_lock.locked(),'failed admission permanently blocks later operations'
    assert server.jobs==[],'unstarted job must not be advertised as running'
    with Common(tmp_path).storage_lock():pass

def test_failed_completion_journal_does_not_wedge_worker(tmp_path,monkeypatch):
    import pytest
    from helper.common import Common
    from helper.service import Server,MoveManager
    import helper.service as service
    c=Common(tmp_path);server=Server(c);journal=c.journal;calls=[]
    def fail_completion(*args):
        calls.append(args)
        if len(calls)>1:raise OSError('injected completion failure')
        journal(*args)
    class ImmediateThread:
        def __init__(self,target,daemon):self.target=target
        def start(self):self.target()
    monkeypatch.setattr(c,'journal',fail_completion)
    monkeypatch.setattr(service.threading,'Thread',ImmediateThread)
    monkeypatch.setattr(MoveManager,'start',lambda *args:{'ok':True})
    with pytest.raises(OSError,match='injected completion'):
        server.schedule('StartMove',('/unused','/unused'),0)
    assert not server.worker_lock.locked(),'failed completion permanently blocks later operations'
    assert c.records('jobs')[0]['state']=='running','disk uncertainty must remain inspect-only after restart'
    with Common(tmp_path).storage_lock():pass

def test_thread_start_failure_does_not_wedge_worker(tmp_path,monkeypatch):
    import pytest
    from helper.common import Common
    from helper.service import Server
    import helper.service as service
    c=Common(tmp_path);server=Server(c)
    class FailedThread:
        def __init__(self,target,daemon):pass
        def start(self):raise RuntimeError('injected thread failure')
    monkeypatch.setattr(service.threading,'Thread',FailedThread)
    with pytest.raises(RuntimeError,match='injected thread'):
        server.schedule('StartMove',('/unused','/unused'),0)
    assert not server.worker_lock.locked(),'thread startup must release admission lock'
    assert server.jobs[0]['state']=='failed','unstarted work must not appear active'
    assert c.records('jobs')[0]['state']=='failed'
    with Common(tmp_path).storage_lock():pass

def test_restart_inspects_interrupted_jobs_without_replaying(tmp_path):
    from helper.common import Common
    from helper.service import Server
    c=Common(tmp_path)
    assert (tmp_path/'jobs').exists(),'job outcomes do not survive helper namespace refresh'
    id='c'*32;c.journal('jobs',id,{'id':id,'method':'StartMove','state':'running','started':1})
    server=Server(c)
    assert server.jobs[0]['state']=='paused'
    assert c.read('jobs',id)['state']=='running', 'startup must inspect, not mutate or resume'
