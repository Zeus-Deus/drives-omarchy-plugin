"""Discard cuts recover by explicit cancellation, never adopting unknown data."""
import json,os,pathlib
import pytest
from helper import moves,destination,maintenance
from helper.common import Failure
import test_private_destination as fixtures


def plan(tmp_path,monkeypatch):
    m,j=fixtures.plan(tmp_path);m.make_destination(j)
    j['state']='awaiting-maintenance';m.c.journal('moves',j['id'],j)
    monkeypatch.setattr(m,'changed',lambda *a:None)
    monkeypatch.setattr(moves,'probe',lambda *a,**k:{'supported':True,'encrypted':True,'disk':{'serial':'TESTDATA'}})
    monkeypatch.setattr(moves,'mount_rows',lambda:[])
    monkeypatch.setattr(m,'bound',lambda *a:False)
    monkeypatch.setattr(maintenance,'LATCH',tmp_path/'no-latch');monkeypatch.setattr(maintenance,'RUNTIME',tmp_path/'no-runtime')
    return m,j


@pytest.mark.parametrize('cut',['content-removed','wrapper-removed','recreated-synced','worker-return','identity-save'])
def test_discard_cut_has_durable_cancel_path_without_adoption(tmp_path,monkeypatch,cut):
    m,j=plan(tmp_path,monkeypatch);original=pathlib.Path(j['source'],'synthetic-token').read_bytes()
    real_rmdir=os.rmdir;real_sync=os.fsync;removed=[False]
    def rmdir(name,*a,**kw):
        result=real_rmdir(name,*a,**kw)
        if name=='content':removed[0]=True
        if (cut=='content-removed' and name=='content') or (cut=='wrapper-removed' and name==pathlib.Path(j['destContainer']).name):raise KeyboardInterrupt('cut after removal')
        return result
    def sync(fd):
        real_sync(fd)
        if cut=='recreated-synced' and removed[0] and os.readlink('/proc/self/fd/'+str(fd))==j['dest']:raise KeyboardInterrupt('cut after recreated content fsync')
    actual=m.destination_op
    def worker(record,*args):
        result=actual(record,*args)
        if cut=='worker-return':raise KeyboardInterrupt('lost worker response')
        return result
    journal=m.c.journal
    def save(kind,id,value):
        if cut=='identity-save' and value.get('destIdentity')!=j['destIdentity']:raise KeyboardInterrupt('identity save interrupted')
        return journal(kind,id,value)
    with monkeypatch.context() as fault:
        fault.setattr(os,'rmdir',rmdir);fault.setattr(os,'fsync',sync)
        fault.setattr(m,'destination_op',worker);fault.setattr(m.c,'journal',save)
        with pytest.raises(KeyboardInterrupt):
            if cut=='wrapper-removed':m.cancel(j['id'])
            else:m.restart(j['id'])
    saved=m.c.read('moves',j['id'])
    assert saved.get('discardIntent'), 'destructive worker must have durable intent before its first unlink'
    # Simulate a different occupant after the cut. Cancellation may retain it,
    # but must never bless its identity or recursively delete it.
    target=pathlib.Path(j['destContainer'])
    if not target.exists():target.mkdir(mode=0o700)
    token=target/'unknown-after-cut';token.write_bytes(b'not owned by the old plan')
    assert m.inspect()[0]['canCancelIncomplete'] is True
    result=m.cancel(j['id'])
    assert result['state']=='rolled-back' and result['retainedDestination']==str(target)
    assert token.read_bytes()==b'not owned by the old plan'
    assert pathlib.Path(j['source'],'synthetic-token').read_bytes()==original
    assert m.c.read('moves',j['id'])['state']=='rolled-back'


def test_cut_after_identity_commit_uses_recorded_new_copy(tmp_path,monkeypatch):
    m,j=plan(tmp_path,monkeypatch);journal=m.c.journal
    def committed(kind,id,value):
        result=journal(kind,id,value)
        if value.get('destIdentity')!=j['destIdentity'] and not value.get('discardIntent'):raise KeyboardInterrupt('cut after identity commit')
        return result
    with monkeypatch.context() as fault:
        fault.setattr(m.c,'journal',committed)
        with pytest.raises(KeyboardInterrupt):m.restart(j['id'])
    saved=m.c.read('moves',j['id'])
    assert not saved.get('discardIntent') and saved['destIdentity']==moves.identity(j['dest'])
    assert m.cancel(j['id'])['state']=='rolled-back'
    assert not pathlib.Path(j['destContainer']).exists()
    assert pathlib.Path(j['source'],'synthetic-token').read_bytes()==b'nonsecret regression fixture'
