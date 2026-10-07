"""Queued VM-only cuts can exercise the pause/completion crash windows."""
import builtins,io,os,pathlib,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper import offline

def test_vm_crash_queue_is_durable_before_each_cut(tmp_path,monkeypatch):
    marker=tmp_path/'marker';marker.write_text('return-copying,return-paused');marker.chmod(0o600)
    monkeypatch.setattr(offline,'QA_CRASH',marker);monkeypatch.setattr(offline,'LOG',tmp_path/'log')
    actual=pathlib.Path.stat
    def root_marker(path,*args,**kwargs):
        info=actual(path,*args,**kwargs)
        if path==marker:
            fields=list(info);fields[4]=0;return os.stat_result(fields)
        return info
    monkeypatch.setattr(pathlib.Path,'stat',root_marker)
    monkeypatch.setattr(offline,'run',lambda *a,**k:b'kvm\n')
    monkeypatch.setattr(offline.time,'sleep',lambda *a:None)
    writes=[];real_open=builtins.open
    class Trigger(io.StringIO):
        def write(self,text):writes.append(text);return super().write(text)
    monkeypatch.setattr(builtins,'open',lambda p,*a,**k:Trigger() if str(p)=='/proc/sysrq-trigger' else real_open(p,*a,**k))
    offline.qa_crash('return-copying')
    assert marker.read_text()=='return-paused'
    assert writes==['b']
    offline.qa_crash('return-paused')
    assert not marker.exists() and writes==['b','b']
