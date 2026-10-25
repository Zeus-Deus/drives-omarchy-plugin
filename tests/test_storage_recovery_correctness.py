"""Storage regressions: root guest components, no mounts or installed writes."""
import os,pathlib,types
import pytest
from helper import moves,offline
from helper.common import Failure
import test_offline as fixtures

pytestmark=pytest.mark.skipif(os.geteuid()!=0,reason='owned root guest fixtures')

@pytest.mark.parametrize('store_saved',[False,True])
def test_recovery_checks_unquarantined_or_already_restored_source_before_config(tmp_path,monkeypatch,store_saved):
    c,w,j,source,dest,calls=fixtures.fixture(tmp_path,monkeypatch)
    j['parentIdentity']=moves.identity(str(source.parent));j['sourceUUID']='SOURCE'
    if store_saved:
        with moves.anchored_tree(str(source)) as (_,fd,_):store=offline.prepare_store(fd,str(tmp_path),j['id'])
        j['quarantine']=store;j['backup']=store['path']+'/original'
    j['state']='quarantining';j['sourceIdentity'][1]+=1
    monkeypatch.setattr(w.m,'changed',types.MethodType(moves.MoveManager.changed,w.m))
    monkeypatch.setattr(moves,'probe',lambda *a,**k:{'supported':True,'chain':[{'uuid':'SOURCE'}]})
    with pytest.raises(offline.Unsafe):w.continue_move(j)
    assert calls==[], 'changed live source must be refused before removing configuration'
    assert (source/'sub'/'note').read_text()=='keep'

@pytest.mark.parametrize('action',['continue','rollback'])
@pytest.mark.parametrize('changed',['uuid','parent','store'])
def test_recovery_refuses_changed_storage_before_any_mutation(tmp_path,monkeypatch,action,changed):
    c,w,j,source,dest,calls=fixtures.fixture(tmp_path,monkeypatch)
    j['parentIdentity']=moves.identity(str(source.parent));j['sourceUUID']='SOURCE'
    with moves.anchored_tree(str(source)) as (_,fd,_):store=offline.prepare_store(fd,str(tmp_path),j['id'])
    j['quarantine']=store;j['backup']=store['path']+'/original'
    offline.move_original(str(source),j['sourceIdentity'],store)
    j['state']='copying' if action=='continue' else 'rolling-back'
    if changed=='parent':j['parentIdentity'][1]+=1
    if changed=='store':j['quarantine']['identity'][1]+=1
    monkeypatch.setattr(w.m,'changed',types.MethodType(moves.MoveManager.changed,w.m))
    monkeypatch.setattr(moves,'probe',lambda *a,**k:{'supported':True,'chain':[{'uuid':'OTHER' if changed=='uuid' else 'SOURCE'}]})
    c.journal('moves',j['id'],j);before=c.read('moves',j['id'])
    with pytest.raises((Failure,OSError)):
        getattr(w,'continue_move' if action=='continue' else 'rollback_move')(c.read('moves',j['id']))
    assert calls==[], 'recovery must reject before config removal or unmount'
    assert not source.exists() and pathlib.Path(j['backup'],'sub','note').read_text()=='keep'
    assert c.read('moves',j['id'])==before

@pytest.mark.parametrize('action',['continue','rollback'])
def test_recovery_allows_reboot_device_renumbering(tmp_path,monkeypatch,action):
    c,w,j,source,dest,calls=fixtures.fixture(tmp_path,monkeypatch)
    j['parentIdentity']=moves.identity(str(source.parent));j['sourceUUID']='SOURCE'
    with moves.anchored_tree(str(source)) as (_,fd,_):store=offline.prepare_store(fd,str(tmp_path),j['id'])
    j['quarantine']=store;j['backup']=store['path']+'/original'
    offline.move_original(str(source),j['sourceIdentity'],store)
    j['state']='copying' if action=='continue' else 'rolling-back'
    for ident in (j['sourceIdentity'],j['parentIdentity'],j['quarantine']['identity']):ident[0]+=100
    monkeypatch.setattr(w.m,'changed',types.MethodType(moves.MoveManager.changed,w.m))
    monkeypatch.setattr(moves,'probe',lambda *a,**k:{'supported':True,'chain':[{'uuid':'SOURCE'}]})
    c.journal('moves',j['id'],j)
    getattr(w,'continue_move' if action=='continue' else 'rollback_move')(c.read('moves',j['id']))
    assert (source/'sub'/'note').read_text()=='keep'
    assert c.read('moves',j['id'])['state']==('paused' if action=='continue' else 'rolled-back')
