"""Finish existing encrypted filesystems without ever invoking a format API."""
import json,os,pathlib,time,uuid
from helper.common import Failure,run,mount_rows,escape_fstab,prepare_mountpoint
from topology import snapshot,blocks,chains

def finish(j,common):
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
    run(['cryptsetup','isLuks',part]);keypath=pathlib.Path('/etc/cryptsetup-keys.d')/(req['name']+'.key')
    if j.get('keyfile')!=str(keypath) and not (finalizing and j.get('keyfile') is None):raise Failure('saved keyfile path is unsafe')
    if not finalizing:
        if keypath.is_symlink() or keypath.stat().st_uid!=0 or keypath.stat().st_mode&0o777!=0o400:raise Failure('saved keyfile is missing or unsafe; use recovery unlock')
        run(['cryptsetup','open','--test-passphrase','--key-file',str(keypath),part],timeout=180)
    mapper='/dev/mapper/'+req['name']
    if finalizing and not os.path.exists(mapper):
        candidates={c[-1]['name'] for cs in chains(blocks()).values() for c in cs if c[-1]['type']=='crypt' and any(n['name']==part for n in c)}
        if len(candidates)!=1:raise Failure('recovery unlock required before manual finalization')
        mapper=candidates.pop()
    elif not os.path.exists(mapper):run(['cryptsetup','open','--key-file',str(keypath),part,req['name']],timeout=180)
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
    if not header.exists():
        temp=header.with_name('.'+header.name+'.'+uuid.uuid4().hex)
        try:
            run(['cryptsetup','luksHeaderBackup',part,'--header-backup-file',str(temp)],timeout=60)
            os.chmod(temp,0o600)
            fd=os.open(temp,os.O_RDONLY|os.O_NOFOLLOW)
            try:os.fsync(fd)
            finally:os.close(fd)
            run(['cryptsetup','isLuks',str(temp)])
            os.replace(temp,header)
        finally:
            if temp.exists():temp.unlink()
    if header.is_symlink() or header.stat().st_uid!=0 or header.stat().st_mode&0o777!=0o600:raise Failure('saved header backup is unsafe')
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
    uuid_value=run(['blkid','-s','UUID','-o','value',part]).decode().strip();j['luksUUID']=uuid_value
    j['partitionById']=req['byId']+'-part1'
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
        if os.path.lexists(keypath):
            if keypath.is_symlink() or keypath.stat().st_uid!=0 or keypath.stat().st_mode&0o777!=0o400:raise Failure('temporary keyfile is unsafe')
            keypath.unlink()
        fd=os.open(keypath.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(fd)
        finally:os.close(fd)
        j['keyfile']=None
    j['state']='ready';j['updated']=time.time();j['error']='';common.journal('drives',id,j)
    return {'ok':True,'id':id,'state':'ready','offMachineHeaderBackupNeeded':True}
