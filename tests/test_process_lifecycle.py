"""Real child processes; no live services or unrelated groups are signalled."""
import os,pathlib,subprocess,sys
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

@pytest.mark.parametrize('code',[0,7])
def test_group_is_killed_before_owned_leader_is_reaped(monkeypatch,code):
    from helper import common
    original=common.subprocess.Popen;killpg=common.os.killpg;events=[]
    class Witness(original):
        def wait(self,*a,**kw):
            result=super().wait(*a,**kw);events.append('reap');return result
    def kill(group,sig):
        events.append('kill');return killpg(group,sig)
    monkeypatch.setattr(common.subprocess,'Popen',Witness)
    monkeypatch.setattr(common.os,'killpg',kill)
    if code:
        with pytest.raises(common.Failure,match='exit 7'):common.run([sys.executable,'-c','raise SystemExit(7)'])
    else:assert common.run([sys.executable,'-c','print("ok")'])==b'ok\n'
    assert 'kill' in events,'successful leader exit left its process group unowned'
    assert events.index('kill')<events.index('reap'),'root group signal after reap permits PID/PGID reuse'
