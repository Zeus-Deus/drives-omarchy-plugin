"""Real Btrfs snapshot identity proof on TESTDATA0001; guest only."""
import json,os,pathlib,sys,uuid
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import Common,Failure,run
from helper.moves import MoveManager,identity
from topology import probe
assert os.geteuid()==0 and run(['systemd-detect-virt']).strip()==b'kvm'
t=probe('/data-fixture',resolve=False);assert t['encrypted'] and t['disk']['serial']=='TESTDATA0001'
base=pathlib.Path('/data-fixture')/('identity-test-'+uuid.uuid4().hex);base.mkdir(mode=0o700)
a=base/'original';b=base/'snapshot';c=Common('/var/tmp/drives-evidence/subvolume-proof-state')
try:
    run(['btrfs','subvolume','create',str(a)]);(a/'fixture').write_bytes(b'fixture data')
    run(['btrfs','subvolume','snapshot','-r',str(a),str(b)])
    original=identity(a);replacement=identity(b)
    assert original[1]==replacement[1]==256
    assert original[2]!=replacement[2]
    try:MoveManager(c).changed(str(b),original,t['chain'][-1]['uuid'])
    except Failure as exc:assert 'identity changed' in str(exc)
    else:raise AssertionError('snapshot accepted as original')
    result={'same_inode':True,'different_subvolume':True,'replacement_refused':True,'original':original,'snapshot':replacement,'boot_id':c.boot_id()}
    pathlib.Path('/var/tmp/drives-evidence/subvolume-native-proof.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
finally:
    for p in (b,a):
        if p.exists():run(['btrfs','subvolume','delete',str(p)])
    base.rmdir()
