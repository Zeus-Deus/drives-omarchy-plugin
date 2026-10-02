"""Guest-only native bind refusal; no provisioning or encryption claim."""
import json,os,pathlib,shutil,sys,time,uuid
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.common import Common,Failure,mount_rows,run
from helper.moves import MoveManager,identity
from helper import client


def main(dbus=False):
    assert os.geteuid()==0 and run(['systemd-detect-virt']).strip()==b'kvm'
    base=pathlib.Path('/var/lib/drives-maintenance-tests')/('native-bind-'+uuid.uuid4().hex)
    base.mkdir(mode=0o700)
    source=base/'source';source.mkdir();(source/'original').write_bytes(b'old copy')
    original=identity(source);backup=base/'source.pre-move';source.rename(backup)
    source.mkdir(mode=0o000)
    dest=base/'destination';dest.mkdir();(dest/'new-work').write_bytes(b'keep active work')
    c=Common() if dbus else Common(base/'state');manager=MoveManager(c);bound=False;results=[];records=[]
    if dbus:assert client.status()['testFixtureMode'] is False
    def invoke(method,move_id):
        if not dbus:return getattr(manager,method)(move_id)
        wire={'cancel':'CancelMove','restart':'RestartMove','rollback':'RollbackMove'}[method]
        response=client.call(wire,(move_id,));assert response['ok'],response
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            job=next(j for j in client.status()['jobs'] if j['id']==response['jobId'])
            if job['state']=='failed':raise Failure(job['error'])
            if job['state']=='done':return job['result']
            time.sleep(0.05)
        raise AssertionError('D-Bus job did not finish')

    try:
        run(['mount','--bind',str(dest),str(source)]);bound=True
        assert any(r['target']==str(source) for r in mount_rows()),'bind not visible in PID1 namespace'
        backup.rename(base/'renamed-backup')
        for state in ('switched','planned'):
            for method in ('cancel','restart','rollback'):
                move_id=uuid.uuid4().hex
                record={'id':move_id,'state':state,'verified':False,'source':str(source),'sourceIdentity':original,'backup':str(backup),'dest':str(dest),'destMount':str(base),'destIdentity':identity(dest),'mountIdentity':identity(base),'diskSerial':'native-fixture-only'}
                c.journal('moves',move_id,record);records.append(move_id)
                try:invoke(method,move_id)
                except Failure as exc:error=str(exc)
                else:raise AssertionError('discard was not refused')
                expected='legacy undo' if method=='rollback' else ('possible switch' if state=='switched' else 'directory identity changed')
                assert expected in error,error
                assert (dest/'new-work').read_bytes()==b'keep active work'
                assert (source/'new-work').read_bytes()==b'keep active work'
                assert c.read('moves',move_id)==record
                results.append({'method':method,'state':state,'refused':True,'error':error,'destinationPreserved':True,'journalUnchanged':True})
        output={'bootId':c.boot_id(),'dbus':dbus,'fixture':'real PID1 bind on guest system filesystem; not encrypted-disk migration','results':results}
        name='native-discard-dbus.json' if dbus else 'native-discard-admission.json'
        pathlib.Path('/var/tmp/drives-evidence',name).write_text(json.dumps(output,indent=2))
        print(json.dumps(output,indent=2))
    finally:
        if bound:run(['umount',str(source)])
        if dbus:
            for move_id in records:
                record=c.read('moves',move_id);assert record['source']==str(source) and record['dest']==str(dest)
                c.path('moves',move_id).unlink()
        assert base.name.startswith('native-bind-') and base.parent==pathlib.Path('/var/lib/drives-maintenance-tests')
        shutil.rmtree(base)


if __name__=='__main__':main(dbus='--dbus' in sys.argv[1:])
