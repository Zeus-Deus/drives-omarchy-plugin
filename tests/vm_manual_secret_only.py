"""PARENT-ONLY native TESTNEW0002 scenario; not a component test.

DESTRUCTIVE: requires root/KVM, a fresh unmounted TESTNEW0002, plain OS
root and explicit DRIVES_NATIVE_MANUAL_ACK=TESTNEW0002. It creates the
configured drive/header/mount/udev rule. No installed code is overwritten.
Never invoke this in a worker lane or on host storage.

Run each --cut on a fresh disposable VM fixture. Keeps the generated recovery
secret only in memory, injects a controlled failure, verifies it with real
cryptsetup, closes the mapping, and recovers through the real UnlockDrive
implementation (no format retry). Does not claim process-death/powercut proof.
"""
import argparse,json,os,pathlib,secrets,subprocess,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))


def run(cut):
    assert os.geteuid()==0
    assert subprocess.check_output(['systemd-detect-virt']).strip()==b'kvm'
    assert os.environ.get('DRIVES_NATIVE_MANUAL_ACK')=='TESTNEW0002'
    from helper import common,provisioning as p,driveconfig as d
    state=p.snapshot()
    assert not state['rootEncrypted'], 'this scenario must prove manual mode on plain root'
    matches=[row for row in state['disks'] if row['serial']=='TESTNEW0002']
    assert len(matches)==1 and matches[0]['selectable'] and not matches[0]['system'] and not matches[0]['mounts']
    disk=matches[0]
    # Find by-id from the actual device, not a guessed transport prefix.
    links=[str(q) for q in pathlib.Path('/dev/disk/by-id').iterdir() if '-part' not in q.name and os.path.realpath(q)==disk['name']]
    assert links
    c=common.Common();key=pathlib.Path('/etc/cryptsetup-keys.d/native-manual.key')
    assert not os.path.lexists(key)
    assert not any(j.get('byId') in links or j.get('name')=='native-manual' for j in c.records('drives'))
    req=dict(byId=sorted(links)[0],serial='TESTNEW0002',name='native-manual',mountpoint='/mnt/drives/native-manual',erase=True,confirmation='0002',autoUnlock=False)
    secret=secrets.token_urlsafe(32).encode();save=c.journal;finish=p.finish;execute=d.run
    checkpoints=[];tripped=False
    def check():assert not os.path.lexists(key), 'manual provisioning persisted an OS unlock key'
    def journal(kind,identifier,value):
        nonlocal tripped
        check();save(kind,identifier,value);check();checkpoints.append(value['state'])
        if cut=='finalizing' and value['state']=='finalizing' and not tripped:
            tripped=True;raise common.Failure('native fixture finalizing cut')
    def stop_after_format(*args):
        nonlocal tripped
        check();tripped=True;raise common.Failure('native fixture formatted cut')
    def before_proof(argv,**kw):
        nonlocal tripped
        check()
        if cut=='before-proof' and argv[0]=='systemd-run' and 'helper.mountproof' in argv and not tripped:
            tripped=True;raise common.Failure('native fixture proof cut')
        return execute(argv,**kw)
    c.journal=journal;d.run=before_proof
    if cut=='formatted':p.finish=stop_after_format
    try:
        try:p.provision(req,secret,c)
        except common.Failure as exc:assert 'native fixture' in str(exc),str(exc)
        else:raise AssertionError('expected injected interruption')
    finally:c.journal=save;p.finish=finish;d.run=execute
    assert tripped;check()
    j=next(j for j in c.records('drives') if j['name']==req['name'])
    assert j['state']=='unfinished' and j['manualSecretOnly'] is True and j['keyfile'] is None
    part=p.configured_partition(j)
    subprocess.run(['cryptsetup','open','--test-passphrase','--key-file','-',part],input=secret,check=True)
    # A random unrelated key must not work: no bypass slot was added.
    assert subprocess.run(['cryptsetup','open','--test-passphrase','--key-file','-',part],input=secrets.token_bytes(64),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode!=0
    mounts=[r for r in common.mount_rows() if r['target']==req['mountpoint'] and r['fstype']!='autofs']
    if mounts:subprocess.run(['umount','--',req['mountpoint']],check=True)
    mapper='/dev/mapper/'+req['name']
    if os.path.exists(mapper):subprocess.run(['cryptsetup','close',req['name']],check=True)
    # Fault before our canonical open may leave UDisks' original mapper instead.
    for path,chains in p.chains(p.blocks()).items():
        if path.startswith('/dev/mapper/') and any(chain[-1]['type']=='crypt' and any(n['name']==part for n in chain) for chain in chains):
            subprocess.run(['cryptsetup','close',pathlib.Path(path).name],check=True)
    p.unlock(j['id'],secret,c);check()
    ready=c.read('drives',j['id']);assert ready['state']=='ready' and ready['keyfile'] is None
    assert ready['mountProof']['readWrite']
    header=pathlib.Path(ready['header']);assert header.stat().st_uid==0 and header.stat().st_mode&0o777==0o600
    assert subprocess.check_output(['cryptsetup','luksUUID',str(header)])==subprocess.check_output(['cryptsetup','luksUUID',part])
    print(json.dumps(dict(ok=True,cut=cut,id=j['id'],states=checkpoints,recoveryPassphraseWorks=True,noDurableRandomKey=True,headerIdentity=True)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cut',choices=['formatted','before-proof','finalizing'],required=True)
    # The native helper's normal scheduler owns this lease. This standalone
    # parent-only runner must hold the same lease for its entire transaction.
    assert os.geteuid()==0 and subprocess.check_output(['systemd-detect-virt']).strip()==b'kvm'
    assert os.environ.get('DRIVES_NATIVE_MANUAL_ACK')=='TESTNEW0002'
    from helper.common import Common
    with Common().storage_lock():run(parser.parse_args().cut)
