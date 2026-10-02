"""Missing-header continuation regression; TESTNEW0002 only, no formatting."""
import os,pathlib,json,sys,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import Common,run
from helper.client import call,status
from topology import snapshot
assert os.geteuid()==0 and run(['systemd-detect-virt']).strip()==b'kvm'
c=Common();j=next(r for r in c.records('drives') if r['serial']=='TESTNEW0002')
d=next(d for d in snapshot()['disks'] if d['serial']==j['serial']);assert not d['system'] and j['testFixture']
header=pathlib.Path(j['header']);saved=header.with_suffix('.header.saved-review')
if sys.argv[1]=='arm':
    assert j['state']=='ready' and header.is_file() and not saved.exists()
    header.rename(saved);j['state']='unfinished';j['interruptedState']='formatted';c.journal('drives',j['id'],j);run(['sync'])
if sys.argv[1]=='check' and j['state']=='ready' and not header.exists():
    assert saved.is_file();j['state']='unfinished';j['interruptedState']='formatted';c.journal('drives',j['id'],j)
reply=call('ResumeDrive',(j['id'],));assert reply['ok'],reply
end=time.monotonic()+180;job={'state':'running'}
while time.monotonic()<end:
    job=next(r for r in status()['jobs'] if r['id']==reply['jobId'])
    if job['state']!='running':break
    time.sleep(.2)
result={'job':job,'header_exists':header.is_file(),'saved_header_exists':saved.is_file(),'boot_id':c.boot_id()}
pathlib.Path('/var/tmp/drives-evidence/header-resume-'+sys.argv[1]+'.json').write_text(json.dumps(result,indent=2))
assert job['state']=='done',job
assert header.is_file(),'ready without the promised header backup'
assert run(['cryptsetup','isLuks',str(header)])==b''
assert os.stat(header).st_uid==0 and os.stat(header).st_mode&0o777==0o600
print(json.dumps({'header_resume':True,'serial':j['serial'],'bytes':header.stat().st_size,'boot_id':c.boot_id()}))
