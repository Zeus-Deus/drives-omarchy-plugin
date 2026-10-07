"""Fixed worker for a move's private folder on an encrypted drive.

The long-running helper keeps every drive read-only in its own namespace: a
drive mounted before it started is remounted read-only there, and listing a
locked drive's automount in ReadWritePaths would stop the helper from starting
at all. Creating or discarding the per-move folder therefore runs here, in a
fresh transient unit that can write only to that one drive mount.
"""
import json,os,sys
from helper.common import Failure
from helper.moves import anchored_tree,delete_tree_fd,durable_directory,identity,identity_fd,private_container_fd,safe_path


def create_private(path):
    """Claim a new wrapper; content alone receives source permissions/ACLs."""
    p=safe_path(path)
    with anchored_tree(str(p.parent)) as (_,parent,_):
        try:os.mkdir(p.name,0o700,dir_fd=parent)
        except FileExistsError:raise Failure('unrecorded destination exists; inspect before continuing') from None
        fd=os.open(p.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        try:
            os.fchmod(fd,os.fstat(fd).st_mode&0o777) # clear inherited setgid, never adopt a widened mode
            container=private_container_fd(fd)
            os.mkdir('content',0o700,dir_fd=fd)
            content=os.open('content',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            try:saved=identity_fd(content);os.fsync(content)
            finally:os.close(content)
            os.fsync(fd);os.fsync(parent)
        finally:os.close(fd)
    return {'identity':saved,'containerIdentity':container}


def create(path):
    p=safe_path(path)
    if os.path.lexists(p):raise Failure('unrecorded destination exists; inspect before continuing')
    os.mkdir(p,0o700);durable_directory(str(p));durable_directory(str(p.parent))
    return {'identity':identity(str(p))}


def discard(path,expected,recreate=False):
    """Delete the move's folder only if it is still the recorded directory."""
    with anchored_tree(path) as (parent,leaf,name):
        held=identity_fd(leaf)
        if held[1:]!=list(expected)[1:]:raise Failure('destination changed; refusing to delete it')
        delete_tree_fd(leaf)
        now=os.stat(name,dir_fd=parent,follow_symlinks=False)
        if (now.st_dev,now.st_ino)!=(held[0],held[1]):raise Failure('destination entry changed during discard')
        os.rmdir(name,dir_fd=parent);os.fsync(parent)
        if not recreate:return {'identity':None}
        os.mkdir(name,0o700,dir_fd=parent)
        fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        try:new=identity_fd(fd);os.fsync(fd)
        finally:os.close(fd)
        os.fsync(parent)
        return {'identity':new}


def discard_private(path,expected,recreate=False,missing_ok=False):
    """Re-anchor both identities; never recurse over unrecorded wrapper entries."""
    with anchored_tree(path) as (parent,fd,name):
        held=private_container_fd(fd)
        if held[1:]!=expected['containerIdentity'][1:]:raise Failure('private container identity changed')
        if set(os.listdir(fd))-{'content'}:raise Failure('unexpected private container entries; refusing deletion')
        try:content=os.open('content',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
        except FileNotFoundError:
            if not missing_ok:raise
        else:
            try:
                current=identity_fd(content)
                if current[1:]!=expected['identity'][1:] or current[0]!=held[0] or current[2:]!=held[2:]:raise Failure('private content identity changed')
                delete_tree_fd(content)
                now=os.stat('content',dir_fd=fd,follow_symlinks=False)
                if (now.st_dev,now.st_ino)!=(current[0],current[1]):raise Failure('private content entry changed during discard')
                os.rmdir('content',dir_fd=fd);os.fsync(fd)
            finally:os.close(content)
        if recreate:
            os.mkdir('content',0o700,dir_fd=fd)
            content=os.open('content',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            try:new=identity_fd(content);os.fsync(content)
            finally:os.close(content)
            os.fsync(fd)
            return {'identity':new,'containerIdentity':held}
        now=os.stat(name,dir_fd=parent,follow_symlinks=False)
        if (now.st_dev,now.st_ino)!=(held[0],held[1]):raise Failure('private container entry changed during discard')
        os.rmdir(name,dir_fd=parent);os.fsync(parent)
        return {'identity':None,'containerIdentity':None}


def main(argv):
    if len(argv) in (3,4) and argv[0]=='discard-private' and argv[3:] in ([],['--recreate']):
        expected=json.loads(argv[2])
        if not isinstance(expected,dict) or set(expected)!={'identity','containerIdentity'} or any(not isinstance(v,list) or len(v)!=3 for v in expected.values()):raise Failure('invalid private destination identity')
        return discard_private(argv[1],expected,recreate=bool(argv[3:]))
    if len(argv)==2 and argv[0]=='create-private':return create_private(argv[1])
    if len(argv)==2 and argv[0]=='create':return create(argv[1])
    if len(argv) in (3,4) and argv[0]=='discard' and argv[3:] in ([],['--recreate']):
        expected=json.loads(argv[2])
        if not isinstance(expected,list) or len(expected)!=3:raise Failure('invalid destination identity')
        return discard(argv[1],expected,recreate=bool(argv[3:]))
    raise Failure('invalid destination worker invocation')


if __name__=='__main__':
    if os.geteuid()!=0:raise Failure('root-only destination worker')
    print(json.dumps(main(sys.argv[1:])))
