"""Fresh /data17 preparation through the production helper namespace."""
import os,pathlib,json,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import run
assert os.geteuid()==0 and run(['systemd-detect-virt']).strip()==b'kvm'
p=pathlib.Path('/data17');assert not os.path.lexists(p),'owned test path must be fresh'
pid=run(['systemctl','show','drives-helper','--property=MainPID','--value']).decode().strip();assert int(pid)>0
code="import sys;sys.path.insert(0,'/usr/lib/drives-helper');from helper.common import prepare_mountpoint;prepare_mountpoint('/data17')"
run(['nsenter','--mount=/proc/'+pid+'/ns/mnt','--','/usr/bin/python3','-B','-c',code],timeout=90)
s=p.stat();assert s.st_uid==0 and s.st_mode&0o777==0 and not list(p.iterdir())
flags=run(['lsattr','-d',str(p)]).decode().split()[0];assert 'i' in flags
run(['nsenter','--mount=/proc/'+pid+'/ns/mnt','--','/usr/bin/python3','-B','-c',code],timeout=90)
result={'fresh_mountpoint_prepared':True,'immutable':True,'root_owned':True,'mode':'000','idempotent':True,'production_namespace_pid':int(pid)}
pathlib.Path('/var/tmp/drives-evidence/mountpoint-native-proof.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
# Remove only the asserted empty fixture, never an arbitrary mountpoint.
run(['chattr','-i',str(p)]);p.rmdir()
