import pathlib,sys,secrets,pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import Common,Failure

def test_formatted_stage_has_durable_recovery_metadata(tmp_path,monkeypatch):
    from helper import provisioning as p
    req={'byId':'/dev/disk/by-id/unit-fixture','serial':'UNIT0001','name':'unit','mountpoint':'/data5','erase':False,'confirmation':'0001','autoUnlock':False}
    device='/dev/unit-fixture';disk={'name':device,'serial':req['serial'],'system':False,'mounts':[],'selectable':True}
    monkeypatch.setattr(p,'snapshot',lambda:{'disks':[disk],'rootEncrypted':False})
    monkeypatch.setattr(p,'blocks',lambda:{'blockdevices':[{'name':device}]})
    monkeypatch.setattr(p,'chains',lambda _: {})
    monkeypatch.setattr(p,'mount_rows',lambda:[])
    monkeypatch.setattr(p,'prepare_mountpoint',lambda path:None)
    events=[]
    monkeypatch.setattr(p,'udisks_call',lambda *a,**k:events.append(('udisks',a[2])) or ('/org/fake/partition',))
    monkeypatch.setattr(p,'hide_from_automount',lambda name,luks_uuid=None,serial=None:events.append(('hide',luks_uuid,serial)))
    def run(argv,**k):
        if argv[0]=='wipefs':return b'{"signatures":[]}'
        if argv[0]=='blkid':return b'64323122-325f-45d2-bd2b-a18b97576363\n'
        events.append(('run',argv[0],argv[1] if len(argv)>1 else ''))
        return b''
    monkeypatch.setattr(p,'run',run)
    realpath=p.os.path.realpath
    monkeypatch.setattr(p.os.path,'realpath',lambda path,**k:device if path==req['byId'] else realpath(path,**k))
    Path=pathlib.Path
    monkeypatch.setattr(p.pathlib,'Path',lambda path:Path(tmp_path/'keys') if str(path)=='/etc/cryptsetup-keys.d' else Path(path))
    c=Common(tmp_path/'state');journal=c.journal
    def crash(kind,id,j):
        journal(kind,id,j)
        if j['state']=='formatted':raise Failure('injected formatted crash')
    monkeypatch.setattr(c,'journal',crash)
    with pytest.raises(Failure,match='injected formatted crash'):p.provision(req,secrets.token_urlsafe().encode(),c)
    j=c.records('drives')[0]
    assert j['interruptedState']=='formatted'
    assert Path(j['keyfile']).is_file()
    assert j['header']==str(c.state_dir/'drives'/(j['id']+'.header'))

    # Desktop automount must never see the disk unhidden: the serial rule comes
    # before the first UDisks format, and the per-volume rule before the
    # opened volume is closed and reopened.
    assert events[0]==('hide',None,'UNIT0001'),events[:3]
    assert events.index(('hide','64323122-325f-45d2-bd2b-a18b97576363',None))<events.index(('run','cryptsetup','luksAddKey'))
