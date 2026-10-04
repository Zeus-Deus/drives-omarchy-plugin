"""Fixed, root-only atomic config worker in a fresh /etc directory sandbox."""
import fcntl,json,os,pathlib,re,sys
from helper.common import Common,Failure,atomic,read_regular

def transform(content,id,line,owned):
    if not re.fullmatch('[a-f0-9]{32}',id):raise Failure('invalid configuration owner')
    if line is not None and (not isinstance(line,str) or len(line)>4096 or any(x in line for x in ('\0','\r','\n'))):raise Failure('invalid configuration line')
    marker='# drives-helper '+id;rows=content.splitlines(keepends=True);out=[];i=0;seen=False
    while i<len(rows):
        row=rows[i]
        if row.rstrip('\n')==marker:
            if seen or i+1==len(rows) or rows[i+1].rstrip('\n') not in owned:raise Failure('owned config entry changed; inspect it before replacing')
            seen=True;i+=2;continue
        out.append(row);i+=1
    result=''.join(out)
    if line is not None:
        if result and not result.endswith('\n'):result+='\n'
        result+=marker+'\n'+line+'\n'
    return result

def write_owned(c,kind,id,line,etc='/etc'):
    """Replace only the entry marked with this owner id (None removes it)."""
    if kind not in ('fstab','crypttab') or not re.fullmatch('[a-f0-9]{32}',id):raise Failure('invalid config request')
    path=pathlib.Path(etc)/kind;ownership=c.state_dir/('config-'+kind+'-'+id+'.json')
    lock=os.open(c.state_dir/'config.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(lock,fcntl.LOCK_EX)
        prior=json.loads(read_regular(ownership,8192)) if ownership.exists() else {}
        owned={v for v in prior.values() if isinstance(v,str)}
        content=read_regular(path).decode() if path.exists() else ''
        updated=transform(content,id,line,owned)
        backup=c.state_dir/(kind+'.'+__import__('uuid').uuid4().hex+'.backup')
        atomic(backup,content.encode())
        atomic(ownership,json.dumps({'before':prior.get('line',prior.get('after')),'after':line}).encode())
        atomic(path,updated.encode(),0o644 if kind=='fstab' else 0o600)
        atomic(ownership,json.dumps({'line':line}).encode())
    finally:os.close(lock)

def apply(payload_name):
    if os.geteuid()!=0 or not re.fullmatch(r'config-request-[a-f0-9]{32}\.json',payload_name):raise Failure('invalid root config invocation')
    c=Common();payload=c.state_dir/payload_name
    p=json.loads(read_regular(payload,8192))
    if set(p)!={'kind','id','line'}:raise Failure('invalid config request')
    write_owned(c,p['kind'],p['id'],p['line'])
    payload.unlink()

if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('invalid invocation')
    apply(sys.argv[1])
