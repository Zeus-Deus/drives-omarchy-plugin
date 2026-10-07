"""Quarantine primitives; caller must already maintain offline writer exclusion.

No D-Bus/CLI entrypoint, automatic migration or proof of exclusion is provided
by this module. The normal-session MoveManager still defers all cutovers.
"""
import contextlib,ctypes,os,pathlib,re,stat
from helper.common import Failure,mount_rows
from helper.maintenance import private_directory
from helper.moves import anchored_tree,identity_fd,safe_path


def closed_hardlinks(path):
    """Outside inode aliases would defeat private-directory isolation after boot."""
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
    if any(expected!=count for expected,count in groups.values()):raise Failure('external hardlink defeats private quarantine')


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


def move_original(source,expected,store):
    """Rename without replacement; caller journals intent and maintains exclusion."""
    if os.geteuid()!=0:raise Failure('quarantine requires root')
    source=safe_path(str(source));private_directory(store['path'])
    if any(r['target']==str(source) or r['target'].startswith(str(source)+'/') for r in mount_rows()):raise Failure('mounted source or nested mount cannot be quarantined')
    with anchored_tree(str(source)) as (parent,sourcefd,name),anchored_tree(store['path']) as (_,storefd,_):
        current=identity_fd(sourcefd);saved=private_store_fd(storefd)
        if current[1:]!=expected[1:]:raise Failure('source identity changed before quarantine')
        if saved!=store['identity']:raise Failure('quarantine identity changed')
        if current[0]!=saved[0] or current[2]!=saved[2]:raise Failure('quarantine filesystem changed')
        closed_hardlinks('/proc/self/fd/'+str(sourcefd))
        entry=os.stat(name,dir_fd=parent,follow_symlinks=False)
        if (entry.st_dev,entry.st_ino)!=(current[0],current[1]):raise Failure('source entry changed before quarantine')
        rename=ctypes.CDLL(None,use_errno=True).renameat2
        rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];rename.restype=ctypes.c_int
        if rename(parent,os.fsencode(name),storefd,b'original',1)!=0:
            error=ctypes.get_errno();raise Failure('quarantine rename refused: '+os.strerror(error))
        os.fsync(parent);os.fsync(storefd)
    return {'path':str(pathlib.Path(store['path'])/'original'),'identity':current}
