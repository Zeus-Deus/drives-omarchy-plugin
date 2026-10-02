"""Exercise early-boot selection without changing the running guest target."""
import importlib.util,json,os,pathlib,sys
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import Common


def implementation():
    assert importlib.util.find_spec('helper.maintenance') is not None, 'maintenance boot gate is not implemented'
    from helper import maintenance
    return maintenance


def test_pending_request_selects_quiet_target_only_on_new_boot(tmp_path,monkeypatch):
    gate=implementation()
    c=Common(tmp_path/'state')
    move_id='a'*32
    c.journal('moves',move_id,{'id':move_id,'state':'awaiting-maintenance','uid':1000})
    request={'version':1,'moveId':move_id,'action':'continue','armedBootId':'11111111-1111-1111-1111-111111111111'}
    p=c.state_dir/'maintenance-request.json';p.write_text(json.dumps(request));p.chmod(0o600)
    monkeypatch.setattr(gate,'LATCH',p)
    early=tmp_path/'early';early.mkdir(mode=0o700)
    runtime=tmp_path/'run'
    assert gate.generate(early,c.state_dir,runtime,request['armedBootId']) is False
    assert not (early/'default.target').exists()
    boot='22222222-2222-2222-2222-222222222222'
    assert gate.generate(early,c.state_dir,runtime,boot) is True
    assert os.readlink(early/'default.target')=='/etc/systemd/system/drives-maintenance.target'
    receipt=json.loads((runtime/'boot.json').read_text())
    assert receipt['valid'] is True and receipt['bootId']==boot and receipt['moveId']==move_id
    assert (runtime/'boot.json').stat().st_mode&0o777==0o600
    for unit in ('graphical.target','multi-user.target','user@.service','timers.target','paths.target','sockets.target','display-manager.service'):
        assert os.readlink(early/unit)=='/dev/null'
    # Inspect-only boot: the selected journal and source are not modified.
    assert c.read('moves',move_id)['state']=='awaiting-maintenance'


def test_unavailable_var_cannot_hide_root_boot_latch(tmp_path,monkeypatch):
    gate=implementation()
    latch=tmp_path/'maintenance-root-latch.json'
    latch.write_text(json.dumps({'version':1,'moveId':'c'*32,'action':'continue','armedBootId':'11111111-1111-1111-1111-111111111111'}));latch.chmod(0o600)
    monkeypatch.setattr(gate,'LATCH',latch,raising=False)
    early=tmp_path/'early';early.mkdir(mode=0o700);runtime=tmp_path/'run'
    assert gate.generate(early,tmp_path/'unmounted-var',runtime,'22222222-2222-2222-2222-222222222222') is True, 'unavailable /var bypassed maintenance boot selection'
    assert os.readlink(early/'default.target')==gate.TARGET
    assert json.loads((runtime/'boot.json').read_text())['valid'] is False


@pytest.mark.parametrize('version',[True,1.0])
def test_invalid_schema_version_holds_gate_closed(tmp_path,version,monkeypatch):
    gate=implementation();c=Common(tmp_path/'state');move_id='b'*32
    c.journal('moves',move_id,{'id':move_id,'state':'awaiting-maintenance','uid':1000})
    p=c.state_dir/'maintenance-request.json'
    p.write_text(json.dumps({'version':version,'moveId':move_id,'action':'continue','armedBootId':'11111111-1111-1111-1111-111111111111'}));p.chmod(0o600)
    monkeypatch.setattr(gate,'LATCH',p)
    early=tmp_path/'early';early.mkdir(mode=0o700);runtime=tmp_path/'run'
    assert gate.generate(early,c.state_dir,runtime,'22222222-2222-2222-2222-222222222222') is True
    assert os.readlink(early/'default.target')==gate.TARGET
    assert json.loads((runtime/'boot.json').read_text())['valid'] is False
    assert c.read('moves',move_id)['state']=='awaiting-maintenance'


def test_deep_control_json_is_a_controlled_refusal(tmp_path):
    from helper.common import Failure
    gate=implementation();p=tmp_path/'deep-control.json'
    p.write_bytes(b'{"nested":'+b'['*2000+b'0'+b']'*2000+b'}');p.chmod(0o600)
    with pytest.raises(Failure,match='invalid maintenance control JSON'):
        gate.control_json(p)


@pytest.mark.parametrize('record',['latch','journal'])
def test_deep_controls_hold_gate_without_changing_journal(tmp_path,monkeypatch,record):
    gate=implementation();c=Common(tmp_path/'state');move_id='d'*32
    c.journal('moves',move_id,{'id':move_id,'state':'awaiting-maintenance'})
    p=tmp_path/'latch.json';p.write_text(json.dumps({'version':1,'moveId':move_id,'action':'continue','armedBootId':'11111111-1111-1111-1111-111111111111'}));p.chmod(0o600)
    journal=c.path('moves',move_id)
    deep=b'['*2000+b'0'+b']'*2000
    if record=='latch':p.write_bytes(b'{"nested":'+deep+b'}')
    else:journal.write_bytes(b'{"id":"'+move_id.encode()+b'","state":"awaiting-maintenance","nested":'+deep+b'}')
    before=journal.read_bytes();monkeypatch.setattr(gate,'LATCH',p)
    early=tmp_path/'early';early.mkdir(mode=0o700);runtime=tmp_path/'run'
    assert gate.generate(early,c.state_dir,runtime,'22222222-2222-2222-2222-222222222222') is True
    assert os.readlink(early/'default.target')==gate.TARGET
    assert json.loads((runtime/'boot.json').read_text())['valid'] is False
    assert journal.read_bytes()==before


@pytest.mark.parametrize('raw',[b'{broken',b'{"text":"\xff"}'])
def test_invalid_control_encoding_or_syntax_is_a_controlled_refusal(tmp_path,raw):
    from helper.common import Failure
    gate=implementation();p=tmp_path/'invalid.json';p.write_bytes(raw);p.chmod(0o600)
    with pytest.raises(Failure,match='invalid maintenance control JSON'):gate.control_json(p)


def test_audit_entrypoint_reports_deep_controls_as_json(tmp_path):
    import subprocess
    p=tmp_path/'deep.json';p.write_bytes(b'{"nested":'+b'['*2000+b'0'+b']'*2000+b'}');p.chmod(0o600)
    script='import runpy,sys; from helper import maintenance; maintenance.request=lambda:maintenance.control_json(sys.argv[1]); runpy.run_module("helper.maintenance_audit",run_name="__main__")'
    result=subprocess.run(['/usr/bin/python3','-B','-c',script,str(p)],cwd=pathlib.Path(__file__).resolve().parents[1],capture_output=True,timeout=15)
    assert result.returncode==1 and result.stderr==b''
    reply=json.loads(result.stdout)
    assert reply['ok'] is False and 'invalid maintenance control JSON' in reply['error']


def test_control_nesting_counter_ignores_escaped_string_content(tmp_path):
    gate=implementation();p=tmp_path/'quoted.json'
    value={'text':('\\"[{\\\\]}"'*200),'nested':[{'unicode':'café'}]}
    p.write_text(json.dumps(value,ensure_ascii=False));p.chmod(0o600)
    assert gate.control_json(p)==value

