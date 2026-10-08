"""Units the desktop enabled for default.target must not join the maintenance boot.

The maintenance target is selected as a default.target alias, so systemd also
applies default.target.wants/ (e.g. `systemctl enable nordvpnd`) and generic
target.d drop-ins to it. The audit then refused every move on such machines.
"""
import json,os,pathlib,shutil,subprocess,sys
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import Common,Failure
from helper import maintenance,maintenance_audit


def unit_tree(root):
    etc=root/'etc';(etc/'default.target.wants').mkdir(parents=True)
    os.symlink('/usr/lib/systemd/system/nordvpnd.service',etc/'default.target.wants'/'nordvpnd.service')
    (etc/'default.target.requires').mkdir()
    os.symlink('/usr/lib/systemd/system/sshd.service',etc/'default.target.requires'/'sshd.service')
    (etc/'target.d').mkdir();(etc/'target.d'/'50-shared.conf').write_text('[Unit]\nWants=syncthing.service\n')
    (etc/'drives-maintenance.target.d').mkdir();(etc/'drives-maintenance.target.d'/'50-own.conf').write_text('[Unit]\nWants=ydotool.service\n')
    (etc/'drives-maintenance.target.wants').mkdir()
    os.symlink('/etc/systemd/system/drives-maintenance-worker.service',etc/'drives-maintenance.target.wants'/maintenance_audit.WORKER_UNIT)
    (etc/'multi-user.target.wants').mkdir();os.symlink('/x/docker.service',etc/'multi-user.target.wants'/'docker.service')
    return etc


def test_masks_cover_alias_dependencies_but_keep_the_declared_worker(tmp_path):
    etc=unit_tree(tmp_path)
    masks=maintenance.target_dependency_masks((str(etc),str(tmp_path/'absent')))
    assert ('default.target.wants','nordvpnd.service') in masks
    assert ('default.target.requires','sshd.service') in masks
    assert ('target.d','50-shared.conf') in masks
    assert ('drives-maintenance.target.d','50-own.conf') in masks
    assert not any(entry==maintenance_audit.WORKER_UNIT for _,entry in masks)
    # Other targets are already masked or unrelated; leave them alone.
    assert not any(d.startswith('multi-user') for d,_ in masks)


@pytest.mark.skipif(os.geteuid()!=0,reason='generator writes root-owned control state')
def test_generator_masks_enabled_default_target_units(tmp_path,monkeypatch):
    etc=unit_tree(tmp_path)
    monkeypatch.setattr(maintenance,'UNIT_DIRS',(str(etc),))
    c=Common(tmp_path/'state');move_id='d'*32
    c.journal('moves',move_id,{'id':move_id,'state':'awaiting-maintenance'})
    latch=tmp_path/'latch.json';latch.write_text(json.dumps({'version':1,'moveId':move_id,'action':'continue','armedBootId':'11111111-1111-1111-1111-111111111111'}));latch.chmod(0o600)
    monkeypatch.setattr(maintenance,'LATCH',latch)
    early=tmp_path/'early';early.mkdir(mode=0o700)
    assert maintenance.generate(early,c.state_dir,tmp_path/'run','22222222-2222-2222-2222-222222222222')
    for rel in ('default.target.wants/nordvpnd.service','default.target.requires/sshd.service','target.d/50-shared.conf','drives-maintenance.target.d/50-own.conf'):
        assert os.readlink(early/rel)=='/dev/null',rel
    assert not (early/'drives-maintenance.target.wants').exists()


def test_closure_refusal_names_the_unit():
    boot='22222222-2222-2222-2222-222222222222'
    request={'version':1,'moveId':'a'*32,'action':'continue','armedBootId':'11111111-1111-1111-1111-111111111111'}
    snap={'bootId':boot,'latch':request,'receipt':dict(request,bootId=boot,valid=True,error=''),'targetActive':True,
          'defaultTarget':maintenance_audit.TARGET,'masksComplete':True,'targetRequires':sorted(maintenance_audit.REQUIRES),
          'targetWants':[maintenance_audit.WORKER_UNIT,'nordvpnd.service'],'sessions':[],'jobs':[],'units':[],'processes':[],'callerUnit':maintenance_audit.WORKER_UNIT}
    with pytest.raises(Failure,match='extra nordvpnd.service'):maintenance_audit.validate_snapshot(snap,'a'*32,'continue',caller=maintenance_audit.WORKER_UNIT)


SYSTEMD='/usr/lib/systemd/systemd'
@pytest.mark.skipif(not os.access(SYSTEMD,os.X_OK) or not pathlib.Path('/etc/systemd/system/drives-maintenance.target').is_file(),reason='needs systemd and the installed target')
def test_real_systemd_closure_is_exactly_the_declared_one(tmp_path):
    """Ask systemd itself what the alias target pulls in, before and after masking."""
    etc=unit_tree(tmp_path);early=tmp_path/'early';early.mkdir()
    os.symlink(maintenance.TARGET,early/'default.target')
    def closure():
        env=dict(os.environ,SYSTEMD_UNIT_PATH=':'.join([str(early),str(etc),'/etc/systemd/system','/usr/lib/systemd/system']))
        out=subprocess.run([SYSTEMD,'--test','--system','--no-pager','--unit=default.target'],env=env,capture_output=True,text=True,timeout=60).stdout
        if 'Unit drives-maintenance.target:' not in out:pytest.skip('systemd --test cannot load units in this sandbox')
        block=out.split('Unit drives-maintenance.target:',1)[1].split('\u2192 Unit ',1)[0]
        return {tuple(line.split()[:2]) for line in block.splitlines() if line.strip().startswith(('Wants:','Requires:'))}
    before=closure()
    assert ('Wants:','nordvpnd.service') in before and ('Wants:','syncthing.service') in before
    real_private=maintenance.private_directory
    maintenance.private_directory=lambda p,create=False:(pathlib.Path(p).mkdir(exist_ok=True) if create else None) or pathlib.Path(p)
    try:maintenance.neutralize_target_dependencies(early,(str(etc),))
    finally:maintenance.private_directory=real_private
    wants={u for k,u in closure() if k=='Wants:'};requires={u for k,u in closure() if k=='Requires:'}
    assert wants<={maintenance_audit.WORKER_UNIT} and requires==maintenance_audit.REQUIRES
