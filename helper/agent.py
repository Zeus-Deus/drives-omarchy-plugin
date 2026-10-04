"""Separate unprivileged GTK4 secret UI. Never imported by QML."""
import argparse,json,os,pathlib,secrets,sys,threading
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.client import call,secret_memfd
from topology import clean

def main():
    import gi
    gi.require_version('Gtk','4.0')
    from gi.repository import Gtk,Gio,GLib,Gdk,GObject
    parser=argparse.ArgumentParser(description='Drives native recovery-secret agent')
    sub=parser.add_subparsers(dest='op',required=True)
    p=sub.add_parser('provision')
    for arg in ('by-id','serial','name','mountpoint','confirmation'):p.add_argument('--'+arg,required=True)
    p.add_argument('--erase',action='store_true');p.add_argument('--manual',action='store_true')
    u=sub.add_parser('unlock');u.add_argument('--drive',required=True);u.add_argument('--label',default='')
    a=parser.parse_args()
    app=Gtk.Application(application_id='io.github.zeus_deus.Drives.Agent',flags=Gio.ApplicationFlags.NON_UNIQUE)
    def activate(app):
        win=Gtk.ApplicationWindow(application=app,title='Drives — Recovery secret')
        win.set_default_size(520,380)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=14,margin_top=24,margin_bottom=24,margin_start=24,margin_end=24);win.set_child(box)
        title=Gtk.Label(label='Encryption & recovery' if a.op=='provision' else 'Unlock encrypted drive',xalign=0);title.add_css_class('title-2');box.append(title)
        detail=Gtk.Label(label=('Serial '+clean(a.serial)+' · '+clean(a.mountpoint)) if a.op=='provision' else (clean(a.label) or 'Configured drive')+' · opens at its usual folder',xalign=0,wrap=True);box.append(detail)
        note=Gtk.Label(label='The recovery secret stays in this separate agent. It is never sent to the desktop panel.',xalign=0,wrap=True);note.add_css_class('dim-label');box.append(note)
        secret=Gtk.PasswordEntry(show_peek_icon=True,hexpand=True,placeholder_text='Recovery passphrase');box.append(secret)
        stored=Gtk.CheckButton(label='I have stored the recovery passphrase safely')
        inputs=[secret]
        if a.op=='provision':
            secret.set_text(secrets.token_urlsafe(32));box.append(stored)
            buttons=Gtk.Box(spacing=10);box.append(buttons)
            copy=Gtk.Button(label='Copy');regen=Gtk.Button(label='Regenerate');buttons.append(copy);buttons.append(regen)
            inputs.extend((stored,copy,regen))
            def copy_value(_):
                clipboard=Gdk.Display.get_default().get_clipboard()
                value=GObject.Value();value.init(str);value.set_string(secret.get_text())
                provider=Gdk.ContentProvider.new_union([Gdk.ContentProvider.new_for_value(value),Gdk.ContentProvider.new_for_bytes('x-kde-passwordManagerHint',GLib.Bytes.new(b'1'))])
                clipboard.set_content(provider);copy.set_label('Copied · 20s')
                def clear_owned():
                    if clipboard.get_content()==provider:clipboard.set_content(None)
                    copy.set_label('Copy');return False
                GLib.timeout_add_seconds(20,clear_owned)
            copy.connect('clicked',copy_value);regen.connect('clicked',lambda _:secret.set_text(secrets.token_urlsafe(32)))
        secret.connect('changed',lambda _:stored.set_active(False))

        message=Gtk.Label(label='',xalign=0,wrap=True);box.append(message)
        actions=Gtk.Box(spacing=10,halign=Gtk.Align.END);cancel=Gtk.Button(label='Cancel');submit=Gtk.Button(label='Encrypt & set up' if a.op=='provision' else 'Unlock');submit.add_css_class('suggested-action');actions.append(cancel);actions.append(submit);box.append(actions)
        cancel.connect('clicked',lambda _:win.close());cancel.grab_focus()
        busy=False;accepted=False
        win.connect('close-request',lambda _:busy)
        def done(result):
            nonlocal busy,accepted
            busy=False;accepted=bool(result.get('ok'))
            submit.set_sensitive(not accepted);cancel.set_sensitive(True)
            for widget in inputs:widget.set_sensitive(not accepted)
            if accepted:
                message.set_text('Operation accepted. Return to Drives to follow progress.' if a.op=='provision' else 'Drive unlocked. Return to Drives and rescan.')
                secret.set_text('');submit.set_sensitive(False);cancel.set_label('Close')
            else:message.set_text(clean(result.get('error','Operation failed')))
        def execute(_):
            nonlocal busy
            if busy or accepted:return
            if a.op=='provision' and not stored.get_active():message.set_text('Store the recovery passphrase before continuing.');return
            value=secret.get_text().encode()
            if not 8<=len(value)<=4096:message.set_text('Use a recovery passphrase of 8–4096 bytes.');return
            busy=True
            for widget in inputs:widget.set_sensitive(False)
            submit.set_sensitive(False);cancel.set_sensitive(False);message.set_text('Waiting for authorization…')
            def work():
                try:
                    if a.op=='provision':
                        request={'byId':a.by_id,'serial':a.serial,'name':a.name,'mountpoint':a.mountpoint,'erase':a.erase,'confirmation':a.confirmation,'autoUnlock':not a.manual}
                        fd=secret_memfd(value)
                        try:result=call('ProvisionDrive',(json.dumps(request),0),secret_fd=fd)
                        finally:os.close(fd)
                    else:
                        # The helper opens it under its configured name and mounts its folder.
                        fd=secret_memfd(value)
                        try:result=call('UnlockDrive',(a.drive,0),secret_fd=fd)
                        finally:os.close(fd)
                except BaseException:result={'ok':False,'error':'Authorization or storage operation failed. No secret was logged.'}
                GLib.idle_add(done,result)
            try:threading.Thread(target=work,daemon=True).start()
            except Exception:done({'ok':False,'error':'Could not start the storage request. Please try again.'})
        submit.connect('clicked',execute)
        win.present()
    app.connect('activate',activate)
    return app.run([])

if __name__=='__main__':raise SystemExit(main())
