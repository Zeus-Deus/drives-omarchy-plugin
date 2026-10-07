"""VM-root DAC components, NOT production bind/boot/polkit qualification.

Run only in the authorized guest with DRIVES_VM_TESTING=1 and a root-owned
private --basetemp (e.g. /root/drives-return-tests). Never on the host.
"""
import os,pathlib,subprocess
import pytest
from test_move_back import system_guards,worker_fixture,PowerCut
from helper import offline,moves

pytestmark=pytest.mark.skipif(os.geteuid()!=0 or os.environ.get('DRIVES_VM_TESTING')!='1',reason='reserved for authorized VM root')


def guest_fixture(tmp_path,monkeypatch,state='cleaned'):
    result=subprocess.run(['/usr/bin/systemd-detect-virt','--vm'],capture_output=True,timeout=10)
    assert result.returncode==0,'root tests require a VM'
    worker,j,tab,ownership,calls=worker_fixture(tmp_path,monkeypatch,state,real_root=True)
    dest=pathlib.Path(j['dest']);dest.chmod(0o755);os.chown(dest,1000,1000)
    token=dest/'latest';token.chmod(0o644);os.chown(token,1000,1000)
    home=pathlib.Path(j['source']).parent;home.chmod(0o700);os.chown(home,1000,1000)
    worker.m.uid=None;j['uid']=1000;worker.c.journal('moves',j['id'],j)
    return worker,j,tab,ownership,calls


def test_guest_root_return_preserves_metadata_without_previous_original(tmp_path,monkeypatch):
    worker,j,tab,ownership,calls=guest_fixture(tmp_path,monkeypatch)
    dest=pathlib.Path(j['dest']);os.setxattr(dest/'latest','user.fixture',b'guest metadata')
    assert not pathlib.Path(j['backup']).exists()
    assert worker.return_move(j)=='returned'
    source=pathlib.Path(j['source'])
    assert source.stat().st_uid==1000 and source.stat().st_mode&0o777==0o755
    assert (source/'latest').stat().st_uid==1000 and (source/'latest').stat().st_mode&0o777==0o644
    assert os.getxattr(source/'latest','user.fixture')==b'guest metadata'
    assert (dest/'latest').read_bytes()==(source/'latest').read_bytes()
    assert pathlib.Path(j['returnStore']['path']).stat().st_uid==0


def test_guest_root_staging_stays_private_with_public_content_and_inherited_acl(tmp_path,monkeypatch):
    worker,j,tab,ownership,calls=guest_fixture(tmp_path,monkeypatch)
    # The reachable sibling control prevents /root's private ancestor from
    # accidentally making a broken private wrapper look safe.
    tmp_path.chmod(0o2755);(tmp_path/'public-control').write_bytes(b'reachable')
    (tmp_path/'public-control').chmod(0o644)
    subprocess.run(['/usr/bin/setfacl','-m','d:u::rwx,d:u:65534:r-x,d:g::r-x,d:m::r-x,d:o::r-x',str(tmp_path)],check=True)
    def cut(stage):
        if stage=='return-switching':raise PowerCut(stage)
    monkeypatch.setattr(offline,'qa_crash',cut)
    with pytest.raises(offline.Unsafe):worker.return_move(j)
    saved=worker.c.read('moves',j['id']);store=pathlib.Path(saved['returnStore']['path'])
    assert store.stat().st_mode&0o7777==0o700 and store.parent.stat().st_mode&0o7777==0o700
    with moves.anchored_tree(str(store)) as (_,fd,_):moves.private_container_fd(fd)
    relative=str((store/'content'/'latest').relative_to(tmp_path))
    def drop():
        os.chdir(tmp_path);os.setgroups([]);os.setgid(65534);os.setuid(65534)
    code="import pathlib,sys;assert pathlib.Path('public-control').read_bytes()==b'reachable';\ntry:pathlib.Path(sys.argv[1]).read_bytes()\nexcept PermissionError:pass\nelse:raise AssertionError('return staging exposed')"
    result=subprocess.run(['/usr/bin/python3','-I','-B','-c',code,relative],preexec_fn=drop,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr.decode()
