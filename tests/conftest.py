"""Shared guards for the helper suite.

The suite runs as root inside the test VM. Provisioning tests stub `run` in
helper.provisioning, but hiding a disk from desktop automount goes through
helper.common's own transient worker, which would write a real
/etc/udev/rules.d file for a fixture disk. Replace it everywhere by default;
tests that check the hide calls install their own recorder on top.
"""
import pathlib,sys
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def no_real_udev_rules(monkeypatch):
    calls=[]
    def record(name,luks_uuid=None,serial=None):calls.append((name,luks_uuid,serial))
    from helper import common,provisioning
    monkeypatch.setattr(common,'hide_from_automount',record)
    monkeypatch.setattr(provisioning,'hide_from_automount',record)
    return calls
