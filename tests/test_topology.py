import pathlib,sys
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def test_bind_autofs_and_system_topology():
    assert (ROOT/'topology.py').exists(), 'topology scanner is not implemented'
    from topology import classify,probe
    block={'blockdevices':[{'name':'/dev/sda','type':'disk','serial':'DATA','model':'QEMU','size':2000,'children':[{'name':'/dev/sda1','type':'part','fstype':'crypto_LUKS','children':[{'name':'/dev/mapper/data','type':'crypt','fstype':'btrfs'}]}]},{'name':'/dev/vda','type':'disk','size':1000,'children':[{'name':'/dev/vda2','type':'part','fstype':'btrfs'}]}]}
    mounts=[{'target':'/','source':'/dev/vda2','fstype':'btrfs','fsroot':'/@','options':'rw'}, {'target':'/data','source':'systemd-1','fstype':'autofs','fsroot':'/','options':'rw'},{'target':'/data','source':'/dev/mapper/data','fstype':'btrfs','fsroot':'/','options':'rw'},{'target':'/home/test/Videos','source':'/dev/mapper/data','fstype':'btrfs','fsroot':'/Videos','options':'rw'}]
    rows=classify(block,mounts,{})
    assert next(r for r in rows if r['serial']=='DATA')['encrypted']
    assert next(r for r in rows if r['name']=='/dev/vda')['system']
    p=probe('/home/test/Videos/movie',block,mounts,resolve=False)
    assert p['disk']['serial']=='DATA' and p['mount']['fsroot']=='/Videos'

def test_ambiguous_and_network_paths_are_unsupported():
    assert (ROOT/'topology.py').exists(), 'topology scanner is not implemented'
    from topology import probe
    rows=[{'target':'/','source':'server:/share','fstype':'nfs','fsroot':'/','options':'rw'}]
    result=probe('/folder',{'blockdevices':[]},rows,resolve=False)
    assert result['supported'] is False
