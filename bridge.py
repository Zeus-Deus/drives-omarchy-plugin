"""One bounded JSON request -> one response; QML never runs privileged commands."""
import json,sys,os,pathlib
from helper.common import Failure,run
from topology import snapshot,probe,clean

def handle(req):
    if not isinstance(req,dict):raise Failure('request must be an object')
    op=req.get('op')
    if op=='status':
        result=snapshot();result.update(ok=True,helperAvailable=False,moves=[],drives=[],jobs=[])
        try:
            from helper.client import status
            h=status();result.update({k:v for k,v in h.items() if k in ('moves','drives','jobs','version','testFixtureMode')});result['helperAvailable']=h.get('ok',False)
        except Exception:result['helperError']='Storage helper is not installed or unavailable. The overview remains read-only.'
        result['agentAvailable']=pathlib.Path(__file__).with_name('helper').joinpath('agent.py').exists()
        # Do not expose unrelated pseudo-filesystems to QML.
        result['mounts']=[m for m in result['mounts'] if m['source'].startswith('/dev/') or m['fstype']=='autofs' and m['target'].startswith(('/data','/mnt/drives'))]
        return result
    if op=='probe':return {'ok':True,**probe(req.get('path',''))}
    methods={'start_move':('StartMove',('src','destMount')),'resume_drive':('ResumeDrive',('id',)),'resume_move':('ResumeMove',('id',)),
        'rollback_move':('RollbackMove',('id',)),'delete_old_copy':('DeleteOldCopy',('id',)),
        'cancel_move':('CancelMove',('id',)),'restart_move':('RestartMove',('id',)),
        'export_header':('ExportHeaderBackup',('name','destination'))}
    if op not in methods:raise Failure('unknown operation')
    method,keys=methods[op]
    if set(req)!={'op',*keys} or any(not isinstance(req[k],str) for k in keys):raise Failure('invalid operation fields')
    from helper.client import call
    return call(method,tuple(req[k] for k in keys))

if __name__=='__main__':
    try:
        data=sys.stdin.buffer.readline(8193)
        if len(data)>8192:raise Failure('request size limit exceeded')
        response=handle(json.loads(data))
    except BaseException as exc:response={'ok':False,'error':clean(str(exc))}
    text=json.dumps(response,ensure_ascii=True,separators=(',',':'))
    if len(text)>2*1024*1024:text=json.dumps({'ok':False,'error':'response size limit exceeded'})
    print(text)
