"""Protocol 2: the folder move itself, run only inside the maintenance boot.

Normal sessions only plan a move and arm a root-filesystem request. The next
boot selects drives-maintenance.target, where no desktop, user session, timer,
socket or ordinary service can start. This worker then re-audits that state,
quarantines the original, copies and verifies it, switches the familiar path
to a bind mount and reboots. Every step is journalled first.

Recovery rule: an interrupted Continue never resumes copying by itself. The
worker restores the untouched original to its familiar path, marks the move
paused and returns to a normal boot; the user decides what happens next.
An interrupted Undo finishes restoring the original (the safe direction).
Anything ambiguous keeps the boot gate closed and both copies.
"""
import ctypes,json,os,pathlib,re,stat,subprocess,sys,time,uuid
from helper.common import Common,Failure,run,mount_rows,mount_for,boot_id,escape_fstab,read_regular,atomic
from helper import maintenance
from helper.moves import (MoveManager,anchored_tree,delete_tree_fd,durable_directory,identity,
    identity_fd,private_container_fd,safe_path,tree_stats)
from helper.quarantine import closed_hardlinks,move_original,prepare_store,private_store_fd

OFFLINE={'quarantining','copying','verifying','switching','rolling-back'}
QA_CRASH=pathlib.Path('/var/lib/drives-helper/qa-crash-at')
RETURN_ROOT=pathlib.Path('/.drives-return')


class Unsafe(Failure):
    """Restoration itself is uncertain: keep the gate closed and both copies."""


LOG=pathlib.Path('/var/lib/drives-helper/maintenance.log')


class NoScreen:
    """Stand-in while the full-screen progress is off (or failed)."""
    active=False
    def step(self,*a,**k):pass
    def note(self,*a,**k):pass
    def totals(self,*a,**k):pass
    def counted(self,*a,**k):pass
    def finish(self,*a,**k):pass


SCREEN=NoScreen()


def say(message):
    """Console for the person at the machine; root-private log for afterwards
    (the maintenance boot's journal is volatile).

    The unit sends stdout to the journal only and stderr to journal+console:
    while the progress screen owns the console a line goes to the journal and
    becomes the screen's activity line; otherwise it is printed on the console."""
    line='Drives: '+message
    if SCREEN.active:
        print(line,flush=True)
        SCREEN.note(message[:1].upper()+message[1:])
    else:print(line,file=sys.stderr,flush=True)
    try:
        fd=os.open(LOG,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:os.write(fd,(time.strftime('%Y-%m-%d %H:%M:%S ')+line+'\n').encode());os.fsync(fd)
        finally:os.close(fd)
    except OSError:pass


def qa_crash(stage):
    """VM-only crash injection for the crash matrix. Needs a root-private marker
    file in a KVM guest; it is consumed once and simulates a power cut."""
    try:
        if not QA_CRASH.is_file() or QA_CRASH.stat().st_uid!=0:return
        pending=read_regular(QA_CRASH,256).decode().strip().split(',')
        if not pending or pending[0]!=stage:return
        if run(['/usr/bin/systemd-detect-virt']).strip()!=b'kvm':return
        if len(pending)>1:atomic(QA_CRASH,','.join(pending[1:]).encode(),0o600)
        else:QA_CRASH.unlink();durable_directory(str(QA_CRASH.parent))
    except (OSError,Failure):return
    say('QA crash injection at '+stage)
    with open('/proc/sysrq-trigger','w') as trigger:trigger.write('b')
    time.sleep(30)


def crypttab_key(mapper):
    """Keyfile for an auto-unlock mapper; manual-unlock drives cannot be used
    unattended in the maintenance boot."""
    for line in read_regular('/etc/crypttab',65536).decode().splitlines():
        fields=line.split()
        if not fields or fields[0].startswith('#') or fields[0]!=mapper:continue
        if len(fields)<3 or not fields[2].startswith('/'):break
        key=pathlib.Path(fields[2])
        try:info=os.stat(key,follow_symlinks=False)
        except FileNotFoundError:raise Failure("this drive's unlock key is missing on this computer, so it cannot be used for a move during restart") from None
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o077:raise Failure('destination keyfile is unsafe')
        return {'mapper':mapper,'device':fields[1],'key':str(key)}
    raise Failure('destination drive must unlock with the OS (keyfile in crypttab)')


def diverged(original,dest):
    """Changes Undo would lose. Undo restores the frozen original and drops the
    copy, so any new, changed or deleted entry counts. A directory whose only
    difference is its timestamp (a file created and removed again) does not."""
    out=run(['/usr/bin/rsync','-aHAXS','--numeric-ids','--checksum','--dry-run','--itemize-changes','--delete','--',dest+'/',original+'/'],timeout=3600,cap=8*1024*1024)
    lines=[line for line in out.decode('utf-8','replace').splitlines() if line.strip()]
    return [line for line in lines if not re.match(r'^\.d\.\.t\.{6} ',line)]


def fstab_line(j):
    # No automount on the bind itself: it mounts at boot (nofail, so boot never
    # waits) and the real mount state stays observable without touching it.
    # nofail must not let profiles start on an empty placeholder while the
    # bind is still queued. A failed/missing drive leaves the mode-000 guard.
    return escape_fstab(j['dest'])+' '+escape_fstab(j['source'])+' none bind,nofail,x-systemd.before=systemd-user-sessions.service,x-systemd.requires='+escape_fstab(j['destMount'])+' 0 0'


def consume_intent(j):
    """Publish alongside the outcome, never in a later journal write."""
    intent=j.get('maintenanceIntent')
    if isinstance(intent,dict) and set(intent)=={'action','armedBootId'}:
        j['maintenanceConsumed']=dict(intent)


class Offline:
    def __init__(self,common,current_boot=None):
        self.c=common;self.boot=current_boot or boot_id();self.m=MoveManager(common)

    def stage(self,j,state):
        j['offlineBoot']=self.boot;self.m.stage(j,state);qa_crash(state)

    # --- storage the maintenance boot does not mount by itself -----------------
    def settle(self):
        """The maintenance target skips coldplug; let udevd (no other service)
        load drivers and publish device links before identities are checked."""
        run(['/usr/bin/udevadm','trigger','--type=devices','--action=add'],timeout=60)
        run(['/usr/bin/udevadm','settle','--timeout=60'],timeout=70)

    def mount_source(self,j):
        rows=mount_rows()
        if mount_for(j['source'],rows)['target']!=j['sourceMount']:
            say('mounting '+j['sourceMount'])
            run(['/usr/bin/mount',j['sourceMount']],timeout=120)
        row=mount_for(j['source'],mount_rows())
        if row['target']!=j['sourceMount'] or row['fstype'] not in ('btrfs','ext4','xfs'):raise Failure('source filesystem is not available')

    def mount_destination(self,j):
        rows=mount_rows()
        if any(r['target']==j['destMount'] and r['fstype']!='autofs' for r in rows):
            self.m.destination(j,allow_removed=j['state']=='rolling-back');return
        entry=crypttab_key(j['destMapper'])
        mapper='/dev/mapper/'+j['destMapper']
        if not os.path.exists(mapper):
            say('unlocking the destination drive')
            device=entry['device']
            if device.startswith('UUID='):device='/dev/disk/by-uuid/'+device[5:]
            if not os.path.exists(device):raise Failure('the destination drive is not connected')
            run(['/usr/bin/cryptsetup','open','--key-file',entry['key'],device,j['destMapper']],timeout=180)
            run(['/usr/bin/udevadm','settle','--timeout=30'],timeout=40)
        run(['/usr/bin/mount','-t','btrfs','-o','compress=zstd:3,nodiscard',mapper,j['destMount']],timeout=120)
        self.m.destination(j,allow_removed=j['state']=='rolling-back')

    def admitted(self,j,action):
        """Re-audit the whole system before every irreversible step."""
        from helper.maintenance_audit import WORKER_UNIT,collect_runtime,validate_snapshot
        run(['/usr/bin/udevadm','settle','--timeout=30'],timeout=40)
        last=Failure('maintenance admission failed')
        for attempt in range(3):
            snapshot=collect_runtime()
            snapshot.update(latch=maintenance.latch_request(),receipt=maintenance.control_json(maintenance.RUNTIME/'boot.json'))
            try:return validate_snapshot(snapshot,j['id'],action,caller=WORKER_UNIT)
            except Failure as error:
                last=error
                active=[u['unit']+'='+u['active'] for u in snapshot['units'] if u['active'] not in ('inactive','failed')]
                say('admission check '+str(attempt+1)+' refused: '+str(error)+' | targetActive='+str(snapshot['targetActive'])+' default='+snapshot['defaultTarget']+' masks='+str(snapshot['masksComplete'])+' jobs='+str(snapshot['jobs'])[:300]+' units='+' '.join(active)[:600])
                try:
                    detail=run(['/usr/bin/systemctl','show','drives-maintenance.target','drives-maintenance-splash.service','-p','Id,ActiveState,SubState,Result,ConditionResult'],timeout=10).decode().replace('\n',' ')
                    failed=[u['unit'] for u in snapshot['units'] if u['active']=='failed']
                    say('  detail: '+detail[:600]+' failed='+' '.join(failed)[:300])
                except Failure:pass
                time.sleep(2)
        raise last

    # --- restoring the original ------------------------------------------------
    def unbind(self,j):
        for row in reversed(mount_rows()):
            if row['target']==j['source']:
                if row['fstype']!='autofs' and not self.m.bound(j):raise Unsafe('an unexpected filesystem is mounted at the folder path')
                run(['/usr/bin/umount',j['source']],timeout=60)

    def remove_placeholder(self,j):
        path=j['source']
        if not os.path.lexists(path):return
        # Interrupted before the quarantine rename: the path is still the original.
        if not os.path.islink(path) and identity(path)[1:]==j['sourceIdentity'][1:]:return
        info=os.stat(path,follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o777 or os.listdir(path):raise Unsafe('the folder path is not our empty placeholder')
        if j.get('placeholderIdentity') and identity(path)[1:]!=j['placeholderIdentity'][1:]:raise Unsafe('placeholder identity changed')
        run(['/usr/bin/chattr','-i',path])
        os.rmdir(path);durable_directory(str(pathlib.Path(path).parent))
        j.pop('placeholderIdentity',None);self.c.journal('moves',j['id'],j)

    def put_back(self,j):
        """Rename the quarantined original back to its familiar path."""
        source=pathlib.Path(j['source']);store=j.get('quarantine')
        original=pathlib.Path(store['path'])/'original' if store else None
        if os.path.lexists(source):
            if identity(str(source))[1:]==j['sourceIdentity'][1:]:return
            raise Unsafe('something else now occupies the folder path')
        if not original or not os.path.lexists(original):raise Unsafe('the original folder cannot be found')
        if identity(str(original))[1:]!=j['sourceIdentity'][1:]:raise Unsafe('quarantined original identity changed')
        import ctypes
        with anchored_tree(str(original)) as (storefd,_,_),anchored_tree(str(source.parent)) as (_,parentfd,_):
            private_store_fd(storefd)
            rename=ctypes.CDLL(None,use_errno=True).renameat2
            rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];rename.restype=ctypes.c_int
            if rename(storefd,b'original',parentfd,os.fsencode(source.name),1)!=0:raise Unsafe('restoring the original failed: '+os.strerror(ctypes.get_errno()))
            os.fsync(storefd);os.fsync(parentfd)

    def drop_store(self,j):
        # A crash between creating the store and journalling it leaves an empty,
        # unjournalled store; the original is only renamed after journalling.
        store=j.get('quarantine') or {'path':j['sourceMount'].rstrip('/')+'/.drives-quarantine/'+j['id']}
        path=pathlib.Path(store['path'])
        try:os.rmdir(path);durable_directory(str(path.parent))
        except FileNotFoundError:pass
        except OSError as error:raise Unsafe('quarantine is not empty; the original may still be inside it') from error

    def restore(self,j,reason):
        """Return to the exact pre-move state. Destination copy is retained."""
        from helper.configwriter import write_owned
        say('restoring the original folder')
        write_owned(self.c,'fstab',j['id'],None)
        self.unbind(j);self.remove_placeholder(j);self.put_back(j);self.drop_store(j)
        if identity(j['source'])[1:]!=j['sourceIdentity'][1:]:raise Unsafe('restored original identity differs')
        if 'destContainer' in j and os.path.lexists(j['destContainer']):self.m.private_destination(j)
        # Back to the pre-move situation: original in use, destination is only a
        # disposable seed, so Continue/Cancel/Start over are all safe again.
        j['restoredFrom']=j['state']
        j.pop('quarantine',None);j.pop('cutover',None);j.pop('verification',None);j['verified']=False
        j['backup']=j['source']+'.pre-move';j['interruptedState']='awaiting-maintenance'
        consume_intent(j);self.m.stage(j,'paused');j['error']=reason;self.c.journal('moves',j['id'],j)

    # --- Continue ----------------------------------------------------------------
    def continue_move(self,j):
        if j['state']=='switched':return 'already moved'
        if j['state'] in OFFLINE:
            try:
                self.mount_source(j)
                self.restore(j,'Interrupted while '+j['state']+'. Your original folder is back in place and unchanged; nothing was deleted.')
            except Unsafe:raise
            except BaseException as error:raise Unsafe('could not restore after interruption: '+str(error)[:200]) from error
            return 'restored after interruption'
        if j['state'] not in ('awaiting-maintenance','paused'):raise Failure('move is not waiting for maintenance')
        if 'destContainer' not in j:raise Failure('legacy plan has no private destination wrapper; retain both copies, cancel the untouched plan and assess a new move')
        self.mount_source(j);self.mount_destination(j)
        self.m.private_destination(j)
        self.m.changed(j['source'],j['sourceIdentity'],j.get('sourceUUID'))
        self.admitted(j,'continue')
        if any(r['target']==j['source'] or r['target'].startswith(j['source']+'/') for r in mount_rows()):raise Failure('nested or existing mount blocks the move')
        SCREEN.step('check')
        stats=tree_stats(j['source'],j['uid'],progress=SCREEN.counted);closed_hardlinks(j['source'])
        SCREEN.totals(files=stats['files'],total_bytes=stats['bytes'],entries=getattr(getattr(SCREEN,'state',None),'counted',0))
        v=os.statvfs(j['destMount'])
        if v.f_bavail*v.f_frsize*5<stats['bytes']*6:raise Failure('destination needs at least 1.2x source apparent size free')
        try:
            # 1. Quarantine: the original becomes unreachable at its usual path.
            self.stage(j,'quarantining')
            with anchored_tree(j['source']) as (_,fd,_):
                store=prepare_store(fd,j['sourceMount'],j['id'])
            j['quarantine']=store;j['backup']=store['path']+'/original';self.c.journal('moves',j['id'],j)
            qa_crash('quarantine-prepared')
            moved=move_original(j['source'],j['sourceIdentity'],store)
            original=moved['path']
            # 2. Copy the frozen original.
            self.stage(j,'copying');SCREEN.step('copy',total_bytes=stats['bytes']);say('copying '+str(stats['files'])+' files')
            self.m.private_destination(j)
            run(['/usr/bin/rsync','-aHAXS','--numeric-ids','--delete','--',original+'/',j['dest']+'/'],timeout=24*3600,cap=8*1024*1024)
            self.m.private_destination(j)
            # 3. Full verification: checksums, metadata and counts.
            self.stage(j,'verifying');SCREEN.step('verify',total_bytes=stats['bytes']);say('verifying every file')
            verification=self.m.verify(original,j['dest'],**screen_kw())
            run(['/usr/bin/sync','-f',j['dest']],timeout=600)
            j['verification']=verification;j['verified']=True;self.c.journal('moves',j['id'],j)
            # 4. Switch: locked placeholder, fstab bind, live proof.
            SCREEN.step('switch')
            self.admitted(j,'continue')
            self.stage(j,'switching')
            self.m.placeholder(j);qa_crash('placeholder')
            from helper.configwriter import write_owned
            write_owned(self.c,'fstab',j['id'],fstab_line(j));qa_crash('fstab')
            run(['/usr/bin/mount','--bind',j['dest'],j['source']],timeout=60)
            self.m.smoke(j)
            run(['/usr/bin/umount',j['source']],timeout=60)
            j['cutover']={'bootId':self.boot,'time':time.time()};j['boot_id']=self.boot
            consume_intent(j);self.m.stage(j,'switched');qa_crash('switched')
            say('moved; the familiar path now opens the encrypted drive')
            return 'moved'
        except Unsafe:raise
        except BaseException as error:
            try:self.restore(j,'Move stopped: '+str(error)[:200]+'. Your original folder is back in place.')
            except BaseException as again:raise Unsafe('could not restore after failure: '+str(again)[:200]) from error
            return 'restored after failure'

    # --- Move latest data back (never Undo's frozen original) ----------------------
    def return_root(self,j):
        return RETURN_ROOT if j['sourceMount']=='/' else pathlib.Path(j['sourceMount'])/RETURN_ROOT.name

    def mount_return_origin(self,j):
        if j.get('sourceMount') not in ('/','/home'):raise Failure('unsupported original OS mount')
        parent=str(safe_path(j['source']).parent)
        if mount_for(parent,mount_rows())['target']!=j['sourceMount']:
            if j['sourceMount']!='/home':raise Failure('original OS root is unavailable')
            run(['/usr/bin/mount','/home'],timeout=120)
        self.m.return_origin(j)

    def prepare_return(self,j):
        """New private staging; unknown leftovers are retained, never adopted."""
        if os.geteuid()!=0:raise Failure('Move back worker requires root')
        root=self.return_root(j)
        maintenance.private_directory(root.parent)
        try:
            os.mkdir(root,0o700);os.chmod(root,0o700)
            durable_directory(str(root.parent))
        except FileExistsError:pass
        with anchored_tree(str(root)) as (_,rootfd,_):
            private_container_fd(rootfd)
            parent=identity(str(pathlib.Path(j['source']).parent));rootid=identity_fd(rootfd)
            if parent[0]!=rootid[0] or parent[2:]!=rootid[2:]:raise Unsafe('return staging filesystem or subvolume differs')
            name=j['id']+'-'+uuid.uuid4().hex
            os.mkdir(name,0o700,dir_fd=rootfd)
            storefd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=rootfd)
            try:
                os.fchmod(storefd,0o700);saved=private_container_fd(storefd)
                os.fsync(storefd);os.fsync(rootfd)
            finally:os.close(storefd)
        j['returnStore']={'path':str(root/name),'identity':saved,'rootIdentity':rootid}
        self.c.journal('moves',j['id'],j);qa_crash('return-store')
        content=j['returnStore']['path']+'/content'
        os.mkdir(content,0o700);durable_directory(content);durable_directory(j['returnStore']['path'])
        j['returnContentIdentity']=identity(content);self.c.journal('moves',j['id'],j)
        return content

    def return_store(self,j,allow_incomplete=False):
        store=j['returnStore'];path=safe_path(store['path'])
        root=self.return_root(j)
        if path.parent!=root or not re.fullmatch(j['id']+'-[a-f0-9]{32}',path.name):raise Unsafe('return staging layout changed')
        with anchored_tree(str(root)) as (_,fd,_):
            actual=private_container_fd(fd)
            if actual[1:]!=store['rootIdentity'][1:]:raise Unsafe('return staging root identity changed')
        with anchored_tree(str(path)) as (_,fd,_):
            actual=private_container_fd(fd)
            if actual[1:]!=store['identity'][1:]:raise Unsafe('return staging identity changed')
            parent=identity(str(pathlib.Path(j['source']).parent))
            if actual[0]!=parent[0] or actual[2:]!=parent[2:]:raise Unsafe('return filesystem changed')
            if set(os.listdir(fd))-{'content'}:raise Unsafe('unexpected data in return staging wrapper')
            if not allow_incomplete:
                expected=[j['returnContentIdentity'][1:]]
                if j['state'] in ('return-switching','return-finishing'):expected.append(j['placeholderIdentity'][1:])
                child=os.open('content',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                try:
                    held=identity_fd(child)
                    if held[1:] not in expected or held[0]!=actual[0] or held[2:]!=actual[2:]:raise Unsafe('return staging content identity changed')
                finally:os.close(child)
        return str(path/'content')

    def return_placeholder(self,j,path=None):
        path=j['source'] if path is None else path
        with anchored_tree(path) as (_,fd,_):
            info=os.fstat(fd)
            if identity_fd(fd)[1:]!=j['placeholderIdentity'][1:] or info.st_uid!=0 or info.st_mode&0o777 or os.listdir(fd):
                raise Unsafe('folder is not the recorded owned empty placeholder')

    def pause_return(self,j):
        # Before the exchange the SSD is authoritative. Do NOT automatically
        # restart a partial copy, and never restore the frozen previous original.
        if not self.m.bound(j):self.return_placeholder(j)
        if j.get('returnStore'):
            self.return_store(j,allow_incomplete=True)
            j.setdefault('retainedReturnStages',[]).append(j['returnStore'])
        for key in ('returnStore','returnContentIdentity','returnVerification'):j.pop(key,None)
        state=j.get('returnFrom')
        if state not in ('switched','cleaned'):raise Unsafe('return recovery has no original completed state')
        consume_intent(j);self.m.stage(j,state)
        j['error']='Move back was interrupted before switching. SSD data is still active; private staging was retained. Request Move back again to retry.'
        self.c.journal('moves',j['id'],j);qa_crash('return-paused')
        return 'return paused; SSD remains active'

    def finish_return(self,j):
        self.m.return_origin(j);self.admitted(j,'return')
        self.m.return_admission(j,offline=True,full=False);self.unbind(j)
        content=self.return_store(j)
        self.m.changed(j['source'],j['returnContentIdentity'],j['sourceUUID'])
        self.return_placeholder(j,content)
        present=self.m.return_config(j,allow_removed=True)
        # Before removal SSD stays authoritative, including edits after a failed
        # return followed by a normal boot. After removal the complete local
        # tree is authoritative: never overwrite new local edits with stale SSD.
        if present:self.m.verify(j['dest'],j['source'])
        elif j['state']!='return-finishing':raise Unsafe('fstab disappeared before the journalled finishing intent')
        j['returnedIdentity']=identity(j['source']);self.stage(j,'return-finishing')
        if present:
            from helper.configwriter import write_owned
            write_owned(self.c,'fstab',j['id'],None);qa_crash('return-fstab')
        if self.m.return_config(j,allow_removed=True):raise Unsafe('owned bind remains configured')
        j['returnedBoot']=self.boot;j['retainedSSD']=j['dest'];j.pop('needsAttention',None)
        consume_intent(j);self.m.stage(j,'returned');qa_crash('returned');say('latest data returned; SSD and previous original copies were retained')
        return 'returned'

    def exchange_return(self,j,content):
        """Atomic exchange: no missing-path window and no partial data exposed."""
        self.m.return_admission(j,offline=True,full=False)
        if content!=self.return_store(j):raise Unsafe('return staging path changed')
        self.m.return_origin(j);self.m.private_destination(j)
        self.unbind(j)
        with anchored_tree(j['source']) as (parent,placeholder,name),anchored_tree(content) as (store,data,_):
            held_parent=identity_fd(parent);self.m.changed(str(pathlib.Path(j['source']).parent),j['parentIdentity'],j['sourceUUID'])
            p=identity_fd(placeholder);d=identity_fd(data);info=os.fstat(placeholder)
            if p[1:]!=j['placeholderIdentity'][1:] or info.st_uid!=0 or info.st_mode&0o777 or os.listdir(placeholder):raise Unsafe('folder is not the recorded owned empty placeholder')
            if private_container_fd(store)[1:]!=j['returnStore']['identity'][1:]:raise Unsafe('return staging identity changed at exchange')
            if d[1:]!=j['returnContentIdentity'][1:] or p[0]!=d[0] or p[2:]!=d[2:]:raise Unsafe('return content identity or filesystem changed')
            if held_parent[1:]!=j['parentIdentity'][1:]:raise Unsafe('original parent identity changed')
            run(['/usr/bin/chattr','-i',j['source']])
            self.m.changed(str(pathlib.Path(j['source']).parent),j['parentIdentity'],j['sourceUUID'])
            entry=os.stat(name,dir_fd=parent,follow_symlinks=False)
            candidate=os.stat('content',dir_fd=store,follow_symlinks=False)
            if (entry.st_dev,entry.st_ino)!=(p[0],p[1]) or (candidate.st_dev,candidate.st_ino)!=(d[0],d[1]):
                raise Unsafe('placeholder or return content name changed before exchange')
            rename=ctypes.CDLL(None,use_errno=True).renameat2
            rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];rename.restype=ctypes.c_int
            if rename(store,b'content',parent,os.fsencode(name),2)!=0:raise Unsafe('atomic return exchange failed: '+os.strerror(ctypes.get_errno()))
            os.fsync(parent);os.fsync(store)
        qa_crash('return-exchanged')

    def return_move(self,j):
        if os.geteuid()!=0:raise Failure('Move back worker requires root')
        if j['state']=='returned':return 'already returned'
        self.mount_return_origin(j);self.mount_destination(j)
        self.admitted(j,'return')
        latest=self.m.return_admission(j,offline=True,full=j['state'] not in maintenance.RETURN_STATES)
        if latest:SCREEN.totals(files=latest['files'],total_bytes=latest['bytes'])
        try:
            if j['state'] in ('return-preparing','return-copying','return-verifying'):return self.pause_return(j)
            if j['state'] in ('return-switching','return-finishing'):
                content=self.return_store(j)
                self.unbind(j)
                if identity(j['source'])[1:]==j['placeholderIdentity'][1:]:
                    if j['state']!='return-switching' or not j.get('returnVerification'):raise Unsafe('return switch has no saved verification')
                    self.m.changed(content,j['returnContentIdentity'],j['sourceUUID'])
                    self.m.verify(j['dest'],content);self.admitted(j,'return')
                    self.exchange_return(j,content)
                return self.finish_return(j)
            j['returnFrom']=j['state']
            self.stage(j,'return-preparing');content=self.prepare_return(j)
            self.stage(j,'return-copying');SCREEN.step('copy');say('copying the latest SSD data back to the original disk')
            self.m.private_destination(j);self.return_store(j)
            run(['/usr/bin/rsync','-aHAXS','--numeric-ids','--delete','--',j['dest']+'/',content+'/'],timeout=24*3600,cap=8*1024*1024)
            self.m.private_destination(j);self.return_store(j)
            self.stage(j,'return-verifying');SCREEN.step('verify')
            j['returnVerification']=self.m.verify(j['dest'],content,**screen_kw())
            run(['/usr/bin/sync','-f',content],timeout=600)
            self.c.journal('moves',j['id'],j)
            self.admitted(j,'return');self.m.return_admission(j,offline=True,full=False)
            SCREEN.step('switch');self.stage(j,'return-switching');self.exchange_return(j,content)
            return self.finish_return(j)
        except Unsafe:raise
        except BaseException as error:raise Unsafe('Move back interrupted; all copies retained: '+str(error)[:200]) from error

    def complete_consumed(self,j,action):
        """Re-prove authoritative storage, then clear a surviving latch only.

        A paused operation must not become a fresh copy after a second cut.
        Completion is not trusted merely because its journal says so.
        """
        try:
            self.admitted(j,action)
            if j['state']=='returned' and action=='return':
                self.mount_return_origin(j)
                self.m.changed(j['source'],j['returnedIdentity'],j['sourceUUID'])
                if self.m.return_config(j,allow_removed=True):raise Unsafe('returned folder still has a configured bind')
            elif j['state'] in ('switched','cleaned'):
                self.mount_source(j);self.mount_destination(j);self.m.destination(j)
                if not j.get('verified') or not self.m.return_config(j):raise Unsafe('active copy or owned bind cannot be established')
                if not self.m.bound(j):self.return_placeholder(j)
                return 'request already handled; SSD remains active'
            elif j['state'] in ('rolled-back','paused','awaiting-maintenance','planned'):
                self.mount_source(j)
                self.m.changed(j['source'],j['sourceIdentity'],j.get('sourceUUID'))
                if j.get('parentIdentity'):self.m.changed(str(pathlib.Path(j['source']).parent),j['parentIdentity'],j.get('sourceUUID'))
                from helper.moves import read_regular as read_config
                for line in read_config('/etc/fstab',65536).decode().splitlines():
                    fields=line.split()
                    if not fields or fields[0].startswith('#') or len(fields)<2:continue
                    target=re.sub(r'\\([0-7]{3})',lambda m:chr(int(m[1],8)),fields[1])
                    if os.path.normpath(target)==j['source']:raise Unsafe('original folder still has a configured mount')
            else:raise Unsafe('consumed request has an unresolved storage state')
            if any(r['target']==j['source'] or r['target'].startswith(j['source']+'/') for r in mount_rows()):raise Unsafe('unexpected mount occupies the restored local folder')
            return 'request already handled; local folder remains active'
        except Unsafe:raise
        except (Failure,OSError,ValueError,KeyError) as error:raise Unsafe('could not prove completed request: '+str(error)[:160]) from error

    # --- Undo --------------------------------------------------------------------
    def rollback_move(self,j):
        if j['state']=='rolled-back':return 'already undone'
        if j.get('maintenanceProtocol')!=2 or j['state'] not in ('switched','rolling-back'):raise Failure('only a completed move can be undone')
        self.mount_source(j);self.mount_destination(j)
        original=j['backup']
        if j['state']=='switched':
            self.admitted(j,'rollback')
            self.m.changed(original,j['sourceIdentity'],j.get('sourceUUID'))
            SCREEN.step('verify',total_bytes=(j.get('verification') or {}).get('bytes'))
            changes=diverged(original,j['dest'])
            if changes:
                j['error']='Undo refused: '+str(len(changes))+' file(s) changed after the move and would be lost. The move is kept.';consume_intent(j);self.c.journal('moves',j['id'],j)
                return 'undo refused: destination diverged'
            self.stage(j,'rolling-back')
        SCREEN.step('switch')
        # From here on the only safe direction is forward: finish restoring.
        try:
            from helper.configwriter import write_owned
            write_owned(self.c,'fstab',j['id'],None)
            self.unbind(j);self.remove_placeholder(j);self.put_back(j);self.drop_store(j)
            if identity(j['source'])[1:]!=j['sourceIdentity'][1:]:raise Unsafe('restored original identity differs')
            if 'destContainer' in j:
                from helper.destination import discard_private
                self.m.private_layout(j)
                if os.path.lexists(j['destContainer']):
                    discard_private(j['destContainer'],{'identity':j['destIdentity'],'containerIdentity':j['containerIdentity']},missing_ok=True)
            elif os.path.lexists(j['dest']):
                with anchored_tree(j['dest']) as (parent,leaf,name):
                    if identity_fd(leaf)[1:]!=j['destIdentity'][1:]:raise Unsafe('destination copy changed')
                    delete_tree_fd(leaf);os.rmdir(name,dir_fd=parent);os.fsync(parent)
            j.pop('quarantine',None);j.pop('cutover',None);j['verified']=False;j['backup']=j['source']+'.pre-move'
            consume_intent(j);self.m.stage(j,'rolled-back');qa_crash('rolled-back')
        except Unsafe:raise
        except BaseException as error:raise Unsafe('undo interrupted: '+str(error)[:200]) from error
        say('undone; the original folder is back in place')
        return 'undone'


def screen_kw():
    return {'screen':SCREEN} if SCREEN.active else {}


def start_screen(action,source):
    """Best effort: the progress screen never decides anything about the move."""
    global SCREEN
    try:
        from helper.bootscreen import Screen
        home=re.sub(r'^/home/[^/]+(?=/|$)','~',source)
        title={'continue':'Moving '+home,'rollback':'Undoing the move of '+home,'return':'Moving '+home+' back'}.get(action,home)
        screen=Screen(action,title)
        if screen.open():SCREEN=screen
    except Exception:SCREEN=NoScreen()


def end_screen(ok,message='',headline='',footer=''):
    global SCREEN
    screen=SCREEN
    try:screen.finish(ok,message,headline=headline,footer=footer)
    except Exception:pass
    if not ok and screen.active:
        # Leave the explanation on screen; normal startup stays blocked or the
        # machine restarts next, and the same text is in the journal and log.
        return
    try:
        if screen.active:screen.close()
    except Exception:pass
    SCREEN=NoScreen()


def main():
    if os.geteuid()!=0:raise SystemExit('maintenance worker requires root')
    current=boot_id()
    try:
        latch=maintenance.request()
        receipt=maintenance.control_json(maintenance.RUNTIME/'boot.json')
        if receipt.get('bootId')!=current or receipt.get('valid') is not True:raise Unsafe('maintenance boot receipt is invalid')
    except BaseException as error:
        # This may be recovery from an earlier mutation. An invalid receipt is
        # never proof that the familiar folder is safe for profile consumers.
        say('maintenance request cannot be established: '+str(error)[:200]+'. Normal startup remains blocked. Keep all copies and request administrator recovery.')
        return 2
    c=Common()
    with c.storage_lock():
        j=c.read('moves',latch['moveId'])
        worker=Offline(c,current)
        start_screen(latch['action'],j['source'])
        say({'continue':'moving ','rollback':'undoing the move of ','return':'returning latest data to '}[latch['action']]+j['source']+'. Do not turn off the computer.')
        try:
            worker.settle()
            if maintenance.consumed_request(latch,j):
                outcome=worker.complete_consumed(j,latch['action'])
                if j.get('needsAttention'):
                    j.pop('needsAttention',None);j['error']='';c.journal('moves',j['id'],j)
            else:
                j['maintenanceIntent']={'action':latch['action'],'armedBootId':latch['armedBootId']}
                c.journal('moves',j['id'],j)
                operation={'continue':worker.continue_move,'rollback':worker.rollback_move,'return':worker.return_move}[latch['action']]
                outcome=operation(j)
        except Unsafe as error:
            # Never trade a blank maintenance screen for profile startup on a
            # missing or ambiguous folder. Retain the durable boot barrier.
            j=c.read('moves',latch['moveId']);j['needsAttention']=True
            j['error']='Needs attention: '+str(error)[:200]+'. Normal startup is blocked; all copies are retained. Original: '+j.get('backup','?')+' · copy: '+j['dest']
            c.journal('moves',j['id'],j)
            say(j['error']);end_screen(False,j['error'],'Needs attention')
            return 1
        except (Failure,OSError,ValueError,KeyError) as error:
            j=c.read('moves',latch['moveId'])
            # Mount/admission can fail before entering a recovery method's
            # try block, even when an earlier boot already mutated storage.
            if j['state'] in OFFLINE|maintenance.RETURN_STATES:
                j['needsAttention']=True
                j['error']='Recovery could not be established: '+str(error)[:160]+'. Normal startup is blocked; all copies are retained.'
                c.journal('moves',j['id'],j);say(j['error']);end_screen(False,j['error'],'Needs attention');return 1
            # Refused while still in a known non-mutating state.
            j['error']='Not moved this time; nothing was changed. Restart again to retry. ('+str(error)[:160]+')'
            consume_intent(j);c.journal('moves',j['id'],j);outcome='refused'
            say(j['error'])
        maintenance.clear_latch(latch['moveId'])
        say(outcome+'. Restarting.')
        good=outcome in ('moved','undone','returned','already moved','already undone','already returned') or outcome.startswith('request already handled')
        if good:end_screen(True)
        elif outcome=='refused':end_screen(False,j.get('error') or outcome,'Not moved this time','Nothing was changed. Restarting into your desktop.')
        else:end_screen(False,j.get('error') or outcome,'Stopped safely','Nothing was deleted. Restarting into your desktop.')
        if not good:time.sleep(8) # time to read why before the restart
    run(['/usr/bin/systemctl','--no-block','reboot'],timeout=30)
    return 0


if __name__=='__main__':raise SystemExit(main())
