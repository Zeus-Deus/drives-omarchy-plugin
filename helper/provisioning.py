"""Guarded drive provisioning. Existing LUKS headers are never reformatted."""
import os,pathlib,re,stat,time,uuid,json
from helper.common import Failure,run,atomic,mount_rows,read_regular,escape_fstab,prepare_mountpoint
from topology import snapshot,blocks,chains,probe

from helper.driveconfig import finish

FIELDS={'byId','serial','name','mountpoint','erase','confirmation','autoUnlock'}
def validate_request(req):
    if not isinstance(req,dict) or set(req)!=FIELDS:raise Failure('invalid provisioning fields')
    if any(not isinstance(req[k],str) for k in ('byId','serial','name','mountpoint','confirmation')):raise Failure('invalid text fields')
    if type(req['erase']) is not bool or type(req['autoUnlock']) is not bool:raise Failure('invalid boolean fields')
    if len(req['serial'])<4 or req['confirmation']!=req['serial'][-4:]:raise Failure('serial confirmation does not match this disk')
    if not re.fullmatch('[a-z][a-z0-9-]{0,31}',req['name']):raise Failure('invalid mapper name')
    if not req['byId'].startswith('/dev/disk/by-id/') or pathlib.Path(req['byId']).name in ('.','..'):raise Failure('stable disk by-id is required')
    p=pathlib.Path(req['mountpoint'])
    if not req['mountpoint'].startswith('/') or str(p)!=req['mountpoint'] or '..' in p.parts or '\n' in str(p) or '\0' in str(p):raise Failure('invalid mountpoint')
    if not (re.fullmatch('/data[0-9]*',str(p)) or re.fullmatch('/mnt/drives/[a-zA-Z0-9/_-]+',str(p))):raise Failure('mountpoint must be /data, /dataN or under /mnt/drives/')
    return req

def udisks_call(path,interface,method,signature,args,timeout=180000):
    from gi.repository import Gio,GLib
    bus=Gio.bus_get_sync(Gio.BusType.SYSTEM,None)
    try:return bus.call_sync('org.freedesktop.UDisks2',path,'org.freedesktop.UDisks2.'+interface,method,GLib.Variant(signature,args),None,Gio.DBusCallFlags.NONE,timeout,None).unpack()
    except GLib.Error as e:raise Failure('udisks '+method+' failed: '+str(e).split(':')[-1][:200]) from e

def block_object(device):
    # UDisks uses hex escapes for non-alphanumeric bytes.
    name=os.path.basename(device)
    return '/org/freedesktop/UDisks2/block_devices/'+''.join(c if c.isalnum() else '_%02x'%ord(c) for c in name)

def inspect(common):
    rows=[]
    for j in common.records('drives'):
        r=dict(j)
        if r['state']!='ready':r['state']='unfinished';r['error']='Interrupted provisioning: inspect existing partition/header. Never format it again.'
        rows.append(r)
    return rows

def provision(request,secret,common):
    req=validate_request(request)
    if not 8<=len(secret)<=4096 or b'\0' in secret:raise Failure('invalid recovery passphrase')
    state=snapshot();device=os.path.realpath(req['byId'],strict=True)
    disk=next((d for d in state['disks'] if d['name']==device),None)
    if not disk or disk['serial']!=req['serial']:raise Failure('disk identity changed')
    if disk['system'] or disk['mounts'] or not disk['selectable']:raise Failure('system or mounted disk cannot be provisioned')
    if any(x.get('byId')==req['byId'] for x in common.records('drives')):raise Failure('drive has existing setup journal: inspect unfinished drive, do not reformat')
    node=next(d for d in blocks()['blockdevices'] if d['name']==device)
    signatures=run(['wipefs','--json',device],timeout=5)
    has_signatures=bool(json.loads(signatures).get('signatures')) or bool(node.get('children'))
    if has_signatures and not req['erase']:raise Failure('existing partitions/signatures require explicit Erase')
    mountpoint=pathlib.Path(req['mountpoint'])
    for p in (mountpoint,)+tuple(mountpoint.parents):
        if p.is_symlink():raise Failure('symlinked mountpoint refused')
    if mountpoint.exists() and (not mountpoint.is_dir() or any(mountpoint.iterdir())):raise Failure('mountpoint must be empty')
    if any(r['target']==str(mountpoint) for r in mount_rows()):raise Failure('mountpoint is already mounted')
    testing=os.environ.get('DRIVES_VM_TESTING')=='1' and req['serial'].startswith('TEST') and run(['systemd-detect-virt']).strip()==b'kvm'
    if req['autoUnlock'] and not state['rootEncrypted'] and not testing:raise Failure('auto-unlock key storage requires an encrypted OS root')
    if os.path.lexists('/dev/mapper/'+req['name']):raise Failure('mapper name already exists')
    keypath=pathlib.Path('/etc/cryptsetup-keys.d')/(req['name']+'.key')
    if os.path.lexists(keypath):raise Failure('keyfile name already exists')
    prepare_mountpoint(str(mountpoint))
    id=uuid.uuid4().hex;j={**req,'id':id,'state':'planned','boot_id':common.boot_id(),'created':time.time(),'testFixture':testing}
    def stage(value):j['state']=value;j['updated']=time.time();common.journal('drives',id,j)
    try:
        stage('partitioning')
        # UDisks owns GPT creation and the encrypted filesystem format.
        from gi.repository import GLib
        obj=block_object(device)
        udisks_call(obj,'Block','Format','(sa{sv})',('gpt',{}))
        part_obj=udisks_call(obj,'PartitionTable','CreatePartition','(ttssa{sv})',(1024*1024,0,'','Drives data',{}))[0]
        run(['udevadm','settle']);part=device+('p1' if device[-1].isdigit() else '1');j['partition']=part
        stage('encrypting')
        # The trusted udisks API requires its secret in an encrypt.passphrase variant;
        # our public helper protocol accepts only a sealed FD and never echoes it.
        udisks_call(part_obj,'Block','Format','(sa{sv})',('btrfs',{'encrypt.type':GLib.Variant('s','luks2'),'encrypt.passphrase':GLib.Variant('s',secret.decode('utf-8')),'label':GLib.Variant('s',req['name'])}))
        run(['udevadm','settle']);stage('encrypted')
        keypath.parent.mkdir(mode=0o700,exist_ok=True)
        fd=os.open(keypath,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400)
        try:os.write(fd,os.urandom(64));os.fsync(fd)
        finally:os.close(fd)
        run(['cryptsetup','luksAddKey','--key-file','-',part,str(keypath)],data=secret,timeout=180)
        run(['cryptsetup','open','--test-passphrase','--key-file',str(keypath),part],timeout=180)
        run(['cryptsetup','open','--test-passphrase','--key-file','-',part],data=secret,timeout=180)
        descendants=chains(blocks());mapper=next((c[-1]['name'] for cs in descendants.values() for c in cs if c[-1]['type']=='crypt' and any(n['name']==part for n in c)),None)
        if mapper:run(['cryptsetup','close',os.path.basename(mapper)])
        j['keyfile']=str(keypath);j['header']=str(common.state_dir/'drives'/(id+'.header'))
        stage('formatted');run(['cryptsetup','open','--key-file',str(keypath),part,req['name']],timeout=180)
        mapper='/dev/mapper/'+req['name']
        filesystem=run(['blkid','-s','TYPE','-o','value',mapper]).strip()
        if filesystem!=b'btrfs':raise Failure('format did not create Btrfs on mapper')
        header=common.state_dir/'drives'/(id+'.header')
        run(['cryptsetup','luksHeaderBackup',part,'--header-backup-file',str(header)],timeout=60);os.chmod(header,0o600)
        j['header']=str(header);j['keyfile']=str(keypath);stage('configuring')
        return finish(j,common)
    except BaseException as exc:
        if common.path('drives',id).exists():j=common.read('drives',id)
        j['interruptedState']=j.get('interruptedState',j['state']) if j['state']=='unfinished' else j['state']
        j['error']=str(exc)[:300];stage('unfinished');raise


def resume(id,common):
    j=common.read('drives',id)
    if j['state']=='ready':return {'ok':True,'id':id,'state':'ready'}
    eligible=j.get('interruptedState') if j['state']=='unfinished' else j['state']
    if eligible not in ('formatted','configuring','finalizing'):raise Failure('unsafe stage: inspect the existing header; never format it again')
    try:return finish(j,common)
    except BaseException as exc:
        durable=common.read('drives',id)
        durable['interruptedState']=durable.get('interruptedState',eligible) if durable['state']=='unfinished' else durable['state']
        durable['state']='unfinished';durable['error']=str(exc)[:300];common.journal('drives',id,durable);raise


def unlock(id,secret,common):
    """Recovery unlock for a configured drive whose keyfile is missing or not
    used: open it under its configured mapper name, start its own mount unit
    and reconnect folders moved onto it. Never formats or edits config."""
    if not re.fullmatch('[a-f0-9]{32}',id):raise Failure('invalid drive id')
    if not 8<=len(secret)<=4096 or b'\0' in secret:raise Failure('invalid recovery passphrase')
    j=common.read('drives',id)
    if j.get('state')!='ready':raise Failure('drive setup is unfinished; continue setup instead')
    name=j['name'];mapper='/dev/mapper/'+name
    part=os.path.realpath(j.get('partitionById') or j['partition'],strict=True)
    run(['cryptsetup','isLuks',part])
    if j.get('luksUUID') and run(['cryptsetup','luksUUID',part]).decode().strip()!=j['luksUUID']:raise Failure('this is not the configured drive')
    if j.get('luksUUID'):
        from helper.common import hide_from_automount
        hide_from_automount(j['name'],j['luksUUID'])
    if os.path.exists(mapper):
        if not any(any(n['name']==part for n in c) for c in chains(blocks()).get(mapper,[])):raise Failure('mapper name is used by another device')
    else:
        try:run(['cryptsetup','open','--key-file','-',part,name],data=secret,timeout=180)
        except Failure:raise Failure('the recovery passphrase did not unlock this drive') from None
    run(['udevadm','settle'],timeout=60)
    mountpoint=j['mountpoint']
    if not any(r['target']==mountpoint and r['fstype']=='btrfs' for r in mount_rows()):
        if j.get('autoUnlock'):
            unit=run(['systemd-escape','--path','--suffix=mount',mountpoint]).decode().strip()
            run(['systemctl','start',unit],timeout=60)
        else:
            from helper.common import host_mount
            host_mount(mapper,mountpoint,'btrfs','compress=zstd:3,nodiscard')
    t=probe(mountpoint,resolve=False)
    if not t['supported'] or t['mount']['target']!=mountpoint or t['disk'].get('serial')!=j['serial']:raise Failure('drive unlocked but its folder did not mount')
    reconnected=[]
    for move in common.records('moves'):
        if move.get('destMount')!=mountpoint or move.get('state') not in ('switched','rebooted','cleaning','cleaned'):continue
        unit=run(['systemd-escape','--path','--suffix=mount',move['source']]).decode().strip()
        try:run(['systemctl','start',unit],timeout=60);reconnected.append(move['source'])
        except Failure:pass
    return {'ok':True,'id':id,'mountpoint':mountpoint,'reconnected':reconnected}


def export_header(name,destination,common,uid):
    records=[r for r in common.records('drives') if r['name']==name and r.get('header')]
    if len(records)!=1:raise Failure('header backup not found')
    import pwd
    home=pwd.getpwuid(uid).pw_dir
    if not destination.startswith(home+'/') or '..' in pathlib.Path(destination).parts:raise Failure('export must be under caller home')
    parts=pathlib.Path(destination).parts;dfd=os.open('/',os.O_DIRECTORY)
    try:
        for part in parts[1:-1]:
            new=os.open(part,os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=dfd);os.close(dfd);dfd=new
        fd=os.open(parts[-1],os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600,dir_fd=dfd)
        try:
            os.fchown(fd,uid,pwd.getpwuid(uid).pw_gid)
            data=read_regular(records[0]['header'],32*1024*1024);view=memoryview(data)
            while view:view=view[os.write(fd,view):]
            os.fsync(fd)
        finally:os.close(fd)
        os.fsync(dfd)
    finally:os.close(dfd)
    return {'ok':True,'path':destination}
