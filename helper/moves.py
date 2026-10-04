"""Fail-closed folder copy/verify/bind with inspect-only crash recovery."""
import contextlib,errno,fcntl,hashlib,json,os,pathlib,pwd,shutil,stat,struct,time,uuid
from helper.common import Failure,run,mount_rows,mount_for,escape_fstab,read_regular
from topology import probe,blocks

ACTIVE={'planned','quarantining','copying','verifying','switching','testing','rolling-back','cleaning'}
WAITING={'awaiting-maintenance'}
PROTECTED={'.hermes','.claude','.codex','.codemux','.opencode','.config','.ssh','.gnupg','.mozilla','.password-store','keyrings','chromium','google-chrome','firefox','postgres','postgresql','mysql','mariadb'}

def durable_directory(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)

def identity_fd(fd):
    s=os.fstat(fd)
    if not stat.S_ISDIR(s.st_mode):raise Failure('expected real directory')
    # Btrfs inode numbers are subvolume-local; snapshots preserve them.
    # BTRFS_IOC_INO_LOOKUP: _IOWR(0x94,18,4096), treeid=0/objectid=256.
    lookup=bytearray(4096);struct.pack_into('QQ',lookup,0,0,256)
    try:
        fcntl.ioctl(fd,0xd0009412,lookup,True)
        subvolume=struct.unpack_from('Q',lookup)[0]
    except OSError as exc:
        if exc.errno!=errno.ENOTTY:raise Failure('cannot establish subvolume identity') from exc
        subvolume=None
    return [s.st_dev,s.st_ino,subvolume]

def identity(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:return identity_fd(fd)
    finally:os.close(fd)

def safe_path(path):
    if not isinstance(path,str) or not path.startswith('/') or '\0' in path or '\n' in path:raise Failure('invalid absolute path')
    p=pathlib.Path(path)
    if str(p)!=path or '..' in p.parts:raise Failure('non-canonical path')
    for ancestor in reversed((p,)+tuple(p.parents)):
        if ancestor.is_symlink():raise Failure('symlinked path refused')
    return p

@contextlib.contextmanager
def anchored_tree(path):
    """Hold no-follow descriptors; later ancestor renames cannot redirect us."""
    p=safe_path(path);parent=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    leaf=None
    try:
        for component in p.parts[1:-1]:
            nextfd=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
            os.close(parent);parent=nextfd
        leaf=os.open(p.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        yield parent,leaf,p.name
    finally:
        if leaf is not None:os.close(leaf)
        os.close(parent)

def delete_tree_fd(fd):
    """Never resolve a deletion through an absolute user-controlled pathname."""
    device=os.fstat(fd).st_dev
    with os.scandir(fd) as entries:
        for entry in entries:
            info=entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child=os.open(entry.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                try:
                    actual=os.fstat(child)
                    if (actual.st_dev,actual.st_ino)!=(info.st_dev,info.st_ino) or actual.st_dev!=device:raise Failure('directory or filesystem changed during cleanup')
                    delete_tree_fd(child)
                    now=os.stat(entry.name,dir_fd=fd,follow_symlinks=False)
                    if (now.st_dev,now.st_ino)!=(actual.st_dev,actual.st_ino):raise Failure('directory changed during cleanup')
                    os.rmdir(entry.name,dir_fd=fd)
                finally:os.close(child)
            else:os.unlink(entry.name,dir_fd=fd)
    os.fsync(fd)

def protected(path):
    p=pathlib.Path(path)
    if not path.startswith('/home/') or len(p.parts)<4 or any(x.lower() in PROTECTED for x in p.parts):raise Failure('protected path: profiles, credentials and system directories cannot move')
    if path.startswith(('/home/root/','/home/lost+found/')):raise Failure('protected path')

def open_users(path):
    found=[]
    for proc in pathlib.Path('/proc').glob('[0-9]*'):
        try:
            targets=list((proc/'fd').iterdir())+[proc/'cwd',proc/'root']
            for fd in targets:
                try:target=os.readlink(fd)
                except PermissionError:raise
                except (FileNotFoundError,ProcessLookupError):continue
                if target==path or target.startswith(path+'/'):
                    found.append({'pid':int(proc.name),'name':(proc/'comm').read_text().strip()});break
        except PermissionError as exc:
            raise Failure("cannot inspect open files; root helper needs proc visibility") from exc
        except (FileNotFoundError,ProcessLookupError):continue
    return found[:24]

def tree_stats(path,uid=None):
    count=0;size=0;directories=0;h=hashlib.sha256();groups=set()
    if uid is not None:
        user=pwd.getpwuid(uid);groups=set(os.getgrouplist(user.pw_name,user.pw_gid))
    def unreadable(exc):raise Failure('unreadable subtree; refusing a silent skip') from exc
    # Sort within each directory, not one million entries in memory.
    for root,dirs,files in os.walk(path,topdown=True,followlinks=False,onerror=unreadable):
        if any(name.lower() in PROTECTED for name in dirs):raise Failure('protected subtree: profiles, credentials or databases cannot move')
        dirs.sort();files.sort();directories+=1
        for name in dirs+files:
            p=os.path.join(root,name);s=os.lstat(p);rel=os.fsencode(os.path.relpath(p,path))
            if uid is not None and not stat.S_ISLNK(s.st_mode):
                permission=(s.st_mode>>6)&7 if s.st_uid==uid else ((s.st_mode>>3)&7 if s.st_gid in groups else s.st_mode&7)
                need=5 if stat.S_ISDIR(s.st_mode) else 4
                if permission&need!=need:raise Failure('unreadable subtree: close its owner and fix permissions before moving')
            if not (stat.S_ISDIR(s.st_mode) or stat.S_ISREG(s.st_mode) or stat.S_ISLNK(s.st_mode)):raise Failure('special file refused (socket, FIFO or device)')
            if stat.S_ISREG(s.st_mode):count+=1;size+=s.st_size
            h.update(len(rel).to_bytes(4,'big'));h.update(rel);h.update(str((s.st_mode,s.st_uid,s.st_gid,s.st_size)).encode())
    return {'files':count,'bytes':size,'directories':directories,'metadataDigest':h.hexdigest()}

class MoveManager:
    def __init__(self,common,uid=None,isolated=False):
        # isolated: running inside drives-helper.service, whose namespace keeps
        # drives read-only; destination writes then go through helper.destination.
        self.c=common;self.uid=uid;self.isolated=isolated
    def destination_op(self,j,*args):
        from helper import destination
        if not self.isolated:return destination.main(list(args))
        safe_path(j['destMount'])
        out=run(['systemd-run','--quiet','--wait','--collect','--pipe','--unit=drives-destination-'+uuid.uuid4().hex,
            '--property=WorkingDirectory=/usr/lib/drives-helper','--property=ProtectSystem=strict',
            '--property=ReadWritePaths='+j['destMount'],'--property=ProtectHome=yes','--property=PrivateTmp=yes',
            '--property=NoNewPrivileges=yes','--property=CapabilityBoundingSet=CAP_DAC_OVERRIDE CAP_FOWNER CAP_DAC_READ_SEARCH',
            '/usr/bin/python3','-B','-m','helper.destination',*args],timeout=3600)
        return json.loads(out)
    def make_destination(self,j):
        j['destIdentity']=self.destination_op(j,'create',j['dest'])['identity']
        self.c.journal('moves',j['id'],j)
    def stage(self,j,state):
        j['state']=state;j['updated']=time.time()
        if state!='paused':j['error']=''
        self.c.journal('moves',j['id'],j)
    def changed(self,path,want,fsuuid=None):
        safe_path(path);actual=identity(path)
        if fsuuid:
            topology=probe(str(path),resolve=False)
            if not topology['supported'] or topology['chain'][-1].get('uuid')!=fsuuid or actual[1]!=want[1] or actual[2:]!=want[2:]:raise Failure('filesystem, subvolume or directory identity changed; inspect before continuing')
        elif actual!=want:raise Failure('directory identity changed; inspect before continuing')
    def preflight(self,src,destMount):
        protected(src)
        origin=probe(src,resolve=False)
        if not origin['supported'] or origin['mount']['fstype'] not in ('btrfs','ext4','xfs'):raise Failure('unsupported source storage topology')
        source=safe_path(src);target=safe_path(destMount)
        s=source.stat();uid=self.uid if self.uid is not None else s.st_uid
        if s.st_uid!=uid:raise Failure('source is not owned by the caller')
        rows=mount_rows()
        if any(r['target']==src or r['target'].startswith(src+'/') for r in rows):raise Failure('nested or existing mount blocks the move')
        owners=open_users(src)
        if owners:raise Failure('open files block the move: '+', '.join(x['name']+' (pid '+str(x['pid'])+')' for x in owners))
        topology=probe(destMount,blocks(),rows,resolve=False)
        if not topology['supported'] or not topology.get('encrypted') or topology['mount']['target']!=destMount:raise Failure('destination must be a mounted single encrypted disk')
        t=target.stat()
        if t.st_uid!=0 or t.st_mode&0o022:raise Failure('destination mount must be root-owned and not group/world writable')
        if destMount==src or destMount.startswith(src+'/') or src.startswith(destMount+'/'):raise Failure('source and destination overlap')
        stats=tree_stats(src,uid);v=os.statvfs(target)
        if v.f_bavail*v.f_frsize*5<stats['bytes']*6:raise Failure('destination needs at least 1.2x source apparent size free')
        return stats,topology,uid
    def offline_layout(self,src,topology):
        """Facts the maintenance boot needs to mount both sides by itself."""
        from helper.offline import crypttab_key
        source_mount=probe(src,resolve=False)['mount']['target']
        if source_mount!='/':
            fstab=[line.split() for line in read_regular('/etc/fstab',65536).decode().splitlines()]
            if not any(len(f)>=2 and not f[0].startswith('#') and f[1]==source_mount for f in fstab):raise Failure('source filesystem is not listed in /etc/fstab')
        crypt=[n for n in topology['chain'] if n.get('type')=='crypt']
        if len(crypt)!=1:raise Failure('destination must be one encrypted volume')
        mapper=os.path.basename(crypt[0]['name'])
        crypttab_key(mapper)
        return source_mount,mapper
    def start(self,src,destMount):
        stats,topology,uid=self.preflight(src,destMount)
        source_mount,mapper=self.offline_layout(src,topology)
        id=uuid.uuid4().hex;dest=destMount+'/drives-'+id;backup=src+'.pre-move'
        if os.path.lexists(backup):raise Failure('old-copy path already exists')
        if any(r.get('source')==src and r['state'] not in ('cleaned','rolled-back') for r in self.c.records('moves')):raise Failure('this folder already has an unfinished move')
        j={'id':id,'state':'planned','source':src,'destMount':destMount,'dest':dest,'backup':backup,
           'sourceIdentity':identity(src),'sourceUUID':probe(src,resolve=False)['chain'][-1]['uuid'],'parentIdentity':identity(str(pathlib.Path(src).parent)),'destUUID':topology['chain'][-1]['uuid'],'mountIdentity':identity(destMount),'diskSerial':topology['disk'].get('serial'),
           'uid':uid,'stats':stats,'boot_id':self.c.boot_id(),'verified':False,'created':time.time(),
           'maintenanceProtocol':2,'sourceMount':source_mount,'destMapper':mapper,
           'destFSRoot':os.path.normpath(topology['mount']['fsroot'].rstrip('/')+'/'+os.path.relpath(dest,destMount))}
        self.stage(j,'planned');self.make_destination(j)
        return self.execute(j)
    # --- maintenance requests (protocol 2) -------------------------------------
    def latch(self,*args):
        """Write /drives-maintenance-request.json from a fresh transient unit:
        this helper's own namespace keeps / read-only."""
        # The Btrfs root directory is mode 0555: even root needs DAC override.
        try:
            run(['systemd-run','--quiet','--wait','--collect','--pipe','--unit=drives-request-'+uuid.uuid4().hex,
                '--property=WorkingDirectory=/usr/lib/drives-helper','--property=ProtectHome=yes',
                '--property=PrivateTmp=yes','--property=NoNewPrivileges=yes','--property=CapabilityBoundingSet=CAP_DAC_OVERRIDE',
                '/usr/bin/python3','-B','-m','helper.latch',*args],timeout=60)
        except Failure as error:
            raise Failure('could not '+('schedule' if args[0]=='arm' else 'withdraw')+' the restart request ('+str(error)+')') from None
    def waiting(self,j):
        return j['state'] in WAITING or (j['state']=='paused' and j.get('interruptedState') in WAITING)
    def request(self,id,action):
        j=self.c.read('moves',id)
        if j.get('maintenanceProtocol')!=2:raise Failure('this older move needs administrator inspection; both copies are kept')
        if action=='continue':
            from helper.offline import OFFLINE
            if j['state'] in OFFLINE:pass # interrupted offline step: the worker restores the original first
            elif not self.waiting(j):raise Failure('this move is not waiting to run')
            else:
                self.changed(j['source'],j['sourceIdentity'],j.get('sourceUUID'))
                if os.path.lexists(j['backup']) or self.bound(j):raise Failure('unexpected old copy or bind; inspect before continuing')
                self.destination(j)
        elif j['state']=='rolling-back':pass # interrupted Undo: the worker finishes restoring
        else:
            if j['state']!='switched' or not j.get('verified') or not self.bound(j):raise Failure('only an active, verified move can be undone')
            if open_users(j['source']):raise Failure('close apps using this folder first')
        self.latch('arm',id,action)
        j['error']='';j.pop('needsAttention',None);self.c.journal('moves',id,j)
        return {'ok':True,'id':id,'state':'restart-required','action':action}
    def cancel_request(self,id):
        from helper.maintenance import latch_request
        value=latch_request()
        if value['moveId']!=id:raise Failure('the pending restart belongs to another move')
        if value['armedBootId']!=self.c.boot_id():raise Failure('this request was not made in the current session; inspect as administrator')
        self.latch('clear',id,value['armedBootId'])
        return {'ok':True,'id':id,'state':'request-cancelled'}
    def destination(self,j):
        safe_path(j['destMount']);safe_path(j['dest']);self.changed(j['destMount'],j['mountIdentity'],j.get('destUUID'));self.changed(j['dest'],j['destIdentity'],j.get('destUUID'))
        t=probe(j['destMount'],resolve=False)
        if not t['supported'] or not t.get('encrypted') or t['disk'].get('serial')!=j['diskSerial']:raise Failure('destination disk missing or changed')
    def verify(self,src,dest):
        difference=run(['rsync','-aHAXS','--numeric-ids','--checksum','--dry-run','--itemize-changes','--delete','--',src+'/',dest+'/'],timeout=3600)
        if difference:raise Failure('checksum or metadata comparison differs; original is still kept')
        a=tree_stats(src);b=tree_stats(dest)
        if a!=b:raise Failure('file count, bytes or metadata differ')
        return a
    def bound(self,j,block=None,rows=None):
        try:
            rows=mount_rows() if rows is None else rows
            block=blocks() if block is None else block
            t=probe(j['destMount'],block,mounts=rows,resolve=False)
            if not t['supported'] or not t.get('encrypted') or t['disk'].get('serial')!=j['diskSerial']:return False
            if j.get('destUUID') and t['chain'][-1].get('uuid')!=j['destUUID']:return False
            row=mount_for(j['source'],rows);dest=t['mount']
            expected=j.get('destFSRoot')
            if not expected or expected!=os.path.normpath(dest['fsroot'].rstrip('/')+'/'+os.path.relpath(j['dest'],j['destMount'])):return False
            return row['target']==j['source'] and row['majorMinor']==dest['majorMinor'] and row['fstype']==dest['fstype'] and row['fsroot']==expected
        except (Failure,OSError,KeyError):return False
    def smoke(self,j):
        if not self.bound(j):raise Failure('expected bind is not active')
        original=os.stat(j['source'],follow_symlinks=False)
        name=j['source']+'/.drives-read-write-'+uuid.uuid4().hex
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:os.write(fd,b'Drives mount verification');os.fsync(fd)
        finally:os.close(fd)
        try:
            if read_regular(name)!=b'Drives mount verification':raise Failure('mount read/write check failed')
        finally:
            os.unlink(name);os.utime(j['source'],ns=(original.st_atime_ns,original.st_mtime_ns),follow_symlinks=False)
            fd=os.open(j['source'],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:os.fsync(fd)
            finally:os.close(fd)
    def placeholder(self,j):
        path=j['source']
        if not os.path.lexists(path):os.mkdir(path,0o000)
        elif not self.bound(j):
            if j.get('placeholderIdentity'):self.changed(path,j['placeholderIdentity'],j.get('sourceUUID'))
            if os.listdir(path):raise Failure('placeholder is not empty; refusing overwrite')
            info=os.stat(path,follow_symlinks=False)
            if info.st_uid!=0 or info.st_mode&0o777:raise Failure('placeholder ownership or permissions changed; inspect before continuing')
        if not self.bound(j):
            run(['chattr','+i',path]);durable_directory(path);durable_directory(str(pathlib.Path(path).parent));j['placeholderIdentity']=identity(path)
            self.c.journal('moves',j['id'],j)
    def execute(self,j):
        # Protocol 1 copied a live source: never promote that path to a verified
        # cutover. Queue untouched sources for the new offline protocol instead.
        # No public caller can opt into protocol 2 through method arguments.
        # Protocol 2 (planned by start) does the same here: plan only. The copy
        # runs in the maintenance boot (helper/offline.py) after a restart.
        if os.path.lexists(j['backup']):raise Failure('legacy interrupted cutover requires maintenance inspection; both copies are retained')
        j['verified']=False;j.pop('cutover',None);j.pop('interruptedState',None)
        self.stage(j,'awaiting-maintenance')
        return {'ok':True,'id':j['id'],'state':'awaiting-maintenance','message':'Planned. The move runs during a restart, while nothing else is using the folder.'}

    def resume(self,id):
        j=self.c.read('moves',id)
        if j.get('maintenanceProtocol')==2:return self.request(id,'continue')
        if not j.get('destUUID'):
            if j['boot_id']!=self.c.boot_id():raise Failure('legacy move requires manual filesystem identity inspection')
            original=j['backup'] if os.path.lexists(j['backup']) else j['source']
            self.changed(original,j['sourceIdentity']);self.destination(j)
            j['sourceUUID']=probe(original,resolve=False)['chain'][-1]['uuid'];j['destUUID']=probe(j['destMount'],resolve=False)['chain'][-1]['uuid']
            j['parentIdentity']=identity(str(pathlib.Path(j['source']).parent));self.c.journal('moves',id,j)
        if j['state'] in ('cleaned','rolled-back'):raise Failure('move is already finished')
        if 'destIdentity' not in j:
            if j['state']!='planned':raise Failure('missing destination identity; inspect before continuing')
            self.changed(j['source'],j['sourceIdentity'],j.get('sourceUUID'))
            _,topology,_=self.preflight(j['source'],j['destMount'])
            self.changed(j['destMount'],j['mountIdentity'],j.get('destUUID'))
            if topology['disk'].get('serial')!=j['diskSerial'] or j['dest']!=j['destMount']+'/drives-'+id:raise Failure('planned destination identity changed')
            if os.path.lexists(j['dest']):raise Failure('unrecorded destination exists; inspect before continuing')
            self.make_destination(j)
        if j['state']=='cleaning':return self.delete_old(id)
        if j['state']=='switched' and self.bound(j):
            self.stage(j,'switched');return {'ok':True,'id':id,'state':'switched'}
        return self.execute(j)
    def rollback(self,id):
        j=self.c.read('moves',id)
        if j.get('maintenanceProtocol')!=2:raise Failure('legacy undo requires maintenance inspection; both copies are retained')
        return self.request(id,'rollback')
    def delete_old(self,id):
        j=self.c.read('moves',id)
        if not j.get('verified') or j.get('boot_id')==self.c.boot_id():raise Failure('cleanup requires saved verification and a successful reboot')
        if j.get('maintenanceProtocol')!=2:raise Failure('legacy live verification cannot authorize cleanup; retain the old copy for maintenance inspection')
        self.destination(j)
        if not self.bound(j):raise Failure('correct bind must be active after reboot')
        if j['state'] not in ('switched','rebooted','cleaning'):raise Failure('move is not eligible for cleanup')
        if open_users(j['backup']):raise Failure('open old-copy files block cleanup')
        if any(r['target']==j['backup'] or r['target'].startswith(j['backup']+'/') for r in mount_rows()):raise Failure('mount in old copy blocks deletion')
        if os.path.lexists(j['backup']):
            with anchored_tree(j['backup']) as (parent,leaf,name):
                self.changed(j['backup'],j['sourceIdentity'],j.get('sourceUUID'))
                held=os.fstat(leaf)
                if identity_fd(leaf)!=identity(j['backup']):raise Failure('old-copy directory changed during cleanup')
                self.stage(j,'cleaning');delete_tree_fd(leaf)
                now=os.stat(name,dir_fd=parent,follow_symlinks=False)
                if (now.st_dev,now.st_ino)!=(held.st_dev,held.st_ino):raise Failure('old-copy name changed during cleanup')
                os.rmdir(name,dir_fd=parent);os.fsync(parent)
        else:self.stage(j,'cleaning')
        if j.get('quarantine'):
            # The private store held only the original; remove it once empty.
            with contextlib.suppress(FileNotFoundError):os.rmdir(j['quarantine']['path'])
        self.stage(j,'cleaned');return {'ok':True,'id':id,'state':'cleaned','spaceWarning':'Btrfs snapshots may retain this data; reclaimed space is not guaranteed.'}
    def discard_admission(self,j):
        before_switch={'planned','copying','verifying','awaiting-maintenance'}
        phase=j.get('interruptedState') if j['state']=='paused' else j['state']
        if phase not in before_switch or j.get('verified') or j.get('cutover') or j.get('placeholderIdentity') or j.get('quarantine'):
            raise Failure('cannot discard a destination after a possible switch; inspect both copies')
        from helper.maintenance import LATCH,latch_request
        if os.path.lexists(LATCH) and latch_request()['moveId']==j['id']:raise Failure('cancel the pending restart first')
        if os.path.lexists(j['backup']) or self.bound(j):raise Failure('active bind or old copy blocks discard')
        self.changed(j['source'],j['sourceIdentity'],j.get('sourceUUID'))
        self.destination(j)
    def discard_destination(self,id,restart=False):
        j=self.c.read('moves',id)
        self.discard_admission(j)
        if any(r['target']==j['dest'] or r['target'].startswith(j['dest']+'/') for r in mount_rows()):raise Failure('mounted destination blocks discard')
        # The worker re-anchors the path itself and deletes only the recorded
        # directory; a swapped ancestor or replaced folder is refused there.
        args=['discard',j['dest'],json.dumps(j['destIdentity'])]+(['--recreate'] if restart else [])
        result=self.destination_op(j,*args)
        if restart:
            j['destIdentity']=result['identity'];j['verified']=False;self.c.journal('moves',id,j)
            return self.execute(j)
        self.stage(j,'rolled-back');return {'ok':True,'id':id,'state':'rolled-back'}
    def cancel(self,id):return self.discard_destination(id)
    def restart(self,id):return self.discard_destination(id,restart=True)
    def inspect(self):
        results=[];block=blocks();rows=mount_rows()
        for j in self.c.records('moves'):
            live=dict(j);live['bound']=self.bound(j,block,rows) if 'destIdentity' in j else False
            live['originalAvailable']=os.path.lexists(j['source']) and not os.path.lexists(j['backup'])
            live['oldCopyAvailable']=os.path.lexists(j['backup'])
            if j['state'] in ACTIVE:
                live['interruptedState']=j['state'];live['state']='paused';live['error']='Interrupted; inspect before Continue or Undo.'
            if j['state']=='switched' and j.get('verified') and j.get('boot_id')!=self.c.boot_id() and live['bound']:
                live['state']='rebooted'
            live['canDelete']=live['state'] in ('rebooted','cleaning') and live['bound'] and j.get('verified',False) and j.get('maintenanceProtocol')==2
            results.append(live)
        return results
