"""Bounded subprocesses and durable root-owned storage journals."""
import contextlib, fcntl, json, os, pathlib, re, selectors, signal, stat, subprocess, time, uuid

class Failure(RuntimeError):
    pass

def run(argv, data=None, timeout=30, cap=2*1024*1024, accepted=(0,)):
    if not isinstance(argv,(list,tuple)) or not argv or any(not isinstance(x,str) or '\0' in x for x in argv):
        raise Failure('invalid command')
    p=subprocess.Popen(argv,stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,
        env={'PATH':'/usr/bin:/bin','LC_ALL':'C','HOME':'/root' if os.geteuid()==0 else os.path.expanduser('~')})
    out,err=bytearray(),bytearray();sel=selectors.DefaultSelector();end=time.monotonic()+timeout
    try:
        if data is not None:
            if len(data)>4096:raise Failure('input limit exceeded')
            p.stdin.write(data);p.stdin.close()
        for pipe,tag in [(p.stdout,'out'),(p.stderr,'err')]:
            os.set_blocking(pipe.fileno(),False);sel.register(pipe,selectors.EVENT_READ,tag)
        while sel.get_map():
            if time.monotonic()>end:raise Failure('command deadline exceeded')
            for key,_ in sel.select(min(.1,max(.001,end-time.monotonic()))):
                chunk=os.read(key.fileobj.fileno(),65536)
                if not chunk:sel.unregister(key.fileobj);continue
                target=out if key.data=='out' else err
                if len(target)+len(chunk)>cap:raise Failure('command output limit exceeded')
                target.extend(chunk)
        code=p.wait(timeout=max(.1,end-time.monotonic()))
        if code not in accepted:raise Failure(pathlib.Path(argv[0]).name+' failed (exit '+str(code)+')')
        return bytes(out)
    except BaseException:
        with contextlib.suppress(ProcessLookupError):os.killpg(p.pid,signal.SIGKILL)
        p.wait()
        raise
    finally:
        sel.close();p.stdout.close();p.stderr.close()

def read_regular(path,cap=2*1024*1024):
    fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size>cap:raise Failure('unsafe or oversized file')
        data=bytearray()
        while True:
            chunk=os.read(fd,min(65536,cap+1-len(data)))
            if not chunk:break
            data.extend(chunk)
            if len(data)>cap:raise Failure('file limit exceeded')
        return bytes(data)
    finally:os.close(fd)

def atomic(path,data,mode=0o600):
    path=pathlib.Path(path)
    if path.is_symlink():raise Failure('refusing symbolic link')
    temp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,mode)
    try:
        view=memoryview(data)
        while view:view=view[os.write(fd,view):]
        os.fsync(fd)
    finally:os.close(fd)
    try:
        os.replace(temp,path)
        d=os.open(path.parent,os.O_DIRECTORY|os.O_NOFOLLOW);os.fsync(d);os.close(d)
    finally:
        with contextlib.suppress(FileNotFoundError):temp.unlink()

def boot_id():return pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()

def mount_rows(scope='host'):
    if scope not in ('host','self'):raise Failure('invalid mount namespace scope')
    data=read_regular('/proc/1/mountinfo' if os.geteuid()==0 and scope=='host' else '/proc/self/mountinfo').decode()
    def unescape(s):return re.sub(r'\\([0-7]{3})',lambda m:chr(int(m[1],8)),s)
    rows=[]
    for line in data.splitlines():
        a,b=line.split(' - ',1);left=a.split();right=b.split()
        rows.append({'id':int(left[0]),'parent':int(left[1]),'majorMinor':left[2],
            'fsroot':unescape(left[3]),'target':unescape(left[4]),'options':left[5]+','+right[2],
            'fstype':right[0],'source':unescape(right[1])})
    return rows

def mount_for(path,rows=None):
    rows=mount_rows() if rows is None else rows
    matches=[r for r in rows if path==r['target'] or path.startswith(r['target'].rstrip('/')+'/')]
    if not matches:raise Failure('no mount for path')
    deepest=max(len(r['target']) for r in matches)
    matches=[r for r in matches if len(r['target'])==deepest]
    return next((r for r in reversed(matches) if r['fstype']!='autofs'),matches[-1])

class Common:
    run=staticmethod(run)
    boot_id=staticmethod(boot_id)
    def __init__(self,state_dir: str | os.PathLike='/var/lib/drives-helper'):
        self.state_dir=pathlib.Path(state_dir)
        if self.state_dir.is_symlink():raise Failure('unsafe state directory')
        self.state_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
        for kind in ('moves','drives','jobs'):
            p=self.state_dir/kind
            if p.is_symlink():raise Failure('unsafe journal directory')
            p.mkdir(mode=0o700,exist_ok=True)
    def path(self,kind,id):
        if kind not in ('moves','drives','jobs') or not re.fullmatch('[a-f0-9]{32}',id):raise Failure('invalid journal id')
        return self.state_dir/kind/(id+'.json')
    def journal(self,kind,id,value):
        atomic(self.path(kind,id),json.dumps(value,ensure_ascii=True,separators=(',',':')).encode())
    def read(self,kind,id):
        try:return json.loads(read_regular(self.path(kind,id),1024*1024))
        except (OSError,ValueError) as e:raise Failure('invalid or missing journal') from e
    def records(self,kind):
        paths=sorted((self.state_dir/kind).glob('*.json'))
        if len(paths)>256:raise Failure('journal count limit exceeded')
        return [self.read(kind,p.stem) for p in paths]
    @contextlib.contextmanager
    def storage_lock(self):
        """A root-private, process-lifetime lease shared by all storage writers."""
        from helper.maintenance import private_directory
        directory=private_directory(self.state_dir)
        fd=os.open(directory/'storage.lock',os.O_RDWR|os.O_CREAT|os.O_NONBLOCK|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o077 or info.st_nlink!=1:
                raise Failure('unsafe storage transaction lock')
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise Failure('another storage operation is already running') from None
            yield
        finally:os.close(fd)
    def config(self,path,id,line=None):
        path=pathlib.Path(path)
        if str(path) not in ('/etc/fstab','/etc/crypttab'):raise Failure('invalid configuration file')
        if not re.fullmatch('[a-f0-9]{32}',id):raise Failure('invalid configuration owner')
        if self.state_dir != pathlib.Path('/var/lib/drives-helper'):raise Failure('system config disabled for custom state directory')
        if line is not None and (len(line)>4096 or '\n' in line or '\0' in line):raise Failure('invalid configuration line')
        request=self.state_dir/('config-request-'+uuid.uuid4().hex+'.json')
        atomic(request,json.dumps({'kind':path.name,'id':id,'line':line}).encode())
        run(['systemd-run','--quiet','--wait','--collect','--pipe',
            '--unit=drives-config-'+uuid.uuid4().hex,
            '--property=WorkingDirectory=/usr/lib/drives-helper',
            '--property=ProtectSystem=strict',
            '--property=ReadWritePaths=/etc /var/lib/drives-helper',
            '--property=ReadOnlyPaths=/etc/passwd /etc/shadow -/etc/sudoers -/etc/polkit-1 /etc/systemd /etc/dbus-1 /etc/cryptsetup-keys.d',
            '--property=ProtectHome=yes','--property=PrivateTmp=yes',
            '--property=NoNewPrivileges=yes','--property=CapabilityBoundingSet=CAP_DAC_OVERRIDE',
            '/usr/bin/python3','-B','-m','helper.configwriter',request.name],timeout=60)

def hide_from_automount(name,luks_uuid=None,serial=None):
    """Keep udiskie/file managers from prompting for a Drives-managed volume.
    With serial= (and no UUID yet) the disk is hidden while it is set up."""
    from helper.udevhide import rule_text,setup_text
    if serial is not None:setup_text(name,serial);args=['--setup',name,serial]
    else:rule_text(name,luks_uuid);args=[name,luks_uuid]  # validate before starting anything
    run(['systemd-run','--quiet','--wait','--collect','--pipe',
        '--unit=drives-udev-'+uuid.uuid4().hex,
        '--property=WorkingDirectory=/usr/lib/drives-helper',
        '--property=ProtectSystem=strict','--property=ReadWritePaths=/etc/udev/rules.d',
        '--property=ProtectHome=yes','--property=PrivateTmp=yes','--property=NoNewPrivileges=yes',
        '--property=CapabilityBoundingSet=CAP_DAC_OVERRIDE',
        '/usr/bin/python3','-B','-m','helper.udevhide',*args],timeout=120)

def prepare_mountpoint(path):
    from helper.mountpoint import validate
    validate(path)
    # /dataN may not exist when the long-lived strict namespace starts.
    # Only this fixed directory worker gets a fresh namespace writable at /.
    run(['systemd-run','--quiet','--wait','--collect','--pipe',
        '--unit=drives-placeholder-'+uuid.uuid4().hex,
        '--property=WorkingDirectory=/usr/lib/drives-helper',
        '--property=ProtectSystem=full',
        '--property=ReadOnlyPaths=/var /home /root /run /dev /proc /sys',
        '--property=NoNewPrivileges=yes','--property=PrivateTmp=yes',
        '--property=CapabilityBoundingSet=CAP_DAC_OVERRIDE CAP_FOWNER CAP_LINUX_IMMUTABLE',
        '/usr/bin/python3','-B','-m','helper.mountpoint',path],timeout=60)

def escape_fstab(path):
    if '\0' in path or '\n' in path:raise Failure('path cannot be represented in fstab')
    return path.replace('\\','\\134').replace(' ','\\040').replace('\t','\\011')


def host_mount(what,where,fstype,options):
    """Ask PID1 to mount in the system namespace, not the sandbox's namespace."""
    from gi.repository import Gio,GLib
    unit=run(['systemd-escape','--path','--suffix=mount',where]).decode().strip()
    props=[('What',GLib.Variant('s',what)),('Where',GLib.Variant('s',where)),
           ('Type',GLib.Variant('s',fstype)),('Options',GLib.Variant('s',options))]
    bus=Gio.bus_get_sync(Gio.BusType.SYSTEM,None)
    bus.call_sync('org.freedesktop.systemd1','/org/freedesktop/systemd1','org.freedesktop.systemd1.Manager','StartTransientUnit',
        GLib.Variant('(ssa(sv)a(sa(sv)))',(unit,'fail',props,[])),None,Gio.DBusCallFlags.NONE,30000,None)
    end=time.monotonic()+30
    while time.monotonic()<end:
        state=run(['systemctl','show',unit,'--property=ActiveState','--value']).strip()
        if state==b'active':return
        if state==b'failed':raise Failure('system mount unit failed')
        time.sleep(.05)
    raise Failure('system mount unit deadline exceeded')

