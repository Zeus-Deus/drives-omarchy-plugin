"""Fail-closed folder copy/verify/bind with inspect-only crash recovery."""
import contextlib,hashlib,os,pathlib,pwd,shutil,stat,time,uuid
from helper.common import Failure,run,mount_rows,mount_for,escape_fstab,read_regular
from topology import probe,blocks

ACTIVE={'planned','copying','verifying','switching','testing','cleaning'}
PROTECTED={'.hermes','.config','.ssh','.gnupg','.mozilla','.password-store','keyrings','chromium','google-chrome','firefox','postgres','postgresql','mysql','mariadb'}

def identity(path):
    s=os.stat(path,follow_symlinks=False)
    if not stat.S_ISDIR(s.st_mode):raise Failure('expected real directory')
    return [s.st_dev,s.st_ino]

def safe_path(path):
    if not isinstance(path,str) or not path.startswith('/') or '\0' in path or '\n' in path:raise Failure('invalid absolute path')
    p=pathlib.Path(path)
    if str(p)!=path or '..' in p.parts:raise Failure('non-canonical path')
    for ancestor in reversed((p,)+tuple(p.parents)):
        if ancestor.is_symlink():raise Failure('symlinked path refused')
    return p

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
                except OSError:continue
                if target==path or target.startswith(path+'/'):
                    found.append({'pid':int(proc.name),'name':(proc/'comm').read_text().strip()});break
        except (OSError,PermissionError):continue
    return found[:24]

def tree_stats(path,uid=None):
    count=0;size=0;directories=0;h=hashlib.sha256();groups=set()
    if uid is not None:
        user=pwd.getpwuid(uid);groups=set(os.getgrouplist(user.pw_name,user.pw_gid))
    # Sort within each directory, not one million entries in memory.
    for root,dirs,files in os.walk(path,topdown=True,followlinks=False):
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
    def __init__(self,common,uid=None):self.c=common;self.uid=uid
    def stage(self,j,state):
        j['state']=state;j['updated']=time.time();self.c.journal('moves',j['id'],j)
    def changed(self,path,want):
        if identity(path)!=want:raise Failure('directory identity changed; inspect before continuing')
    def preflight(self,src,destMount):
        protected(src);source=safe_path(src);target=safe_path(destMount)
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
    def start(self,src,destMount):
        stats,topology,uid=self.preflight(src,destMount)
        id=uuid.uuid4().hex;dest=destMount+'/drives-'+id;backup=src+'.pre-move'
        if os.path.lexists(backup):raise Failure('old-copy path already exists')
        j={'id':id,'state':'planned','source':src,'destMount':destMount,'dest':dest,'backup':backup,
           'sourceIdentity':identity(src),'mountIdentity':identity(destMount),'diskSerial':topology['disk'].get('serial'),
           'uid':uid,'stats':stats,'boot_id':self.c.boot_id(),'verified':False,'created':time.time()}
        self.stage(j,'planned');os.mkdir(dest,0o700);j['destIdentity']=identity(dest);self.c.journal('moves',id,j)
        return self.execute(j)
    def destination(self,j):
        safe_path(j['destMount']);safe_path(j['dest']);self.changed(j['destMount'],j['mountIdentity']);self.changed(j['dest'],j['destIdentity'])
        t=probe(j['destMount'],resolve=False)
        if not t['supported'] or not t.get('encrypted') or t['disk'].get('serial')!=j['diskSerial']:raise Failure('destination disk missing or changed')
    def verify(self,src,dest):
        difference=run(['rsync','-aHAXS','--numeric-ids','--checksum','--dry-run','--itemize-changes','--delete','--',src+'/',dest+'/'],timeout=3600)
        if difference:raise Failure('checksum or metadata comparison differs; original is still kept')
        a=tree_stats(src);b=tree_stats(dest)
        if a!=b:raise Failure('file count, bytes or metadata differ')
        return a
    def bound(self,j):
        try:
            row=mount_for(j['source']);return row['target']==j['source'] and identity(j['source'])==j['destIdentity']
        except (Failure,OSError):return False
    def smoke(self,j):
        if not self.bound(j):raise Failure('expected bind is not active')
        name=j['source']+'/.drives-read-write-'+uuid.uuid4().hex
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:os.write(fd,b'Drives mount verification');os.fsync(fd)
        finally:os.close(fd)
        try:
            if read_regular(name)!=b'Drives mount verification':raise Failure('mount read/write check failed')
        finally:os.unlink(name)
    def placeholder(self,j):
        path=j['source']
        if not os.path.lexists(path):os.mkdir(path,0o000)
        elif not self.bound(j):
            if os.listdir(path):raise Failure('placeholder is not empty; refusing overwrite')
            os.chmod(path,0o000)
        if not self.bound(j):run(['chattr','+i',path]);j['placeholderIdentity']=identity(path)
    def execute(self,j):
        try:
            self.destination(j)
            switched=os.path.lexists(j['backup'])
            if not switched:
                self.changed(j['source'],j['sourceIdentity'])
                self.preflight(j['source'],j['destMount'])
                self.stage(j,'copying')
                run(['rsync','-aHAXS','--numeric-ids','--delete','--',j['source']+'/',j['dest']+'/'],timeout=3600)
                self.stage(j,'verifying');j['cutover']=self.verify(j['source'],j['dest']);j['verified']=True
                self.stage(j,'switching');self.changed(j['source'],j['sourceIdentity'])
                if open_users(j['source']):raise Failure('a process reopened the source; switch refused')
                if os.path.lexists(j['backup']):raise Failure('old-copy path appeared')
                os.rename(j['source'],j['backup'])
                parent=os.open(pathlib.Path(j['source']).parent,os.O_DIRECTORY);os.fsync(parent);os.close(parent)
            else:
                self.changed(j['backup'],j['sourceIdentity'])
                # No write has been promised since an interrupted switch. Fully re-verify.
                self.stage(j,'verifying');j['cutover']=self.verify(j['backup'],j['dest']);j['verified']=True
                self.stage(j,'switching')
            self.placeholder(j)
            if not self.bound(j):
                from helper.common import host_mount
                host_mount(j['dest'],j['source'],'none','bind')
            self.c.config('/etc/fstab',j['id'],escape_fstab(j['dest'])+' '+escape_fstab(j['source'])+' none bind,nofail,x-systemd.requires-mounts-for='+escape_fstab(j['destMount'])+' 0 0')
            run(['systemctl','daemon-reload']);self.stage(j,'testing');self.smoke(j)
            self.stage(j,'switched');return {'ok':True,'id':j['id'],'state':'switched'}
        except BaseException as exc:
            j['interruptedState']=j['state'];j['error']=str(exc)[:300];self.stage(j,'paused');raise
    def resume(self,id):
        j=self.c.read('moves',id)
        if j['state'] in ('cleaned','rolled-back'):raise Failure('move is already finished')
        if j['state']=='cleaning':return self.delete_old(id)
        if j['state']=='switched' and self.bound(j):return {'ok':True,'id':id,'state':'switched'}
        return self.execute(j)
    def rollback(self,id):
        j=self.c.read('moves',id);self.destination(j)
        if j['state']=='cleaned' or not os.path.lexists(j['backup']):raise Failure('no intact old copy available to undo')
        self.changed(j['backup'],j['sourceIdentity']);self.verify(j['backup'],j['dest'])
        if open_users(j['source']):raise Failure('open files block undo')
        self.stage(j,'switching')
        self.c.config('/etc/fstab',id);run(['systemctl','daemon-reload'])
        if self.bound(j):
            unit=run(['systemd-escape','--path','--suffix=mount',j['source']]).decode().strip()
            run(['systemctl','stop',unit])
        if os.path.lexists(j['source']):
            if j.get('placeholderIdentity'):self.changed(j['source'],j['placeholderIdentity'])
            if os.listdir(j['source']):raise Failure('placeholder changed; refusing undo')
            run(['chattr','-i',j['source']]);os.rmdir(j['source'])
        os.rename(j['backup'],j['source']);self.changed(j['source'],j['sourceIdentity']);self.stage(j,'rolled-back')
        return {'ok':True,'id':id,'state':'rolled-back'}
    def delete_old(self,id):
        j=self.c.read('moves',id)
        if not j.get('verified') or j.get('boot_id')==self.c.boot_id():raise Failure('cleanup requires saved verification and a successful reboot')
        self.destination(j)
        if not self.bound(j):raise Failure('correct bind must be active after reboot')
        if j['state'] not in ('switched','rebooted','cleaning'):raise Failure('move is not eligible for cleanup')
        if os.path.lexists(j['backup']):self.changed(j['backup'],j['sourceIdentity'])
        if open_users(j['backup']):raise Failure('open old-copy files block cleanup')
        if any(r['target']==j['backup'] or r['target'].startswith(j['backup']+'/') for r in mount_rows()):raise Failure('mount in old copy blocks deletion')
        self.stage(j,'cleaning')
        if os.path.lexists(j['backup']):shutil.rmtree(j['backup'])
        self.stage(j,'cleaned');return {'ok':True,'id':id,'state':'cleaned','spaceWarning':'Btrfs snapshots may retain this data; reclaimed space is not guaranteed.'}
    def cancel(self,id):
        j=self.c.read('moves',id)
        if os.path.lexists(j['backup']):raise Failure('switch occurred; choose verified Undo instead')
        self.destination(j);self.changed(j['source'],j['sourceIdentity']);self.stage(j,'rolled-back')
        shutil.rmtree(j['dest']);return {'ok':True,'id':id,'state':'rolled-back'}
    def restart(self,id):
        j=self.c.read('moves',id)
        if os.path.lexists(j['backup']):raise Failure('cannot start over after switch')
        self.destination(j);shutil.rmtree(j['dest']);os.mkdir(j['dest'],0o700);j['destIdentity']=identity(j['dest']);j['verified']=False
        return self.execute(j)
    def inspect(self):
        results=[]
        for j in self.c.records('moves'):
            live=dict(j);live['bound']=self.bound(j) if 'destIdentity' in j else False
            live['originalAvailable']=os.path.lexists(j['source']) and not os.path.lexists(j['backup'])
            live['oldCopyAvailable']=os.path.lexists(j['backup'])
            if j['state'] in ACTIVE:
                live['interruptedState']=j['state'];live['state']='paused';live['error']='Interrupted; inspect before Continue or Undo.'
            if j['state']=='switched' and j.get('verified') and j.get('boot_id')!=self.c.boot_id() and live['bound']:
                live['state']='rebooted'
            live['canDelete']=live['state'] in ('rebooted','cleaning') and live['bound'] and j.get('verified',False)
            results.append(live)
        return results
