"""Boot-order unit generation only: no mounts, reloads or service operations."""
import os,pathlib,subprocess,sys
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.offline import fstab_line

@pytest.mark.parametrize('source',['/home/demo/.config','/home/demo/.local','/home/demo/.hermes','/home/demo/Documents'])
def test_home_folder_bind_orders_before_user_sessions(source):
    line=fstab_line({'source':source,'dest':'/data/drives-fixture','destMount':'/data'})
    assert 'x-systemd.before=systemd-user-sessions.service' in line
    assert 'bind,nofail,' in line and 'x-systemd.automount' not in line

@pytest.mark.skipif(not pathlib.Path('/usr/lib/systemd/system-generators/systemd-fstab-generator').is_file(),reason='systemd generator unavailable')
def test_actual_generator_preserves_before_and_drive_requirement(tmp_path):
    fstab=tmp_path/'fixture-fstab';out=tmp_path/'generated';early=tmp_path/'early';late=tmp_path/'late'
    for d in (out,early,late):d.mkdir()
    fstab.write_text(fstab_line({'source':'/home/demo/.config','dest':'/data/drives-fixture','destMount':'/data'})+'\n')
    env={'PATH':'/usr/bin:/bin','SYSTEMD_LOG_LEVEL':'warning','SYSTEMD_FSTAB':str(fstab),'SYSTEMD_SYSROOT_FSTAB':'/dev/null','SYSTEMD_PROC_CMDLINE':'','SYSTEMD_IN_INITRD':'0'}
    result=subprocess.run(['/usr/lib/systemd/system-generators/systemd-fstab-generator',str(out),str(early),str(late)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    unit=out/'home-demo-.config.mount';assert unit.is_file(),result.stderr
    text=unit.read_text()
    assert 'SourcePath='+str(fstab) in text,'generator must read only the fixture, not host fstab'
    assert 'Before=systemd-user-sessions.service' in text
    assert 'Requires=data.mount' in text and 'After=data.mount' in text
    assert (out/'local-fs.target.wants'/unit.name).is_symlink()
