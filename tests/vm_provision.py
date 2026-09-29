"""Destructive only to TESTNEW0002 in a KVM guest; never accepts device args."""
import hashlib,json,os,pathlib,secrets,sys,time,subprocess
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.client import call,status,secret_memfd
from helper.common import Common,Failure,run,read_regular
from helper.provisioning import provision
from topology import snapshot
assert os.geteuid()==0 and run(['systemd-detect-virt']).strip()==b'kvm'
e=pathlib.Path('/var/tmp/drives-evidence')
state=snapshot();new=next(d for d in state['disks'] if d['serial']=='TESTNEW0002');decoy=next(d for d in state['disks'] if d['serial']=='TESTDECOY003')
assert new['selectable'] and not new['system']
with open(decoy['name'],'rb') as f:before=hashlib.sha256(f.read(1024*1024)).hexdigest()
request={'byId':new['byId'],'serial':'TESTNEW0002','name':'data2','mountpoint':'/data2','erase':False,'confirmation':'0002','autoUnlock':True}
secret=secrets.token_urlsafe(32).encode();result={}
try:provision(request,secret,Common(str(e/'plaintext-root-guard')))
except Failure as ex:assert 'encrypted OS root' in str(ex);result['plaintext_root_refused']=True
else:raise AssertionError('product plaintext-root safeguard failed')
fd=secret_memfd(secret)
try:
    bad=dict(request,confirmation='Y003')
    wrong=call('ProvisionDrive',(json.dumps(bad),0),secret_fd=fd)
    assert not wrong['ok'] and 'serial confirmation' in wrong['error'];result['wrong_decoy_fragment_refused']=True
    reply=call('ProvisionDrive',(json.dumps(request),0),secret_fd=fd);assert reply['ok'],reply
finally:os.close(fd)
end=time.monotonic()+240;job={}
while time.monotonic()<end:
    job=next(j for j in status()['jobs'] if j['id']==reply['jobId'])
    if job['state']!='running':break
    time.sleep(.2)
if job['state']!='done':
    (e/'provision-failure.json').write_text(json.dumps(job,indent=2));raise AssertionError(job)
record=next(r for r in status()['drives'] if r['id']==job['result']['id'])
assert record['state']=='ready';part=record['partition'];key=record['keyfile']
run(['cryptsetup','open','--test-passphrase','--key-file',key,part],timeout=120)
run(['cryptsetup','open','--test-passphrase','--key-file','-',part],data=secret,timeout=120)
assert os.stat(key).st_mode&0o777==0o400
assert run(['blkid','-s','TYPE','-o','value','/dev/mapper/data2']).strip()==b'btrfs'
run(['cryptsetup','luksHeaderRestore','--batch-mode',part,'--header-backup-file',record['header']],timeout=120)
run(['cryptsetup','open','--test-passphrase','--key-file','-',part],data=secret,timeout=120)
with open(decoy['name'],'rb') as f:after=hashlib.sha256(f.read(1024*1024)).hexdigest()
assert before==after
result.update(keyfile_unlock=True,recovery_unlock=True,header_restore=True,mapper_btrfs=True,generated_units=True,decoy_unchanged=True,decoy_before=before,decoy_after=after,record=record,boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip())
(e/'provision-success.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
