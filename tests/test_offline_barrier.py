"""Maintenance failure must never silently permit profile startup."""
import contextlib,pathlib,sys
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper import offline
from helper.common import Failure

BOOT='00000000-0000-0000-0000-000000000001'
MOVE='a'*32

@pytest.fixture
def boundary(tmp_path,monkeypatch):
    journal={'id':MOVE,'source':str(tmp_path/'source'),'dest':str(tmp_path/'dest'),'backup':str(tmp_path/'original'),'state':'copying'}
    events=[]
    class Common:
        def storage_lock(self):return contextlib.nullcontext()
        def read(self,*args):return dict(journal)
        def journal(self,kind,id,value):journal.update(value)
    class Worker:
        error=offline.Unsafe('restoration rename could not be established')
        def __init__(self,*args):pass
        def settle(self):pass
        def continue_move(self,j):raise self.error
        rollback_move=continue_move
        return_move=continue_move
    latch=tmp_path/'request.json';latch.write_bytes(b'original maintenance request')
    monkeypatch.setattr(offline,'LOG',tmp_path/'maintenance.log')
    monkeypatch.setattr(offline,'boot_id',lambda:BOOT)
    monkeypatch.setattr(offline.os,'geteuid',lambda:0)
    monkeypatch.setattr(offline,'Common',Common)
    monkeypatch.setattr(offline,'Offline',Worker)
    monkeypatch.setattr(offline.maintenance,'LATCH',latch)
    monkeypatch.setattr(offline.maintenance,'request',lambda:{'moveId':MOVE,'action':'continue','armedBootId':'11111111-1111-1111-1111-111111111111'})
    monkeypatch.setattr(offline.maintenance,'control_json',lambda *args:{'bootId':BOOT,'valid':True})
    monkeypatch.setattr(offline.maintenance,'clear_latch',lambda *args:events.append('release'))
    monkeypatch.setattr(offline,'run',lambda args,**kwargs:events.append(args))
    return journal,events,latch,Worker

def test_ambiguous_recovery_retains_normal_boot_barrier(boundary):
    journal,events,latch,_=boundary
    assert offline.main()==1
    assert events==[],'neither release nor normal reboot is permitted'
    assert latch.read_bytes()==b'original maintenance request'
    assert journal['needsAttention'] is True
    assert 'restoration rename' in journal['error']

def test_invalid_request_retains_exact_control_file(boundary,monkeypatch):
    _,events,latch,_=boundary
    def invalid():raise Failure('invalid request after a possible interrupted move')
    monkeypatch.setattr(offline.maintenance,'request',invalid)
    assert offline.main()==2
    assert events==[]
    assert latch.read_bytes()==b'original maintenance request'

def test_generic_failure_in_interrupted_mutation_keeps_barrier(boundary):
    journal,events,latch,worker=boundary
    worker.error=Failure('interrupted source filesystem is unavailable')
    assert offline.main()==1
    assert journal['needsAttention'] is True
    assert events==[] and latch.exists()

def test_legacy_flat_plan_cannot_copy_without_private_wrapper(tmp_path,monkeypatch):
    worker=offline.Offline(object(),BOOT);touched=[]
    monkeypatch.setattr(worker,'mount_source',lambda j:touched.append('source'))
    monkeypatch.setattr(worker,'mount_destination',lambda j:touched.append('destination'))
    monkeypatch.setattr(worker.m,'changed',lambda *args:None)
    def admission(*args):raise Failure('test reached maintenance admission')
    monkeypatch.setattr(worker,'admitted',admission)
    j={'state':'awaiting-maintenance','maintenanceProtocol':2,'source':str(tmp_path/'source'),'dest':str(tmp_path/'flat'),'sourceIdentity':[0,0,0]}
    with pytest.raises(Failure,match='private|legacy'):
        worker.continue_move(j)
    assert touched==[]
