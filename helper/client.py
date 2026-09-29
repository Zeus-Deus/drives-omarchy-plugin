"""Bounded user-side D-Bus client; imports GI only when called."""
import json,os,fcntl
from helper.common import Failure
from helper.service import BUS,OBJECT,SIGNATURES

def secret_memfd(value):
    fd=os.memfd_create('drives-recovery',os.MFD_ALLOW_SEALING|os.MFD_CLOEXEC)
    try:
        if not 8<=len(value)<=4096 or b'\0' in value:raise Failure('invalid recovery passphrase')
        os.write(fd,value);os.lseek(fd,0,os.SEEK_SET)
        fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
        return fd
    except BaseException:os.close(fd);raise

def call(method,args=(),secret_fd=None):
    from gi.repository import Gio,GLib
    if method not in SIGNATURES:raise Failure('unknown helper method')
    bus=Gio.bus_get_sync(Gio.BusType.SYSTEM,None)
    params=GLib.Variant('('+SIGNATURES[method]+')',tuple(args))
    if secret_fd is not None:
        fds=Gio.UnixFDList.new();fds.append(secret_fd)
        reply,_=bus.call_with_unix_fd_list_sync(BUS,OBJECT,BUS,method,params,GLib.VariantType.new('(s)'),Gio.DBusCallFlags.NONE,125000,fds,None)
    else:reply=bus.call_sync(BUS,OBJECT,BUS,method,params,GLib.VariantType.new('(s)'),Gio.DBusCallFlags.NONE,125000,None)
    text=reply.unpack()[0]
    if len(text)>2*1024*1024:raise Failure('helper response limit exceeded')
    return json.loads(text)

def status():return call('Status')
