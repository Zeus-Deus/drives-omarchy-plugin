
def test_root_helper_reads_pid1_mounts_not_its_systemd_sandbox(monkeypatch):
    from helper import common
    seen=[]
    def read(path,cap=2*1024*1024):
        seen.append(path)
        return b'1 0 0:29 / / rw - btrfs /dev/vda2 rw\n'
    monkeypatch.setattr(common,'read_regular',read)
    monkeypatch.setattr(common.os,'geteuid',lambda:0)
    assert common.mount_rows()[0]['target']=='/'
    assert seen==['/proc/1/mountinfo'], 'ReadWritePaths creates sandbox-only bind mounts, not real disk mounts'
