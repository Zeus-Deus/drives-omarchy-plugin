"""Fixed worker for a move's private folder on an encrypted drive.

The long-running helper keeps every drive read-only in its own namespace: a
drive mounted before it started is remounted read-only there, and listing a
locked drive's automount in ReadWritePaths would stop the helper from starting
at all. Creating or discarding the per-move folder therefore runs here, in a
fresh transient unit that can write only to that one drive mount.
"""
import json,os,sys
from helper.common import Failure
from helper.moves import anchored_tree,delete_tree_fd,durable_directory,identity,identity_fd,safe_path


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


def main(argv):
    if len(argv)==2 and argv[0]=='create':return create(argv[1])
    if len(argv) in (3,4) and argv[0]=='discard' and argv[3:] in ([],['--recreate']):
        expected=json.loads(argv[2])
        if not isinstance(expected,list) or len(expected)!=3:raise Failure('invalid destination identity')
        return discard(argv[1],expected,recreate=bool(argv[3:]))
    raise Failure('invalid destination worker invocation')


if __name__=='__main__':
    if os.geteuid()!=0:raise Failure('root-only destination worker')
    print(json.dumps(main(sys.argv[1:])))
