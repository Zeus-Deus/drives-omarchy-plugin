"""Fixed worker: hide a Drives-managed LUKS partition from desktop automounters.

Omarchy autostarts udiskie, which prompts for any locked LUKS partition it
sees. For a Drives disk that prompt is wrong: a passphrase typed there opens the
volume as luks-<uuid> and mounts nothing at the configured path. Marking the
partition UDISKS_IGNORE (udiskie skips HintIgnore devices) leaves unlocking to
systemd at boot and to the panel's recovery unlock.

Runs in a transient unit that can write only /etc/udev/rules.d.
"""
import os,pathlib,re,sys
from helper.common import Failure,atomic,run

RULES=pathlib.Path('/etc/udev/rules.d')
UUID=re.compile('[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')


def rule_path(name,rules=RULES):
    if not re.fullmatch('[a-z][a-z0-9-]{0,31}',name):raise Failure('invalid mapper name')
    return rules/('90-drives-'+name+'.rules')


SERIAL=re.compile('[A-Za-z0-9._-]{4,64}')


def rule_text(name,luks_uuid):
    if not UUID.fullmatch(luks_uuid or ''):raise Failure('invalid LUKS UUID')
    # The opened volume (dm-crypt cleartext) is hidden too: otherwise udiskie
    # tries to mount /dev/mapper/<name> itself and polkit asks for a password.
    return ('# Drives plugin: '+name+' unlocks with the OS or via the Drives panel, not desktop automount.\n'
            'SUBSYSTEM=="block", ENV{ID_FS_TYPE}=="crypto_LUKS", ENV{ID_FS_UUID}=="'+luks_uuid.lower()+'", ENV{UDISKS_IGNORE}="1"\n'
            'SUBSYSTEM=="block", ENV{DM_UUID}=="CRYPT-LUKS?-'+luks_uuid.lower().replace('-','')+'-*", ENV{UDISKS_IGNORE}="1"\n')


def setup_path(name,rules=RULES):
    rule_path(name,rules)
    return rules/('90-drives-'+name+'-setup.rules')


def setup_text(name,serial):
    """While a disk is being set up there is no LUKS UUID yet: hide the whole
    disk and its partitions by serial so no automount prompt appears mid-setup."""
    rule_path(name)
    if not SERIAL.fullmatch(serial or ''):raise Failure('invalid disk serial')
    return ('# Drives plugin: '+name+' is being set up; removed when setup finishes.\n'
            'SUBSYSTEM=="block", ENV{ID_SERIAL_SHORT}=="'+serial+'", ENV{UDISKS_IGNORE}="1"\n')


def write(name,luks_uuid,rules=RULES):
    path=rule_path(name,rules);text=rule_text(name,luks_uuid).encode()
    if path.is_symlink():raise Failure('refusing symbolic link')
    if path.exists() and path.read_bytes()==text:return False
    atomic(path,text,0o644)
    return True


def apply(name,luks_uuid):
    changed=write(name,luks_uuid,RULES)
    setup=setup_path(name,RULES)
    if setup.exists() and not setup.is_symlink():setup.unlink();changed=True
    if changed:run(['udevadm','control','--reload'],timeout=30)
    # Re-evaluate the partition and its opened volume so udisks drops HintAuto immediately.
    run(['udevadm','trigger','--action=change','--subsystem-match=block','--property-match=ID_FS_UUID='+luks_uuid.lower()],timeout=30)
    run(['udevadm','trigger','--action=change','--subsystem-match=block','--property-match=DM_UUID=CRYPT-LUKS?-'+luks_uuid.lower().replace('-','')+'-*'],timeout=30)
    run(['udevadm','settle'],timeout=60)


def apply_setup(name,serial,rules=RULES):
    path=setup_path(name,rules);text=setup_text(name,serial).encode()
    if path.is_symlink():raise Failure('refusing symbolic link')
    if not (path.exists() and path.read_bytes()==text):
        atomic(path,text,0o644)
        if rules==RULES:run(['udevadm','control','--reload'],timeout=30)
    if rules==RULES:
        run(['udevadm','trigger','--action=change','--subsystem-match=block','--property-match=ID_SERIAL_SHORT='+serial],timeout=30)
        run(['udevadm','settle'],timeout=60)


def apply_all(state_dir='/var/lib/drives-helper'):
    """Installer upgrade path: cover drives set up before this rule existed."""
    import json
    for p in sorted(pathlib.Path(state_dir,'drives').glob('*.json')):
        try:j=json.loads(p.read_text())
        except (OSError,ValueError):continue
        if j.get('state')=='ready' and j.get('luksUUID') and j.get('name'):apply(j['name'],j['luksUUID'])


if __name__=='__main__':
    if os.geteuid()!=0:raise Failure('root-only udev worker')
    if sys.argv[1:]==['--all']:apply_all()
    elif len(sys.argv)==4 and sys.argv[1]=='--setup':apply_setup(sys.argv[2],sys.argv[3])
    elif len(sys.argv)==3:apply(sys.argv[1],sys.argv[2])
    else:raise SystemExit('invalid invocation')
