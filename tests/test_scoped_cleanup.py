"""Cleanup dispatch and real narrowly writable systemd guest component."""
import json,os,pathlib,subprocess
import pytest
from helper import moves,quarantine
from helper.common import Common,Failure


def fixture(tmp_path,monkeypatch):
    root=tmp_path/'origin';root.mkdir();container=root/'.drives-quarantine';container.mkdir(mode=0o700)
    store=container/('a'*32);store.mkdir(mode=0o700);original=store/'original';original.mkdir();(original/'keep').write_text('old')
    c=Common(tmp_path/'state');m=moves.MoveManager(c,isolated=True)
    j={'id':'a'*32,'state':'switched','source':str(root/'home'/'data'),'sourceMount':str(root),'sourceUUID':'SRC',
       'backup':str(original),'sourceIdentity':moves.identity(str(original)),'quarantine':{'path':str(store),'identity':moves.identity(str(store))},
       'verified':True,'maintenanceProtocol':2,'boot_id':'old'}
    c.journal('moves',j['id'],j)
    monkeypatch.setattr(m,'wake',lambda *a:None);monkeypatch.setattr(m,'destination',lambda *a:None)
    monkeypatch.setattr(m,'bound',lambda *a:True);monkeypatch.setattr(m,'changed',lambda *a:None)
    monkeypatch.setattr(moves,'open_users',lambda *a:[]);monkeypatch.setattr(moves,'mount_rows',lambda:[])
    return c,m,j,original


def test_isolated_cleanup_uses_scoped_worker_not_readonly_helper(tmp_path,monkeypatch):
    c,m,j,original=fixture(tmp_path,monkeypatch);commands=[]
    def worker(argv,**kw):
        commands.append(argv)
        assert argv[0]=='systemd-run'
        assert '--property=ProtectSystem=strict' in argv
        assert '--property=ReadWritePaths='+str(original.parent.parent) in argv
        assert '--property=ReadWritePaths=/' not in argv
        return b'{"ok":true}'
    monkeypatch.setattr(moves,'run',worker)
    monkeypatch.setattr(moves,'delete_tree_fd',lambda *a,**k:pytest.fail('readonly long-running helper attempted recursive cleanup'))
    assert m.delete_old(j['id'])['state']=='cleaned'
    assert commands and c.read('moves',j['id'])['state']=='cleaned'


@pytest.mark.parametrize('changed',['store','original','layout'])
def test_cleanup_worker_refuses_unknown_names(tmp_path,monkeypatch,changed):
    c,m,j,original=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(moves,'probe',lambda *a,**k:{'supported':True,'chain':[{'uuid':'SRC'}]})
    monkeypatch.setattr(quarantine,'mount_rows',lambda:[])
    if changed=='store':j['quarantine']['identity'][1]+=1
    elif changed=='original':j['sourceIdentity'][1]+=1
    else:j['backup']=str(original.parent)
    with pytest.raises(Failure):quarantine.cleanup(j)
    assert (original/'keep').read_text()=='old'


@pytest.mark.skipif(os.environ.get('RUN_STORAGE_SYSTEMD')!='1',reason='explicit owned guest systemd component')
def test_real_strict_namespace_scoped_cleanup(tmp_path,monkeypatch):
    c,m,j,original=fixture(tmp_path,monkeypatch)
    # Use the candidate checkout, never /usr/lib/drives-helper or installed state.
    source_root=pathlib.Path(__file__).resolve().parents[1]
    import topology
    actual=topology.probe(str(original.parent.parent),resolve=False)
    assert actual['supported']
    j['sourceUUID']=actual['chain'][-1]['uuid'];c.journal('moves',j['id'],j)
    sibling=original.parent.parent/('b'*32);sibling.mkdir(mode=0o700)
    (sibling/'keep').write_bytes(b'unrelated quarantine')
    control=tmp_path/'outside-scope';control.write_bytes(b'read-only control')
    prefix=['systemd-run','--quiet','--wait','--collect','--pipe','--property=ProtectSystem=strict',
        '--property=ProtectHome=read-only','--property=ReadWritePaths='+str(original.parent.parent)]
    # Actual mount-namespace negative: the same worker cannot write its sibling
    # outside the selected quarantine container. No host/guest storage mount call.
    code="import pathlib,sys,errno; p=pathlib.Path(sys.argv[1]);\ntry:p.unlink()\nexcept OSError as e:assert e.errno==errno.EROFS\nelse:raise AssertionError('namespace writable outside cleanup scope')"
    subprocess.run(prefix+['/usr/bin/python3','-c',code,str(control)],check=True,capture_output=True)
    def real_worker(argv,**kw):
        argv=[('--property=WorkingDirectory='+str(source_root)) if a=='--property=WorkingDirectory=/usr/lib/drives-helper' else a for a in argv]
        return subprocess.run(argv,check=True,capture_output=True,timeout=60).stdout
    monkeypatch.setattr(moves,'run',real_worker)
    assert m.delete_old(j['id'])['state']=='cleaned'
    assert not original.parent.exists() and (sibling/'keep').read_bytes()==b'unrelated quarantine'
    assert control.read_bytes()==b'read-only control'
    assert c.read('moves',j['id'])['state']=='cleaned'
