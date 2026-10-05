"""Reconnect brings back a re-plugged drive with its own key, never a passphrase."""
import pathlib,sys
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from helper.common import Common,Failure
from helper import provisioning


def record(c,**extra):
    j={'id':'a'*32,'name':'data2','state':'ready','autoUnlock':True,'serial':'TESTNEW0002','mountpoint':'/data2','partitionById':'/dev/disk/by-id/x-part1','luksUUID':'u'}
    j.update(extra);c.journal('drives',j['id'],j);return j


def test_manual_unlock_drives_are_refused(tmp_path):
    c=Common(tmp_path/'state');record(c,autoUnlock=False)
    with pytest.raises(Failure,match='recovery passphrase'):provisioning.reconnect('a'*32,c)


def test_unplugged_drive_says_so(tmp_path,monkeypatch):
    c=Common(tmp_path/'state');record(c)
    monkeypatch.setattr(provisioning,'configured_partition',lambda j:(_ for _ in ()).throw(FileNotFoundError()))
    with pytest.raises(Failure,match='not connected'):provisioning.reconnect('a'*32,c)


def test_missing_key_points_to_recovery(tmp_path,monkeypatch):
    c=Common(tmp_path/'state');record(c)
    monkeypatch.setattr(provisioning,'configured_partition',lambda j:'/dev/sdc1')
    monkeypatch.setattr(provisioning,'keyfile_ok',lambda j:False)
    with pytest.raises(Failure,match='key is missing'):provisioning.reconnect('a'*32,c)


def test_unlocks_through_the_generated_cryptsetup_unit(tmp_path,monkeypatch):
    c=Common(tmp_path/'state');record(c);calls=[]
    monkeypatch.setattr(provisioning,'configured_partition',lambda j:'/dev/sdc1')
    monkeypatch.setattr(provisioning,'keyfile_ok',lambda j:True)
    monkeypatch.setattr(provisioning.os.path,'exists',lambda p:False)
    def run(argv,**k):
        calls.append(argv)
        return b'systemd-cryptsetup@data2.service\n' if argv[0]=='systemd-escape' else b''
    monkeypatch.setattr(provisioning,'run',run)
    monkeypatch.setattr(provisioning,'mount_and_reconnect',lambda j,c:{'ok':True,'reconnected':[]})
    assert provisioning.reconnect('a'*32,c)['ok']
    assert ['systemctl','start','systemd-cryptsetup@data2.service'] in calls
    assert not any('cryptsetup'==a[0] for a in calls), 'no direct cryptsetup open with a secret'
