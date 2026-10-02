import pathlib,sys,pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import Common,Failure
from helper import moves

def test_same_btrfs_uuid_and_inode_do_not_override_subvolume_identity(tmp_path,monkeypatch):
    m=moves.MoveManager(Common(tmp_path/'state'));p=tmp_path/'directory';p.mkdir()
    monkeypatch.setattr(moves,'identity',lambda _: [123,256,301])
    monkeypatch.setattr(moves,'probe',lambda *a,**k:{'supported':True,'chain':[{'uuid':'SAME'}]})
    with pytest.raises(Failure,match='identity changed'):m.changed(str(p),[999,256,300],'SAME')

def test_bind_does_not_follow_replaced_destination_subvolume(tmp_path,monkeypatch):
    m=moves.MoveManager(Common(tmp_path/'state'))
    dest={'target':'/data','source':'/dev/mapper/data','fstype':'btrfs','fsroot':'/replacement','majorMinor':'0:55'}
    bound={'target':'/home/test/Videos','source':'/dev/mapper/data','fstype':'btrfs','fsroot':'/replacement/drives-abc','majorMinor':'0:55'}
    monkeypatch.setattr(moves,'probe',lambda *a,**k:{'supported':True,'encrypted':True,'disk':{'serial':'TESTDATA0001'},'chain':[{'uuid':'SAME'}],'mount':dest})
    j={'source':'/home/test/Videos','destMount':'/data','dest':'/data/drives-abc','destUUID':'SAME','diskSerial':'TESTDATA0001','destFSRoot':'/drives-abc'}
    assert not m.bound(j,block={},rows=[dest,bound])
