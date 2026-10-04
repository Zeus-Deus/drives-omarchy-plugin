"""Opt-in real GTK checks inside the owned Omarchy VM, one process per case.

DRIVES_GTK_TEST=1 requires a private VM display. Window construction, native
signals and bindings are real; the privileged client is replaced by a bounded
error gate for pending/error UI checks. These are not storage/polkit acceptance.
"""
import os,pathlib,subprocess,sys
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[1]
pytestmark=pytest.mark.skipif(os.environ.get('DRIVES_GTK_TEST')!='1',reason='native GTK opt-in requires the owned Omarchy VM')

def run_case(case):
    value=subprocess.run([sys.executable,str(pathlib.Path(__file__).resolve()),case],stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=20)
    assert value.returncode==0,value.stdout+value.stderr
    assert value.stdout.strip()=='PASS '+case

@pytest.mark.parametrize('op',['provision','unlock'])
def test_recovery_window_constructs_native_controls(op):run_case('startup-'+op)

@pytest.mark.parametrize('change',['edit','regenerate'])
def test_recovery_acknowledgement_belongs_to_current_passphrase(change):run_case('ack-'+change)

@pytest.mark.parametrize('check',['controls','duplicate','close'])
def test_pending_authorization_is_immutable_and_denial_recovers(check):run_case('pending-'+check)

def test_accepted_operation_is_terminal_and_clears_recovery_entry():run_case('accepted')

def test_worker_start_failure_restores_native_form():run_case('worker-start-failure')

def native_case(case):
    assert os.environ.get('DRIVES_GTK_TEST')=='1'
    import gi
    gi.require_version('Gtk','4.0')
    from gi.repository import Gtk,GLib,Gdk
    assert Gdk.Display.get_default() is not None,'the native VM display must be available'
    sys.path.insert(0,str(ROOT))
    from helper import agent
    op='unlock' if case=='startup-unlock' else 'provision'
    sys.argv=['drives-agent',op]
    if op=='provision':sys.argv+=['--by-id','/dev/disk/by-id/GUI-NEVER-PROVISION','--serial','GUIFIXTURE0002','--name','gui-fixture','--mountpoint','/gui-fixture','--confirmation','0002','--manual']
    else:sys.argv+=['--drive','f'*32,'--label','GUI fixture (never unlocked)']
    def forbidden(*args,**kwargs):raise AssertionError('native UI checks must not invoke storage')
    agent.call=forbidden
    if case=='accepted':agent.call=lambda *args,**kwargs:{'ok':True,'jobId':'UI-FIXTURE-ONLY'}
    errors=[]
    import threading,time
    entered=threading.Event();release=threading.Event();second=threading.Event();calls=[]
    if case.startswith('pending-'):
        def held_denial(*args,**kwargs):
            calls.append(1)
            if len(calls)>1:second.set()
            entered.set()
            assert release.wait(5),'bounded fixture denial gate timed out'
            return {'ok':False,'error':'Fixture authorization denied'}
        agent.call=held_denial
    def widgets(parent):
        result=[parent];child=parent.get_first_child()
        while child is not None:result.extend(widgets(child));child=child.get_next_sibling()
        return result
    original_run=Gtk.Application.run
    def run(app,argv):
        def check():
            try:
                windows=app.get_windows();assert len(windows)==1,'one native window required'
                all_widgets=widgets(windows[0])
                entries=[w for w in all_widgets if isinstance(w,Gtk.PasswordEntry)]
                assert len(entries)==1,'recovery entry did not finish construction'
                secret=entries[0]
                assert secret.props.placeholder_text=='Recovery passphrase','placeholder property missing'
                buttons={w.get_label():w for w in all_widgets if isinstance(w,Gtk.Button) and w.get_label() is not None}
                expected={'Cancel','Unlock'} if op=='unlock' else {'Copy','Regenerate','Cancel','Encrypt & set up'}
                assert expected<=set(buttons),'native actions did not finish construction'
                if op=='provision':
                    stored=next(w for w in all_widgets if isinstance(w,Gtk.CheckButton))
                    assert not stored.get_active(),'initial acknowledgement must be clear'
                    if case.startswith('ack-'):
                        secret.set_text('PUBLIC-UI-FIXTURE-ONLY');stored.set_active(True)
                        if case=='ack-edit':secret.set_text('DIFFERENT-PUBLIC-UI-FIXTURE')
                        else:buttons['Regenerate'].emit('clicked')
                        assert not stored.get_active(),'changed passphrase requires a fresh safe-storage acknowledgement'
                    if case.startswith('pending-'):
                        secret.set_text('PUBLIC-UI-FIXTURE-ONLY');stored.set_active(True)
                        submit=buttons['Encrypt & set up'];submit.emit('clicked')
                        assert entered.wait(1),'worker did not reach the bounded client gate'
                        try:
                            if case=='pending-controls':
                                controls={'entry':secret,'acknowledgement':stored,**buttons}
                                for name,widget in controls.items():assert not widget.get_sensitive(),name+' remained editable during authorization'
                            elif case=='pending-duplicate':
                                submit.emit('clicked')
                                assert not second.wait(1),'a duplicate signal launched a second worker'
                            else:assert windows[0].emit('close-request') is True,'closing must wait until the request returns'
                        finally:release.set()
                        context=GLib.MainContext.default();deadline=time.monotonic()+3
                        while not submit.get_sensitive() and time.monotonic()<deadline:
                            context.iteration(False);time.sleep(.005)
                        assert submit.get_sensitive(),'denial did not restore the form'
                        for name,widget in {'entry':secret,'acknowledgement':stored,**buttons}.items():assert widget.get_sensitive(),name+' stayed disabled after denial'
                        assert stored.get_active(),'denial must not revoke acknowledgement for an unchanged passphrase'
                    if case=='accepted':
                        secret.set_text('PUBLIC-UI-FIXTURE-ONLY');stored.set_active(True)
                        submit=buttons['Encrypt & set up'];submit.emit('clicked')
                        context=GLib.MainContext.default();deadline=time.monotonic()+3
                        while secret.get_text() and time.monotonic()<deadline:context.iteration(False);time.sleep(.005)
                        assert not secret.get_text(),'accepted operation must clear the recovery entry'
                        for name,widget in {'entry':secret,'acknowledgement':stored,'copy':buttons['Copy'],'regenerate':buttons['Regenerate'],'submit':submit}.items():assert not widget.get_sensitive(),name+' remained usable after acceptance'
                        assert buttons['Cancel'].get_sensitive() and buttons['Cancel'].get_label()=='Close'
                    if case=='worker-start-failure':
                        secret.set_text('PUBLIC-UI-FIXTURE-ONLY');stored.set_active(True)
                        original_start=threading.Thread.start
                        def fail_start(self):raise RuntimeError('public fixture worker startup failure')
                        threading.Thread.start=fail_start
                        try:buttons['Encrypt & set up'].emit('clicked')
                        finally:threading.Thread.start=original_start
                        for name,widget in {'entry':secret,'acknowledgement':stored,**buttons}.items():assert widget.get_sensitive(),name+' stayed disabled when no worker started'
                        assert stored.get_active(),'startup failure must retain acknowledgement for the unchanged passphrase'
            except BaseException as e:errors.append(type(e).__name__+': '+str(e))
            finally:release.set()
            app.quit();return False
        GLib.timeout_add(200,check)
        return original_run(app,argv)
    Gtk.Application.run=run
    assert agent.main()==0,'native application returned an error'
    assert not errors,'; '.join(errors)
    print('PASS '+case)

if __name__=='__main__':native_case(sys.argv[1])
