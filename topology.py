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
        out.append({'id':ident,'byId':path,'name':disk['name'],'model':clean(disk.get('model') or 'System drive'),'serial':disk.get('serial') or '',
            'displaySerial':clean(disk.get('serial')),'encryptedObject':next(('/org/freedesktop/UDisks2/block_devices/'+''.join(ch if ch.isalnum() else '_%02x'%ord(ch) for ch in os.path.basename(c[-1]['name'])) for c in related if c[-1].get('fstype')=='crypto_LUKS'),''),'size':int(disk.get('size') or 0),'system':disk['name'] in system,'encrypted':crypt,
            'mounts':volume_mounts,'state':'unsupported' if not supported else ('mounted' if volume_mounts else ('unlocked' if open_crypt else ('locked' if crypt else 'new'))),
            'selectable':supported and disk['name'] not in system and not volume_mounts and bool(path and disk.get('serial')),
            'usage':[usage[m['target']] for m in volume_mounts if m['target'] in usage],'transport':disk.get('tran') or 'virtual',
            'smart':'not checked'})
    return out

def snapshot():
    block=blocks();mounts=mount_rows();usage={};seen=set()
    for mount in mounts:
        if mount['fstype'] not in ('btrfs','ext4','xfs') or not mount['source'].startswith('/dev/'):continue
        try:
            p=probe(mount['target'],block,mounts,resolve=False)
            if not p['supported'] or mount['majorMinor'] in seen:continue
            seen.add(mount['majorMinor']);s=os.statvfs(mount['target'])
            total=s.f_blocks*s.f_frsize;free=s.f_bavail*s.f_frsize
            usage[mount['target']]={'target':mount['target'],'total':total,'used':total-free,'free':free,'percent':round(100*(total-free)/total) if total else 0}
        except OSError:continue
    root=probe('/',block,mounts,resolve=False)
    return {'disks':classify(block,mounts,usage),'mounts':mounts,'rootEncrypted':root.get('encrypted',False)}
