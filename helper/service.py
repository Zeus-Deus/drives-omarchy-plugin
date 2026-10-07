"""System D-Bus entrypoint. Authorize unique sender per operation, no QML privilege."""
import contextlib,fcntl,json,os,pathlib,stat,threading,time,uuid
from helper.common import Common,Failure
from helper.moves import MoveManager,ACTIVE,ScheduleFailure

BUS='io.github.zeus_deus.Drives';OBJECT='/io/github/zeus_deus/Drives'
ACTIONS={'ProvisionDrive':'provision','ResumeDrive':'resume-drive','StartMove':'move','ScheduleMove':'move','MoveBack':'move','ResumeMove':'resume','RollbackMove':'rollback','DeleteOldCopy':'delete','CancelMove':'cancel','RestartMove':'restart','ExportHeaderBackup':'export','CancelRestart':'resume','UnlockDrive':'unlock','ReconnectDrive':'reconnect','PrepareDrive':'prepare'}
SIGNATURES={'ProvisionDrive':'sh','UnlockDrive':'sh','ReconnectDrive':'s','PrepareDrive':'s','ResumeDrive':'s','StartMove':'ss','AssessMove':'ss','ScheduleMove':'ssb','MoveBack':'s','ResumeMove':'s','RollbackMove':'s','DeleteOldCopy':'s','CancelMove':'s','RestartMove':'s','ExportHeaderBackup':'ss','CancelRestart':'s','Status':''}

def pending_restart():
    """The armed maintenance request, as plain facts for the panel."""
    from helper.maintenance import LATCH,latch_request
    if not os.path.lexists(LATCH):return None
    try:value=latch_request()
    except Exception:return {'valid':False}
    from helper.common import boot_id
    return {'valid':True,'moveId':value['moveId'],'action':value['action'],'thisSession':value['armedBootId']==boot_id()}
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
        self.c=common or Common();self.worker_lock=threading.Lock();self.draining=False;self.connection=None
        from helper.health import HealthCache,present_disks
        self.health=HealthCache(present_disks)
        self.jobs=sorted(self.c.records('jobs'),key=lambda j:j['started'])[-24:]
        for job in self.jobs:
            if job['state']=='running':job['state']='paused';job['error']='Interrupted helper job; inspect before explicitly continuing.'
    def authorize(self,sender,method):
        from gi.repository import Gio,GLib
        action='io.github.zeus-deus.drives.'+ACTIONS[method]
        subject=('system-bus-name',{'name':GLib.Variant('s',sender)})
        reply=self.connection.call_sync('org.freedesktop.PolicyKit1','/org/freedesktop/PolicyKit1/Authority','org.freedesktop.PolicyKit1.Authority','CheckAuthorization',
            GLib.Variant('((sa{sv})sa{ss}us)',(subject,action,{},1,'')),None,Gio.DBusCallFlags.NONE,120000,None).unpack()[0]
        if not reply[0]:raise Failure('administrator authorization denied')
        return self.sender_uid(sender)
    def sender_uid(self,sender):
        from gi.repository import Gio,GLib
        if not isinstance(sender,str) or not sender.startswith(':'):raise Failure('unique D-Bus sender required')
        return self.connection.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','GetConnectionUnixUser',GLib.Variant('(s)',(sender,)),None,Gio.DBusCallFlags.NONE,5000,None).unpack()[0]
    def status(self):
        from helper.provisioning import inspect
        moves=MoveManager(self.c).inspect()
        running=next((j for j in reversed(self.jobs) if j['state']=='running'),None)
        if running:
            for m in moves:
                if m.get('updated',0)>=running['started'] and m.get('interruptedState') in ACTIVE:
                    m['state']=m['interruptedState'];m['error']=''
        return {'ok':True,'version':'0.2.0','seamlessMoves':True,'moves':moves,'drives':inspect(self.c),'jobs':list(self.jobs),'restartPending':pending_restart(),'health':self.health.snapshot(),'testFixtureMode':os.environ.get('DRIVES_VM_TESTING')=='1'}
    def schedule(self,method,args,uid,secret=None):
        if self.draining:raise Failure('helper is refreshing its mount namespace; rescan shortly')
        if not self.worker_lock.acquire(blocking=False):raise Failure('another storage operation is already running')
        job={'id':uuid.uuid4().hex,'method':method,'state':'running','started':time.time()}
        lease=None;leased=False
        def release():
            try:
                if leased and lease is not None:lease.__exit__(None,None,None)
            finally:self.worker_lock.release()
        try:
            lease=self.c.storage_lock();lease.__enter__();leased=True
            from helper.maintenance import LATCH,RUNTIME
            # Withdrawing this session's own request is the one write allowed
            # while it is armed; MoveManager.cancel_request re-checks it exactly.
            if os.path.lexists(RUNTIME) or (os.path.lexists(LATCH) and method!='CancelRestart'):
                raise Failure('maintenance controls require inspection before normal storage operations')
            self.c.journal('jobs',job['id'],job)
            self.jobs=(self.jobs+[job])[-24:]
            for old in self.c.records('jobs'):
                if old['id'] not in {j['id'] for j in self.jobs}:self.c.path('jobs',old['id']).unlink()
        except BaseException:
            self.jobs=[j for j in self.jobs if j['id']!=job['id']]
            release()
            raise
        worker_started=threading.Event()
        def work():
            worker_started.set()
            try:
                manager=MoveManager(self.c,uid=uid if method in ('AssessMove','ScheduleMove') else (None if uid==0 else uid),isolated=True)
                if method=='ProvisionDrive':
                    from helper.provisioning import provision
                    result=provision(args[0],secret,self.c)
                elif method=='UnlockDrive':
                    from helper.provisioning import unlock
                    result=unlock(args[0],secret,self.c)
                elif method=='ReconnectDrive':
                    from helper.provisioning import reconnect
                    result=reconnect(args[0],self.c)
                elif method=='PrepareDrive':
                    from helper.preparedrive import run_isolated
                    result=run_isolated(args[0])
                elif method=='ResumeDrive':
                    from helper.provisioning import resume
                    result=resume(args[0],self.c)
                elif method=='StartMove':result=manager.start(*args)
                elif method=='AssessMove':result=manager.assess(*args)
                elif method=='ScheduleMove':result=manager.schedule_move(*args)
                elif method=='ExportHeaderBackup':
                    from helper.provisioning import export_header
                    result=export_header(*args,self.c,uid)
                else:
                    name={'ResumeMove':'resume','RollbackMove':'rollback','MoveBack':'move_back','DeleteOldCopy':'delete_old','CancelMove':'cancel','RestartMove':'restart','CancelRestart':'cancel_request'}[method]
                    result=getattr(manager,name)(args[0])
                job['state']='done';job['result']=result
                if method in ('ProvisionDrive','ResumeDrive'):self.draining=True
            except BaseException as exc:
                job['state']='failed';job['error']=str(exc)[:300]
                if isinstance(exc,ScheduleFailure):job['result']=exc.result
            finally:
                try:self.c.journal('jobs',job['id'],job)
                finally:
                    release()
                    if self.draining:
                        from gi.repository import GLib
                        GLib.timeout_add(750,lambda:os._exit(75))
        try:threading.Thread(target=work,daemon=True).start()
        except BaseException as exc:
            if not worker_started.is_set():
                job['state']='failed';job['error']='Worker did not start: '+str(exc)[:250]
                try:self.c.journal('jobs',job['id'],job)
                finally:release()
            raise
        return {'ok':True,'jobId':job['id']}
    def dispatch(self,connection,sender,object_path,interface,method,parameters,invocation):
        from gi.repository import GLib
        try:
            if method=='Status':result=self.status()
            else:
                uid=self.sender_uid(sender) if method=='AssessMove' else self.authorize(sender,method)
                args=parameters.unpack();secret=None
                if method in ('ProvisionDrive','UnlockDrive'):
                    if len(args[0].encode())>8192:raise Failure('request size limit exceeded')
                    if method=='ProvisionDrive':
                        request=json.loads(args[0])
                        from helper.provisioning import validate_request
                        validate_request(request)
                    else:request=args[0]
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
