"""Fixed worker: make a hand-made encrypted drive able to receive moved folders.

Folders are only moved into a drive whose top folder no user can rename things
in (helper/moves.py preflight): a root process writing into a user-owned
directory could be redirected by any program running as that user. A drive
set up by hand is usually mounted with the user as owner of its top folder.

This changes exactly one thing: the owner and mode of the drive's top folder
(root:root 0755). Nothing inside it changes, so folders already there stay
the user's. It refuses unless the path is the root mount of an encrypted,
btrfs, non-system data disk that unlocks at boot from a root-only keyfile in
crypttab, which is what the maintenance boot needs to move folders onto it.

Runs in a transient unit that can write only that one mount.
"""
import json,os,pathlib,stat,sys
from helper.common import Failure,mount_rows,mount_for

REFUSED=('/home','/root','/run','/proc','/sys','/dev','/usr','/etc','/var','/boot','/tmp','/opt')


def check(path,probe=None,crypttab_key=None,rows=None):
    """The drive facts the change depends on; raises Failure on any doubt."""
    if probe is None:from topology import probe
    if crypttab_key is None:from helper.offline import crypttab_key
    if not isinstance(path,str) or not path.startswith('/') or path=='/' or '\0' in path or '\n' in path:raise Failure('invalid drive path')
    p=pathlib.Path(path)
    if str(p)!=path or '..' in p.parts:raise Failure('non-canonical drive path')
    if path.startswith(tuple(r+'/' for r in REFUSED)) or path in REFUSED:raise Failure('only a data drive mounted outside system and home folders can be prepared')
    for ancestor in (p,)+tuple(p.parents):
        if ancestor.is_symlink():raise Failure('symlinked path refused')
    t=probe(path,None,rows,resolve=False)
    if not t.get('supported') or not t.get('encrypted'):raise Failure('not a single encrypted disk')
    m=t['mount']
    if m['target']!=path or m.get('fsroot')!='/' or m['fstype']!='btrfs':raise Failure('this path is not the btrfs root mount of the drive')
    from topology import blocks,classify
    disk=next((d for d in classify(blocks(),mount_rows() if rows is None else rows,{}) if d['name']==t['disk']['name']),None)
    if disk is None or disk['system']:raise Failure('the OS disk cannot be prepared')
    crypt=[n for n in t['chain'] if n.get('type')=='crypt']
    if len(crypt)!=1:raise Failure('expected one encrypted volume')
    crypttab_key(os.path.basename(crypt[0]['name']))
    return m


def is_drive_root(fd,path,m):
    """The opened folder is still the checked drive's top folder. btrfs gives
    each subvolume its own st_dev, so the mount table's major:minor can't be
    compared; instead: this namespace still mounts the same device there, the
    folder is a mount root (its parent is another filesystem), and it is the
    top btrfs subvolume (inode 256)."""
    own=mount_for(path,mount_rows(scope='self'))
    parent=os.stat('..',dir_fd=fd,follow_symlinks=False)
    return (own['target']==path and own['source']==m['source'] and own.get('fsroot')=='/' and own['fstype']=='btrfs'
        and os.fstat(fd).st_ino==256 and parent.st_dev!=os.fstat(fd).st_dev)


def prepare(path,**kw):
    m=check(path,**kw)
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        s=os.fstat(fd)
        if not stat.S_ISDIR(s.st_mode) or not is_drive_root(fd,path,m):raise Failure('the drive changed while preparing it')
        before={'uid':s.st_uid,'gid':s.st_gid,'mode':oct(stat.S_IMODE(s.st_mode))}
        if s.st_uid!=0 or s.st_gid!=0:os.fchown(fd,0,0)
        if stat.S_IMODE(os.fstat(fd).st_mode)!=0o755:os.fchmod(fd,0o755)
        os.fsync(fd)
        a=os.fstat(fd)
        if a.st_uid!=0 or a.st_mode&0o022:raise Failure('the drive folder is still writable by a user')
    finally:os.close(fd)
    return {'ok':True,'mountpoint':path,'before':before,'after':{'uid':0,'gid':0,'mode':'0o755'}}


def run_isolated(path):
    """Called by the helper: check, then change ownership in a fresh unit
    that can write only this mount."""
    import uuid
    from helper.common import run
    check(path)
    out=run(['systemd-run','--quiet','--wait','--collect','--pipe','--unit=drives-prepare-'+uuid.uuid4().hex,
        '--property=WorkingDirectory=/usr/lib/drives-helper','--property=ProtectSystem=strict',
        '--property=ReadWritePaths='+path,'--property=ProtectHome=yes','--property=PrivateTmp=yes',
        '--property=NoNewPrivileges=yes','--property=CapabilityBoundingSet=CAP_CHOWN CAP_FOWNER CAP_DAC_READ_SEARCH',
        '/usr/bin/python3','-B','-m','helper.preparedrive',path],timeout=120)
    return json.loads(out)


if __name__=='__main__':
    if os.geteuid()!=0:raise Failure('root-only prepare worker')
    if len(sys.argv)!=2:raise SystemExit('invalid prepare invocation')
    print(json.dumps(prepare(sys.argv[1])))
