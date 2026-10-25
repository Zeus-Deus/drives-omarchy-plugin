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


def owned_rule(path,name,setup=False):
    """Accept exact shipped bodies, never a merely similar rule syntax.

    There were two final-rule releases (one/two device matches) and one
    setup-rule release. Extract only the variable, then compare ALL bytes.
    Marked rules must also match one of these exact bodies.
    """
    from helper.ownership import read,unmark,refuse
    from helper.legacy_udev import render,FINAL_V1,FINAL_V2,SETUP_V1
    data=read(path,8192)
    if data is None:return None
    body=unmark(data) or data
    if setup:
        match=re.search(rb'ID_SERIAL_SHORT}=="([A-Za-z0-9._-]{4,64})"',body)
        expected=render(SETUP_V1,name=name,serial=match[1].decode()) if match else b''
        valid=bool(expected) and body==expected
    else:
        match=re.search(rb'ID_FS_UUID}=="([0-9a-f-]{36})"',body)
        valid=False
        if match and UUID.fullmatch(match[1].decode()):
            uid=match[1].decode()
            valid=body in tuple(render(parts,name=name,uuid=uid,compact=uid.replace('-','')) for parts in (FINAL_V1,FINAL_V2))
    if not valid:refuse(path,'foreign or modified udev rule')
    return data


def write(name,luks_uuid,rules=RULES):
    from helper.ownership import marked,directory
    path=rule_path(name,rules);text=marked(rule_text(name,luks_uuid).encode())
    old=owned_rule(path,name)
    if old==text:return False
    directory(path.parent)
    atomic(path,text,0o644)
    return True


def apply(name,luks_uuid):
    setup=setup_path(name,RULES)
    old_setup=owned_rule(setup,name,True)
    changed=write(name,luks_uuid,RULES)
    if old_setup is not None:setup.unlink();changed=True
    if changed:run(['udevadm','control','--reload'],timeout=30)
    # Re-evaluate the partition and its opened volume so udisks drops HintAuto immediately.
    run(['udevadm','trigger','--action=change','--subsystem-match=block','--property-match=ID_FS_UUID='+luks_uuid.lower()],timeout=30)
    run(['udevadm','trigger','--action=change','--subsystem-match=block','--property-match=DM_UUID=CRYPT-LUKS?-'+luks_uuid.lower().replace('-','')+'-*'],timeout=30)
    run(['udevadm','settle'],timeout=60)


def apply_setup(name,serial,rules=RULES):
    from helper.ownership import marked,directory
    path=setup_path(name,rules);text=marked(setup_text(name,serial).encode())
    old=owned_rule(path,name,True)
    if old!=text:
        directory(path.parent)
        atomic(path,text,0o644)
        if rules==RULES:run(['udevadm','control','--reload'],timeout=30)
    if rules==RULES:
        run(['udevadm','trigger','--action=change','--subsystem-match=block','--property-match=ID_SERIAL_SHORT='+serial],timeout=30)
        run(['udevadm','settle'],timeout=60)


def apply_all(state_dir='/var/lib/drives-helper'):
    """Installer upgrade path: cover drives set up before this rule existed."""
    import json
    from helper.ownership import read
    ready=[]
    for p in sorted(pathlib.Path(state_dir,'drives').glob('*.json')):
        try:j=json.loads(read(p,1024*1024))
        except (OSError,ValueError):continue
        if j.get('state')=='ready' and j.get('luksUUID') and j.get('name'):
            name=j['name'];uuid=j['luksUUID'];rule_text(name,uuid)
            owned_rule(rule_path(name,RULES),name)
            owned_rule(setup_path(name,RULES),name,True)
            ready.append((name,uuid))
    for name,uuid in ready:apply(name,uuid)


if __name__=='__main__':
    if os.geteuid()!=0:raise Failure('root-only udev worker')
    if sys.argv[1:]==['--all']:apply_all()
    elif len(sys.argv)==4 and sys.argv[1]=='--setup':apply_setup(sys.argv[2],sys.argv[3])
    elif len(sys.argv)==3:apply(sys.argv[1],sys.argv[2])
    else:raise SystemExit('invalid invocation')
