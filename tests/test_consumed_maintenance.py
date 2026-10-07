"""A durable completed/paused outcome cannot replay its surviving request."""
import contextlib,json,pathlib,sys
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper import maintenance,offline
from helper.common import Failure
from test_move_back import system_guards,worker_fixture

ARMED='11111111-1111-1111-1111-111111111111'
NEXT='22222222-2222-2222-2222-222222222222'

def selection(action='return'):
    return {'version':1,'moveId':'a'*32,'action':action,'armedBootId':ARMED}

def test_completed_return_surviving_latch_is_cleanup_not_invalid_request():
    value=selection();j={'id':value['moveId'],'state':'returned','maintenanceProtocol':2,
        'returnRequest':{'action':'return','armedBootId':ARMED},'maintenanceConsumed':{'action':'return','armedBootId':ARMED}}
    assert maintenance.validate_move(value,j)==value
    j['maintenanceConsumed']['armedBootId']=NEXT
    with pytest.raises(Failure):maintenance.validate_move(value,j)

def test_pause_consumes_intent_in_the_same_durable_state_write(tmp_path,monkeypatch):
    worker,j,_,_,_=worker_fixture(tmp_path,monkeypatch,'cleaned')
    j.update(state='return-copying',returnFrom='cleaned',maintenanceIntent={'action':'return','armedBootId':ARMED})
    monkeypatch.setattr(worker.m,'bound',lambda *a,**k:True)
    writes=[];real=worker.c.journal
    def record(kind,id,value):
        writes.append(json.loads(json.dumps(value)));return real(kind,id,value)
    monkeypatch.setattr(worker.c,'journal',record)
    assert worker.pause_return(j)=='return paused; SSD remains active'
    first=next(x for x in writes if x['state']=='cleaned')
    assert first['maintenanceConsumed']=={'action':'return','armedBootId':ARMED}

def test_main_does_not_repeat_an_already_consumed_return(tmp_path,monkeypatch):
    value=selection();j={'id':value['moveId'],'source':str(tmp_path/'source'),'dest':str(tmp_path/'dest'),
        'state':'cleaned','maintenanceConsumed':{'action':'return','armedBootId':ARMED},'needsAttention':True,'error':'previous admission failed'}
    calls=[]
    class Common:
        def storage_lock(self):return contextlib.nullcontext()
        def read(self,*a):return j
        def journal(self,*a):pass
    class Worker:
        def __init__(self,*a):pass
        def settle(self):pass
        def complete_consumed(self,plan,action):calls.append(('prove',action));return 'already handled'
        def return_move(self,j):pytest.fail('paused return replayed without a fresh request')
        continue_move=return_move
        rollback_move=return_move
    monkeypatch.setattr(offline.os,'geteuid',lambda:0)
    monkeypatch.setattr(offline,'Common',Common);monkeypatch.setattr(offline,'Offline',Worker)
    monkeypatch.setattr(offline,'boot_id',lambda:NEXT)
    monkeypatch.setattr(maintenance,'request',lambda:value)
    monkeypatch.setattr(maintenance,'control_json',lambda *a:{'bootId':NEXT,'valid':True})
    monkeypatch.setattr(maintenance,'clear_latch',lambda *a:calls.append('clear'))
    monkeypatch.setattr(offline,'run',lambda *a,**k:calls.append('reboot'))
    assert offline.main()==0
    assert calls==[('prove','return'),'clear','reboot']
    assert not j.get('needsAttention') and not j.get('error')
