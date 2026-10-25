"""Portable verification retains rsync's checksum, owner, ACL and xattr checks."""
import os,stat,subprocess
import pytest
from helper import moves
from helper.common import Common,Failure,run


def copied(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'nested').mkdir()
    (source/'nested'/'file').write_bytes(b'original')
    (source/'link').symlink_to('nested/file')
    dest=tmp_path/'dest';dest.mkdir()
    run(['rsync','-aHAXS','--numeric-ids','--',str(source)+'/',str(dest)+'/'])
    return moves.MoveManager(Common(tmp_path/'state')),source,dest


def test_directory_storage_size_is_not_portable_metadata(tmp_path,monkeypatch):
    manager,source,dest=copied(tmp_path);actual=os.lstat
    def metadata(path,*a,**k):
        s=actual(path,*a,**k)
        if stat.S_ISDIR(s.st_mode) and str(path).startswith(str(dest)):
            values=list(s);values[6]=s.st_size+4096;return os.stat_result(values)
        return s
    monkeypatch.setattr(os,'lstat',metadata)
    assert os.lstat(dest/'nested').st_size!=os.lstat(source/'nested').st_size
    assert manager.verify(str(source),str(dest))['files']==1


@pytest.mark.parametrize('kind',['size','owner','mode','acl','xattr','checksum','symlink'])
def test_portable_verification_still_rejects_real_differences(tmp_path,kind):
    manager,source,dest=copied(tmp_path);p=dest/'nested'/'file'
    if kind=='size':p.write_bytes(b'longer content')
    elif kind=='owner':os.chown(p,65534,65534)
    elif kind=='mode':p.chmod(0o600)
    elif kind=='acl':subprocess.run(['setfacl','-m','u:65534:r--',str(p)],check=True)
    elif kind=='xattr':os.setxattr(p,'user.fixture',b'changed')
    elif kind=='checksum':p.write_bytes(b'changed!') # same byte count, different checksum
    else:(dest/'link').unlink();(dest/'link').symlink_to('other-place')
    with pytest.raises(Failure,match='checksum or metadata'):manager.verify(str(source),str(dest))


def test_regular_file_size_remains_in_metadata_digest(tmp_path):
    manager,source,dest=copied(tmp_path)
    a=moves.tree_stats(str(source));(source/'nested'/'file').write_bytes(b'longer content')
    assert a['metadataDigest']!=moves.tree_stats(str(source))['metadataDigest']
