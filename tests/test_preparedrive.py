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
    assert (s.st_uid,s.st_gid,stat.S_IMODE(s.st_mode))==(0,0,0o755)
    assert os.stat(top/'Projects').st_uid==1000 and result['before']['uid']==1000


def test_a_folder_that_is_not_the_drive_root_is_refused(tmp_path,notsystem):
    fd=os.open(tmp_path,os.O_RDONLY|os.O_DIRECTORY)
    try:assert preparedrive.is_drive_root(fd,str(tmp_path),{'source':'/dev/mapper/bulk'}) is False
    finally:os.close(fd)
