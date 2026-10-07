"""Fixed worker: make a hand-made encrypted drive able to receive moved folders.

Folders are only moved into a drive whose top folder no user can rename things
in (helper/moves.py preflight): a root process writing into a user-owned
directory could be redirected by any program running as that user. A drive
set up by hand is usually mounted with the user as owner of its top folder.

This changes only the drive's top folder: root owns it, no other account can
write there, and the former owner's read/traverse access is kept with an ACL.
Existing group/named/other access is not widened. Nothing inside it changes.
It refuses unless the path is the root mount of an encrypted,
btrfs, non-system data disk that unlocks at boot from a root-only keyfile in
crypttab, which is what the maintenance boot needs to move folders onto it.

Runs in a transient unit that can write only that one mount.
"""
import errno,json,os,pathlib,stat,struct,sys
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


def secured_acl(data,info):
    """Linux POSIX ACL xattr v2: retain effective read/traverse, remove writes.

    Freeze existing masked grants BEFORE adding the former owner. Otherwise
    raising the mask for that new entry could expose a formerly masked user.
    Layout/constants are defined by linux/posix_acl{,_xattr}.h, not libacl ABI.
    """
    undefined=0xffffffff
    if data is None:
        entries=[(1,(info.st_mode>>6)&7,undefined),(4,(info.st_mode>>3)&7,undefined),(32,info.st_mode&7,undefined)]
    else:
        if len(data)<4 or (len(data)-4)%8 or struct.unpack('<I',data[:4])[0]!=2:raise Failure('unsupported drive ACL format')
        entries=list(struct.iter_unpack('<HHI',data[4:]))
        if any(tag not in (1,2,4,8,16,32) or perm>7 for tag,perm,uid in entries):raise Failure('invalid drive ACL')
        if any(sum(tag==base for tag,perm,uid in entries)!=1 for base in (1,4,32)):raise Failure('incomplete drive ACL')
    mask=next((perm for tag,perm,uid in entries if tag==16),7)
    owner=next(perm for tag,perm,uid in entries if tag==1)&5
    kept=[]
    for tag,perm,uid in entries:
        if tag==16 or (tag==2 and uid==info.st_uid):continue
        kept.append((tag,7 if tag==1 else perm&(mask if tag in (2,4,8) else 7)&5,uid))
    if info.st_uid!=0:kept.append((2,owner,info.st_uid))
    effective=0
    for tag,perm,uid in kept:
        if tag in (2,4,8):effective|=perm
    kept.append((16,effective,undefined));kept.sort(key=lambda e:(e[0],e[2]))
    return struct.pack('<I',2)+b''.join(struct.pack('<HHI',*entry) for entry in kept)


def prepare(path,expected=None,**kw):
    m=check(path,**kw)
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        s=os.fstat(fd)
        if not stat.S_ISDIR(s.st_mode) or not is_drive_root(fd,path,m):raise Failure('the drive changed while preparing it')
        if expected is not None:
            from helper.moves import identity,identity_fd
            if identity_fd(fd)!=expected or identity(path)!=expected:raise Failure('drive directory identity changed before preparation')
        before={'uid':s.st_uid,'gid':s.st_gid,'mode':oct(stat.S_IMODE(s.st_mode))}
        try:acl=os.getxattr(fd,'system.posix_acl_access')
        except OSError as exc:
            if exc.errno!=errno.ENODATA:raise Failure('cannot establish existing drive access permissions') from exc
            acl=None
        secured=secured_acl(acl,s)
        # Never pass through a more-public intermediate mode. Preserve the
        # existing group identity; its effective access is preserved in the ACL.
        if s.st_uid!=0:os.fchown(fd,0,-1)
        os.fchmod(fd,0o700)
        os.setxattr(fd,'system.posix_acl_access',secured)
        os.fsync(fd)
        a=os.fstat(fd)
        if a.st_uid!=0 or a.st_mode&0o022:raise Failure('the drive folder is still writable by a user')
    finally:os.close(fd)
    return {'ok':True,'mountpoint':path,'before':before,
        'after':{'uid':a.st_uid,'gid':a.st_gid,'mode':oct(stat.S_IMODE(a.st_mode))},'accessPreserved':True}


def run_isolated(path,expected=None):
    """Called by the helper: check, then change ownership in a fresh unit
    that can write only this mount."""
    import uuid
    from helper.common import run
    check(path)
    args=[path]
    if expected is not None:
        from helper.moves import identity
        if identity(path)!=expected:raise Failure('drive directory identity changed before preparation')
        args.append(json.dumps(expected))
    out=run(['systemd-run','--quiet','--wait','--collect','--pipe','--unit=drives-prepare-'+uuid.uuid4().hex,
        '--property=WorkingDirectory=/usr/lib/drives-helper','--property=ProtectSystem=strict',
        '--property=ReadWritePaths='+path,'--property=ProtectHome=yes','--property=PrivateTmp=yes',
        '--property=NoNewPrivileges=yes','--property=CapabilityBoundingSet=CAP_CHOWN CAP_FOWNER CAP_DAC_READ_SEARCH',
        '/usr/bin/python3','-B','-m','helper.preparedrive',*args],timeout=120)
    return json.loads(out)


if __name__=='__main__':
    if os.geteuid()!=0:raise Failure('root-only prepare worker')
    if len(sys.argv) not in (2,3):raise SystemExit('invalid prepare invocation')
    expected=json.loads(sys.argv[2]) if len(sys.argv)==3 else None
    if expected is not None and (not isinstance(expected,list) or len(expected)!=3 or any(type(v) is not int or v<0 for v in expected[:2]) or (expected[2] is not None and (type(expected[2]) is not int or expected[2]<0))):raise Failure('invalid expected drive identity')
    print(json.dumps(prepare(sys.argv[1],expected=expected)))
