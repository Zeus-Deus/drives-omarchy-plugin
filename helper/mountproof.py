"""Fresh namespace read/write proof for an already configured encrypted mount."""
import json,os,pathlib,sys,uuid
from helper.common import Failure,mount_rows,mount_for,read_regular
from topology import probe

def verify(where,serial):
    if os.geteuid()!=0:raise Failure('root-only mount proof')
    p=probe(where,resolve=False)
    if not p['supported'] or not p.get('encrypted') or p['disk'].get('serial')!=serial or p['mount']['target']!=where or p['mount']['fstype']!='btrfs':raise Failure('wrong encrypted mount for proof')
    own=mount_for(where,mount_rows(scope='self'))
    if own['target']!=where or any(own[k]!=p['mount'][k] for k in ('fstype','source','fsroot')):raise Failure('sandbox is not viewing the intended mount')
    actual=own['majorMinor']
    path=where+'/.drives-proof-'+uuid.uuid4().hex
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:os.write(fd,b'encrypted mount verification');os.fsync(fd)
    finally:os.close(fd)
    try:
        if read_regular(path)!=b'encrypted mount verification':raise Failure('mount read/write proof failed')
    finally:os.unlink(path)
    return {'readWrite':True,'serial':serial,'mountpoint':where,'majorMinor':actual}

if __name__=='__main__':
    if len(sys.argv)!=3:raise SystemExit('invalid proof invocation')
    print(json.dumps(verify(sys.argv[1],sys.argv[2])))
