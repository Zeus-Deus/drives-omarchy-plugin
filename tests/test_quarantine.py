"""Offline quarantine primitives, not proof of maintained writer exclusion."""
import importlib.util,os,pathlib,sys
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import Failure


def implementation():
    assert importlib.util.find_spec('helper.quarantine') is not None,'root-private quarantine is not implemented'
    from helper import quarantine
    return quarantine


@pytest.mark.parametrize('kind',['regular','symlink'])
def test_external_hardlink_rejected_including_symlinks(tmp_path,kind):
    q=implementation();source=tmp_path/'source';source.mkdir();p=source/'first'
    if kind=='regular':p.write_bytes(b'work')
    else:p.symlink_to('missing-target')
    os.link(p,source/'inside',follow_symlinks=False)
    q.closed_hardlinks(source)
    os.link(p,tmp_path/'outside-alias',follow_symlinks=False)
    with pytest.raises(Failure,match='external hardlink'):
        q.closed_hardlinks(source)


def test_quarantine_rename_preserves_inode_and_metadata(tmp_path):
    q=implementation()
    assert callable(getattr(q,'prepare_store',None)), 'private quarantine preparation is missing'
    assert callable(getattr(q,'move_original',None)), 'descriptor-relative quarantine rename is missing'
    from helper.moves import identity
    source=tmp_path/'source';source.mkdir();(source/'work').write_bytes(b'latest work')
    source.chmod(0o750);os.chown(source,1000,1000)
    before=source.stat();want=identity(source)
    fd=os.open(source,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:store=q.prepare_store(fd,tmp_path,'a'*32)
    finally:os.close(fd)
    parent=pathlib.Path(store['path'])
    assert parent.stat().st_uid==0 and parent.stat().st_mode&0o777==0o700
    result=q.move_original(source,want,store)
    original=pathlib.Path(result['path']);after=original.stat()
    assert not source.exists() and (original/'work').read_bytes()==b'latest work'
    assert identity(original)==want
    assert (after.st_uid,after.st_gid,after.st_mode,after.st_mtime_ns)==(before.st_uid,before.st_gid,before.st_mode,before.st_mtime_ns)
    assert original.parent==parent


def test_filesystem_root_rejected_before_store_creation():
    q=implementation();fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        with pytest.raises(Failure,match='filesystem root cannot'):
            q.prepare_store(fd,'/','b'*32)
    finally:os.close(fd)


def test_external_hardlink_allowed_only_with_consent(tmp_path):
    q=implementation()
    import os
    source=tmp_path/'src';source.mkdir();(source/'f').write_bytes(b'x');os.link(source/'f',tmp_path/'outside')
    with pytest.raises(Failure,match='external hardlink'):q.closed_hardlinks(source)
    q.closed_hardlinks(source,allow_external=True)
