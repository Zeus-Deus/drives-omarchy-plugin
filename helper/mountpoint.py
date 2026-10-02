"""Fixed root-only mount placeholder worker; no disk or configuration APIs."""
import os,pathlib,re,stat,sys
from helper.common import Failure,run,mount_rows

def validate(path):
    if not isinstance(path,str) or str(pathlib.Path(path))!=path or '..' in pathlib.Path(path).parts:raise Failure('invalid mountpoint')
    if not (re.fullmatch('/data[0-9]*',path) or re.fullmatch('/mnt/drives/[a-zA-Z0-9/_-]+',path)):raise Failure('invalid mountpoint')
    return pathlib.Path(path)

def prepare(path):
    if os.geteuid()!=0:raise Failure('mountpoint preparation requires root')
    p=validate(path)
    if any(r['target']==path for r in mount_rows()):raise Failure('mountpoint is already mounted')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for index,name in enumerate(p.parts[1:]):
            leaf=index==len(p.parts)-2
            try:child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(name,0o000 if leaf else 0o755,dir_fd=fd);os.fsync(fd)
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            try:
                info=os.fstat(child)
                if info.st_uid!=0 or info.st_mode&0o022:raise Failure('mountpoint ancestors must be root-owned and not user writable')
                if leaf:
                    if os.listdir(child):raise Failure('mount placeholder is not empty')
                    if stat.S_IMODE(info.st_mode)!=0:os.fchmod(child,0o000)
                    run(['chattr','+i',path]);os.fsync(child);os.fsync(fd)
            except BaseException:os.close(child);raise
            os.close(fd);fd=child
    finally:os.close(fd)

if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('invalid invocation')
    prepare(sys.argv[1])
