"""Runtime admission, not just generator selection, is required for maintenance."""
import importlib.util,pathlib,sys
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import Failure


def implementation():
    assert importlib.util.find_spec('helper.maintenance_audit') is not None,'runtime maintenance admission is missing'
    from helper import maintenance_audit
    return maintenance_audit


def quiet_snapshot():
    boot='22222222-2222-2222-2222-222222222222'
    request={'version':1,'moveId':'a'*32,'action':'continue','armedBootId':'11111111-1111-1111-1111-111111111111'}
    return {'bootId':boot,'latch':request,'receipt':dict(request,bootId=boot,valid=True,error=''),'targetActive':True,'defaultTarget':'/etc/systemd/system/drives-maintenance.target','masksComplete':True,'targetRequires':['systemd-remount-fs.service','systemd-journald.service','systemd-udevd.service','drives-maintenance-splash.service'],'targetWants':[],'sessions':[],'jobs':[],'units':[{'unit':'systemd-udevd.service','active':'active','sub':'running'}],'processes':[],'callerUnit':'drives-maintenance-audit.service'}


def test_quiet_boot_admitted_but_live_writer_and_activation_refused():
    gate=implementation();snapshot=quiet_snapshot()
    result=gate.validate_snapshot(snapshot,'a'*32,'continue')
    assert result['ok'] is True and result['bootId']==snapshot['bootId']
    snapshot['units'].append({'unit':'docker.socket','active':'active','sub':'listening'})
    with pytest.raises(Failure,match='activation|unexpected unit'):gate.validate_snapshot(snapshot,'a'*32,'continue')
    snapshot=quiet_snapshot();snapshot['processes']=[{'pid':42,'uid':[1000]*4,'exe':'/usr/bin/app','unit':'user@1000.service'}]
    with pytest.raises(Failure,match='process'):gate.validate_snapshot(snapshot,'a'*32,'continue')


@pytest.mark.parametrize('change',[{'bootId':'33333333-3333-3333-3333-333333333333'},{'callerUnit':'sshd.service'},{'targetWants':['sshd.service']},{'sessions':['1']},{'jobs':['pending.service']}])
def test_stale_or_incomplete_exclusion_is_refused(change):
    gate=implementation();snapshot=quiet_snapshot();snapshot.update(change)
    with pytest.raises(Failure):gate.validate_snapshot(snapshot,'a'*32,'continue')


def test_real_live_runtime_refuses_even_with_matching_receipt(tmp_path,monkeypatch):
    gate=implementation()
    assert callable(getattr(gate,'collect_runtime',None)), 'real runtime collector is missing'
    snapshot=quiet_snapshot();snapshot.update(gate.collect_runtime())
    snapshot['receipt']['bootId']=snapshot['bootId']
    assert snapshot['targetActive'] is False, 'test must run on the ordinary guest boot'
    with pytest.raises(Failure):gate.validate_snapshot(snapshot,'a'*32,'continue')


def test_readonly_audit_entrypoint_refuses_an_ordinary_boot():
    import json,subprocess
    assert not pathlib.Path('/drives-maintenance-request.json').exists()
    result=subprocess.run(['/usr/bin/python3','-B','-m','helper.maintenance_audit'],cwd=pathlib.Path(__file__).resolve().parents[1],capture_output=True,timeout=15)
    assert result.returncode==1, 'ordinary boot was not refused by the real entrypoint'
    assert json.loads(result.stdout)['ok'] is False


def test_stock_udevd_executable_alias_is_not_an_unexpected_process():
    import os
    gate=implementation();snapshot=quiet_snapshot()
    snapshot['processes']=[{'pid':42,'uid':[0,0,0,0],'exe':os.path.realpath('/usr/lib/systemd/systemd-udevd'),'unit':'systemd-udevd.service'}]
    assert gate.validate_snapshot(snapshot,'a'*32,'continue')['ok'] is True


def test_unexpected_process_refusal_names_executable_and_unit():
    gate=implementation();snapshot=quiet_snapshot()
    snapshot['processes']=[{'pid':42,'uid':[0,0,0,0],'exe':'/usr/bin/unexpected-worker','unit':'systemd-udevd.service'}]
    with pytest.raises(Failure) as error:gate.validate_snapshot(snapshot,'a'*32,'continue')
    assert '/usr/bin/unexpected-worker' in str(error.value) and 'systemd-udevd.service' in str(error.value)


def test_finished_boot_splash_is_allowed_but_residual_daemon_is_refused():
    gate=implementation();snapshot=quiet_snapshot()
    snapshot['units'].append({'unit':'drives-maintenance-splash.service','active':'active','sub':'exited'})
    assert gate.validate_snapshot(snapshot,'a'*32,'continue')['ok'] is True
    snapshot['processes']=[{'pid':42,'uid':[0,0,0,0],'exe':'/usr/bin/plymouthd (deleted)','unit':''}]
    with pytest.raises(Failure,match='unexpected process'):gate.validate_snapshot(snapshot,'a'*32,'continue')


def test_audit_service_is_explicit_and_packaged():
    root=pathlib.Path(__file__).resolve().parents[1]
    unit=root/'packaging/drives-maintenance-audit.service'
    assert unit.is_file(), 'read-only fixed audit service is missing'
    text=unit.read_text()
    assert 'DefaultDependencies=no' in text and 'ExecStart=/usr/bin/python3 -B -m helper.maintenance_audit' in text
    assert '[Install]' not in text and 'WantedBy=' not in text
    assert 'drives-maintenance-audit.service' in (root/'helper/install.sh').read_text()
