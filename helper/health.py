"""SMART health summary for the panel (read-only, cached, never blocks Status).

smartctl needs root, so the helper runs it and Status reports only a small
verdict per disk serial. A disk that cannot report SMART (USB bridges, virtual
disks) is 'unavailable' with the reason, never a guessed 'healthy'.
"""
import json,shutil,threading,time
from helper.common import run

TTL=600
# smartctl exit status bits (man smartctl, RETURN VALUES).
OPEN_FAILED=0b11      # bits 0-1: command line / device open failed
FAILING=1<<3          # bit 3: SMART status says DISK FAILING
PREFAIL=1<<4          # bit 4: prefail attributes at or below threshold
ERRORS=(1<<6)|(1<<7)  # bits 6-7: error log / self-test log has errors


def verdict(data):
    """Reduce one `smartctl -j -H -A -i` document to {state, temperature, reason}."""
    status=int(((data or {}).get('smartctl') or {}).get('exit_status',2))
    temp=((data or {}).get('temperature') or {}).get('current')
    out={'temperature':temp if isinstance(temp,int) and 0<temp<150 else None}
    if status&OPEN_FAILED or 'smart_status' not in data:
        messages=((data or {}).get('smartctl') or {}).get('messages') or []
        reason=next((m.get('string','') for m in messages if isinstance(m,dict)),'') or 'SMART is not available for this device'
        if 'Unknown USB bridge' in reason:reason='SMART is not available through this USB adapter'
        elif 'Unable to detect device type' in reason:reason='SMART is not available for this virtual disk'
        return dict(out,state='unavailable',reason=reason[:160])
    if status&FAILING or not data['smart_status'].get('passed',False):
        return dict(out,state='failing',reason='The drive reports it is failing. Back up its data now.')
    if status&(PREFAIL|ERRORS):
        return dict(out,state='warning',reason='The drive logged errors or worn attributes. Back up and keep an eye on it.')
    return dict(out,state='passed',reason='')


class HealthCache:
    def __init__(self,devices,clock=time.monotonic,probe=None):
        self.devices=devices;self.clock=clock;self.probe=probe or self.smartctl
        self.lock=threading.Lock();self.values={};self.at=None;self.running=False
    @staticmethod
    def smartctl(device):
        out=run(['smartctl','-j','-H','-A','-i',device],timeout=20,accepted=tuple(range(256)))
        return verdict(json.loads(out or b'{}'))
    def refresh(self):
        values={}
        try:
            if not shutil.which('smartctl'):
                values={'*':{'state':'unavailable','reason':'smartmontools is not installed','temperature':None}}
            else:
                for serial,device in self.devices():
                    try:values[serial]=self.probe(device)
                    except Exception as exc:values[serial]={'state':'unavailable','reason':'SMART check failed: '+str(exc)[:100],'temperature':None}
        finally:
            with self.lock:self.values=values;self.at=self.clock();self.running=False
    def snapshot(self):
        """Current verdicts; starts a background refresh when stale."""
        with self.lock:
            stale=self.at is None or self.clock()-self.at>TTL
            if stale and not self.running:
                self.running=True;threading.Thread(target=self.refresh,daemon=True).start()
            return {'checkedAgo':None if self.at is None else int(self.clock()-self.at),'disks':dict(self.values)}


def present_disks():
    from topology import blocks
    return [(d.get('serial'),d['name']) for d in blocks().get('blockdevices',[])
            if d.get('type')=='disk' and d.get('serial') and not d['name'].startswith('/dev/zram')]
