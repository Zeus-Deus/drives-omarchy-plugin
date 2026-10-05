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
import json,os,pathlib,re,stat,subprocess,sys,time
from helper.common import Common,Failure,run,mount_rows,mount_for,boot_id,escape_fstab,read_regular
from helper import maintenance
from helper.moves import (MoveManager,anchored_tree,delete_tree_fd,durable_directory,identity,
    identity_fd,safe_path,tree_stats)
from helper.quarantine import closed_hardlinks,move_original,prepare_store,private_store_fd

OFFLINE={'quarantining','copying','verifying','switching','rolling-back'}
QA_CRASH=pathlib.Path('/var/lib/drives-helper/qa-crash-at')


class Unsafe(Failure):
    """Restoration itself is uncertain: keep the gate closed and both copies."""


LOG=pathlib.Path('/var/lib/drives-helper/maintenance.log')


def say(message):
    """Console for the person at the machine; root-private log for afterwards
    (the maintenance boot's journal is volatile)."""
    line='Drives: '+message
    print(line,flush=True)
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
        if read_regular(QA_CRASH,64).decode().strip()!=stage:return
        if run(['/usr/bin/systemd-detect-virt']).strip()!=b'kvm':return
        QA_CRASH.unlink();durable_directory(str(QA_CRASH.parent))
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
    return escape_fstab(j['dest'])+' '+escape_fstab(j['source'])+' none bind,nofail,x-systemd.requires='+escape_fstab(j['destMount'])+' 0 0'


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
            self.m.destination(j);return
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
        self.m.destination(j)

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
        # Back to the pre-move situation: original in use, destination is only a
        # disposable seed, so Continue/Cancel/Start over are all safe again.
        j['restoredFrom']=j['state']
        j.pop('quarantine',None);j.pop('cutover',None);j.pop('verification',None);j['verified']=False
        j['backup']=j['source']+'.pre-move';j['interruptedState']='awaiting-maintenance'
        self.m.stage(j,'paused');j['error']=reason;self.c.journal('moves',j['id'],j)

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
        self.mount_source(j);self.mount_destination(j)
        self.m.changed(j['source'],j['sourceIdentity'],j.get('sourceUUID'))
        self.admitted(j,'continue')
        stats=tree_stats(j['source'],j['uid']);closed_hardlinks(j['source'])
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
            self.stage(j,'copying');say('copying '+str(stats['files'])+' files')
            run(['/usr/bin/rsync','-aHAXS','--numeric-ids','--delete','--',original+'/',j['dest']+'/'],timeout=24*3600,cap=8*1024*1024)
            # 3. Full verification: checksums, metadata and counts.
            self.stage(j,'verifying');say('verifying every file')
            verification=self.m.verify(original,j['dest'])
            run(['/usr/bin/sync','-f',j['dest']],timeout=600)
            j['verification']=verification;j['verified']=True;self.c.journal('moves',j['id'],j)
            # 4. Switch: locked placeholder, fstab bind, live proof.
            self.admitted(j,'continue')
            self.stage(j,'switching')
            self.m.placeholder(j);qa_crash('placeholder')
            from helper.configwriter import write_owned
            write_owned(self.c,'fstab',j['id'],fstab_line(j));qa_crash('fstab')
            run(['/usr/bin/mount','--bind',j['dest'],j['source']],timeout=60)
            self.m.smoke(j)
            run(['/usr/bin/umount',j['source']],timeout=60)
            j['cutover']={'bootId':self.boot,'time':time.time()};j['boot_id']=self.boot
            self.m.stage(j,'switched')
            say('moved; the familiar path now opens the encrypted drive')
            return 'moved'
        except Unsafe:raise
        except BaseException as error:
            try:self.restore(j,'Move stopped: '+str(error)[:200]+'. Your original folder is back in place.')
            except BaseException as again:raise Unsafe('could not restore after failure: '+str(again)[:200]) from error
            return 'restored after failure'

    # --- Undo --------------------------------------------------------------------
    def rollback_move(self,j):
        if j['state']=='rolled-back':return 'already undone'
        if j.get('maintenanceProtocol')!=2 or j['state'] not in ('switched','rolling-back'):raise Failure('only a completed move can be undone')
        self.mount_source(j);self.mount_destination(j)
        original=j['backup']
        if j['state']=='switched':
            self.admitted(j,'rollback')
            self.m.changed(original,j['sourceIdentity'],j.get('sourceUUID'))
            changes=diverged(original,j['dest'])
            if changes:
                j['error']='Undo refused: '+str(len(changes))+' file(s) changed after the move and would be lost. The move is kept.';self.c.journal('moves',j['id'],j)
                return 'undo refused: destination diverged'
            self.stage(j,'rolling-back')
        # From here on the only safe direction is forward: finish restoring.
        try:
            from helper.configwriter import write_owned
            write_owned(self.c,'fstab',j['id'],None)
            self.unbind(j);self.remove_placeholder(j);self.put_back(j);self.drop_store(j)
            if identity(j['source'])[1:]!=j['sourceIdentity'][1:]:raise Unsafe('restored original identity differs')
            if os.path.lexists(j['dest']):
                with anchored_tree(j['dest']) as (parent,leaf,name):
                    if identity_fd(leaf)[1:]!=j['destIdentity'][1:]:raise Unsafe('destination copy changed')
                    delete_tree_fd(leaf);os.rmdir(name,dir_fd=parent);os.fsync(parent)
            j.pop('quarantine',None);j.pop('cutover',None);j['verified']=False;j['backup']=j['source']+'.pre-move'
            self.m.stage(j,'rolled-back')
        except Unsafe:raise
        except BaseException as error:raise Unsafe('undo interrupted: '+str(error)[:200]) from error
        say('undone; the original folder is back in place')
        return 'undone'


def main():
    if os.geteuid()!=0:raise SystemExit('maintenance worker requires root')
    current=boot_id()
    try:
        latch=maintenance.request()
        receipt=maintenance.control_json(maintenance.RUNTIME/'boot.json')
        if receipt.get('bootId')!=current or receipt.get('valid') is not True:raise Unsafe('maintenance boot receipt is invalid')
    except BaseException as error:
        # Nothing has been touched yet. Keep the request for inspection under a
        # different name (the boot gate only reads the exact latch path) and
        # boot normally rather than stranding the user at a blank screen.
        say('maintenance request is invalid: '+str(error)[:200]+'. Nothing was changed.')
        if os.path.lexists(maintenance.LATCH):
            os.rename(maintenance.LATCH,str(maintenance.LATCH).replace('.json','.invalid-'+str(int(time.time()))+'.json'))
            durable_directory(str(maintenance.LATCH.parent))
        run(['/usr/bin/systemctl','--no-block','reboot'],timeout=30)
        return 2
    c=Common()
    with c.storage_lock():
        j=c.read('moves',latch['moveId'])
        worker=Offline(c,current)
        say(('moving ' if latch['action']=='continue' else 'undoing the move of ')+j['source']+'. Do not turn off the computer.')
        try:
            worker.settle()
            outcome=worker.continue_move(j) if latch['action']=='continue' else worker.rollback_move(j)
        except Unsafe as error:
            # There is no console to inspect from here, so holding the gate
            # would only strand the user at a blank screen. Keep both copies,
            # record exactly where they are and boot normally; nothing further
            # happens to this move until the user asks again.
            j=c.read('moves',latch['moveId']);j['needsAttention']=True
            j['error']='Needs attention: '+str(error)[:200]+'. Nothing was deleted. Original: '+j.get('backup','?')+' · copy: '+j['dest']
            c.journal('moves',j['id'],j);outcome='stopped safely'
            say(j['error'])
        except (Failure,OSError,ValueError,KeyError) as error:
            # Refused before any change (identity, space, exclusion): nothing moved.
            # Changes after quarantine are handled inside continue/rollback.
            j=c.read('moves',latch['moveId'])
            j['error']='Not moved this time; nothing was changed. Restart again to retry. ('+str(error)[:160]+')'
            c.journal('moves',j['id'],j);outcome='refused'
            say(j['error'])
        maintenance.clear_latch(latch['moveId'])
        say(outcome+'. Restarting.')
    run(['/usr/bin/systemctl','--no-block','reboot'],timeout=30)
    return 0


if __name__=='__main__':raise SystemExit(main())
