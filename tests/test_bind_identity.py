import pathlib,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))

def test_bind_status_uses_mount_root_and_uuid_not_ephemeral_device_number(tmp_path,monkeypatch):
    from helper.common import Common
    from helper.moves import MoveManager
    from helper import moves
    block={'blockdevices':[{'name':'/dev/sda','type':'disk','serial':'TESTDATA0001','children':[{'name':'/dev/sda1','type':'part','fstype':'crypto_LUKS','children':[{'name':'/dev/mapper/data','type':'crypt','fstype':'btrfs','uuid':'FILESYSTEM'}]}]}]}
    rows=[{'target':'/data','source':'/dev/mapper/data','fstype':'btrfs','fsroot':'/','options':'rw','majorMinor':'0:55'}, {'target':'/home/test/Videos','source':'/dev/mapper/data','fstype':'btrfs','fsroot':'/drives-abc','options':'rw','majorMinor':'0:55'}]
    m=MoveManager(Common(tmp_path))
    monkeypatch.setattr(moves,'mount_rows',lambda:rows)
    monkeypatch.setattr(moves,'blocks',lambda:block)
    j={'source':'/home/test/Videos','destMount':'/data','dest':'/data/drives-abc','destIdentity':[99999,258],'destFSRoot':'/drives-abc','destUUID':'FILESYSTEM','diskSerial':'TESTDATA0001'}
    assert m.bound(j), 'st_dev can change across boot and differs from mountinfo on Btrfs'
