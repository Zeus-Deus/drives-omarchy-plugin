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
            h=status();result.update({k:v for k,v in h.items() if k in ('moves','drives','jobs','version','testFixtureMode','restartPending','health')});result['helperAvailable']=h.get('ok',False)
        except Exception:result['helperError']='Storage helper is not installed or unavailable. The overview remains read-only.'
        result['agentAvailable']=pathlib.Path(__file__).with_name('helper').joinpath('agent.py').exists()
        import shutil
        result['apps']={'docker':bool(shutil.which('docker')) or os.path.isdir('/var/lib/docker'),'steam':steam_library(os.path.expanduser('~'))}
        # Do not expose unrelated pseudo-filesystems to QML.
        result['mounts']=[m for m in result['mounts'] if m['source'].startswith('/dev/') or m['fstype']=='autofs' and m['target'].startswith(('/data','/mnt/drives'))]
        return result
    if op=='probe':return {'ok':True,**probe(req.get('path',''))}
    methods={'start_move':('StartMove',('src','destMount')),'resume_drive':('ResumeDrive',('id',)),'reconnect_drive':('ReconnectDrive',('id',)),'resume_move':('ResumeMove',('id',)),
        'rollback_move':('RollbackMove',('id',)),'delete_old_copy':('DeleteOldCopy',('id',)),
        'cancel_move':('CancelMove',('id',)),'restart_move':('RestartMove',('id',)),'cancel_restart':('CancelRestart',('id',)),
        'export_header':('ExportHeaderBackup',('name','destination'))}
    if op not in methods:raise Failure('unknown operation')
    method,keys=methods[op]
    if set(req)!={'op',*keys} or any(not isinstance(req[k],str) for k in keys):raise Failure('invalid operation fields')
    from helper.client import call
    return call(method,tuple(req[k] for k in keys))

STEAM=('.local/share/Steam/steamapps','.local/share/Steam')
SIZE_BUDGET=150

def size_request(req):
    """Validate a sizes request: optional extra absolute folders (bind mounts
    on a data drive the panel wants measured)."""
    if not isinstance(req,dict) or req.get('op')!='sizes' or set(req)-{'op','paths'}:raise Failure('invalid size request')
    paths=req.get('paths',[])
    if not isinstance(paths,list) or len(paths)>16 or any(not isinstance(p,str) or not p.startswith('/') or len(p)>4096 or '\0' in p or '\n' in p for p in paths):
        raise Failure('invalid size paths')
    return paths

def stream_sizes(req,write,home=None,budget=SIZE_BUDGET):
    """Read-only disk use, measured as the desktop user, so du never sees more
    than the user can already read. One JSON line per result as du finishes it:
    first each top-level folder of home on the OS disk (du -x stays on one
    filesystem, so folders already on a drive are not counted twice), then
    Steam's library, then the requested folders; finally a done line."""
    import threading,time
    from topology import du
    paths=size_request(req);home=home or os.path.expanduser('~')
    steam=steam_library(home)
    lock=threading.Lock();state={'complete':True}
    def out(obj):
        with lock:write(obj)
    out({'steam':steam,'home':home})
    end=time.monotonic()+budget
    def home_scan():
        _,ok=du(['--max-depth=1','--',home],end-time.monotonic(),emit=lambda e:out({'kind':'home' if e['path']==home else 'entry',**e}))
        state['complete']&=ok
    def extras():
        # Run beside the home scan: a big home folder must not hide these.
        for p in ([steam] if steam else [])+[p for p in paths if p!=steam]:
            left=end-time.monotonic()
            if left<=0:state['complete']=False;break
            if not os.path.isdir(p) or not os.access(p,os.R_OK|os.X_OK):out({'kind':'extra','path':p,'bytes':None});continue
            e,ok=du(['-s','--',p],left);state['complete']&=ok
            out({'kind':'extra','path':p,'bytes':e[0]['bytes'] if e and ok else None})
    threads=[threading.Thread(target=f,daemon=True) for f in (home_scan,extras)]
    for t in threads:t.start()
    for t in threads:t.join()
    out({'done':True,'complete':state['complete']})

def steam_library(home):
    """Where Omarchy's Steam (omarchy-install-gaming-steam) keeps its games."""
    for s in STEAM:
        p=os.path.join(home,s)
        if os.path.isdir(p) and not os.path.islink(p):return p
    return ''

def sizes_main():
    def write(obj):sys.stdout.write(json.dumps(obj,ensure_ascii=True,separators=(',',':'))+'\n');sys.stdout.flush()
    try:
        data=sys.stdin.buffer.readline(8193)
        if len(data)>8192:raise Failure('request size limit exceeded')
        stream_sizes(json.loads(data),write)
    except BaseException as exc:write({'done':True,'complete':False,'error':clean(str(exc))})

if __name__=='__main__' and sys.argv[1:]==['--sizes']:
    sizes_main();sys.exit(0)

if __name__=='__main__':
    try:
        data=sys.stdin.buffer.readline(8193)
        if len(data)>8192:raise Failure('request size limit exceeded')
        response=handle(json.loads(data))
    except BaseException as exc:response={'ok':False,'error':clean(str(exc))}
    text=json.dumps(response,ensure_ascii=True,separators=(',',':'))
    if len(text)>2*1024*1024:text=json.dumps({'ok':False,'error':'response size limit exceeded'})
    print(text)
