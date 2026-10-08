"""Maintenance-boot progress screen: real numbers, and it can never affect a move."""
import fcntl,os,pathlib,pty,shutil,struct,subprocess,sys,termios,threading,time
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper import bootscreen,offline
from helper.common import Common
from helper.moves import MoveManager,tree_stats

pyte=pytest.importorskip('pyte')


class Console:
    """A pty sized like a 2560x1440 console, read back through a VT emulator."""
    def __init__(self,rows=45,cols=160):
        self.master,self.slave=pty.openpty();self.rows=rows;self.cols=cols
        fcntl.ioctl(self.slave,termios.TIOCSWINSZ,struct.pack('HHHH',rows,cols,0,0))
        self.screen=pyte.Screen(cols,rows);self.stream=pyte.ByteStream(self.screen);self.raw=bytearray()
        self.alive=True;threading.Thread(target=self.read,daemon=True).start()
    def read(self):
        while self.alive:
            try:data=os.read(self.master,65536)
            except OSError:return
            self.raw.extend(data);self.stream.feed(data)
    def text(self,settle=0.3):
        time.sleep(settle);return '\n'.join(line.rstrip() for line in self.screen.display)
    @property
    def path(self):return os.ttyname(self.slave)


def tree(root,big=20,small=600):
    (root/'media').mkdir(parents=True);(root/'notes').mkdir()
    for i in range(big):(root/'media'/('clip'+str(i))).write_bytes(os.urandom(1_000_000))
    for i in range(small):(root/'notes'/('n'+str(i))).write_text('note '+str(i))
    return root


def test_live_numbers_come_from_the_real_copy(tmp_path):
    src=tree(tmp_path/'src');dst=tmp_path/'dst';dst.mkdir();con=Console()
    s=bootscreen.Screen('continue','Moving ~/Videos',path=con.path,palette=False,sample=0.1)
    assert s.open()
    try:
        assert 'Getting ready' in con.text(2.8) and '\u2588\u2588\u2588' in con.text(0), 'logo and first step'
        s.step('check');stats=tree_stats(str(src),progress=s.counted)
        s.totals(files=stats['files'],total_bytes=stats['bytes'],entries=s.state.counted)
        s.step('copy',total_bytes=stats['bytes'])
        p=subprocess.Popen(['rsync','-a','--bwlimit=8000','--',str(src)+'/',str(dst)+'/'])
        seen=[]
        while p.poll() is None:
            time.sleep(0.3);screen=con.text(0)
            line=next((l.strip() for l in screen.splitlines() if l.strip().startswith('Copied ')),None)
            if line:seen.append(line)
        assert p.returncode==0 and seen, 'no live copy line was drawn'
        assert any(' of '+bootscreen.size(stats['bytes'])+' ' in l and '/s' in l for l in seen), seen[-3:]
        handled=[float(l.split()[1]) for l in seen]
        assert handled==sorted(handled) and handled[-1]>handled[0], 'copied amount must climb'
        s.step('verify',total_bytes=stats['bytes'])
        result=MoveManager(Common(tmp_path/'state')).verify(str(src),str(dst),screen=s)
        assert result['files']==stats['files']
        assert '{0:,} of {0:,} compared'.format(2*s.state.entries) in con.text()
        s.step('switch');s.finish(True,hold=0)
        final=con.text()
        assert '100%' in final and 'Every file matched' in final
    finally:s.close();con.alive=False


def test_failure_replaces_the_bar_with_the_reason(tmp_path):
    con=Console();s=bootscreen.Screen('continue','Moving ~/Videos',path=con.path,palette=False)
    assert s.open()
    try:
        time.sleep(2.6);s.step('copy',total_bytes=10)
        s.finish(False,'Not moved this time; nothing was changed. (the destination drive is not connected)',headline='Not moved this time',footer='Nothing was changed. Restarting into your desktop.')
        text=' '.join(con.text().split()) # the reason wraps across lines
        assert 'Not moved this time' in text and 'destination drive is not connected' in text and 'Restarting into your desktop' in text
        assert '%' not in text, 'no progress bar on a failure'
    finally:s.close();con.alive=False


def test_small_console_uses_the_half_width_logo(tmp_path):
    con=Console(25,80);s=bootscreen.Screen('return','Moving ~/x back',path=con.path,palette=False)
    assert s.open()
    try:
        text=con.text(2.8)
        assert '\u2588' in text and max(len(l) for l in text.splitlines())<=80
        assert 'ready  \u00b7  copy  \u00b7  verify' in text
    finally:s.close();con.alive=False


def test_half_width_logo_keeps_its_shape():
    logo=bootscreen.load_logo()
    if logo is None:pytest.skip('Omarchy logo not installed')
    small=bootscreen.half_width(logo)
    assert len(small)==len(logo) and max(map(len,small))<=(max(map(len,logo))+1)//2


def test_no_console_means_plain_text(tmp_path):
    s=bootscreen.Screen('continue','x',path=str(tmp_path/'missing'))
    assert s.open() is False and s.active is False


def test_rsync_bytes_are_counted_from_this_process_tree_only(tmp_path):
    src=tree(tmp_path/'src',big=8,small=10);total=sum(f.stat().st_size for f in src.rglob('*') if f.is_file())
    # An rsync that is not our descendant (setsid -f reparents it) is ignored.
    marker=tmp_path/'other-done'
    subprocess.run(['setsid','-f','sh','-c','rsync -a --bwlimit=4000 -- "$1"/ "$2"/; touch "$3"','sh',str(src),str(tmp_path/'other'),str(marker)],check=True)
    p=subprocess.Popen(['rsync','-a','--bwlimit=4000','--',str(src)+'/',str(tmp_path/'dst')+'/']);best=0
    while p.poll() is None:best=max(best,bootscreen.rsync_read_bytes());time.sleep(0.05)
    end=time.monotonic()+30
    while not marker.exists() and time.monotonic()<end:time.sleep(0.1)
    # One copy reads every byte twice (sender from disk, receiver from the pipe).
    assert 0.5*total<best<=2.2*total, (best,total)


def test_a_broken_screen_never_reaches_the_move(monkeypatch):
    class Broken:
        active=True
        def __getattr__(self,name):raise RuntimeError('drawing broke')
    monkeypatch.setattr(offline,'SCREEN',Broken())
    offline.end_screen(True)
    assert isinstance(offline.SCREEN,offline.NoScreen)
    class Explodes:
        def __init__(self,*a,**k):raise RuntimeError('no console')
    import helper.bootscreen as module
    monkeypatch.setattr(module,'Screen',Explodes)
    offline.start_screen('continue','/home/u/Videos')
    assert isinstance(offline.SCREEN,offline.NoScreen)


def test_drawing_errors_turn_the_screen_off(tmp_path,monkeypatch):
    con=Console();s=bootscreen.Screen('continue','x',path=con.path,palette=False)
    monkeypatch.setattr(s,'render',lambda *a:(_ for _ in ()).throw(ValueError('bad frame')))
    assert s.open();time.sleep(0.5)
    assert s.active is False, 'a drawing error must hand the console back'
    con.alive=False


def test_status_lines_still_reach_the_console_without_the_screen(capsys,monkeypatch):
    monkeypatch.setattr(offline,'SCREEN',offline.NoScreen());monkeypatch.setattr(offline,'LOG',pathlib.Path('/nonexistent/log'))
    offline.say('copying 3 files')
    out=capsys.readouterr()
    assert 'Drives: copying 3 files' in out.err and out.out==''


def test_status_lines_feed_the_screen_and_journal_while_it_is_shown(capsys,monkeypatch):
    notes=[]
    class Live(offline.NoScreen):
        active=True
        def note(self,text):notes.append(text)
    monkeypatch.setattr(offline,'SCREEN',Live());monkeypatch.setattr(offline,'LOG',pathlib.Path('/nonexistent/log'))
    offline.say('unlocking the destination drive')
    out=capsys.readouterr()
    assert out.err=='' and 'Drives: unlocking' in out.out and notes==['Unlocking the destination drive']


def test_worker_unit_keeps_errors_on_the_console():
    unit=(pathlib.Path(__file__).resolve().parents[1]/'packaging/drives-maintenance-worker.service').read_text()
    assert 'StandardOutput=journal\n' in unit and 'StandardError=journal+console' in unit
