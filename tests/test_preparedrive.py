import os,pathlib,stat,sys
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import pytest
from helper import preparedrive
from helper.common import Failure


def drive(path,encrypted=True,fsroot='/',fstype='btrfs',chain=None):
    """A probe() result for an encrypted btrfs drive mounted at path."""
    def probe(p,block,rows,resolve=False):
        return {'supported':True,'encrypted':encrypted,'disk':{'name':'/dev/sdz'},
            'mount':{'target':path,'fsroot':fsroot,'fstype':fstype,'majorMinor':'%d:%d'%(os.major(os.stat(path).st_dev),os.minor(os.stat(path).st_dev))},
            'chain':chain if chain is not None else [{'name':'/dev/sdz','type':'disk'},{'name':'/dev/sdz1','type':'part'},{'name':'/dev/mapper/bulk','type':'crypt'}]}
    return probe


@pytest.fixture
def notsystem(monkeypatch):
    # pytest's tmp_path can live under /home; the path rule has its own test.
    monkeypatch.setattr(preparedrive,'REFUSED',())
    import topology
    monkeypatch.setattr(topology,'blocks',lambda:{})
    monkeypatch.setattr(topology,'classify',lambda b,r,u:[{'name':'/dev/sdz','system':False}])


@pytest.mark.parametrize('bad',['relative','/','/home/u/data','/var/lib/docker','/etc','/data/../etc','/data/','/a\nb'])
def test_refuses_paths_that_are_not_a_data_drive_root(bad):
    with pytest.raises(Failure):preparedrive.check(bad,probe=lambda *a,**k:{},crypttab_key=lambda m:None,rows=[])


def test_refuses_unencrypted_subvolume_or_non_keyfile_drives(tmp_path,notsystem):
    p=str(tmp_path)
    ok=lambda m:{'mapper':m}
    with pytest.raises(Failure,match='encrypted'):preparedrive.check(p,probe=drive(p,encrypted=False),crypttab_key=ok,rows=[])
    with pytest.raises(Failure,match='root mount'):preparedrive.check(p,probe=drive(p,fsroot='/Projects'),crypttab_key=ok,rows=[])
    with pytest.raises(Failure,match='root mount'):preparedrive.check(p,probe=drive(p,fstype='ext4'),crypttab_key=ok,rows=[])
    def nokey(m):raise Failure('destination drive must unlock with the OS (keyfile in crypttab)')
    with pytest.raises(Failure,match='keyfile'):preparedrive.check(p,probe=drive(p),crypttab_key=nokey,rows=[])


def test_refuses_the_os_disk(tmp_path,monkeypatch):
    monkeypatch.setattr(preparedrive,'REFUSED',())
    import topology
    monkeypatch.setattr(topology,'blocks',lambda:{})
    monkeypatch.setattr(topology,'classify',lambda b,r,u:[{'name':'/dev/sdz','system':True}])
    with pytest.raises(Failure,match='OS disk'):preparedrive.check(str(tmp_path),probe=drive(str(tmp_path)),crypttab_key=lambda m:None,rows=[])


@pytest.mark.skipif(os.geteuid()!=0,reason='chown needs root (runs in the VM suite)')
def test_changes_only_the_top_folder(tmp_path,notsystem,monkeypatch):
    # A temp folder is not a btrfs mount root; the real identity check is
    # exercised live in the VM (tests/vm_e2e.py prepare).
    monkeypatch.setattr(preparedrive,'is_drive_root',lambda fd,path,m:True)
    top=tmp_path/'bulk';(top/'Projects').mkdir(parents=True);(top/'Projects'/'f').write_text('x')
    os.chown(top,1000,1000);os.chmod(top,0o750);os.chown(top/'Projects',1000,1000)
    result=preparedrive.prepare(str(top),probe=drive(str(top)),crypttab_key=lambda m:None,rows=[])
    s=os.stat(top)
    assert (s.st_uid,s.st_gid,stat.S_IMODE(s.st_mode))==(0,1000,0o750)
    assert os.stat(top/'Projects').st_uid==1000 and result['before']['uid']==1000


def test_a_folder_that_is_not_the_drive_root_is_refused(tmp_path,notsystem):
    fd=os.open(tmp_path,os.O_RDONLY|os.O_DIRECTORY)
    try:assert preparedrive.is_drive_root(fd,str(tmp_path),{'source':'/dev/mapper/bulk'}) is False
    finally:os.close(fd)


def test_scoped_prepare_refuses_replaced_directory_before_mutation(tmp_path,monkeypatch):
    from helper.moves import identity
    path=tmp_path/'drive';path.mkdir();expected=identity(path)
    path.rename(tmp_path/'original');path.mkdir();before=path.stat()
    monkeypatch.setattr(preparedrive,'check',lambda *a,**k:{})
    monkeypatch.setattr(preparedrive,'is_drive_root',lambda *a:True)
    with pytest.raises(Failure,match='identity'):
        preparedrive.prepare(str(path),expected=expected)
    after=path.stat();assert (before.st_uid,before.st_gid,before.st_mode)==(after.st_uid,after.st_gid,after.st_mode)


def test_expected_identity_passed_to_fixed_preparation_worker(tmp_path,monkeypatch):
    from helper import common
    from helper.moves import identity
    path=str(tmp_path);expected=identity(path);calls=[]
    monkeypatch.setattr(preparedrive,'check',lambda *a,**k:{})
    monkeypatch.setattr(common,'run',lambda argv,**k:calls.append(argv) or b'{"ok":true}')
    assert preparedrive.run_isolated(path,expected=expected)['ok']
    import json
    assert calls[0][-2]==path and json.loads(calls[0][-1])==expected
    assert '--property=ReadWritePaths='+path in calls[0]

@pytest.mark.skipif(os.geteuid()!=0,reason='ownership/access checks need the VM root suite')
@pytest.mark.parametrize('mode',[0o700,0o750,0o755])
def test_preparation_never_opens_previously_private_drive_to_other_users(tmp_path,notsystem,monkeypatch,mode):
    import struct
    monkeypatch.setattr(preparedrive,'is_drive_root',lambda *a:True)
    top=tmp_path/'private-drive';top.mkdir();os.chown(top,1000,1000);top.chmod(mode)
    result=preparedrive.prepare(str(top),probe=drive(str(top)),crypttab_key=lambda m:None,rows=[])
    actual=top.stat()
    assert actual.st_uid==0 and actual.st_gid==1000
    assert stat.S_IMODE(actual.st_mode)&7==mode&5,'must not add other-user read/traverse permission'
    data=os.getxattr(top,'system.posix_acl_access')
    entries=list(struct.iter_unpack('<HHI',data[4:]))
    assert (2,5,1000) in entries,'former owner retains read/traverse, not top-level writes'
    assert all(not perm&2 for tag,perm,uid in entries if tag!=1)
    assert result['after']['mode']==oct(stat.S_IMODE(actual.st_mode))

def test_preparation_acl_keeps_masked_named_grants_masked():
    import struct,types
    # Named user 2000 looks readable but was masked to execute-only before.
    original=[(1,7,0xffffffff),(2,7,2000),(4,7,0xffffffff),(8,7,3000),(16,1,0xffffffff),(32,0,0xffffffff)]
    data=struct.pack('<I',2)+b''.join(struct.pack('<HHI',*e) for e in original)
    result=preparedrive.secured_acl(data,types.SimpleNamespace(st_uid=1000,st_mode=0o741))
    entries=list(struct.iter_unpack('<HHI',result[4:]))
    assert (2,1,2000) in entries and (8,1,3000) in entries
    assert (4,1,0xffffffff) in entries and (32,0,0xffffffff) in entries
    assert (2,5,1000) in entries
