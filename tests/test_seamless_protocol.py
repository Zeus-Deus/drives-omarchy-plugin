"""Exercise real dispatch/async scheduler and bridge, with storage isolated."""
import contextlib,json,os,pathlib,sys,types
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper import service,client,maintenance,moves
from helper.common import Common,Failure
import bridge

class Variant:
    def __init__(self,signature,value):self.signature=signature;self.value=value
    def unpack(self):return self.value

@pytest.fixture
def server(tmp_path,monkeypatch):
    c=Common(tmp_path/'state');monkeypatch.setattr(c,'storage_lock',contextlib.nullcontext)
    monkeypatch.setattr(maintenance,'LATCH',tmp_path/'no-latch');monkeypatch.setattr(maintenance,'RUNTIME',tmp_path/'no-runtime')
    glib=types.SimpleNamespace(Variant=Variant)
    gio=types.SimpleNamespace(DBusCallFlags=types.SimpleNamespace(NONE=0))
    monkeypatch.setitem(sys.modules,'gi',types.ModuleType('gi'))
    monkeypatch.setitem(sys.modules,'gi.repository',types.SimpleNamespace(GLib=glib,Gio=gio))
    s=service.Server(c);s.pending=[];s.calls=[]
    class Deferred:
        def __init__(self,target,daemon):s.pending.append(target)
        def start(self):pass
    monkeypatch.setattr(service.threading,'Thread',Deferred)
    class Connection:
        def call_sync(self,dest,path,interface,method,params,*args):
            s.calls.append((method,params.unpack()))
            if method=='GetConnectionUnixUser':return Variant('(u)',(s.uid,))
            if method=='CheckAuthorization':return Variant('x',((s.authorized,False,{}),))
            pytest.fail('unexpected bus call '+method)
    s.connection=Connection();s.uid=os.getuid();s.authorized=True
    return s


def dispatch(server,method,args,sender=':1.23'):
    class Invocation:
        def return_value(self,value):self.value=json.loads(value.unpack()[0])
    inv=Invocation();server.dispatch(server.connection,sender,service.OBJECT,service.BUS,method,Variant('x',args),inv)
    return inv.value


def test_protocol_signatures_and_reused_single_move_authorization():
    assert service.SIGNATURES.get('AssessMove')=='ss'
    assert service.SIGNATURES.get('ScheduleMove')=='ssbb'
    assert 'AssessMove' not in service.ACTIONS
    assert service.ACTIONS.get('ScheduleMove')=='move'
    assert 'name="AssessMove"' in service.XML and 'name="ScheduleMove"' in service.XML


def test_assessment_dispatch_derives_unique_sender_uid_without_polkit(server,monkeypatch):
    received=[]
    def assess(m,src,dest):
        received.append((m.uid,src,dest));return {'ok':True,'source':src,'destMount':dest,'needsPreparation':False,'stats':{'files':0,'bytes':0}}
    monkeypatch.setattr(moves.MoveManager,'assess',assess)
    result=dispatch(server,'AssessMove',('/source','/dest'))
    assert result['ok'] and result['jobId']
    assert server.calls==[('GetConnectionUnixUser',(':1.23',))]
    assert server.worker_lock.locked()
    with pytest.raises(Failure,match='another storage'):server.schedule('AssessMove',('/source','/dest'),server.uid)
    server.pending.pop()()
    assert received==[(server.uid,'/source','/dest')]
    assert server.jobs[0]['state']=='done' and server.jobs[0]['result']['stats']['files']==0
    assert not server.worker_lock.locked()
    assert server.c.records('moves')==[] and server.c.records('drives')==[]


def test_assessment_refuses_nonunique_sender_before_uid_lookup(server):
    result=dispatch(server,'AssessMove',('/source','/dest'),sender='forged.wellknown')
    assert result['ok'] is False and 'unique' in result['error']
    assert server.calls==[] and server.c.records('jobs')==[]


def test_schedule_dispatch_one_authorization_and_exact_uid_zero(server,monkeypatch):
    server.uid=0;received=[]
    def schedule(m,src,dest,prepare):
        received.append((m.uid,src,dest,prepare));return {'ok':True,'id':'a'*32,'state':'restart-required','action':'continue'}
    monkeypatch.setattr(moves.MoveManager,'schedule_move',schedule)
    result=dispatch(server,'ScheduleMove',('/source','/dest',True));assert result['ok']
    assert [name for name,args in server.calls]==['CheckAuthorization','GetConnectionUnixUser']
    assert server.calls[0][1][1]=='io.github.zeus-deus.drives.move'
    server.pending.pop()()
    assert received==[(0,'/source','/dest',True)]
    assert server.jobs[0]['result']['state']=='restart-required'


def test_denied_schedule_does_not_create_job_or_run_worker(server):
    server.authorized=False
    result=dispatch(server,'ScheduleMove',('/source','/dest',True))
    assert result['ok'] is False and 'authorization denied' in result['error']
    assert server.c.records('jobs')==[] and server.pending==[]


def test_partial_schedule_outcome_survives_async_job_failure(server,monkeypatch):
    result={'ok':False,'id':'a'*32,'state':'paused','preparation':{'attempted':True,'completed':True}}
    def failed(*a):raise moves.ScheduleFailure('fixture permanent preparation',result)
    monkeypatch.setattr(moves.MoveManager,'schedule_move',failed)
    dispatch(server,'ScheduleMove',('/source','/dest',True));server.pending.pop()()
    assert server.jobs[0]['state']=='failed' and server.jobs[0]['result']==result
    assert server.c.records('jobs')[0]['result']==result and not server.worker_lock.locked()


def test_status_exports_capability(server,monkeypatch):
    from helper import provisioning
    monkeypatch.setattr(provisioning,'inspect',lambda c:[])
    monkeypatch.setattr(moves.MoveManager,'inspect',lambda m:[])
    assert server.status()['seamlessMoves'] is True

@pytest.mark.parametrize('op,method,extra',[('assess_move','AssessMove',{}),('schedule_move','ScheduleMove',{'prepareDestination':True,'splitShared':False})])
def test_bridge_exact_new_operations(op,method,extra,monkeypatch):
    calls=[];monkeypatch.setattr(client,'status',lambda:{'ok':True,'seamlessMoves':True,'sharedLinks':True})
    monkeypatch.setattr(client,'call',lambda method,args:calls.append((method,args)) or {'ok':True,'jobId':'a'*32})
    assert bridge.handle({'op':op,'src':'/source','destMount':'/dest',**extra})['jobId']=='a'*32
    assert calls==[(method,('/source','/dest',True,False) if extra else ('/source','/dest'))]


def test_schedule_with_an_old_helper_asks_for_the_update(monkeypatch):
    monkeypatch.setattr(client,'status',lambda:{'ok':True,'seamlessMoves':True})
    monkeypatch.setattr(client,'call',lambda *a:pytest.fail('old helper must not be called with the new signature'))
    with pytest.raises(Failure,match='Update the storage helper'):bridge.handle({'op':'schedule_move','src':'/s','destMount':'/d','prepareDestination':False,'splitShared':False})

@pytest.mark.parametrize('value',[None,1,'true',False])
def test_old_helper_requires_explicit_update_without_legacy_fallback(monkeypatch,value):
    monkeypatch.setattr(client,'status',lambda:{'ok':True,'seamlessMoves':value})
    monkeypatch.setattr(client,'call',lambda *a:pytest.fail('old helper called mutation or fallback'))
    with pytest.raises(Failure,match='update|Update'):bridge.handle({'op':'assess_move','src':'/source','destMount':'/dest'})

@pytest.mark.parametrize('req',[
    {'op':'assess_move','src':'/source','destMount':'/dest','uid':1000},
    {'op':'schedule_move','src':'/source','destMount':'/dest'},
    {'op':'schedule_move','src':'/source','destMount':'/dest','prepareDestination':1},
    {'op':'schedule_move','src':'/source','destMount':'/dest','prepareDestination':'true'},
])
def test_bridge_refuses_spoofed_uid_and_nonboolean_consent(req,monkeypatch):
    monkeypatch.setattr(client,'call',lambda *a:pytest.fail('invalid request reached bus'))
    with pytest.raises(Failure,match='fields'):bridge.handle(req)


def test_bridge_forwards_status_capability(monkeypatch):
    monkeypatch.setattr(bridge,'snapshot',lambda:{'mounts':[]})
    monkeypatch.setattr(client,'status',lambda:{'ok':True,'seamlessMoves':True})
    monkeypatch.setattr(bridge,'steam_library',lambda home:'')
    assert bridge.handle({'op':'status'})['seamlessMoves'] is True
