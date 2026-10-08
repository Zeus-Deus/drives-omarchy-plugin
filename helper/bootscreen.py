"""Full-screen progress for the maintenance boot, drawn on the text console.

It runs as a thread inside the worker, so the maintenance audit sees no extra
process. Every number on it is measured:

- files found by the pre-copy check (tree_stats progress),
- bytes rsync has read, from /proc/<pid>/io of the worker's rsync children
  (a copy or a checksum comparison reads each byte twice across its
  processes, so read/2 is the bytes handled),
- entries compared by the final file-list comparison.

Drawing is best effort. Any error turns the picture off and the worker goes
back to its plain "Drives: ..." lines on the console (see offline.say).
"""
import fcntl,math,os,random,struct,termios,threading,time

LOGO_PATH='/usr/share/omarchy/logo.txt'
CONSOLES=('/dev/tty0','/dev/console')
# ttfx's default gradient, the screensaver look. Loaded into the console's
# bright palette slots 9-14 on a real Linux VT (ESC ] P n rrggbb).
GRADIENT=('ff9048','e8925f','cf97a0','ab9dff','b4cdf4','bdffea')
PALETTE={0:'000000',7:'a9adbf',8:'5a5e70',15:'ffffff',1:'e05a5a'}
GRAD=(9,10,11,12,13,14);WHITE=15;TEXT=7;DIM=8;RED=1;BAR_EMPTY=8
NOISE='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789#$%&*+=<>?/|\u2591\u2592\u2593\u25a0\u00b7'
HALF={' ':(0,0),'\u2580':(1,0),'\u2584':(0,1),'\u2588':(1,1)}
CHAR={v:k for k,v in HALF.items()}
KDGETMODE=0x4B3B;KDSETMODE=0x4B3A;KD_TEXT=0

# Steps shown along the bottom, and the share of the overall bar each gets.
STEPS={
    'continue':(('prepare','Getting ready',0.00,0.02),('check','Counting files',0.02,0.06),('copy','Copying',0.06,0.55),
                ('verify','Checking every file',0.55,0.92),('compare','Comparing file lists',0.92,0.97),('switch','Switching over',0.97,1.0)),
    'return':(('prepare','Getting ready',0.00,0.04),('copy','Copying back',0.04,0.55),('verify','Checking every file',0.55,0.92),
              ('compare','Comparing file lists',0.92,0.97),('switch','Switching over',0.97,1.0)),
    'rollback':(('prepare','Getting ready',0.00,0.04),('verify','Looking for changes',0.04,0.80),('switch','Putting the original back',0.80,1.0)),
}
STEP_NAMES={'prepare':'ready','check':'count','copy':'copy','verify':'verify','compare':'compare','switch':'switch'}


def size(n):
    n=float(max(0,n or 0));units='BKMGT';i=0
    while n>=1024 and i<4:n/=1024;i+=1
    return (str(round(n)) if n>=100 or i==0 else ('%.1f'%n).rstrip('0').rstrip('.'))+' '+units[i]


def duration(s):
    s=int(math.ceil(max(0,s)))
    if s<60:return str(max(1,s))+' s'
    if s<600:return str(s//60)+' min '+str(s%60//10*10)+' s' if s%60>=10 else str(s//60)+' min'
    if s<3600:return str(round(s/60))+' min'
    return str(s//3600)+' h '+str((s%3600)//60)+' min'


def load_logo(path=LOGO_PATH):
    try:
        with open(path,encoding='utf-8') as f:raw=f.read(16384)
    except (OSError,ValueError):return None
    lines=[line.rstrip() for line in raw.splitlines()]
    while lines and not lines[-1]:lines.pop()
    if not lines or any(c not in HALF for line in lines for c in line):return None
    return lines


def half_width(lines):
    """Same logo at half the width: each character is two vertical pixels,
    so merge neighbouring columns (a pixel is set if either one was)."""
    width=max(len(l) for l in lines);out=[]
    for line in lines:
        line=line.ljust(width+1);row=''
        for c in range(0,width,2):
            a=HALF[line[c]];b=HALF[line[c+1]]
            row+=CHAR[(a[0]|b[0],a[1]|b[1])]
        out.append(row.rstrip())
    return out


def ease_back(x):
    c1=1.70158;c2=c1*1.525
    return (pow(2*x,2)*((c2+1)*2*x-c2))/2 if x<.5 else (pow(2*x-2,2)*((c2+1)*(x*2-2)+c2)+2)/2


def clamp(x,lo=0.0,hi=1.0):return max(lo,min(hi,x))


def rsync_read_bytes(root=None):
    """Bytes read so far by rsync processes below this worker (0 if none)."""
    root=os.getpid() if root is None else root;parents={};names={}
    for entry in os.listdir('/proc'):
        if not entry.isdigit():continue
        try:
            with open('/proc/'+entry+'/stat','rb') as f:stat=f.read()
            head,_,tail=stat.rpartition(b')')
            parents[int(entry)]=int(tail.split()[1]);names[int(entry)]=head.partition(b'(')[2]
        except (OSError,ValueError,IndexError):continue
    children={}
    for pid,ppid in parents.items():children.setdefault(ppid,[]).append(pid)
    total=0;queue=[root]
    while queue:
        pid=queue.pop()
        queue.extend(children.get(pid,()))
        if pid==root or names.get(pid)!=b'rsync':continue
        try:
            with open('/proc/'+str(pid)+'/io','rb') as f:
                for line in f.read().splitlines():
                    if line.startswith(b'rchar:'):total+=int(line.split()[1])
        except (OSError,ValueError):continue
    return total


class State:
    """Everything the screen shows; written by the worker, read by the drawer."""
    def __init__(self,action,title):
        self.action=action if action in STEPS else 'continue';self.title=title
        self.step='prepare';self.label=STEPS[self.action][0][1];self.detail='';self.changed=0.0
        self.total_bytes=0;self.files=0;self.entries=0;self.counted=0;self.count_total=0
        self.fraction=0.0;self.handled=0;self.rate=0.0;self.left=None;self.samples=[]
        self.outcome=None;self.message='';self.headline='';self.footer='';self.finished_at=None


class Screen:
    def __init__(self,action,title,path=None,fps=10,sample=0.5,clock=time.monotonic,palette=None,measure=rsync_read_bytes):
        self.state=State(action,title);self.lock=threading.Lock();self.clock=clock
        self.fps=fps;self.sample_every=sample;self.measure=measure
        self.active=False;self.fd=None;self.thread=None;self.stop=threading.Event();self.frames=0
        self.logo=load_logo();self.small=half_width(self.logo) if self.logo else None
        self.rng=random.Random(7);self.prev=None;self.full_at=0.0;self.start=clock()
        self.palette=palette;self.path=path;self.last_sample=0.0;self.last_frame=None
        self.state.changed=self.start

    # --- worker API ---------------------------------------------------------
    def open(self):
        """Take the console. False means: keep the plain text lines."""
        for path in ((self.path,) if self.path else CONSOLES):
            try:
                fd=os.open(path,os.O_WRONLY|os.O_NOCTTY|os.O_CLOEXEC)
            except OSError:continue
            try:
                rows,cols=self.size(fd)
                if cols<40 or rows<16:raise OSError('console too small')
                vt=self.palette
                if vt is None:
                    try:fcntl.ioctl(fd,KDGETMODE,b'\0\0\0\0');vt=True
                    except OSError:vt=False
                if vt:
                    try:fcntl.ioctl(fd,KDSETMODE,KD_TEXT)
                    except OSError:pass
                self.palette=vt;self.fd=fd
                setup='\033[0m\033[?25l\033[?7l\033[2J\033[H'+('\033[9;0]\033[14;0]' if vt else '')
                if vt:setup+=''.join('\033]P'+format(n,'X')+rgb for n,rgb in list(PALETTE.items())+list(zip(GRAD,GRADIENT)))
                self.write(setup)
                self.active=True
                self.thread=threading.Thread(target=self.loop,name='drives-screen',daemon=True);self.thread.start()
                return True
            except OSError:
                os.close(fd);self.fd=None
        return False

    def step(self,key,detail='',total_bytes=None,count_total=None):
        with self.lock:
            s=self.state;labels={k:label for k,label,_,_ in STEPS[s.action]}
            if key!=s.step or not s.label:s.changed=self.clock()
            s.step=key;s.label=labels.get(key,key);s.detail=detail
            s.handled=0;s.rate=0.0;s.left=None;s.samples=[];s.counted=0
            if total_bytes is not None:s.total_bytes=int(total_bytes)
            if count_total is not None:s.count_total=int(count_total)

    def note(self,detail):
        with self.lock:self.state.detail=detail

    def totals(self,files=None,total_bytes=None,entries=None):
        with self.lock:
            if files is not None:self.state.files=int(files)
            if total_bytes is not None:self.state.total_bytes=int(total_bytes)
            if entries is not None:self.state.entries=int(entries)

    def counted(self,n,total=None):
        with self.lock:
            self.state.counted=max(self.state.counted,int(n))
            if total:self.state.count_total=int(total)

    def finish(self,ok,message='',hold=1.6,headline='',footer=''):
        with self.lock:
            s=self.state;s.outcome='ok' if ok else 'failed';s.message=message;s.headline=headline;s.footer=footer;s.finished_at=self.clock();s.changed=s.finished_at
        if self.active and self.thread:
            # Wait for frames rendered after this change (one may be in flight).
            self.wait_frames(2)
            if ok:
                time.sleep(hold);self.stop.set();self.thread.join(3)
            else:
                # Keep drawing so the headline finishes revealing; the reason stays up.
                time.sleep(0.8)

    def wait_frames(self,n,timeout=3):
        end=time.monotonic()+timeout;target=self.frames+n
        while self.frames<target and self.active and time.monotonic()<end:time.sleep(0.02)

    def close(self):
        """Give the console back to plain text (on failure of the drawing)."""
        self.active=False;self.stop.set()
        if self.fd is not None:
            try:self.write('\033[0m'+('\033]R' if self.palette else '')+'\033[?7h\033[?25h\033[2J\033[H')
            except OSError:pass

    # --- measuring ----------------------------------------------------------
    def sample(self,now):
        s=self.state
        if s.step not in ('copy','verify') or now-self.last_sample<self.sample_every:return
        self.last_sample=now
        try:read=self.measure()
        except OSError:return
        handled=min(s.total_bytes,read//2) if s.total_bytes else read//2
        if handled<s.handled:return # a finished rsync drops out of the tree
        s.handled=handled;s.samples=[x for x in s.samples if now-x[0]<=10]+[(now,handled)]
        if len(s.samples)>=2 and s.samples[-1][0]>s.samples[0][0]:
            s.rate=(s.samples[-1][1]-s.samples[0][1])/(s.samples[-1][0]-s.samples[0][0])
            s.left=(s.total_bytes-handled)/s.rate if s.rate>0 and s.total_bytes else None

    def progress(self):
        """(overall fraction, step fraction or None when unknown)."""
        s=self.state;bands={k:(a,b) for k,_,a,b in STEPS[s.action]}
        if s.outcome=='ok':return 1.0,1.0
        a,b=bands.get(s.step,(0,0));part=None
        if s.step in ('copy','verify') and s.total_bytes:part=clamp(s.handled/s.total_bytes,0,0.995)
        elif s.step=='compare' and s.count_total:part=clamp(s.counted/s.count_total,0,0.99)
        overall=a+(b-a)*(part or 0)
        s.fraction=max(s.fraction,overall) if s.outcome is None else s.fraction
        return s.fraction,part

    def facts(self):
        s=self.state
        if s.outcome=='failed':return ''
        if s.outcome=='ok':return s.message or 'Every file matched. Restarting into your desktop.'
        if s.step in ('copy','verify') and (s.total_bytes or s.handled):
            done='Copied ' if s.step=='copy' else 'Checked '
            out=done+size(s.handled)+(' of '+size(s.total_bytes)+' ('+str(math.floor(100*s.handled/s.total_bytes))+'%)' if s.total_bytes else ' so far')
            if s.rate>0:out+='  \u00b7  '+size(s.rate)+'/s'
            if s.left is not None and s.handled>0:out+='  \u00b7  about '+duration(s.left)+' left'
            return out
        if s.step=='check':return '{:,} files and folders found'.format(s.counted) if s.counted else 'Looking at every file'
        if s.step=='compare':return ('{:,} of {:,} compared'.format(min(s.counted,s.count_total),s.count_total) if s.count_total else '{:,} compared'.format(s.counted)) if s.counted else 'Comparing file lists'
        return s.detail

    # --- drawing ------------------------------------------------------------
    def size(self,fd=None):
        rows,cols,_,_=struct.unpack('HHHH',fcntl.ioctl(self.fd if fd is None else fd,termios.TIOCGWINSZ,b'\0'*8))
        return rows or 25,cols or 80

    def write(self,text):
        data=text.encode('utf-8')
        while data:data=data[os.write(self.fd,data):]

    def loop(self):
        try:
            while not self.stop.is_set():
                now=self.clock()
                with self.lock:
                    self.sample(now)
                    frame=self.render(now-self.start,*self.size())
                self.paint(frame,now)
                self.frames+=1
                self.stop.wait(1/self.fps)
        except Exception:
            self.close()

    def paint(self,frame,now):
        rows=len(frame);cols=len(frame[0]) if rows else 0
        full=self.prev is None or len(self.prev)!=rows or len(self.prev[0])!=cols or now-self.full_at>5
        if full:self.full_at=now
        out=['\033[0m\033[2J'] if self.prev is None or full and (len(self.prev)!=rows or len(self.prev[0])!=cols) else []
        color=None
        for r in range(rows):
            line=frame[r];old=None if full else self.prev[r];c=0
            while c<cols:
                if old is not None and line[c]==old[c]:c+=1;continue
                out.append('\033['+str(r+1)+';'+str(c+1)+'H')
                while c<cols and (old is None or line[c]!=old[c]):
                    if r==rows-1 and c==cols-1:break
                    ch,col=line[c]
                    if col!=color:out.append(sgr(col));color=col
                    out.append(ch);c+=1
                c+=1
        if out:self.write(''.join(out))
        self.prev=frame;self.last_frame=frame

    def render(self,t,rows,cols):
        s=self.state;grid=[[(' ',TEXT)]*cols for _ in range(rows)]
        def put(r,c,text,col):
            if 0<=r<rows:
                for i,ch in enumerate(text):
                    if 0<=c+i<cols:grid[r][c+i]=(ch,col)
        def center(r,text,col):put(r,max(0,(cols-len(text))//2),text[:cols],col)
        logo=self.logo if self.logo and cols>=max(len(l) for l in self.logo)+2 and rows>=28 else (self.small if self.small and cols>=44 and rows>=22 else None)
        lh=len(logo) if logo else 0;lw=max(len(l) for l in logo) if logo else 0
        block=(lh+3 if logo else 0)+9;top=max(1,(rows-block-2)//2)
        now=t;intro=2.4
        if logo:
            lc=(cols-lw)//2;cells=[(r,c,ch) for r,line in enumerate(logo) for c,ch in enumerate(line) if ch!=' ']
            rng=random.Random(7);starts=[(rng.randrange(rows),rng.randrange(cols),rng.random()*0.5) for _ in cells]
            for (r,c,ch),(sr,sc,delay) in zip(cells,starts):
                tr,tc=top+r,lc+c;col=GRAD[min(5,r*6//max(1,lh))]
                if s.outcome=='failed':col=DIM
                elif now<intro:
                    p=ease_back(clamp((now-delay)/(intro-0.6)));tr=round(sr+(tr-sr)*p);tc=round(sc+(tc-sc)*p)
                    if p<0.98:col=DIM
                elif s.outcome=='ok':
                    sweep=((self.clock()-s.finished_at)*70)%(lw+30)-15
                    if abs(c+r*0.6-sweep)<4:col=WHITE
                put(tr,tc,ch,col)
        if now<intro-0.4 and logo:return grid
        row=top+(lh+3 if logo else 0)
        failed=s.outcome=='failed';ok=s.outcome=='ok'
        label=(s.headline or 'Stopped safely') if failed else ('Moved' if ok and s.action=='continue' else ('Done' if ok else s.label))
        age=self.clock()-s.changed;center(row,decrypt(label,age,hash(label)%997),RED if failed else (GRAD[-1] if ok else WHITE))
        width=max(10,min(64,cols-12));bc=max(0,(cols-width-6)//2);overall,part=self.progress()
        if failed:
            lines=wrap(s.message,min(cols-4,76))
            for i,line in enumerate(lines[:max(1,rows-row-6)]):center(row+2+i,line,TEXT)
            center(rows-2,s.footer or 'Nothing was deleted. Both copies are kept.',DIM)
            return grid
        filled=round(width*overall)
        for k in range(width):
            if k<filled:put(row+2,bc+k,'\u2588',GRAD[min(5,k*6//width)])
            elif part is None and s.step not in ('copy','verify') and not ok:
                d=abs(((now*22)%(width+16))-8-k);put(row+2,bc+k,'\u2593' if d<2 else ('\u2592' if d<5 else '\u2591'),GRAD[3] if d<5 else BAR_EMPTY)
            else:put(row+2,bc+k,'\u2591',BAR_EMPTY)
        put(row+2,bc+width+2,str(math.floor(overall*100)).rjust(3)+'%',WHITE)
        center(row+4,self.facts()[:cols],TEXT)
        keys=[k for k,_,_,_ in STEPS[s.action]];current=len(keys) if ok else keys.index(s.step) if s.step in keys else 0
        names=[STEP_NAMES.get(k,k) for k in keys];text='  \u00b7  '.join(names);c=max(0,(cols-len(text))//2)
        for i,name in enumerate(names):
            put(row+7,c,name,GRAD[3] if i<current else (WHITE if i==current else DIM));c+=len(name)
            if i<len(names)-1:put(row+7,c,'  \u00b7  ',DIM);c+=5
        center(rows-2,(s.title+'  \u00b7  ' if s.title else '')+("Don't turn off the computer" if not ok else (s.footer or 'Restarting')),DIM)
        return grid


def sgr(col):
    return '\033[0;'+('1;' if col>=8 else '')+str(30+col%8)+'m'


def decrypt(text,age,seed):
    """Reveal left to right over 0.7 s; hidden characters flicker (ttfx decrypt look)."""
    reveal=clamp(age/0.7)*len(text);rng=random.Random(seed*1000+int(age*30));out=''
    for k,ch in enumerate(text):out+=ch if (k<reveal-2 or ch==' ' or age>=0.7) else NOISE[rng.randrange(len(NOISE))]
    return out


def wrap(text,width):
    words=str(text).split();lines=[];line=''
    for w in words:
        if line and len(line)+1+len(w)>width:lines.append(line);line=w
        else:line=(line+' '+w).strip()
    if line:lines.append(line)
    return lines or ['']


def demo(argv=None):
    """Show the screen on this console with a real copy: run it from a text
    console (Ctrl+Alt+F3), not a terminal window. Copies /usr/share/omarchy to
    a private temporary folder, checks every file, then deletes the copy.
    Nothing outside that temporary folder is touched."""
    import shutil,subprocess,sys,tempfile
    sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from helper.moves import tree_stats
    source=(argv or [])[0] if argv else '/usr/share/omarchy'
    work=tempfile.mkdtemp(prefix='drives-demo-')
    try:
        screen=Screen('continue','Demo: copying '+source+' (nothing is moved)',path=os.ttyname(0))
        if not screen.open():raise SystemExit('Run this from a text console (Ctrl+Alt+F3).')
        try:
            time.sleep(2.6)
            screen.step('check');stats=tree_stats(source,progress=screen.counted)
            screen.totals(files=stats['files'],total_bytes=stats['bytes'],entries=screen.state.counted)
            screen.step('copy',total_bytes=stats['bytes'])
            subprocess.run(['rsync','-aS','--',source+'/',work+'/copy/'],check=True,stdout=subprocess.DEVNULL)
            screen.step('verify',total_bytes=stats['bytes'])
            # Same real checksum pass as a move. Run as your user, so owners differ
            # from a root-owned source; compare content and counts only.
            diff=subprocess.run(['rsync','-rlt','--checksum','--dry-run','--itemize-changes','--',source+'/',work+'/copy/'],check=True,capture_output=True).stdout
            if diff.strip():raise RuntimeError('the copy differs: '+diff.decode('utf-8','replace')[:120])
            first=[0];screen.step('compare',count_total=2*screen.state.entries)
            def one(n):first[0]=n;screen.counted(n)
            a=tree_stats(source,progress=one);b=tree_stats(work+'/copy',progress=lambda n:screen.counted(first[0]+n))
            if (a['files'],a['bytes'])!=(b['files'],b['bytes']):raise RuntimeError('file count or size differs')
            screen.step('switch');time.sleep(1)
            screen.finish(True,'Every file matched. Demo copy deleted; nothing was moved.',hold=4,footer='Done')
        except BaseException as error:
            screen.finish(False,str(error)[:200],headline='Demo stopped',footer='Press Enter to go back.')
            input()
        finally:screen.close()
    finally:shutil.rmtree(work,ignore_errors=True)


if __name__=='__main__':
    import sys
    if sys.argv[1:2]==['--demo']:demo(sys.argv[2:])
    else:raise SystemExit('Usage: python3 -m helper.bootscreen --demo [folder]')
