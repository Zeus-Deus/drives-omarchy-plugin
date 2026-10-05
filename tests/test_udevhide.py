"""Drives volumes are hidden from desktop automount prompts (udiskie)."""
import json,pathlib,sys
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from helper.common import Failure
from helper import udevhide

UUID='64323122-325f-45d2-bd2b-a18b97576363'


def test_rule_marks_only_that_luks_volume_ignored():
    text=udevhide.rule_text('data',UUID.upper())
    assert 'ENV{ID_FS_UUID}=="'+UUID+'"' in text and 'ENV{ID_FS_TYPE}=="crypto_LUKS"' in text
    assert 'ENV{UDISKS_IGNORE}="1"' in text


@pytest.mark.parametrize('name,uuid',[('Data',UUID),('../x',UUID),('data','not-a-uuid'),('data',UUID+'"\nRUN+="/bin/sh"')])
def test_rule_refuses_untrusted_values(name,uuid):
    with pytest.raises(Failure):udevhide.write(name,uuid,pathlib.Path('/nonexistent'))


def test_write_is_idempotent(tmp_path):
    assert udevhide.write('data',UUID,tmp_path) is True
    assert udevhide.write('data',UUID,tmp_path) is False
    assert (tmp_path/'90-drives-data.rules').stat().st_mode&0o777==0o644


def test_installer_upgrade_covers_existing_ready_drives(tmp_path,monkeypatch):
    (tmp_path/'drives').mkdir()
    (tmp_path/'drives'/('a'*32+'.json')).write_text(json.dumps({'name':'data','state':'ready','luksUUID':UUID}))
    (tmp_path/'drives'/('b'*32+'.json')).write_text(json.dumps({'name':'half','state':'unfinished'}))
    seen=[]
    monkeypatch.setattr(udevhide,'apply',lambda name,uuid:seen.append((name,uuid)))
    udevhide.apply_all(tmp_path)
    assert seen==[('data',UUID)]


def test_install_runs_the_upgrade_and_recovery_unlock_hides_the_volume():
    assert 'helper.udevhide --all' in (ROOT/'helper/install.sh').read_text()
    assert 'hide_from_automount' in (ROOT/'helper/provisioning.py').read_text()
    assert 'hide_from_automount' in (ROOT/'helper/driveconfig.py').read_text()
