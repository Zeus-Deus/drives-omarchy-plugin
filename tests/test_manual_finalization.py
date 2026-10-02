import pathlib,sys,pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import Common,Failure

def test_manual_finalization_continuation_is_eligible(tmp_path,monkeypatch):
    from helper import provisioning as p
    c=Common(tmp_path);id='c'*32
    c.journal('drives',id,{'id':id,'state':'unfinished','interruptedState':'finalizing','autoUnlock':False,'keyfile':None})
    called=[]
    def finish(j,common):called.append(j['id']);return {'ok':True,'state':'ready'}
    monkeypatch.setattr(p,'finish',finish)
    assert p.resume(id,c)['state']=='ready'
    assert called==[id]

def test_retry_uses_durable_stage_if_ready_journal_write_fails(tmp_path,monkeypatch):
    from helper import provisioning as p
    c=Common(tmp_path);id='e'*32
    c.journal('drives',id,{'id':id,'state':'configuring'})
    def interrupted(j,common):
        j['state']='finalizing';common.journal('drives',id,j)
        j['state']='ready';raise Failure('injected ready persistence failure')
    monkeypatch.setattr(p,'finish',interrupted)
    with pytest.raises(Failure,match='persistence failure'):p.resume(id,c)
    assert c.read('drives',id)['interruptedState']=='finalizing'


def test_initial_provision_keeps_finalizing_if_ready_save_fails(tmp_path,monkeypatch):
    import secrets
    from helper import provisioning as p
    req={'byId':'/dev/disk/by-id/unit-manual','serial':'UNIT0001','name':'unit','mountpoint':'/data17','erase':False,'confirmation':'0001','autoUnlock':False}
    device='/dev/unit-manual';disk={'name':device,'serial':req['serial'],'system':False,'mounts':[],'selectable':True}
    monkeypatch.setattr(p,'snapshot',lambda:{'disks':[disk],'rootEncrypted':False})
    monkeypatch.setattr(p,'blocks',lambda:{'blockdevices':[{'name':device}]})
    monkeypatch.setattr(p,'chains',lambda _: {})
    monkeypatch.setattr(p,'mount_rows',lambda:[])
    monkeypatch.setattr(p,'prepare_mountpoint',lambda path:None)
    monkeypatch.setattr(p,'udisks_call',lambda *a,**k:('/org/fake/partition',))
    Path=pathlib.Path
    def run(argv,**kwargs):
        if argv[0]=='wipefs':return b'{"signatures":[]}'
        if argv[0]=='blkid':return b'btrfs'
        if 'luksHeaderBackup' in argv:Path(argv[-1]).write_bytes(b'unit header fixture')
        return b''
    monkeypatch.setattr(p,'run',run)
    realpath=p.os.path.realpath
    monkeypatch.setattr(p.os.path,'realpath',lambda path,**k:device if path==req['byId'] else realpath(path,**k))
    monkeypatch.setattr(p.pathlib,'Path',lambda path:Path(tmp_path/'keys') if str(path)=='/etc/cryptsetup-keys.d' else Path(path))
    c=Common(tmp_path/'state');save=c.journal
    def journal(kind,id,j):
        if j['state']=='ready':raise Failure('injected ready save failure')
        save(kind,id,j)
    monkeypatch.setattr(c,'journal',journal)
    def finish(j,common):
        j['state']='finalizing';common.journal('drives',j['id'],j)
        Path(j['keyfile']).unlink();j['keyfile']=None;j['state']='ready';common.journal('drives',j['id'],j)
    monkeypatch.setattr(p,'finish',finish)
    with pytest.raises(Failure,match='ready save failure'):p.provision(req,secrets.token_urlsafe().encode(),c)
    assert c.records('drives')[0]['interruptedState']=='finalizing'
