"""System D-Bus entrypoint. Authorize unique sender per operation, no QML privilege."""
import contextlib,fcntl,json,os,pathlib,stat,threading,time,uuid
from helper.common import Common,Failure
from helper.moves import MoveManager,ACTIVE

BUS='io.github.zeus_deus.Drives';OBJECT='/io/github/zeus_deus/Drives'
ACTIONS={'ProvisionDrive':'provision','StartMove':'move','ResumeMove':'resume','RollbackMove':'rollback','DeleteOldCopy':'delete','CancelMove':'cancel','RestartMove':'restart','ExportHeaderBackup':'export'}
SIGNATURES={'ProvisionDrive':'sh','StartMove':'ss','ResumeMove':'s','RollbackMove':'s','DeleteOldCopy':'s','CancelMove':'s','RestartMove':'s','ExportHeaderBackup':'ss','Status':''}
XML='<node><interface name="'+BUS+'">'+''.join('<method name="'+m+'">'+''.join('<arg type="'+s+'" direction="in"/>' for s in signature)+'<arg type="s" direction="out"/></method>' for m,signature in SIGNATURES.items())+'</interface></node>'

def read_secret_fd(fd):
    s=os.fstat(fd)
    required=fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL
    try:seals=fcntl.fcntl(fd,fcntl.F_GET_SEALS)
    except OSError as exc:raise Failure('secret must be sealed memfd') from exc
    if not stat.S_ISREG(s.st_mode) or not os.readlink('/proc/self/fd/'+str(fd)).startswith('/memfd:') or seals&required!=required:raise Failure('secret must be sealed memfd')
    if not 8<=s.st_size<=4096:raise Failure('secret length outside limits')
    value=os.pread(fd,s.st_size,0)
    if b'\0' in value:raise Failure('invalid secret')
    value.decode('utf-8');return value

class Server:
    def __init__(self,common=None):
        self.c=common or Common();self.worker_lock=threading.Lock();self.jobs=[];self.connection=None
    def authorize(self,sender,method):
        from gi.repository import Gio,GLib
        action='io.github.zeus-deus.drives.'+ACTIONS[method]
        subject=('system-bus-name',{'name':GLib.Variant('s',sender)})
        reply=self.connection.call_sync('org.freedesktop.PolicyKit1','/org/freedesktop/PolicyKit1/Authority','org.freedesktop.PolicyKit1.Authority','CheckAuthorization',
            GLib.Variant('((sa{sv})sa{ss}us)',(subject,action,{},1,'')),None,Gio.DBusCallFlags.NONE,120000,None).unpack()[0]
        if not reply[0]:raise Failure('administrator authorization denied')
        uid=self.connection.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','GetConnectionUnixUser',GLib.Variant('(s)',(sender,)),None,Gio.DBusCallFlags.NONE,5000,None).unpack()[0]
        return uid
    def status(self):
        from helper.provisioning import inspect
        moves=MoveManager(self.c).inspect()
        running=next((j for j in reversed(self.jobs) if j['state']=='running'),None)
        if running:
            for m in moves:
                if m.get('updated',0)>=running['started'] and m.get('interruptedState') in ACTIVE:
                    m['state']=m['interruptedState'];m['error']=''
        return {'ok':True,'version':'0.1.0','moves':moves,'drives':inspect(self.c),'jobs':list(self.jobs),'testFixtureMode':os.environ.get('DRIVES_VM_TESTING')=='1'}
    def schedule(self,method,args,uid,secret=None):
        if not self.worker_lock.acquire(blocking=False):raise Failure('another storage operation is already running')
        job={'id':uuid.uuid4().hex,'method':method,'state':'running','started':time.time()};self.jobs.append(job);self.jobs=self.jobs[-24:]
        def work():
            try:
                manager=MoveManager(self.c,uid=None if uid==0 else uid)
                if method=='ProvisionDrive':
                    from helper.provisioning import provision
                    result=provision(args[0],secret,self.c)
                elif method=='StartMove':result=manager.start(*args)
                elif method=='ExportHeaderBackup':
                    from helper.provisioning import export_header
                    result=export_header(*args,self.c,uid)
                else:
                    name={'ResumeMove':'resume','RollbackMove':'rollback','DeleteOldCopy':'delete_old','CancelMove':'cancel','RestartMove':'restart'}[method]
                    result=getattr(manager,name)(args[0])
                job['state']='done';job['result']=result
            except BaseException as exc:
                job['state']='failed';job['error']=str(exc)[:300]
            finally:self.worker_lock.release()
        threading.Thread(target=work,daemon=True).start()
        return {'ok':True,'jobId':job['id']}
    def dispatch(self,connection,sender,object_path,interface,method,parameters,invocation):
        from gi.repository import GLib
        try:
            if method=='Status':result=self.status()
            else:
                uid=self.authorize(sender,method);args=parameters.unpack();secret=None
                if method=='ProvisionDrive':
                    if len(args[0].encode())>8192:raise Failure('request size limit exceeded')
                    request=json.loads(args[0])
                    from helper.provisioning import validate_request
                    validate_request(request)
                    fds=invocation.get_message().get_unix_fd_list()
                    if fds is None or fds.get_length()!=1 or args[1]!=0:raise Failure('exactly one secret FD required')
                    fd=fds.get(args[1])
                    try:secret=read_secret_fd(fd)
                    finally:os.close(fd)
                    args=(request,)
                if any(isinstance(x,str) and (len(x)>4096 or '\0' in x) for x in args):raise Failure('invalid method argument')
                result=self.schedule(method,args,uid,secret)
            payload=json.dumps(result,ensure_ascii=True)
            if len(payload)>2*1024*1024:raise Failure('response limit exceeded')
            invocation.return_value(GLib.Variant('(s)',(payload,)))
        except BaseException as exc:
            invocation.return_value(GLib.Variant('(s)',(json.dumps({'ok':False,'error':str(exc)[:300]}),)))
    def start(self):
        from gi.repository import Gio,GLib
        self.connection=Gio.bus_get_sync(Gio.BusType.SYSTEM,None)
        node=Gio.DBusNodeInfo.new_for_xml(XML)
        self.connection.register_object(OBJECT,node.interfaces[0],self.dispatch,None,None)
        name=Gio.bus_own_name_on_connection(self.connection,BUS,Gio.BusNameOwnerFlags.NONE,None,None)
        GLib.MainLoop().run()

if __name__=='__main__':
    if os.geteuid()!=0:raise SystemExit('The system helper must run as root.')
    Server().start()
