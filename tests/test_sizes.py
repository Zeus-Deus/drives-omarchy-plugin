import json,os,pathlib,sys,time
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import pytest
import bridge,topology
from helper.common import Failure

def test_boot_unlock_reads_the_generated_cryptsetup_unit(tmp_path):
    (tmp_path/'systemd-cryptsetup@bulk.service').write_text("[Service]\nExecStart=/usr/bin/systemd-cryptsetup attach 'bulk' '/dev/disk/by-uuid/x' '/etc/cryptsetup-keys.d/bulk.key' 'luks,nofail'\n")
    (tmp_path/'systemd-cryptsetup@ask.service').write_text("ExecStart=/usr/bin/systemd-cryptsetup attach 'ask' '/dev/sdb1' '-' 'luks'\n")
    assert topology.boot_unlock('bulk',tmp_path)=='keyfile'
    assert topology.boot_unlock('ask',tmp_path)=='prompt'
    assert topology.boot_unlock('missing',tmp_path)==''
    assert topology.boot_unlock('../etc/x',tmp_path)==''

def test_du_streams_finished_folders_and_stops_at_its_budget(tmp_path):
    (tmp_path/'a').mkdir();(tmp_path/'a'/'f').write_bytes(b'x'*8192);(tmp_path/'b').mkdir()
    seen=[];entries,complete=topology.du(['--max-depth=1','--',str(tmp_path)],10,emit=seen.append)
    assert complete and {e['path'] for e in entries}=={str(tmp_path),str(tmp_path/'a'),str(tmp_path/'b')} and seen==entries
    slow=tmp_path/'slow.sh';slow.write_text('#!/bin/sh\nprintf "1\\t/one\\n";sleep 30\n');slow.chmod(0o755)
    t=time.monotonic();entries,complete=topology.du([],0.5,argv0=(str(slow),))
    assert not complete and entries==[{'path':'/one','bytes':1}] and time.monotonic()-t<5

def test_size_stream_reports_home_folders_steam_and_requested_paths(tmp_path):
    home=tmp_path/'home';(home/'Videos').mkdir(parents=True);(home/'Videos'/'v').write_bytes(b'x'*4096)
    steam=home/'.local/share/Steam/steamapps';steam.mkdir(parents=True)
    extra=tmp_path/'bound';extra.mkdir()
    lines=[];bridge.stream_sizes({'op':'sizes','paths':[str(extra),str(tmp_path/'gone')]},lines.append,home=str(home),budget=20)
    assert lines[0]=={'steam':str(steam),'home':str(home)} and lines[-1]=={'done':True,'complete':True}
    assert any(l.get('kind')=='entry' and l['path']==str(home/'Videos') for l in lines)
    extras={l['path']:l['bytes'] for l in lines if l.get('kind')=='extra'}
    assert set(extras)=={str(steam),str(extra),str(tmp_path/'gone')} and extras[str(tmp_path/'gone')] is None

@pytest.mark.parametrize('req',[{'op':'sizes','paths':'x'},{'op':'sizes','paths':['rel']},{'op':'sizes','paths':['/a\nb']},{'op':'sizes','paths':['/a']*17},{'op':'sizes','extra':1},{'op':'other'}])
def test_size_requests_are_validated(req):
    with pytest.raises(Failure):bridge.size_request(req)
