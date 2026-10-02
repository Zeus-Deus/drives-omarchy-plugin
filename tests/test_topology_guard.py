import pathlib,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))

def test_mapper_alias_cannot_hide_system_disk(monkeypatch):
    import topology
    actual=topology.os.path.realpath
    alias=lambda p,*args,**kwargs:'/dev/dm-0' if str(p)=='/dev/mapper/os' else actual(p,*args,**kwargs)
    monkeypatch.setattr(topology.os.path,'realpath',alias)
    block={'blockdevices':[{'name':'/dev/nvme0n1','type':'disk','serial':'OS','size':1000,'children':[{'name':'/dev/nvme0n1p2','type':'part','fstype':'crypto_LUKS','children':[{'name':'/dev/mapper/os','type':'crypt','fstype':'btrfs','uuid':'ROOT-FS'}]}]}]}
    rows=[{'target':'/','source':'/dev/dm-0','fstype':'btrfs','fsroot':'/@','options':'rw'}]
    disks=topology.classify(block,rows,{})
    assert disks[0]['system'] is True and disks[0]['selectable'] is False

def test_multidevice_btrfs_is_unsupported_and_every_root_member_is_protected():
    import topology
    block={'blockdevices':[{'name':'/dev/sda','type':'disk','serial':'A','size':1000,'children':[{'name':'/dev/sda1','type':'part','fstype':'btrfs','uuid':'SHARED'}]},{'name':'/dev/sdb','type':'disk','serial':'B','size':1000,'children':[{'name':'/dev/sdb1','type':'part','fstype':'btrfs','uuid':'SHARED'}]}]}
    rows=[{'target':'/','source':'/dev/sda1','fstype':'btrfs','fsroot':'/','options':'rw'}]
    result=topology.probe('/folder',block,rows,resolve=False)
    assert result['supported'] is False
    disks=topology.classify(block,rows,{})
    assert all(d['system'] and not d['selectable'] for d in disks)
