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
assert record['state']=='ready';part=os.path.realpath(record['partitionById'],strict=True);key=record['keyfile']
run(['cryptsetup','open','--test-passphrase','--key-file',key,part],timeout=120)
run(['cryptsetup','open','--test-passphrase','--key-file','-',part],data=secret,timeout=120)
assert os.stat(key).st_mode&0o777==0o400
assert run(['blkid','-s','TYPE','-o','value','/dev/mapper/data2']).strip()==b'btrfs'
# Component export checks actual ownership/bytes; stock D-Bus auth stays in force.
from helper.provisioning import export_header
export='/var/tmp/drives-evidence/offmachine-test-'+record['id']+'.header'
export_header('data2',export,Common(),1000)
assert os.stat(export).st_uid==1000 and os.stat(export).st_mode&0o777==0o600
assert pathlib.Path(export).read_bytes()==pathlib.Path(record['header']).read_bytes()
run(['systemctl','stop','drives-helper.service']);run(['systemctl','stop','data2.automount','data2.mount'],timeout=60)
run(['systemctl','stop','systemd-cryptsetup@data2.service'],timeout=60)
if os.path.exists('/dev/mapper/data2'):run(['cryptsetup','close','data2'])
run(['cryptsetup','luksHeaderRestore','--batch-mode',part,'--header-backup-file',export],timeout=120)
run(['cryptsetup','open','--test-passphrase','--key-file','-',part],data=secret,timeout=120)
# Exercise the same trusted UDisks recovery API as the separate GTK agent.
from helper.provisioning import udisks_call,block_object
opened=udisks_call(block_object(part),'Encrypted','Unlock','(sa{sv})',(secret.decode(),{}))[0]
assert opened.startswith('/org/freedesktop/UDisks2/block_devices/')
from topology import chains,blocks
mapper=next(c[-1]['name'] for cs in chains(blocks()).values() for c in cs if c[-1]['type']=='crypt' and any(n['name']==part for n in c))
run(['cryptsetup','close',os.path.basename(mapper)])
run(['udevadm','settle'])
# udev may auto-start crypttab's service during UDisks unlock. Reset its active state.
run(['systemctl','stop','systemd-cryptsetup@data2.service'],timeout=60)
run(['systemctl','start','systemd-cryptsetup@data2.service'],timeout=60)
run(['systemctl','start','data2.mount'],timeout=60);run(['systemctl','start','drives-helper.service'])
with open(decoy['name'],'rb') as f:after=hashlib.sha256(f.read(1024*1024)).hexdigest()
assert before==after
result.update(keyfile_unlock=True,recovery_unlock=True,udisks_recovery_unlock=True,header_restore=True,header_export_component=True,header_export=export,mapper_btrfs=True,generated_units=True,decoy_unchanged=True,decoy_before=before,decoy_after=after,record=record,boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip())
(e/'provision-success.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
