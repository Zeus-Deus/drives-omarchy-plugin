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


def rule_text(name,luks_uuid):
    if not UUID.fullmatch(luks_uuid or ''):raise Failure('invalid LUKS UUID')
    return ('# Drives plugin: '+name+' unlocks with the OS or via the Drives panel, not desktop automount.\n'
            'SUBSYSTEM=="block", ENV{ID_FS_TYPE}=="crypto_LUKS", ENV{ID_FS_UUID}=="'+luks_uuid.lower()+'", ENV{UDISKS_IGNORE}="1"\n')


def write(name,luks_uuid,rules=RULES):
    path=rule_path(name,rules);text=rule_text(name,luks_uuid).encode()
    if path.is_symlink():raise Failure('refusing symbolic link')
    if path.exists() and path.read_bytes()==text:return False
    atomic(path,text,0o644)
    return True


def apply(name,luks_uuid):
    if write(name,luks_uuid):run(['udevadm','control','--reload'],timeout=30)
    # Re-evaluate the partition so udisks drops HintAuto immediately.
    run(['udevadm','trigger','--action=change','--subsystem-match=block','--property-match=ID_FS_UUID='+luks_uuid.lower()],timeout=30)
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
    elif len(sys.argv)==3:apply(sys.argv[1],sys.argv[2])
    else:raise SystemExit('invalid invocation')
