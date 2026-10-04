"""Run the shipped uninstaller with private path fixtures and systemctl spy.

Only fixed filesystem paths are remapped; no production test override is added.
Native guest tests separately exercise the unmodified script at real paths.
"""
import json,os,pathlib,subprocess
import pytest

ROOT=pathlib.Path(__file__).resolve().parents[1]
OWNED=[
    '/etc/systemd/system/drives-helper.service',
    '/etc/systemd/system/drives-maintenance.target',
    '/etc/systemd/system/drives-maintenance-splash.service',
    '/etc/systemd/system/drives-maintenance-audit.service',
    '/etc/systemd/system/drives-maintenance-worker.service',
    '/etc/systemd/system-generators/drives-maintenance-generator',
    '/usr/share/polkit-1/actions/io.github.zeus-deus.drives.policy',
    '/etc/dbus-1/system.d/io.github.zeus_deus.Drives.conf',
    '/etc/systemd/system/drives-normal-boot-guard.service',
    '/etc/systemd/system/sysinit.target.d/drives-normal-boot-guard.conf',
    '/usr/lib/drives-helper/normal_boot_guard.py',
]


def harness(tmp_path):
    assert os.geteuid()==0,'run uninstall qualification as root inside the test guest'
    paths=OWNED+['/drives-maintenance-request.json','/run/drives-maintenance']
    mapped={name:tmp_path/('path-'+str(index)) for index,name in enumerate(paths)}
    mapped['/run/drives-maintenance/boot.json']=mapped['/run/drives-maintenance']/'boot.json'
    for name in OWNED:mapped[name].write_bytes(('owned:'+name).encode())
    script=(ROOT/'helper/uninstall.sh').read_text()
    for name in sorted(mapped,key=len,reverse=True):script=script.replace(name,str(mapped[name]))
    # Every absolute path the script would remove must be remapped, or the
    # test deletes real files from the guest it runs in.
    import re
    leaked=[p for p in re.findall(r'(?<![\w.-])/(?:etc|usr|run)/[\w./@-]+',script) if not p.startswith(str(tmp_path))]
    assert not [p for p in leaked if not p.startswith(('/usr/bin','/run/drives-maintenance'))],leaked
    runner=tmp_path/'uninstall.sh';runner.write_text(script)
    spy=tmp_path/'systemctl'
    spy.write_text('#!/usr/bin/python3\nimport json,os,pathlib,sys\na=sys.argv[1:]\nwith open(os.environ["SPY_LOG"],"a") as f:f.write(json.dumps(a)+"\\n")\nif a[0]=="show":print(os.environ.get("TARGET_STATE","inactive"))\nif a[0]=="stop" and os.environ.get("LATE_LATCH"):pathlib.Path(os.environ["LATE_LATCH"]).write_text("late request")\n')
    spy.chmod(0o755)
    log=tmp_path/'calls.jsonl'
    env={'PATH':str(tmp_path)+':/usr/bin:/bin','SPY_LOG':str(log),'LC_ALL':'C'}
    return mapped,runner,log,env


@pytest.mark.parametrize('kind',['regular','dangling'])
def test_uninstall_refuses_armed_latch_before_any_mutation(tmp_path,kind):
    mapped,runner,log,env=harness(tmp_path)
    latch=mapped['/drives-maintenance-request.json']
    if kind=='regular':latch.write_bytes(b'invalid request still holds recovery')
    else:latch.symlink_to(tmp_path/'missing')
    before={name:mapped[name].read_bytes() for name in OWNED}
    result=subprocess.run(['bash',str(runner)],env=env,capture_output=True,timeout=15)
    assert result.returncode!=0,'uninstall removed recovery infrastructure while armed'
    assert b'maintenance' in result.stdout+result.stderr
    assert not log.exists(),'armed refusal must occur before even contacting systemd'
    assert {name:mapped[name].read_bytes() for name in OWNED}==before
    assert os.path.lexists(latch)


def test_uninstall_removes_only_owned_infrastructure_when_unarmed(tmp_path):
    mapped,runner,log,env=harness(tmp_path)
    retained={name:tmp_path/name for name in ('fstab','crypttab','journal','keyfile','foreign.service','original-data')}
    for name,path in retained.items():path.write_bytes(('retain:'+name).encode())
    before={name:path.read_bytes() for name,path in retained.items()}
    result=subprocess.run(['bash',str(runner)],env=env,capture_output=True,timeout=15)
    assert result.returncode==0,result.stderr
    assert all(not mapped[name].exists() for name in OWNED),'owned maintenance infrastructure was left installed'
    assert {name:path.read_bytes() for name,path in retained.items()}==before
    calls=[json.loads(line) for line in log.read_text().splitlines()]
    assert ['stop','drives-helper.service'] in calls
    assert ['disable','drives-helper.service'] in calls
    assert ['daemon-reload'] in calls and ['reload','dbus.service'] in calls
    assert ['stop','drives-normal-boot-guard.service'] not in calls,'stopping a required guard can stop the running sysinit target'


@pytest.mark.parametrize('state',['active','activating','deactivating','failed',''])
def test_uninstall_refuses_noninactive_maintenance_target(tmp_path,state):
    mapped,runner,log,env=harness(tmp_path);env['TARGET_STATE']=state
    before={name:mapped[name].read_bytes() for name in OWNED}
    result=subprocess.run(['bash',str(runner)],env=env,capture_output=True,timeout=15)
    assert result.returncode!=0
    calls=[json.loads(line) for line in log.read_text().splitlines()]
    assert all(call[0]=='show' for call in calls)
    assert {name:mapped[name].read_bytes() for name in OWNED}==before


def test_uninstall_refuses_remaining_boot_receipt(tmp_path):
    mapped,runner,log,env=harness(tmp_path)
    mapped['/run/drives-maintenance'].mkdir()
    mapped['/run/drives-maintenance/boot.json'].write_bytes(b'invalid receipt is not absence')
    result=subprocess.run(['bash',str(runner)],env=env,capture_output=True,timeout=15)
    assert result.returncode!=0 and not log.exists()
    assert all(mapped[name].exists() for name in OWNED)


def test_late_request_after_helper_stop_retains_recovery_infrastructure(tmp_path):
    mapped,runner,log,env=harness(tmp_path);env['LATE_LATCH']=str(mapped['/drives-maintenance-request.json'])
    before={name:mapped[name].read_bytes() for name in OWNED}
    result=subprocess.run(['bash',str(runner)],env=env,capture_output=True,timeout=15)
    assert result.returncode!=0
    calls=[json.loads(line) for line in log.read_text().splitlines()]
    assert ['stop','drives-helper.service'] in calls
    assert not any(call[0] in ('disable','daemon-reload','reload') for call in calls)
    assert {name:mapped[name].read_bytes() for name in OWNED}==before
    assert mapped['/drives-maintenance-request.json'].read_bytes()==b'late request'


@pytest.mark.parametrize('kind',['empty','dangling'])
def test_uninstall_refuses_ambiguous_runtime_directory(tmp_path,kind):
    mapped,runner,log,env=harness(tmp_path);runtime=mapped['/run/drives-maintenance']
    if kind=='empty':runtime.mkdir()
    else:runtime.symlink_to(tmp_path/'missing-runtime')
    before={name:mapped[name].read_bytes() for name in OWNED}
    result=subprocess.run(['bash',str(runner)],env=env,capture_output=True,timeout=15)
    assert result.returncode!=0 and not log.exists()
    assert {name:mapped[name].read_bytes() for name in OWNED}==before

