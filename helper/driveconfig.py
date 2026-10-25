"""Finish existing encrypted filesystems without ever invoking a format API."""
import json,os,pathlib,time,uuid,re,hashlib,stat
from helper.common import Failure,run,mount_rows,escape_fstab,prepare_mountpoint
from topology import snapshot,blocks,chains


def backup_header(part,header):
    temp=header.with_name('.'+header.name+'.'+uuid.uuid4().hex)
    try:
        run(['cryptsetup','luksHeaderBackup',part,'--header-backup-file',str(temp)],timeout=60)
        os.chmod(temp,0o600)
        fd=os.open(temp,os.O_RDONLY|os.O_NOFOLLOW)
        try:os.fsync(fd)
        finally:os.close(fd)
        run(['cryptsetup','isLuks',str(temp)])
        if run(['cryptsetup','luksUUID',str(temp)]).strip()!=run(['cryptsetup','luksUUID',part]).strip():raise Failure('header backup belongs to another drive')
        os.replace(temp,header)
        fd=os.open(header.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        if temp.exists():temp.unlink()


def retire_manual_key(j,common,part,keypath,header,recovery_secret=None):
    """Journal the exact legacy bypass slot before touching it; retry safely.

    The original formatter verified a recovery secret before adding one random
    key. Never guess a slot number or remove a last/ambiguous remaining slot.
    """
    def slots():
        value=json.loads(run(['cryptsetup','luksDump','--dump-json-metadata',part]))
        rows=value.get('keyslots')
        if not isinstance(rows,dict) or not rows or any(not re.fullmatch('[0-9]{1,2}',s) for s in rows):raise Failure('unsafe legacy LUKS keyslot metadata')
        return rows
    active=slots();slot=j.get('manualKeySlot')
    if 'manualKeySlot' not in j:
        key_exists=os.path.lexists(keypath)
        if not key_exists and recovery_secret is None:raise Failure('recovery unlock required to retire the legacy manual keyslot')
        if key_exists:
            if keypath.is_symlink() or not stat.S_ISREG(keypath.stat().st_mode) or keypath.stat().st_uid!=0 or keypath.stat().st_mode&0o777!=0o400:raise Failure('temporary keyfile is unsafe')
        matching=[]
        for candidate in sorted(active):
            argv=['cryptsetup','open','--test-passphrase','--key-slot',candidate,'--key-file',str(keypath) if key_exists else '-',part]
            try:run(argv,data=None if key_exists else recovery_secret,timeout=180)
            except Failure as exc:
                if str(exc)!='cryptsetup failed (exit 2)':raise
            else:matching.append(candidate)
        if len(matching)!=1:raise Failure('legacy manual keyslot is ambiguous; inspect recovery keys')
        if key_exists:
            if len(active)<2:raise Failure('refusing to remove the last recovery keyslot')
            slot=matching[0]
        else:
            # Recovery authentication proves only that matching slot is usable,
            # not that any other slot is the historical temporary-key slot.
            # It may already have been replaced by an administrator before this
            # first decision. Neither today's metadata nor a fresh backup can
            # establish provenance; retain every slot unless none needs removal.
            if len(active)!=1:raise Failure('legacy manual keyslot provenance is unavailable; explicit administrator inspection required; all keyslots retained')
            slot=None
        j['manualKeySlotFingerprint']=hashlib.sha256(json.dumps(active[slot],sort_keys=True).encode()).hexdigest() if slot is not None else None
        j['manualKeySlot']=slot;j['manualRetirementUUID']=run(['cryptsetup','luksUUID',part]).decode().strip()
        common.journal('drives',j['id'],j)
    if j.get('manualRetirementUUID')!=run(['cryptsetup','luksUUID',part]).decode().strip():raise Failure('manual retirement belongs to another drive')
    if slot is not None and slot in active:
        if j.get('manualKeySlotFingerprint')!=hashlib.sha256(json.dumps(active[slot],sort_keys=True).encode()).hexdigest():raise Failure('legacy manual keyslot changed since retirement was planned')
        if len(active)<2:raise Failure('refusing to remove the last recovery keyslot')
        # Installed cryptsetup(8): batch-mode KillSlot requires no passphrase.
        # The exact slot was authenticated above and durably recorded first.
        run(['cryptsetup','luksKillSlot','--batch-mode',part,slot],timeout=180)
    remaining=slots()
    if slot in remaining:raise Failure('legacy manual keyslot retirement did not complete')
    # The old backup contains that same bypass slot. Replace it before deleting
    # the key or publishing ready; interrupted retries reuse the recorded slot.
    backup_header(part,header)
    j['manualKeyRetired']=True;common.journal('drives',j['id'],j)


def finish(j,common,recovery_secret=None):
    from helper.provisioning import validate_request,FIELDS
    req=validate_request({k:j[k] for k in FIELDS});id=j['id']
    state=snapshot();device=os.path.realpath(req['byId'],strict=True)
    disk=next((d for d in state['disks'] if d['name']==device),None)
    if not disk or disk['system'] or disk['serial']!=req['serial']:raise Failure('drive identity changed')
    testing=os.environ.get('DRIVES_VM_TESTING')=='1' and req['serial'].startswith('TEST') and run(['systemd-detect-virt']).strip()==b'kvm'
    if req['autoUnlock'] and not state['rootEncrypted'] and not testing:raise Failure('auto-unlock key storage requires an encrypted OS root')
    part=os.path.realpath(j.get('partitionById') or j['partition'],strict=True)
    if not any(any(n['name']==part for n in c) and c[0]['name']==device for cs in chains(blocks()).values() for c in cs):raise Failure('partition does not belong to saved drive')
    finalizing=(j.get('interruptedState') if j['state']=='unfinished' else j['state'])=='finalizing'
    if finalizing and (req['autoUnlock'] or not j.get('mountProof',{}).get('readWrite')):raise Failure('unsafe manual finalization state')
    run(['cryptsetup','isLuks',part])
    if j.get('luksUUID') and run(['cryptsetup','luksUUID',part]).decode().strip()!=j['luksUUID']:raise Failure('configured LUKS identity changed')
    if j.get('partitionUUID') and run(['blkid','-s','PARTUUID','-o','value',part]).decode().strip()!=j['partitionUUID']:raise Failure('configured partition identity changed')
    keypath=pathlib.Path('/etc/cryptsetup-keys.d')/(req['name']+'.key')
    secret_only=j.get('manualSecretOnly') is True
    if secret_only and (req['autoUnlock'] or j.get('keyfile') is not None):raise Failure('unsafe secret-only journal')
    if j.get('keyfile')!=str(keypath) and not ((finalizing or secret_only or (not req['autoUnlock'] and recovery_secret is not None)) and j.get('keyfile') is None):raise Failure('saved keyfile path is unsafe')
    if not finalizing and not secret_only and recovery_secret is None:
        if keypath.is_symlink() or not stat.S_ISREG(keypath.stat().st_mode) or keypath.stat().st_uid!=0 or keypath.stat().st_mode&0o777!=0o400:raise Failure('saved keyfile is missing or unsafe; use recovery unlock')
        run(['cryptsetup','open','--test-passphrase','--key-file',str(keypath),part],timeout=180)
    mapper='/dev/mapper/'+req['name']
    if finalizing and not os.path.exists(mapper):
        candidates={c[-1]['name'] for cs in chains(blocks()).values() for c in cs if c[-1]['type']=='crypt' and any(n['name']==part for n in c)}
        if len(candidates)!=1:raise Failure('recovery unlock required before manual finalization')
        mapper=candidates.pop()
    elif not os.path.exists(mapper):
        if secret_only:raise Failure('recovery unlock required before continuing manual setup')
        if recovery_secret is not None:run(['cryptsetup','open','--key-file','-',part,req['name']],data=recovery_secret,timeout=180)
        else:run(['cryptsetup','open','--key-file',str(keypath),part,req['name']],timeout=180)
    if run(['blkid','-s','TYPE','-o','value',mapper]).strip()!=b'btrfs':raise Failure('unsafe format state: existing filesystem is not Btrfs; never reformat automatically')
    current=chains(blocks()).get(mapper,[])
    if len(current)!=1 or not any(n['name']==part for n in current[0]):raise Failure('mapper belongs to a different drive')
    mountpoint=pathlib.Path(req['mountpoint'])
    for p in (mountpoint,)+tuple(mountpoint.parents):
        if p.is_symlink():raise Failure('symlinked mountpoint refused')
    actual_mounts=[m for m in mount_rows() if m['target']==str(mountpoint) and m['fstype']!='autofs']
    already=bool(actual_mounts)
    if already and (actual_mounts[-1]['source']!=mapper or actual_mounts[-1]['fstype']!='btrfs'):raise Failure('wrong filesystem is mounted at destination')
    # A formatted-stage interruption can precede header creation. Complete it
    # privately and atomically before promising a configured/ready drive.
    header=common.state_dir/'drives'/(id+'.header')
    if j.get('header')!=str(header):raise Failure('saved header path is missing or unsafe')
    if not header.exists():backup_header(part,header)
    if header.is_symlink() or not stat.S_ISREG(header.stat().st_mode) or header.stat().st_uid!=0 or header.stat().st_mode&0o777!=0o600:raise Failure('saved header backup is unsafe')
    if run(['cryptsetup','luksUUID',str(header)]).strip()!=run(['cryptsetup','luksUUID',part]).strip():raise Failure('header backup belongs to another drive')
    fd=os.open(header,os.O_RDONLY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)
    fd=os.open(header.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)
    if not finalizing:
        j['state']='configuring';j['updated']=time.time();j['error']='';common.journal('drives',id,j)
    if not already:prepare_mountpoint(str(mountpoint))
    uuid_value=run(['blkid','-s','UUID','-o','value',part]).decode().strip()
    if j.get('luksUUID') and j['luksUUID']!=uuid_value:raise Failure('configured LUKS identity changed')
    j['luksUUID']=uuid_value
    j['partitionById']=j.get('partitionById') or req['byId']+'-part1'
    common.journal('drives',id,j)
    # Before crypttab exists: no desktop automounter may prompt for this volume.
    from helper.common import hide_from_automount
    hide_from_automount(req['name'],uuid_value)
    if req['autoUnlock']:
        common.config('/etc/crypttab',id,req['name']+' UUID='+uuid_value+' '+str(keypath)+' luks,nofail,headless=yes')
        common.config('/etc/fstab',id,mapper+' '+escape_fstab(str(mountpoint))+' btrfs compress=zstd:3,nodiscard,nofail,x-systemd.automount,x-systemd.device-timeout=30s 0 0')
        if not already:run(['cryptsetup','close',req['name']])
        run(['systemctl','daemon-reload'])
        crypt_unit=run(['systemd-escape','--template=systemd-cryptsetup@.service',req['name']]).decode().strip()
        mount_unit=run(['systemd-escape','--path','--suffix=mount',str(mountpoint)]).decode().strip()
        run(['systemctl','start',crypt_unit],timeout=60);run(['systemctl','start',mount_unit],timeout=60)
    elif not already:
        from helper.common import host_mount
        host_mount(mapper,str(mountpoint),'btrfs','compress=zstd:3,nodiscard')
    result=run(['systemd-run','--quiet','--wait','--collect','--pipe','--unit=drives-proof-'+uuid.uuid4().hex,
        '--property=WorkingDirectory=/usr/lib/drives-helper','--property=ProtectSystem=strict',
        '--property=ReadWritePaths='+str(mountpoint),'--property=ProtectHome=yes','--property=NoNewPrivileges=yes',
        '--property=CapabilityBoundingSet=CAP_DAC_OVERRIDE','/usr/bin/python3','-B','-m','helper.mountproof',str(mountpoint),req['serial']],timeout=60)
    j['mountProof']=json.loads(result)
    if not req['autoUnlock']:
        j['state']='finalizing';j['updated']=time.time();common.journal('drives',id,j)
        if not secret_only:retire_manual_key(j,common,part,keypath,header,recovery_secret)
        if not secret_only and os.path.lexists(keypath):
            if keypath.is_symlink() or not stat.S_ISREG(keypath.stat().st_mode) or keypath.stat().st_uid!=0 or keypath.stat().st_mode&0o777!=0o400:raise Failure('temporary keyfile is unsafe')
            keypath.unlink()
        if not secret_only:
            fd=os.open(keypath.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:os.fsync(fd)
            finally:os.close(fd)
        j['keyfile']=None;j['manualSecretOnly']=True
    j['state']='ready';j['updated']=time.time();j['error']='';common.journal('drives',id,j)
    return {'ok':True,'id':id,'state':'ready','offMachineHeaderBackupNeeded':True}
