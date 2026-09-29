"""Own-VM integration regression: hidden /proc must never mean no open files."""
import json,os,pathlib,subprocess,sys,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.client import call,status
assert os.geteuid()==0
assert subprocess.check_output(['systemd-detect-virt']).strip()==b'kvm'
source='/var/tmp/drives-fixtures/Videos'
assert pathlib.Path(source,'example.txt').exists()
p=subprocess.Popen([sys.executable,'-c',"import sys,time;f=open(sys.argv[1],'rb');sys.stdout.write('R');sys.stdout.flush();time.sleep(120)",source+'/example.txt'],stdout=subprocess.PIPE)
assert p.stdout is not None
job={}
try:
    assert p.stdout.read(1)==b'R'
    result=call('StartMove',(source,'/data-fixture'));assert result['ok'],result
    end=time.monotonic()+15
    while time.monotonic()<end:
        job=next(j for j in status()['jobs'] if j['id']==result['jobId'])
        if job['state']!='running':break
        time.sleep(.05)
    pathlib.Path('/var/tmp/drives-evidence/open-file-regression.json').write_text(json.dumps(job,indent=2))
    assert job['state']=='failed' and 'open files block' in job.get('error',''),job
    print(json.dumps({'passed':True,'job':job},indent=2))
finally:
    p.terminate();p.wait();p.stdout.close()
