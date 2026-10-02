import pathlib,sys,secrets,pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import Common,Failure

def test_uncreatable_mountpoint_refuses_before_destructive_disk_call(tmp_path,monkeypatch):
    from helper import provisioning as p
    req={'byId':'/dev/disk/by-id/unit-preflight','serial':'UNIT0001','name':'unit','mountpoint':'/data17','erase':False,'confirmation':'0001','autoUnlock':False}
    device='/dev/unit-preflight';disk={'name':device,'serial':req['serial'],'system':False,'mounts':[],'selectable':True}
    monkeypatch.setattr(p,'snapshot',lambda:{'disks':[disk],'rootEncrypted':False})
    monkeypatch.setattr(p,'blocks',lambda:{'blockdevices':[{'name':device}]})
    monkeypatch.setattr(p,'mount_rows',lambda:[])
    monkeypatch.setattr(p,'run',lambda argv,**k:b'{"signatures":[]}' if argv[0]=='wipefs' else b'')
    realpath=p.os.path.realpath
    monkeypatch.setattr(p.os.path,'realpath',lambda path,**k:device if path==req['byId'] else realpath(path,**k))
    def unavailable(path):raise Failure('cannot prepare mountpoint')
    monkeypatch.setattr(p,'prepare_mountpoint',unavailable,raising=False)
    writes=[]
    def destructive(*a,**k):writes.append(a);raise Failure('destructive call before mountpoint preparation')
    monkeypatch.setattr(p,'udisks_call',destructive)
    with pytest.raises(Failure,match='cannot prepare mountpoint'):p.provision(req,secrets.token_urlsafe().encode(),Common(tmp_path/'state'))
    assert not writes
