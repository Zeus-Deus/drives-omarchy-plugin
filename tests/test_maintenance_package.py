"""Package the boot gate without broad sysinit/basic/multi-user dependency closure."""
import pathlib

ROOT=pathlib.Path(__file__).resolve().parents[1]


def test_minimal_maintenance_target_and_generator_are_packaged():
    target=ROOT/'packaging/drives-maintenance.target'
    assert target.is_file(),'minimal maintenance target is not packaged'
    text=target.read_text()
    assert 'DefaultDependencies=no' in text
    assert 'Requires=systemd-remount-fs.service systemd-journald.service systemd-udevd.service' in text
    assert not any(x in text for x in ('sysinit.target','basic.target','local-fs.target','multi-user.target','graphical.target','sshd','getty'))
    launcher=ROOT/'packaging/drives-maintenance-generator'
    assert launcher.is_file(),'early generator launcher is not packaged'
    assert '/usr/lib/drives-helper' in launcher.read_text()
    install=(ROOT/'helper/install.sh').read_text()
    assert '/etc/systemd/system-generators' in install and 'drives-maintenance.target' in install


def test_maintenance_ends_boot_splash_without_normal_boot_dependencies():
    unit=ROOT/'packaging/drives-maintenance-splash.service'
    assert unit.is_file(), 'maintenance must end the initramfs splash before audit'
    text=unit.read_text()
    assert 'DefaultDependencies=no' in text
    assert 'Type=oneshot' in text and 'RemainAfterExit=yes' in text
    assert 'ExecStart=-/usr/bin/timeout 5 /usr/bin/plymouth quit' in text
    assert 'ExecStart=-/usr/bin/timeout 5 /usr/bin/plymouth --wait' in text
    assert 'ExecStart=-/usr/bin/pkill --exact plymouthd' in text
    assert 'TimeoutStartSec=20' in text
    # A hung boot screen once timed the unit out and failed the target, so the
    # move was refused; the bounded steps must fit inside the unit timeout.
    budget=sum(int(line.split()[1]) for line in text.splitlines() if line.startswith('ExecStart=-/usr/bin/timeout '))
    assert budget<20
    assert '[Install]' not in text
    target=(ROOT/'packaging/drives-maintenance.target').read_text()
    for key in ('Requires','After'):
        value=next(line.split('=',1)[1].split() for line in target.splitlines() if line.startswith(key+'='))
        assert 'drives-maintenance-splash.service' in value
    assert 'drives-maintenance-splash.service' in (ROOT/'helper/install.sh').read_text()
    assert not any(x in text for x in ('sysinit.target','basic.target','local-fs.target','multi-user.target','graphical.target','plymouth-quit.service','plymouth-quit-wait.service'))
