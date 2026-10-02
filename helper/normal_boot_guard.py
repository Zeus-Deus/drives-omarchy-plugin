"""Standalone normal-boot failure barrier; no helper imports or gate release.

The sysinit requirement is persistent. This check runs once on normal startup,
not as a maintained writer lease, and must never be restarted to isolate a live
session. Even malformed/dangling controls retain the normal-boot barrier.
"""
import os,sys

CONTROLS=('/drives-maintenance-request.json','/run/drives-maintenance')


def check():
    if os.geteuid()!=0:raise RuntimeError('normal boot guard requires root')
    for path in CONTROLS:
        try:os.lstat(path)
        except FileNotFoundError:continue
        except OSError:raise RuntimeError('maintenance control state is unavailable') from None
        raise RuntimeError('maintenance recovery is present')


if __name__=='__main__':
    try:
        if len(sys.argv)!=1:raise RuntimeError('normal boot guard accepts no parameters')
        check()
    except RuntimeError as error:
        print('Normal boot blocked: '+str(error)+'. Inspect before normal startup.',file=sys.stderr)
        raise SystemExit(1)
