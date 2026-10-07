"""Fail-closed folder copy/verify/bind with inspect-only crash recovery."""
import contextlib,errno,fcntl,hashlib,json,os,pathlib,pwd,re,shutil,stat,struct,time,uuid
from helper.common import Failure,run,mount_rows,mount_for,escape_fstab,read_regular
from topology import probe,blocks
from helper.maintenance import RETURN_STATES

ACTIVE={'planned','quarantining','copying','verifying','switching','testing','rolling-back','cleaning'}|RETURN_STATES
WAITING={'awaiting-maintenance'}

class ScheduleFailure(Failure):
    """Structured partial outcome: preparation is permanent, not rolled back."""
    def __init__(self,message,result):
        super().__init__(message);self.result=result

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

def private_container_fd(fd):
    """The wrapper, never rsync's content root, is the confidentiality boundary."""
    s=os.fstat(fd)
    if not stat.S_ISDIR(s.st_mode) or s.st_uid!=0 or stat.S_IMODE(s.st_mode)!=0o700:
        raise Failure('destination container must be root-private mode 0700')
    try:acl=os.getxattr(fd,'system.posix_acl_access')
    except OSError as error:
        if error.errno not in (errno.ENODATA,errno.ENOTSUP):raise
    else:
        if len(acl)<4 or (len(acl)-4)%8 or struct.unpack_from('<I',acl)[0]!=2:raise Failure('invalid private-container ACL')
        entries=[struct.unpack_from('<HHI',acl,i) for i in range(4,len(acl),8)]
        masks=[permission for tag,permission,_ in entries if tag==16]
        # Linux applies the access mask to *all* named UID/GID entries. Do
        # not accept an effective named-ACL bypass even if stat was forged.
        if len(masks)!=1 or masks[0]!=0 or any(tag==32 and permission for tag,permission,_ in entries):
            raise Failure('destination container ACL is not private (effective mask must be zero)')
    return identity_fd(fd)


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

def protected(path,uid=None):
    """Only a real directory strictly inside the actual caller's home.

    Keep the old name/API, but do not treat hidden/profile names as danger.
    uid=None is the legacy administrator API's source-owner policy; the new
    assessed methods always supply the unique bus sender's exact UID.
    """
    p=safe_path(path)
    s=p.stat()
    uid=s.st_uid if uid is None else uid
    if uid==0:raise Failure('protected path: root home moves are unsupported')
    try:user=pwd.getpwuid(uid);home=safe_path(user.pw_dir)
    except (KeyError,OSError) as exc:raise Failure('cannot establish caller home') from exc
    system_roots={'etc','usr','bin','sbin','lib','lib64','var','run','proc','sys','dev','boot','opt','tmp'}
    if len(home.parts)<3 or home.parts[1] in system_roots or p==home or home not in p.parents:raise Failure('protected path: choose a folder inside your own home, not the whole home or a system directory')
    if not stat.S_ISDIR(s.st_mode):raise Failure('source must be a real directory')
    if home.stat().st_uid!=uid:raise Failure('caller home is not owned by the caller')
    # Scheduling already requires administrator authentication; a folder in
    # your home owned by another user (or behind a private parent) moves too.
    return uid

def open_users(path,proc_root='/proc'):
    """Inspect process metadata only, never user file contents or environments.

    A /proc snapshot is a refusal aid, not a writer lease. The maintenance
    boot still establishes exclusion before copying/cutover.
    """
    found=[];path=str(path)
    def inside(target):
        if target==path or target.startswith(path+'/'):return True
        if target.endswith(' (deleted)'):target=target[:-10]
        return target==path or target.startswith(path+'/')
    for proc in pathlib.Path(proc_root).glob('[0-9]*'):
        try:
            used=False
            targets=list((proc/'fd').iterdir())+[proc/'cwd',proc/'root',proc/'exe']
            for fd in targets:
                try:target=os.readlink(fd)
                except PermissionError:raise
                except (FileNotFoundError,ProcessLookupError):continue
                if inside(target):used=True;break
            if not used:
                # mmap can survive closing the last fd. /proc maps exposes
                # backing paths, not mapped contents; newline is escaped there.
                for line in read_regular(proc/'maps',8*1024*1024).decode('utf-8','surrogateescape').split('\n'):
                    fields=line.split(None,5)
                    if len(fields)==6 and (inside(fields[5]) or inside(fields[5].replace('\\012','\n'))):used=True;break
            if used:
                name=read_regular(proc/'comm',4096).decode('utf-8','replace').strip()[:80]
                entry={'pid':int(proc.name),'name':name}
                unit=process_unit(proc)
                if unit:entry['unit']=unit
                found.append(entry)
        except PermissionError as exc:
            raise Failure('cannot inspect active use; root helper needs proc visibility') from exc
        except (FileNotFoundError,ProcessLookupError):continue
    return found[:48]

def process_unit(proc):
    """The systemd service or scope a process runs in, so the panel can say
    'background service' or name the app. Metadata only; '' when unknown."""
    try:lines=read_regular(proc/'cgroup',16384).decode('utf-8','replace').splitlines()
    except (OSError,Failure):return ''
    for line in lines:
        if line.startswith('0::'):
            parts=[p for p in line[3:].split('/') if p.endswith(('.service','.scope'))]
            return parts[-1][:120] if parts else ''
    return ''

def _shown(path):
    return os.fsdecode(path).encode('utf-8','replace').decode('utf-8')[:160]

def tree_stats(path,uid=None,progress=None):
    # The copy runs as root (rsync -aHAXS --numeric-ids), like `sudo` in a
    # terminal: files owned by other users, files the caller cannot read, and
    # FIFOs, sockets and device nodes are all copied exactly with their owner
    # and mode. Only things that would break the result are refused below.
    # uid is kept for API compatibility; access is never judged per user.
    count=0;size=0;directories=0;h=hashlib.sha256();seen=0
    def unreadable(exc):raise Failure('could not read '+_shown(getattr(exc,'filename','') or path)+' even as administrator ('+(exc.strerror or 'I/O error')+'); nothing is skipped silently') from exc
    root_info=os.lstat(path)
    if not stat.S_ISDIR(root_info.st_mode):raise Failure('source must be a real directory')
    links={}  # (dev, ino) -> [link count, names seen inside this tree]
    # Sort within each directory, not one million entries in memory.
    for root,dirs,files in os.walk(path,topdown=True,followlinks=False,onerror=unreadable):
        dirs.sort();files.sort();directories+=1
        for name in dirs+files:
            p=os.path.join(root,name);s=os.lstat(p);rel=os.fsencode(os.path.relpath(p,path))
            if s.st_dev!=root_info.st_dev:raise Failure('nested filesystem blocks the move')
            # rsync -a preserves the link itself, never its referent. Relative
            # and absolute external links keep their meaning through the
            # familiar bind path; rejecting them would block ordinary profiles.
            # Sockets, FIFOs and device nodes are names, not data; nothing runs
            # in the maintenance boot and rsync -a recreates each exactly.
            if stat.S_ISREG(s.st_mode):count+=1;size+=s.st_size
            if not stat.S_ISDIR(s.st_mode) and s.st_nlink>1:
                links.setdefault((s.st_dev,s.st_ino),[s.st_nlink,0,p])[1]+=1
            h.update(len(rel).to_bytes(4,'big'));h.update(rel);h.update(str((s.st_mode,s.st_uid,s.st_gid,s.st_size)).encode())
            seen+=1
            if progress and seen%5000==0:progress(seen)
    if progress:progress(seen)
    # A hardlink to a file outside the folder would silently become two
    # separate files after the move; refuse instead of splitting it.
    split=[name for total,inside,name in links.values() if inside<total]
    if split:raise Failure(str(len(split))+' file(s) here also have a name outside this folder (hard links), e.g. '+_shown(split[0])+'. Moving would split each into two separate copies')
    return {'files':count,'bytes':size,'directories':directories,'metadataDigest':h.hexdigest()}

WAITING_TEXT='another folder is already waiting for the next restart; one folder moves per restart, so restart first and then move this one'
INSPECT_TEXT='maintenance controls require inspection before normal storage operations'


def armed_refusal():
    """Why a normal storage operation must wait: a valid armed request is
    simply a folder waiting for the restart; anything else needs inspection."""
    from helper.maintenance import latch_request
    try:latch_request()
    except Exception:return Failure(INSPECT_TEXT)
    return Failure(WAITING_TEXT)


class MoveManager:
    def __init__(self,common,uid=None,isolated=False,progress=None):
        # isolated: running inside drives-helper.service, whose namespace keeps
        # drives read-only; destination writes then go through helper.destination.
        self.c=common;self.uid=uid;self.isolated=isolated;self.progress=progress
    def wake(self,mountpoint):
        """Start a drive's own mount unit when only its idle automount is there.

        After boot an x-systemd.automount drive stays an autofs placeholder
        until something opens it, which made assessment and resume report the
        drive as missing. Only explicit user operations call this, never
        Status/inspect, and only for a mountpoint this machine already
        configured (a Drives record or an /etc/fstab line): starting that unit
        is exactly what the first access would do."""
        if not isinstance(mountpoint,str) or not mountpoint.startswith('/'):return
        rows=[r for r in mount_rows() if r['target']==mountpoint]
        if not rows or any(r['fstype']!='autofs' for r in rows):return
        known=any(d.get('state')=='ready' and d.get('mountpoint')==mountpoint for d in self.c.records('drives'))
        if not known:
            try:fstab=read_regular('/etc/fstab',65536).decode('utf-8','replace').splitlines()
            except (OSError,Failure):fstab=[]
            known=any(len(f)>=2 and not f[0].startswith('#') and f[1]==escape_fstab(mountpoint) for f in (line.split() for line in fstab))
        if not known:return
        safe_path(mountpoint)
        unit=run(['systemd-escape','--path','--suffix=mount',mountpoint]).decode().strip()
        try:run(['systemctl','start',unit],timeout=90)
        except Failure:pass  # the admission below reports the drive state itself
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
        if 'destContainer' in j:
            self.private_layout(j)
            result=self.destination_op(j,'create-private',j['destContainer'])
            j['destIdentity']=result['identity'];j['containerIdentity']=result['containerIdentity']
        else:j['destIdentity']=self.destination_op(j,'create',j['dest'])['identity']
        self.c.journal('moves',j['id'],j)
    def private_layout(self,j):
        if j['destContainer']!=j['destMount']+'/drives-'+j['id'] or j['dest']!=j['destContainer']+'/content':
            raise Failure('private destination layout changed; inspect before continuing')
        safe_path(j['destContainer']);safe_path(j['dest'])
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
    def _preflight(self,src,destMount,allow_preparation=False):
        uid=protected(src,self.uid)
        source=safe_path(src);target=safe_path(destMount)
        rows=mount_rows();block=blocks()
        origin=probe(src,block,rows,resolve=False)
        if not origin['supported'] or origin['mount']['fstype'] not in ('btrfs','ext4','xfs'):raise Failure('unsupported source storage topology')
        source_id=identity(src)
        parent_id=identity(str(source.parent));mount_id=identity(destMount)
        if source_id[1]==256 and source_id[2] is not None:raise Failure('source subvolume root cannot be quarantined; choose an ordinary folder')
        if any(r['target']==src or r['target'].startswith(src+'/') for r in rows):raise Failure('nested or existing mount blocks the move')
        # Active use is reported, not refused: the move runs in the maintenance
        # boot, after a normal shutdown has stopped every app and service, and
        # that boot independently refuses any non-root process. The list lets
        # the review say what the restart will close.
        owners=open_users(src)
        topology=probe(destMount,block,rows,resolve=False)
        if not topology['supported'] or not topology.get('encrypted') or topology['mount']['target']!=destMount:raise Failure('destination must be a mounted single encrypted disk')
        if 'ro' in topology['mount'].get('options','').split(','):raise Failure('destination is read-only')
        # The offline worker mounts a Btrfs drive root, not an arbitrary bind,
        # system disk, subvolume or manual-unlock destination.
        from helper.preparedrive import check
        check(destMount,probe=probe,rows=rows)
        t=target.stat();needs=t.st_uid!=0 or bool(t.st_mode&0o022)
        if needs:
            if not allow_preparation:raise Failure('destination mount must be root-owned and not group/world writable; explicit preparation consent is required')
            if t.st_uid not in (0,uid):raise Failure('destination mount is not owned by the caller or root')
        if destMount==src or destMount.startswith(src+'/') or src.startswith(destMount+'/'):raise Failure('source and destination overlap')
        stats=tree_stats(src,uid,self.progress);v=os.statvfs(target)
        if v.f_bavail*v.f_frsize*5<stats['bytes']*6:raise Failure('destination needs at least 1.2x source apparent size free')
        if identity(src)!=source_id or identity(str(source.parent))!=parent_id or identity(destMount)!=mount_id:raise Failure('source or destination directory changed during assessment')
        identities={'sourceIdentity':source_id,'parentIdentity':parent_id,'mountIdentity':mount_id,
            'sourceUUID':origin['chain'][-1]['uuid'],'sourceMount':origin['mount']['target']}
        self.in_use=owners
        return stats,topology,uid,needs,identities
    def preflight(self,src,destMount):
        stats,topology,uid,_,_=self._preflight(src,destMount)
        return stats,topology,uid
    def conflicts(self,src,exclude=None):
        if os.path.lexists(src+'.pre-move'):raise Failure('old-copy path already exists')
        for record in self.c.records('moves'):
            if record['id']==exclude or record['state']=='rolled-back':continue
            # Returned local data is a fresh source again; history still owns
            # its frozen original and SSD copy, never the returned live folder.
            keys=('backup','dest') if record['state']=='returned' else ('source','backup','dest')
            paths=[record.get(k,'') for k in keys]
            if record.get('returnStore'):paths.append(record['returnStore']['path'])
            paths.extend(store['path'] for store in record.get('retainedReturnStages',[]))
            if any(p and (p==src or p.startswith(src+'/') or src.startswith(p+'/')) for p in paths):raise Failure('this folder overlaps an existing move; inspect it first')
    def controls(self):
        from helper.maintenance import LATCH,RUNTIME
        if os.path.lexists(RUNTIME):raise Failure(INSPECT_TEXT)
        if os.path.lexists(LATCH):raise armed_refusal()
    def admit(self,src,destMount,allow_preparation=False,exclude=None):
        self.controls();self.conflicts(src,exclude)
        stats,topology,uid,needs,identities=self._preflight(src,destMount,allow_preparation)
        source_mount,mapper=self.offline_layout(src,topology)
        if source_mount!=identities['sourceMount']:raise Failure('source mount changed during assessment')
        safe_path(src);safe_path(destMount);protected(src,uid)
        if identity(src)!=identities['sourceIdentity'] or identity(str(pathlib.Path(src).parent))!=identities['parentIdentity'] or identity(destMount)!=identities['mountIdentity']:raise Failure('source or destination directory changed during assessment')
        self.controls();self.conflicts(src,exclude)
        return {'stats':stats,'topology':topology,'uid':uid,'needsPreparation':needs,
            'destMapper':mapper,'inUse':getattr(self,'in_use',[]),**identities}
    def assess(self,src,destMount):
        self.wake(destMount)
        facts=self.admit(src,destMount,allow_preparation=True)
        return {'ok':True,'source':src,'destMount':destMount,'needsPreparation':facts['needsPreparation'],'stats':facts['stats'],'inUse':facts['inUse']}
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
        self.conflicts(src)
        facts={'stats':stats,'topology':topology,'uid':uid,'sourceMount':source_mount,'destMapper':mapper,
            'sourceIdentity':identity(src),'sourceUUID':probe(src,resolve=False)['chain'][-1]['uuid'],
            'parentIdentity':identity(str(pathlib.Path(src).parent)),'mountIdentity':identity(destMount)}
        j=self.new_plan(src,destMount,facts)
        self.stage(j,'planned');self.make_destination(j)
        return self.execute(j)
    def new_plan(self,src,destMount,facts):
        topology=facts['topology']
        id=uuid.uuid4().hex;container=destMount+'/drives-'+id;dest=container+'/content';backup=src+'.pre-move'
        return {'id':id,'state':'planned','source':src,'destMount':destMount,'dest':dest,'destContainer':container,'backup':backup,
           'sourceIdentity':facts['sourceIdentity'],'sourceUUID':facts['sourceUUID'],'parentIdentity':facts['parentIdentity'],'destUUID':topology['chain'][-1]['uuid'],'mountIdentity':facts['mountIdentity'],'diskSerial':topology['disk'].get('serial'),
           'uid':facts['uid'],'stats':facts['stats'],'boot_id':self.c.boot_id(),'verified':False,'created':time.time(),
           'maintenanceProtocol':2,'sourceMount':facts['sourceMount'],'destMapper':facts['destMapper'],
           'destFSRoot':os.path.normpath(topology['mount']['fsroot'].rstrip('/')+'/'+os.path.relpath(dest,destMount))}
    def same_admission(self,j,facts):
        for key in ('sourceIdentity','sourceUUID','parentIdentity','mountIdentity','sourceMount','destMapper','uid'):
            if facts[key]!=j[key]:raise Failure('source or destination identity changed during scheduling; inspect the plan')
        t=facts['topology']
        if t['chain'][-1]['uuid']!=j['destUUID'] or t['disk'].get('serial')!=j['diskSerial'] or os.path.normpath(t['mount']['fsroot'].rstrip('/')+'/'+os.path.relpath(j['dest'],j['destMount']))!=j['destFSRoot']:
            raise Failure('destination topology changed during scheduling; inspect the plan')
    def schedule_move(self,src,destMount,prepareDestination):
        if type(prepareDestination) is not bool:raise Failure('preparation consent must be a boolean')
        self.wake(destMount)
        facts=self.admit(src,destMount,allow_preparation=True)
        if facts['needsPreparation'] and not prepareDestination:raise Failure('explicit consent is required for permanent destination mount-root preparation')
        j=self.new_plan(src,destMount,facts)
        j['preparation']={'required':facts['needsPreparation'],'attempted':False,'completed':False}
        # Save a recoverable identity/intent before a possibly permanent change.
        self.stage(j,'planned')
        arm_attempted=False
        try:
            if facts['needsPreparation']:
                from helper.preparedrive import run_isolated
                j['preparation']['attempted']=True;self.c.journal('moves',j['id'],j)
                result=run_isolated(destMount,expected=j['mountIdentity'])
                if not result.get('ok'):raise Failure('destination preparation worker did not confirm completion')
                j['preparation']['completed']=True;j['preparation']['result']=result
                self.c.journal('moves',j['id'],j)
            # Do not trust either the assessment or the preparation worker.
            current=self.admit(src,destMount,exclude=j['id']);self.same_admission(j,current)
            j['stats']=current['stats'];self.c.journal('moves',j['id'],j)
            self.make_destination(j);self.execute(j)
            current=self.admit(src,destMount,exclude=j['id']);self.same_admission(j,current)
            self.c.journal('moves',j['id'],j)
            arm_attempted=True
            result=self.request(j['id'],'continue')
            result['preparation']=j['preparation'];result['inUse']=current['inUse']
            return result
        except BaseException as exc:
            prep=j['preparation']
            effect=('Destination mount-root preparation permanently changed its owner/mode; it was not rolled back. ' if prep['completed'] else
                'Destination mount-root owner/mode may already have changed permanently; inspect it. ' if prep['attempted'] else 'Destination preparation was not performed. ')
            message=effect+('Restart request may be armed; inspect or cancel it. ' if arm_attempted else '')+'Plan '+j['id']+': '+str(exc)
            j['interruptedState']=j['state'];j['state']='paused';j['needsAttention']=True;j['error']=message[:300]
            j['restartRequestMayBeArmed']=arm_attempted
            saved=True
            try:self.c.journal('moves',j['id'],j)
            except BaseException:saved=False
            raise ScheduleFailure(message,{'ok':False,'id':j['id'],'state':'paused','preparation':prep,
                'restartRequestMayBeArmed':arm_attempted,'failureJournalSaved':saved}) from exc
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
        if action not in ('continue','rollback','return'):raise Failure('invalid maintenance action')
        j=self.c.read('moves',id)
        self.wake(j.get('destMount'))
        if j.get('maintenanceProtocol')!=2:raise Failure('this older move needs administrator inspection; both copies are kept')
        if action=='return':
            self.controls();self.return_admission(j)
            # Persist the intent before launching the root latch writer. Failure
            # after its launch must never claim that the request is unarmed.
            j['returnRequest']={'action':'return','armedBootId':self.c.boot_id()}
            self.c.journal('moves',id,j)
            attempted=False
            try:
                attempted=True;self.latch('arm',id,action)
                j['error']='';j.pop('needsAttention',None);self.c.journal('moves',id,j)
            except BaseException as error:
                raise ScheduleFailure('Move back restart request may be armed; inspect or cancel it: '+str(error),
                    {'ok':False,'id':id,'state':j['state'],'restartRequestMayBeArmed':attempted}) from error
            return {'ok':True,'id':id,'state':'restart-required','action':action}
        if action=='continue':
            from helper.offline import OFFLINE
            if j['state'] in OFFLINE:pass # interrupted offline step: the worker restores the original first
            elif not self.waiting(j):raise Failure('this move is not waiting to run')
            else:
                self.copy_layout(j)
                self.changed(j['source'],j['sourceIdentity'],j.get('sourceUUID'))
                if os.path.lexists(j['backup']) or self.bound(j):raise Failure('unexpected old copy or bind; inspect before continuing')
                self.destination(j)
        elif j['state']=='rolling-back':pass # interrupted Undo: the worker finishes restoring
        else:
            if j['state']!='switched' or not j.get('verified') or not self.bound(j):raise Failure('only an active, verified move can be undone')
            # Undo also runs in the maintenance boot, after shutdown closed every
            # user of the folder; in-session users are not a reason to refuse.
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
    def copy_layout(self,j):
        # Flat journals remain inspectable and eligible for existing Undo/cleanup,
        # but may never plan a new copy that loses the source's ancestor privacy.
        # Protocol-1 planning cannot authorize Continue (request refuses it).
        if j.get('maintenanceProtocol')==2 and 'destContainer' not in j:
            raise Failure('Legacy flat-layout destination cannot copy safely; all copies are retained. Cancel this untouched plan and replan with a new private destination.')
    def private_destination(self,j,allow_removed=False):
        if 'destContainer' not in j:return # existing journals retain their layout
        self.private_layout(j)
        if allow_removed and not os.path.lexists(j['destContainer']):return
        self.changed(j['destContainer'],j['containerIdentity'],j.get('destUUID'))
        with anchored_tree(j['destContainer']) as (_,fd,_):
            held=private_container_fd(fd)
            if held[1:]!=j['containerIdentity'][1:]:raise Failure('private container identity changed')
            try:content=os.open('content',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            except FileNotFoundError:
                if allow_removed:return
                raise
            try:
                actual=identity_fd(content)
                if actual[1:]!=j['destIdentity'][1:] or actual[0]!=held[0] or actual[2:]!=held[2:]:raise Failure('private content identity changed')
            finally:os.close(content)
    def destination(self,j,allow_removed=False):
        # Missing copy paths are admissible only while finishing a journalled
        # Undo, never for Continue, bind status, Cancel or Start over.
        allow_removed=allow_removed and j['state']=='rolling-back' and 'destContainer' in j
        # Observe PID1 topology before touching paths: a missing drive's autofs
        # must not stall Status or start an unlock merely to inspect a record.
        t=probe(j['destMount'],resolve=False)
        if not t['supported'] or not t.get('encrypted') or t['disk'].get('serial')!=j['diskSerial']:raise Failure('destination disk missing or changed')
        safe_path(j['destMount']);safe_path(j['dest']);self.changed(j['destMount'],j['mountIdentity'],j.get('destUUID'))
        if not allow_removed or os.path.lexists(j['dest']):self.changed(j['dest'],j['destIdentity'],j.get('destUUID'))
        self.private_destination(j,allow_removed=allow_removed)
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
            self.private_destination(j)
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
        self.copy_layout(j)
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
    def move_back(self,id):
        return self.request(id,'return')
    def return_config(self,j,allow_removed=False):
        """Prove an exact owned bind stanza; a manual lookalike is not ours."""
        from helper.offline import fstab_line
        from helper.configwriter import transform
        expected=fstab_line(j);marker='# drives-helper '+j['id']
        content=read_regular('/etc/fstab',65536).decode()
        ownership=json.loads(read_regular(self.c.state_dir/('config-fstab-'+j['id']+'.json'),8192))
        present=marker in content.splitlines()
        if present:
            if expected not in (ownership.get('line'),ownership.get('before')):raise Failure('fstab ownership differs')
            remaining=transform(content,j['id'],None,{expected})
        else:
            if not allow_removed or ownership.get('line','missing') is not None and not (ownership.get('after','missing') is None and ownership.get('before')==expected):
                raise Failure('owned fstab bind is missing')
            remaining=content
        for row in remaining.splitlines():
            fields=row.split()
            if fields and not fields[0].startswith('#') and len(fields)>1:
                target=re.sub(r'\\([0-7]{3})',lambda m:chr(int(m[1],8)),fields[1])
                if os.path.normpath(target)==j['source']:raise Failure('foreign fstab entry targets this folder')
        return present
    def return_origin(self,j):
        if j.get('sourceMount') not in ('/','/home'):raise Failure('Move back currently supports only original OS / or /home storage')
        parent=str(safe_path(j['source']).parent)
        self.changed(parent,j['parentIdentity'],j.get('sourceUUID'))
        for path in (parent,'/'):
            t=probe(path,resolve=False)
            expected='/' if path=='/' else j['sourceMount']
            if not t['supported'] or t['mount']['target']!=expected or t['mount']['fstype'] not in ('btrfs','ext4','xfs') or t['chain'][-1].get('uuid')!=j['sourceUUID'] or 'ro' in t['mount'].get('options','').split(','):
                raise Failure('original filesystem topology changed or is read-only')
        root=identity(j['sourceMount']);held=identity(parent)
        if root[0]!=held[0] or root[2:]!=held[2:]:raise Failure('return staging must share the original filesystem and subvolume')
    def return_admission(self,j,offline=False,full=True):
        if j.get('maintenanceProtocol')!=2 or not j.get('verified') or j.get('state') not in {'switched','cleaned'}|RETURN_STATES:
            raise Failure('only an owned verified protocol-2 move can return')
        if self.uid is not None and self.uid!=j['uid']:raise Failure('folder move belongs to another user')
        if 'destContainer' not in j:raise Failure('Move back requires the private destination layout; retain legacy copies for inspection')
        rows=mount_rows();source_rows=[r for r in rows if r['target']==j['source']]
        if len(source_rows)>1 or source_rows and (source_rows[0]['fstype']=='autofs' or not self.bound(j,rows=rows)):
            raise Failure('foreign or stacked bind blocks Move back')
        if not offline and not source_rows and j['state'] not in RETURN_STATES:raise Failure('only an active owned bind can move back')
        self.return_origin(j);self.destination(j)
        paths=[j['source'],j['destContainer'],j['backup'],j['sourceMount'].rstrip('/')+'/.drives-return']
        if j.get('returnStore'):paths.append(j['returnStore']['path'])
        if any(r['target'].startswith(p+'/') or (r['target']==p and p!=j['source']) for p in paths for r in rows):raise Failure('nested mount blocks Move back')
        if any(r['target']!=j['source'] and (r.get('fsroot')==j['destFSRoot'] or r.get('fsroot','').startswith(j['destFSRoot']+'/')) for r in rows):
            raise Failure('foreign bind exposes the SSD content')

        self.return_config(j,allow_removed=j['state']=='return-finishing')
        if not full:return None
        # In the normal session the shutdown before the maintenance boot closes
        # these users; inside the maintenance boot nothing may hold them.
        if offline:
            for path in (j['source'],j['dest']):
                users=open_users(path)
                if users:
                    names=', '.join(p['name']+' (pid '+str(p['pid'])+')' for p in users[:16])
                    raise Failure('unexpected process holds the folder or SSD copy during maintenance: '+names)
        stats=tree_stats(j['dest'],j['uid'])
        if j['state'] not in RETURN_STATES:
            v=os.statvfs(str(pathlib.Path(j['source']).parent))
            if v.f_bavail*v.f_frsize*5<stats['bytes']*6:raise Failure('original filesystem needs at least 1.2x latest apparent size free')
        return stats
    def delete_old(self,id):
        j=self.c.read('moves',id)
        self.wake(j.get('destMount'))
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
    def recorded_destination(self,j):
        keys=('destIdentity','containerIdentity') if 'destContainer' in j else ('destIdentity',)
        return all(isinstance(j.get(key),list) and len(j[key])==3 for key in keys)
    def discard_admission(self,j,allow_incomplete=False):
        before_switch={'planned','copying','verifying','awaiting-maintenance'}
        phase=j.get('interruptedState') if j['state']=='paused' else j['state']
        if phase not in before_switch or j.get('verified') or any(key in j for key in ('cutover','placeholderIdentity','quarantine','verification')):
            raise Failure('cannot discard a destination after a possible switch; inspect both copies')
        from helper.maintenance import LATCH,RUNTIME,latch_request
        if j.get('maintenanceProtocol')==2:
            if os.path.lexists(LATCH) or os.path.lexists(RUNTIME):raise Failure('cancel the pending restart or inspect maintenance controls first')
            store=j.get('sourceMount','').rstrip('/')+'/.drives-quarantine/'+j['id']
            if os.path.lexists(store):raise Failure('quarantine exists; inspect before cancellation')
            fstab=read_regular('/etc/fstab',65536).decode()
            if '# drives-helper '+j['id'] in fstab.splitlines():raise Failure('saved mount configuration blocks cancellation')
            ownership=self.c.state_dir/('config-fstab-'+j['id']+'.json')
            if os.path.lexists(ownership):
                record=json.loads(read_regular(ownership,8192))
                if record.get('line') is not None or record.get('after') is not None:raise Failure('possible mount configuration blocks cancellation')
        elif os.path.lexists(LATCH) and latch_request()['moveId']==j['id']:raise Failure('cancel the pending restart first')
        rows=mount_rows()
        # Incomplete plans have no owned destination to validate. An exact or
        # nested source mount is independently refused below, without probing
        # an unrecorded private child through bound().
        if os.path.lexists(j['backup']) or self.recorded_destination(j) and self.bound(j):raise Failure('active bind or old copy blocks discard')
        targets=[j['source'],j['backup'],j.get('destContainer',j['dest'])]
        if any(row['target']==path or row['target'].startswith(path+'/') for row in rows for path in targets):raise Failure('mounted source or destination blocks discard')
        self.changed(j['source'],j['sourceIdentity'],j.get('sourceUUID'))
        if j.get('parentIdentity'):self.changed(str(pathlib.Path(j['source']).parent),j['parentIdentity'],j.get('sourceUUID'))
        if self.recorded_destination(j):self.destination(j)
        elif not allow_incomplete or j.get('maintenanceProtocol')!=2:
            raise Failure('missing destination identity; only safe protocol-2 cancellation is available')
    def discard_destination(self,id,restart=False):
        j=self.c.read('moves',id)
        self.wake(j.get('destMount'))
        if restart:self.copy_layout(j)
        self.discard_admission(j,allow_incomplete=not restart)
        if not self.recorded_destination(j):
            # A failed worker/journal write can leave an unowned directory.
            # Cancel only this plan; NEVER adopt, delete or recreate that path.
            target=j.get('destContainer',j['dest'])
            try:
                rows=mount_rows();t=probe(j['destMount'],mounts=rows,resolve=False)
                mounted=[r for r in rows if r['target']==j['destMount'] and r['fstype']!='autofs']
                # Kernel rows and nonresolving topology must agree on one live,
                # writable, owned filesystem before even lstat of an unknown child.
                inspected=bool(len(mounted)==1 and t['supported'] and t.get('encrypted') and
                    t['mount']==mounted[0] and t['mount']['fstype'] in ('btrfs','ext4','xfs') and
                    'ro' not in t['mount'].get('options','').split(',') and
                    t['disk'].get('serial')==j['diskSerial'] and t['chain'][-1].get('uuid')==j['destUUID'] and
                    os.path.normpath(t['mount']['fsroot'].rstrip('/')+'/'+os.path.relpath(j['dest'],j['destMount']))==j['destFSRoot'])
            except (Failure,OSError,KeyError,IndexError,TypeError,ValueError):inspected=False
            retained=target if not inspected or os.path.lexists(target) else None
            message='Plan cancelled. Destination preparation, if performed, remains permanent.'
            if not inspected:message+=' Destination path was not inspected and may be retained for administrator inspection: '+target
            elif retained:message+=' Unrecorded destination directory retained for administrator inspection: '+retained
            if retained:j['retainedDestination']=retained
            j['destinationInspected']=inspected
            self.stage(j,'rolled-back')
            return {'ok':True,'id':id,'state':'rolled-back','message':message,'retainedDestination':retained,'destinationInspected':inspected}
        target=j.get('destContainer',j['dest'])
        if any(r['target']==target or r['target'].startswith(target+'/') for r in mount_rows()):raise Failure('mounted destination blocks discard')
        # The worker re-anchors the path itself and deletes only the recorded
        # directories; a swapped ancestor or replaced folder is refused there.
        if 'destContainer' in j:
            args=['discard-private',j['destContainer'],json.dumps({'identity':j['destIdentity'],'containerIdentity':j['containerIdentity']})]
        else:args=['discard',j['dest'],json.dumps(j['destIdentity'])]
        if restart:args+=['--recreate']
        result=self.destination_op(j,*args)
        if restart:
            j['destIdentity']=result['identity']
            if 'destContainer' in j:j['containerIdentity']=result['containerIdentity']
            j['verified']=False;self.c.journal('moves',id,j)
            return self.execute(j)
        self.stage(j,'rolled-back');return {'ok':True,'id':id,'state':'rolled-back'}
    def cancel(self,id):return self.discard_destination(id)
    def restart(self,id):return self.discard_destination(id,restart=True)
    def inspect(self):
        results=[];block=blocks();rows=mount_rows()
        for j in self.c.records('moves'):
            live=dict(j);live['bound']=self.bound(j,block,rows) if self.recorded_destination(j) else False
            live['originalAvailable']=os.path.lexists(j['source']) and not os.path.lexists(j['backup'])
            live['oldCopyAvailable']=os.path.lexists(j['backup'])
            if j['state'] in ACTIVE:
                live['interruptedState']=j['state'];live['state']='paused';live['error']='Interrupted; inspect before Continue or Undo.'
                if j['state'] in RETURN_STATES:live['error']=j.get('error') or 'Move back interrupted; inspect before retrying Move back. All copies are retained.'
            if j['state']=='switched' and j.get('verified') and j.get('boot_id')!=self.c.boot_id() and live['bound']:
                live['state']='rebooted'
            live['canDelete']=live['state'] in ('rebooted','cleaning') and live['bound'] and j.get('verified',False) and j.get('maintenanceProtocol')==2
            live['canCancelIncomplete']=False
            live['canMoveBack']=False
            if j.get('state') in {'switched','cleaned'}|RETURN_STATES:
                try:self.return_admission(j,full=False)
                except (Failure,OSError,KeyError,ValueError,TypeError):pass
                else:live['canMoveBack']=True
            if j.get('maintenanceProtocol')==2:
                try:self.discard_admission(j,allow_incomplete=True)
                except (Failure,OSError,KeyError,ValueError,TypeError):pass
                else:live['canCancelIncomplete']=True
            results.append(live)
        return results
