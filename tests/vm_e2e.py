"""VM-only end-to-end driver for the installed helper (root, KVM guest only).

Each subcommand drives the REAL system D-Bus service and prints one JSON line.
It refuses to run outside a KVM guest and only touches TEST* serial disks.

  provision <SERIAL> <name> <mountpoint>   ProvisionDrive with a generated recovery secret
  unlock <name>                            UnlockDrive: wrong passphrase, then the saved recovery secret
  plan <src> <destMount>                   StartMove (normal session: plan only)
  request <moveId> continue|rollback       Resume/RollbackMove: arm the next-boot request
  cancel-request <moveId>                  CancelRestart
  delete <moveId>                          DeleteOldCopy
  status                                   helper Status
  crash-at <stage>                         arm one QA power-cut injection for the next maintenance boot
"""
import json,os,pathlib,secrets,sys,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.client import call,status,secret_memfd
from helper.common import run

assert os.geteuid()==0 and run(['systemd-detect-virt']).strip()==b'kvm','VM-only test driver'
EVIDENCE=pathlib.Path('/root/drives-e2e');EVIDENCE.mkdir(mode=0o700,exist_ok=True)


def wait(job):
    end=time.monotonic()+600
    while time.monotonic()<end:
        record=next((j for j in status()['jobs'] if j['id']==job),None)
        if record and record['state']!='running':return record
        time.sleep(.2)
    raise SystemExit('job deadline')


def submit(method,args,secret=None):
    if secret is None:reply=call(method,args)
    else:
        fd=secret_memfd(secret)
        try:reply=call(method,args,secret_fd=fd)
        finally:os.close(fd)
    if not reply.get('ok'):return reply
    return wait(reply['jobId'])


def main(argv):
    op=argv[0]
    if op=='provision':
        serial,name,mountpoint=argv[1:4]
        assert serial.startswith('TEST')
        from topology import snapshot
        disk=next(d for d in snapshot()['disks'] if d['serial']==serial)
        secret=secrets.token_urlsafe(32).encode()
        # The recovery secret is kept root-private in the guest for the recovery-unlock test only.
        path=EVIDENCE/(name+'.recovery');fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        try:os.write(fd,secret)
        finally:os.close(fd)
        request={'byId':disk['byId'],'serial':serial,'name':name,'mountpoint':mountpoint,'erase':False,'confirmation':serial[-4:],'autoUnlock':True}
        decoy=dict(request,confirmation='Y003')
        result={'decoyFragment':submit('ProvisionDrive',(json.dumps(decoy),0),secret)}
        result['job']=submit('ProvisionDrive',(json.dumps(request),0),secret)
    elif op=='unlock':
        # Recovery unlock with the secret saved at provisioning (root-private, guest only).
        name=argv[1];record=next(d for d in status()['drives'] if d['name']==name)
        wrong=submit('UnlockDrive',(record['id'],0),b'definitely-not-the-passphrase')
        secret=(EVIDENCE/(name+'.recovery')).read_bytes()
        result={'wrongPassphrase':wrong,'job':submit('UnlockDrive',(record['id'],0),secret)}
    elif op=='plan':result=submit('StartMove',(argv[1],argv[2]))
    elif op=='request':result=submit('ResumeMove' if argv[2]=='continue' else 'RollbackMove',(argv[1],))
    elif op=='cancel-request':result=submit('CancelRestart',(argv[1],))
    elif op=='delete':result=submit('DeleteOldCopy',(argv[1],))
    elif op=='status':result=status()
    elif op=='crash-at':
        stage=argv[1];assert stage in ('quarantining','quarantine-prepared','copying','verifying','switching','placeholder','fstab','rolling-back')
        p=pathlib.Path('/var/lib/drives-helper/qa-crash-at');p.write_text(stage);p.chmod(0o600);result={'armed':stage}
    else:raise SystemExit(__doc__)
    print(json.dumps(result,ensure_ascii=True))


if __name__=='__main__':main(sys.argv[1:])
