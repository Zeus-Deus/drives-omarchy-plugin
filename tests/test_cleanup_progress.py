"""Deleting an old copy reports progress and finishes after an interrupted restart."""
import os,pathlib,sys,time
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import Common,Failure
from helper import moves


def old_copy(tmp_path,files=1200):
    parent=tmp_path/'user';parent.mkdir();backup=parent/'Videos.pre-move';backup.mkdir()
    for d in range(3):
        sub=backup/('d'+str(d));sub.mkdir()
        for i in range(files//3):(sub/('f'+str(i))).write_bytes(b'x')
    return backup


def manager(tmp_path,backup,state='switched',progress=None,extra=None):
    c=Common(tmp_path/'state');m=moves.MoveManager(c,progress=progress);id='c'*32
    j={'id':id,'state':state,'source':str(tmp_path/'user'/'Videos'),'backup':str(backup),'sourceIdentity':moves.identity(backup),
       'verified':True,'maintenanceProtocol':2,'boot_id':'previous','verification':{'files':1200,'directories':3,'bytes':1200}}
    j.update(extra or {})
    c.journal('moves',id,j)
    return c,m,id


@pytest.fixture
def admitted(monkeypatch):
    def apply(m):
        monkeypatch.setattr(m,'destination',lambda j:None);monkeypatch.setattr(m,'bound',lambda j,*a:True)
        monkeypatch.setattr(m,'wake',lambda mount:None);monkeypatch.setattr(m,'changed',lambda *a:None)
        monkeypatch.setattr(moves,'open_users',lambda p:[]);monkeypatch.setattr(moves,'mount_rows',lambda:[])
    return apply


def test_delete_reports_progress_against_the_verified_total(tmp_path,admitted):
    backup=old_copy(tmp_path);seen=[]
    c,m,id=manager(tmp_path,backup,progress=lambda n,total=None:seen.append((n,total)))
    admitted(m)
    assert m.delete_old(id)['state']=='cleaned'
    assert not backup.exists()
    assert seen and all(total==1203 for _,total in seen), 'progress must use files+directories from verification'
    assert [n for n,_ in seen]==sorted(n for n,_ in seen) and seen[-1][0]<=1203


def test_interrupted_delete_resumes_and_keeps_count(tmp_path,admitted,monkeypatch):
    backup=old_copy(tmp_path)
    c,m,id=manager(tmp_path,backup)
    admitted(m)
    real_unlink=os.unlink;calls=[0]
    def power_cut(*a,**k):
        calls[0]+=1
        if calls[0]==700:raise KeyboardInterrupt('power cut')
        return real_unlink(*a,**k)
    # Save the count on every progress tick: a clock local to moves.py only.
    import types
    clock=[0.0]
    def tick_clock():clock[0]+=10;return clock[0]
    monkeypatch.setattr(moves,'time',types.SimpleNamespace(monotonic=tick_clock,time=time.time,sleep=time.sleep))
    monkeypatch.setattr(moves,'blocks',lambda:{})
    monkeypatch.setattr(moves.os,'unlink',power_cut)
    with pytest.raises(KeyboardInterrupt):m.delete_old(id)
    saved=c.read('moves',id)
    assert saved['state']=='cleaning' and saved['cleanup']['total']==1203 and 0<saved['cleanup']['deleted']<1203
    monkeypatch.setattr(moves.os,'unlink',real_unlink)
    # After the restart the panel shows it as a deletion, not a fault.
    live=m.inspect()[0]
    assert live['state']=='cleaning' and live['error']=='' and 'interruptedState' not in live
    seen=[];m.progress=lambda n,total=None:seen.append(n)
    assert m.delete_old(id)['state']=='cleaned' and not backup.exists()
    assert seen and seen[0]>saved['cleanup']['deleted'], 'resumed count must continue from the saved one'


def test_helper_finishes_interrupted_cleanup_by_itself(tmp_path,admitted,monkeypatch):
    from helper import maintenance
    monkeypatch.setattr(maintenance,'LATCH',tmp_path/'no-latch');monkeypatch.setattr(maintenance,'RUNTIME',tmp_path/'no-runtime')
    backup=old_copy(tmp_path,30)
    c,_,id=manager(tmp_path,backup,state='cleaning',extra={'cleanup':{'total':1203,'deleted':5}})
    from helper import service
    real=service.MoveManager
    def patched(*a,**k):
        m=real(*a,**k);admitted(m);return m
    monkeypatch.setattr(service,'MoveManager',patched)
    server=service.Server(c)
    assert server.resume_cleanups(attempts=3,delay=0,poll=0.01) is True
    assert c.read('moves',id)['state']=='cleaned' and not backup.exists()
    job=server.jobs[-1]
    assert job['method']=='DeleteOldCopy' and job['automatic'] is True and job['state']=='done'


def test_resume_does_nothing_without_pending_cleanup(tmp_path):
    from helper import service
    c=Common(tmp_path/'state');c.journal('moves','d'*32,{'id':'d'*32,'state':'rebooted'})
    server=service.Server(c)
    assert server.resume_cleanups(attempts=1,delay=0) is True and server.jobs==[]


def test_resume_still_refuses_an_unsafe_cleanup(tmp_path,monkeypatch):
    """Resuming never widens what may be deleted: the same checks apply."""
    from helper import maintenance,service
    monkeypatch.setattr(maintenance,'LATCH',tmp_path/'no-latch');monkeypatch.setattr(maintenance,'RUNTIME',tmp_path/'no-runtime')
    backup=old_copy(tmp_path,30)
    c,_,id=manager(tmp_path,backup,state='cleaning',extra={'verified':False})
    server=service.Server(c)
    assert server.resume_cleanups(attempts=2,delay=0,poll=0.01) is False
    assert backup.exists() and c.read('moves',id)['state']=='cleaning'
    assert all(j['state']=='failed' for j in server.jobs)
