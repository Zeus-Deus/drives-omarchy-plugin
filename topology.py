"""Read-only topology: kernel mountinfo first, no automount path probes."""
import json,os,pathlib,re
from helper.common import Failure,run,mount_rows,mount_for

def clean(value):
    return re.sub(r'\s+',' ',re.sub('[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u206f]','',str(value or '')))[:240]

def blocks():
    return json.loads(run(['lsblk','-J','-p','-b','-o','NAME,TYPE,TRAN,MODEL,SERIAL,WWN,SIZE,FSTYPE,UUID,MOUNTPOINTS'],timeout=5))

def chains(block):
    result={}
    def visit(node,parents):
        chain=parents+[node]
        for name in dict.fromkeys([node['name'],os.path.realpath(node['name'])]):result.setdefault(name,[]).append(chain)
        for child in node.get('children',[]):visit(child,chain)
    for root in block.get('blockdevices',[]):visit(root,[])
    return result

def by_id(name):
    entries=[]
    for p in pathlib.Path('/dev/disk/by-id').glob('*'):
        try:
            if '-part' not in p.name and str(p.resolve())==name:entries.append(str(p))
        except OSError:continue
    return sorted(entries,key=lambda p:(not ('wwn-' in p or 'nvme-eui.' in p),p))[0] if entries else ''

def device_source(mount):return os.path.realpath(re.sub(r'\[.*\]$','',mount['source']))

def probe(path,block=None,mounts=None,resolve=True):
    if resolve:path=os.path.realpath(path,strict=True)
    block=blocks() if block is None else block;mounts=mount_rows() if mounts is None else mounts
    mount=mount_for(path,mounts);candidates=chains(block).get(device_source(mount),[])
    if mount['fstype']=='autofs':return {'path':path,'mount':mount,'supported':False,'reason':'automount is inactive; no access triggered'}
    allowed={'disk','part','crypt'}
    supported=len(candidates)==1 and candidates[0][0]['type']=='disk' and all(n['type'] in allowed for n in candidates[0])
    if supported:
        fsuuid=candidates[0][-1].get('uuid')
        members={c[0]['name'] for cs in chains(block).values() for c in cs if fsuuid and c[-1].get('uuid')==fsuuid and c[-1].get('fstype')=='btrfs'}
        if len(members)>1:supported=False
    if supported:
        chain=candidates[0];disk=chain[0];encrypted=any(n['type']=='crypt' for n in chain)
        return {'path':path,'mount':mount,'supported':True,'encrypted':encrypted,'disk':disk,'chain':chain}
    return {'path':path,'mount':mount,'supported':False,'reason':'ambiguous or unsupported storage topology','chain':candidates}

def classify(block,mounts,usage):
    indexed=chains(block)
    system=set();systemUUIDs=set()
    for mount in mounts:
        if mount['target'] in ('/','/boot','/home','/usr','/var'):
            for chain in indexed.get(device_source(mount),[]):
                system.add(chain[0]['name'])
                if chain[-1].get('uuid'):systemUUIDs.add(chain[-1]['uuid'])
    for cs in indexed.values():
        for chain in cs:
            if chain[-1].get('uuid') in systemUUIDs:system.add(chain[0]['name'])
    out=[]
    for disk in block.get('blockdevices',[]):
        if disk.get('type')!='disk' or disk['name'].startswith('/dev/zram'):continue
        related=[chain for cs in indexed.values() for chain in cs if chain[0]['name']==disk['name']]
        names={os.path.realpath(c[-1]['name']) for c in related}
        volume_mounts=[m for m in mounts if device_source(m) in names and m['fstype']!='autofs']
        crypt=any(n.get('type')=='crypt' or n.get('fstype')=='crypto_LUKS' for c in related for n in c)
        open_crypt=any(n.get('type')=='crypt' for c in related for n in c)
        ident=disk.get('wwn') or disk.get('serial') or by_id(disk['name']) or disk['name']
        supported=all(n.get('type') in ('disk','part','crypt') for c in related for n in c)
        for c in related:
            fsuuid=c[-1].get('uuid')
            members={a[0]['name'] for cs in indexed.values() for a in cs if fsuuid and a[-1].get('uuid')==fsuuid and a[-1].get('fstype')=='btrfs'}
            if len(members)>1:supported=False
        path=by_id(disk['name'])
        mapper=next((os.path.basename(n['name']) for c in related for n in c if n.get('type')=='crypt'),'')
        out.append({'mapper':mapper,'id':ident,'byId':path,'name':disk['name'],'model':clean(disk.get('model') or 'System drive'),'serial':disk.get('serial') or '',
            'displaySerial':clean(disk.get('serial')),'encryptedObject':next(('/org/freedesktop/UDisks2/block_devices/'+''.join(ch if ch.isalnum() else '_%02x'%ord(ch) for ch in os.path.basename(c[-1]['name'])) for c in related if c[-1].get('fstype')=='crypto_LUKS'),''),'size':int(disk.get('size') or 0),'system':disk['name'] in system,'encrypted':crypt,
            'mounts':volume_mounts,'state':'unsupported' if not supported else ('mounted' if volume_mounts else ('unlocked' if open_crypt else ('locked' if crypt else 'new'))),
            'selectable':supported and disk['name'] not in system and not volume_mounts and bool(path and disk.get('serial')),
            'usage':[usage[m['target']] for m in volume_mounts if m['target'] in usage],'transport':disk.get('tran') or 'virtual',
            'smart':'not checked'})
    return out

def diskstats(text=None):
    """Cumulative bytes read/written per whole disk from /proc/diskstats.
    Sector counts there are always 512-byte units, whatever the device."""
    if text is None:
        try:text=pathlib.Path('/proc/diskstats').read_text()
        except OSError:return {}
    out={}
    for line in text.splitlines():
        f=line.split()
        # major minor name reads merged sectors-read ms writes merged sectors-written ...
        if len(f)<10 or not re.fullmatch('[A-Za-z0-9._-]+',f[2]):continue
        try:out['/dev/'+f[2]]={'read':int(f[5])*512,'written':int(f[9])*512}
        except ValueError:continue
    return out

GENERATOR='/run/systemd/generator'

def boot_unlock(mapper,root=GENERATOR):
    """How systemd opens this mapper at boot, read from the unit its crypttab
    generator wrote (crypttab itself is root-only): 'keyfile', 'prompt', or ''
    when nothing opens it at boot."""
    if not re.fullmatch('[A-Za-z0-9_.-]{1,64}',mapper or ''):return ''
    try:text=pathlib.Path(root,'systemd-cryptsetup@'+mapper+'.service').read_text()[:16384]
    except OSError:return ''
    m=re.search(r"^ExecStart=\S+ attach '([^']*)' '([^']*)' '([^']*)'",text,re.M)
    if not m or m.group(1)!=mapper:return ''
    return 'prompt' if m.group(3) in ('','-','none') else 'keyfile'

def du(args,budget,emit=None,argv0=('ionice','-c3','nice','-n19','du')):
    """Run `du -x -B1 <args>` for at most `budget` seconds (read-only). du
    prints each directory as soon as it is measured; each finished line is
    passed to emit() right away, so a slow tree never hides the others.
    Returns (entries, complete); complete is False on timeout."""
    import selectors,subprocess,time
    p=subprocess.Popen([*argv0,'-x','-B1',*args],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL,
        env={'PATH':'/usr/bin:/bin','LC_ALL':'C'},start_new_session=True)
    pipe=p.stdout;assert pipe is not None
    buf=b'';entries=[];complete=True;end=time.monotonic()+max(0.1,budget)
    sel=selectors.DefaultSelector();sel.register(pipe,selectors.EVENT_READ)
    def take(line):
        size,_,path=line.decode('utf-8','replace').partition('\t')
        if size.isdigit() and path:
            e={'path':path,'bytes':int(size)};entries.append(e)
            if emit:emit(e)
    try:
        while True:
            left=end-time.monotonic()
            if left<=0:complete=False;break
            if not sel.select(min(left,.5)):continue
            chunk=os.read(pipe.fileno(),65536)
            if not chunk:break
            buf+=chunk
            *lines,buf=buf.split(b'\n')
            for line in lines:take(line)
            if len(buf)>65536 or len(entries)>4096:complete=False;break
    finally:
        if p.poll() is None:
            try:os.killpg(p.pid,9)
            except ProcessLookupError:pass
        p.wait();sel.close();pipe.close()
    return entries,complete

def snapshot():
    block=blocks();mounts=mount_rows();usage={};seen=set()
    # A drive's own mount (fsroot /) before its bind mounts, so usage is
    # reported under /data and not under whichever bind came first.
    for mount in sorted(mounts,key=lambda m:m.get('fsroot')!='/'):
        if mount['fstype'] not in ('btrfs','ext4','xfs') or not mount['source'].startswith('/dev/'):continue
        try:
            p=probe(mount['target'],block,mounts,resolve=False)
            if not p['supported'] or mount['majorMinor'] in seen:continue
            seen.add(mount['majorMinor']);s=os.statvfs(mount['target']);st=os.stat(mount['target'])
            total=s.f_blocks*s.f_frsize;free=s.f_bavail*s.f_frsize
            usage[mount['target']]={'target':mount['target'],'total':total,'used':total-free,'free':free,'percent':round(100*(total-free)/total) if total else 0,
                # The helper only moves folders onto a mount that no user can rename things in.
                'rootOwned':st.st_uid==0 and not st.st_mode&0o022}
        except OSError:continue
    root=probe('/',block,mounts,resolve=False)
    disks=classify(block,mounts,usage);io=diskstats()
    for d in disks:d['io']=io.get(d['name']);d['bootUnlock']=boot_unlock(d['mapper'])
    import time
    return {'disks':disks,'mounts':mounts,'rootEncrypted':root.get('encrypted',False),'sampledAt':int(time.monotonic()*1000)}
