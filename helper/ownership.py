"""Small root-owned infrastructure boundary shared by setup and udev workers."""
import hashlib,os,pathlib,stat
from typing import NoReturn
from helper.common import Failure

LIMIT=2*1024*1024

def refuse(path,reason) -> NoReturn:
    raise Failure(f'{reason}: {path}. Retain this file; inspect/back it up as administrator before retrying.')


def directory(path,missing=False):
    """Inspect every ancestor; never chmod an existing shared directory."""
    path=pathlib.Path(path).absolute()
    for part in reversed((path,*path.parents)):
        try:info=part.lstat()
        except FileNotFoundError:
            if missing:continue
            refuse(part,'missing infrastructure directory')
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
            refuse(part,'unsafe infrastructure directory')
    return path


def read(path,limit=LIMIT):
    path=pathlib.Path(path);directory(path.parent,missing=True)
    try:fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:return None
    except OSError:refuse(path,'cannot safely open infrastructure file')
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022 or info.st_nlink!=1 or info.st_size>limit:
            refuse(path,'unsafe infrastructure file')
        data=os.read(fd,limit+1)
        if len(data)>limit:refuse(path,'oversized infrastructure file')
        return data
    finally:os.close(fd)


def digest(data):return hashlib.sha256(data).hexdigest()


def marked(body):return ('# Drives-owned-v1 sha256='+digest(body)+'\n').encode()+body


def unmark(data):
    head,sep,body=data.partition(b'\n')
    return body if sep and head==('# Drives-owned-v1 sha256='+digest(body)).encode() else None
