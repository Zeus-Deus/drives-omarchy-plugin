"""Status resolves actual unique bus credentials; global hardware remains readable."""
import json,pathlib,sys,types
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def test_status_uses_unique_sender_and_hides_foreign_records(tmp_path,monkeypatch):
    from helper import service,provisioning
    from helper.common import Common
    server=service.Server(Common(tmp_path));calls=[]
    class Variant:
        def __init__(self,signature,value):self.value=value
        def unpack(self):return self.value
    class Connection:
        def call_sync(self,*args):
            calls.append((args[3],args[4].unpack()));return Variant('(u)',(1001,))
    monkeypatch.setitem(sys.modules,'gi.repository',types.SimpleNamespace(GLib=types.SimpleNamespace(Variant=Variant),Gio=types.SimpleNamespace(DBusCallFlags=types.SimpleNamespace(NONE=0))))
    server.connection=Connection()
    rows=[{'id':'a'*32,'uid':1001,'source':'/home/alice/public-to-alice','state':'planned'}, {'id':'b'*32,'uid':1002,'source':'/home/bob/PRIVATE','state':'planned'}, {'id':'c'*32,'source':'/legacy/PRIVATE','state':'planned'}]
    monkeypatch.setattr(service.MoveManager,'inspect',lambda self:rows)
    monkeypatch.setattr(provisioning,'inspect',lambda c:[{'name':'data','serial':'GLOBAL'}])
    monkeypatch.setattr(service,'pending_restart',lambda:{'valid':True,'moveId':'b'*32,'action':'return','thisSession':True})
    monkeypatch.setattr(server.health,'snapshot',lambda:{'GLOBAL':'healthy'})
    server.jobs=[{'id':'d'*32,'uid':1001,'state':'done','started':1,'result':{'path':'alice'}},{'id':'e'*32,'uid':1002,'state':'done','started':2,'result':{'path':'bob-PRIVATE'}},{'id':'f'*32,'state':'done','started':3,'error':'legacy-PRIVATE'}]
    class Invocation:
        def return_value(self,value):self.value=json.loads(value.unpack()[0])
    invocation=Invocation();server.dispatch(server.connection,':1.9',service.OBJECT,service.BUS,'Status',Variant('()',()),invocation)
    result=invocation.value
    assert calls==[('GetConnectionUnixUser',(':1.9',))],'Status never authenticated its sender'
    assert result['ok'] and [m['id'] for m in result['moves']]==['a'*32]
    assert [j['id'] for j in result['jobs']]==['d'*32]
    assert 'PRIVATE' not in json.dumps(result) and 'moveId' not in result['restartPending']
    assert result['drives'][0]['serial']=='GLOBAL' and result['health']=={'GLOBAL':'healthy'}
    assert len(server.status()['moves'])==3,'root CLI inspection must retain legacy/foreign records'
    calls.clear();bad=Invocation();server.dispatch(server.connection,'forged.name',service.OBJECT,service.BUS,'Status',Variant('()',()),bad)
    assert bad.value['ok'] is False and calls==[]

def test_job_captures_initiating_uid_before_worker_runs(tmp_path,monkeypatch):
    from helper import service,maintenance
    from helper.common import Common
    server=service.Server(Common(tmp_path));pending=[]
    class Thread:
        def __init__(self,target,daemon):pending.append(target)
        def start(self):pass
    monkeypatch.setattr(service.threading,'Thread',Thread)
    monkeypatch.setattr(service.MoveManager,'start',lambda *a:{'ok':True})
    monkeypatch.setattr(maintenance,'LATCH',tmp_path/'no-latch');monkeypatch.setattr(maintenance,'RUNTIME',tmp_path/'no-runtime')
    server.schedule('StartMove',('/unused','/unused'),1001)
    try:assert server.c.records('jobs')[0].get('uid')==1001,'journal cannot be filtered safely without authenticated owner'
    finally:pending.pop()()


def test_foreign_move_is_filtered_before_inspection_io(tmp_path,monkeypatch):
    from helper import service,provisioning
    from helper.common import Common
    c=Common(tmp_path);server=service.Server(c);seen=[]
    c.journal('moves','a'*32,{'id':'a'*32,'uid':1001,'state':'planned'})
    c.journal('moves','b'*32,{'id':'b'*32,'uid':1002,'state':'planned'})
    def inspect(manager):
        seen.extend(manager.c.records('moves'));return list(seen)
    monkeypatch.setattr(service.MoveManager,'inspect',inspect)
    monkeypatch.setattr(provisioning,'inspect',lambda c:[])
    monkeypatch.setattr(service,'pending_restart',lambda:None)
    server.status(1001)
    assert [j['id'] for j in seen]==['a'*32],'foreign inspection I/O can surface private error paths'
