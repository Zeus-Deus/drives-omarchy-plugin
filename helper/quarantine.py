"""Quarantine primitives; caller must already maintain offline writer exclusion.

The root-only cleanup worker removes only a previously journalled original;
it provides no migration or writer-exclusion authority for cutover. The
normal-session MoveManager still defers all cutovers.
"""
import contextlib,ctypes,json,os,pathlib,re,stat,sys
from helper.common import Failure,mount_rows
from helper.maintenance import private_directory
from helper.moves import anchored_tree,identity,identity_fd,safe_path


def closed_hardlinks(path,allow_external=False):
    """Outside inode aliases would defeat private-directory isolation after boot.

    allow_external: the person agreed to split hard links shared with files
    outside the folder. The quarantined original then still shares those
    inodes with the outside names; that exposes nothing new (they are the
    person's own files) and Undo's change check refuses if one was edited."""
    groups={}
    def unreadable(exc):raise Failure('cannot inspect quarantine source') from exc
    for root,dirs,files in os.walk(path,followlinks=False,onerror=unreadable):
        for name in dirs+files:
            s=os.lstat(os.path.join(root,name))
            if stat.S_ISDIR(s.st_mode):continue
            # Sockets, FIFOs and device nodes are inert names here (nothing runs
            # in the maintenance boot); rsync -a recreates them exactly.
            if s.st_nlink>1:
                key=(s.st_dev,s.st_ino)
                expected,count=groups.get(key,(s.st_nlink,0))
                if expected!=s.st_nlink:raise Failure('hardlink group changed during quarantine inspection')
                groups[key]=(expected,count+1)
    if not allow_external and any(expected!=count for expected,count in groups.values()):raise Failure('external hardlink defeats private quarantine')


def private_store_fd(fd):
    s=os.fstat(fd)
    if s.st_uid!=0 or s.st_mode&0o077:raise Failure('quarantine must be root-private')
    return identity_fd(fd)


@contextlib.contextmanager
def directory_fd(path):
    if str(path)!='/':
        with anchored_tree(str(path)) as (_,fd,_):yield fd
    else:
        fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:yield fd
        finally:os.close(fd)


def prepare_store(source_fd,filesystem_root,move_id):
    """Return a durable empty store to journal BEFORE the source rename."""
    if os.geteuid()!=0:raise Failure('quarantine requires root')
    if not isinstance(move_id,str) or not re.fullmatch('[a-f0-9]{32}',move_id):raise Failure('invalid quarantine move id')
    root=private_directory(filesystem_root);source_id=identity_fd(source_fd)
    with directory_fd(root) as rootfd:
        root_id=identity_fd(rootfd)
        if source_id[0]!=root_id[0] or source_id[2]!=root_id[2]:raise Failure('quarantine must share the source filesystem and subvolume')
        if source_id==root_id:raise Failure('filesystem root cannot be quarantined')
        try:os.mkdir('.drives-quarantine',0o700,dir_fd=rootfd);os.fsync(rootfd)
        except FileExistsError:pass
        container=os.open('.drives-quarantine',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=rootfd)
        try:
            private_store_fd(container)
            os.mkdir(move_id,0o700,dir_fd=container) # Unknown existing stores are never adopted.
            leaf=os.open(move_id,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=container)
            try:
                saved=private_store_fd(leaf);os.fsync(leaf);os.fsync(container)
            finally:os.close(leaf)
        finally:os.close(container)
    return {'path':str(root/'.drives-quarantine'/move_id),'identity':saved}


def move_original(source,expected,store,allow_external=False):
    """Rename without replacement; caller journals intent and maintains exclusion."""
    if os.geteuid()!=0:raise Failure('quarantine requires root')
    source=safe_path(str(source));private_directory(store['path'])
    if any(r['target']==str(source) or r['target'].startswith(str(source)+'/') for r in mount_rows()):raise Failure('mounted source or nested mount cannot be quarantined')
    with anchored_tree(str(source)) as (parent,sourcefd,name),anchored_tree(store['path']) as (_,storefd,_):
        current=identity_fd(sourcefd);saved=private_store_fd(storefd)
        if current[1:]!=expected[1:]:raise Failure('source identity changed before quarantine')
        if saved!=store['identity']:raise Failure('quarantine identity changed')
        if current[0]!=saved[0] or current[2]!=saved[2]:raise Failure('quarantine filesystem changed')
        closed_hardlinks('/proc/self/fd/'+str(sourcefd),allow_external)
        entry=os.stat(name,dir_fd=parent,follow_symlinks=False)
        if (entry.st_dev,entry.st_ino)!=(current[0],current[1]):raise Failure('source entry changed before quarantine')
        rename=ctypes.CDLL(None,use_errno=True).renameat2
        rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];rename.restype=ctypes.c_int
        if rename(parent,os.fsencode(name),storefd,b'original',1)!=0:
            error=ctypes.get_errno();raise Failure('quarantine rename refused: '+os.strerror(error))
        os.fsync(parent);os.fsync(storefd)
    return {'path':str(pathlib.Path(store['path'])/'original'),'identity':current}


def cleanup_scope(j):
    """Only the root-private quarantine container, never / or a drive mount."""
    if not re.fullmatch('[a-f0-9]{32}',j['id']):raise Failure('invalid quarantine move id')
    store=safe_path(j['quarantine']['path'])
    expected=j['sourceMount'].rstrip('/')+'/.drives-quarantine/'+j['id']
    if str(store)!=expected or j['backup']!=str(store/'original'):raise Failure('quarantine cleanup layout changed')
    private_directory(store.parent)
    return str(store.parent)


def cleanup(j):
    """Resumable fixed worker; delete only the journalled original and store."""
    from helper.moves import MoveManager,delete_tree_fd
    scope=cleanup_scope(j);store=j['quarantine'];m=MoveManager(None)
    m.changed(scope,identity(scope),j.get('sourceUUID'))
    if any(r['target']==scope or r['target'].startswith(scope+'/') for r in mount_rows()):raise Failure('mount in quarantine blocks deletion')
    with anchored_tree(scope) as (_,container,_):
        private_store_fd(container)
        try:fd=os.open(j['id'],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=container)
        except FileNotFoundError:return {'ok':True}
        try:
            held=private_store_fd(fd)
            if held[1:]!=store['identity'][1:]:raise Failure('quarantine store identity changed')
            if set(os.listdir(fd))-{'original'}:raise Failure('unexpected quarantine entries')
            try:leaf=os.open('original',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            except FileNotFoundError:pass
            else:
                try:
                    current=identity_fd(leaf)
                    if current[1:]!=j['sourceIdentity'][1:] or current[0]!=held[0] or current[2:]!=held[2:]:raise Failure('original identity changed')
                    delete_tree_fd(leaf)
                    now=os.stat('original',dir_fd=fd,follow_symlinks=False)
                    if (now.st_dev,now.st_ino)!=(current[0],current[1]):raise Failure('original name changed')
                    os.rmdir('original',dir_fd=fd);os.fsync(fd)
                finally:os.close(leaf)
            now=os.stat(j['id'],dir_fd=container,follow_symlinks=False)
            if (now.st_dev,now.st_ino)!=(held[0],held[1]):raise Failure('quarantine name changed')
            os.rmdir(j['id'],dir_fd=container);os.fsync(container)
        finally:os.close(fd)
    return {'ok':True}


if __name__=='__main__':
    if os.geteuid()!=0 or len(sys.argv)!=3 or sys.argv[1]!='cleanup':raise Failure('invalid root-only quarantine worker invocation')
    print(json.dumps(cleanup(json.loads(sys.argv[2]))))
