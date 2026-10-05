"""SMART verdicts and I/O counters for the read-only overview."""
import pathlib,sys,threading
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from helper import health
from topology import diskstats


def doc(status,passed=True,temp=None,msg=None):
    d={'smartctl':{'exit_status':status,'messages':[{'string':msg,'severity':'error'}] if msg else []}}
    if passed is not None:d['smart_status']={'passed':passed}
    if temp:d['temperature']={'current':temp}
    return d


def test_verdicts():
    assert health.verdict(doc(0,temp=38))=={'temperature':38,'state':'passed','reason':''}
    assert health.verdict(doc(1<<3,passed=False))['state']=='failing'
    assert health.verdict(doc(1<<4))['state']=='warning'
    assert health.verdict(doc(1<<6))['state']=='warning'
    usb=health.verdict(doc(1,passed=None,msg='/dev/sdb: Unknown USB bridge [0x46f4:0x0001 (0x000)]'))
    assert usb['state']=='unavailable' and 'USB adapter' in usb['reason']
    assert health.verdict({})['state']=='unavailable'


def test_cache_never_blocks_and_refreshes_in_background():
    gate=threading.Event();calls=[]
    def probe(device):gate.wait(5);calls.append(device);return {'state':'passed','reason':'','temperature':None}
    now=[0.0]
    c=health.HealthCache(lambda:[('S1','/dev/sda')],clock=lambda:now[0],probe=probe)
    c.refresh=c.refresh  # real refresh, slow probe
    import shutil;real=shutil.which;shutil.which=lambda name:'/usr/bin/smartctl'
    try:
        first=c.snapshot();assert first=={'checkedAgo':None,'disks':{}}
        gate.set()
        for _ in range(100):
            if c.at is not None:break
            threading.Event().wait(0.02)
        assert c.snapshot()['disks']['S1']['state']=='passed' and calls==['/dev/sda']
        now[0]=health.TTL-1;c.snapshot();assert calls==['/dev/sda']
    finally:shutil.which=real


def test_missing_smartctl_is_reported_not_guessed(monkeypatch):
    monkeypatch.setattr(health.shutil,'which',lambda name:None)
    c=health.HealthCache(lambda:[('S1','/dev/sda')]);c.refresh()
    assert c.values=={'*':{'state':'unavailable','reason':'smartmontools is not installed','temperature':None}}


def test_diskstats_uses_512_byte_sectors_for_whole_disks_and_partitions():
    text='   8       0 sda 37 1 1992 45 0 0 16 0 0 38 45 0 0 0 0 0 0\n 259 0 nvme0n1 1 0 8 0 2 0 4 0 0 0 0 0\n'
    s=diskstats(text)
    assert s['/dev/sda']=={'read':1992*512,'written':16*512}
    assert s['/dev/nvme0n1']=={'read':8*512,'written':4*512}
