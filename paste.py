"""Explicit clipboard transfer; always launched below supervisor.py.

No Qt clipboard API is used. The supervisor bounds the entire wl-paste tree,
time and output. This reader bounds memory before encoding the JSON reply.
"""
import json
import subprocess
import sys

LIMIT = 65536

def read_paste(argv=('wl-paste', '--no-newline', '--type', 'text')):
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
    try:
        assert child.stdout is not None
        raw = child.stdout.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError('Clipboard text exceeds 64 KiB.')
        if child.wait() != 0:
            raise ValueError('Clipboard text is unavailable.')
        return raw.decode('utf-8', 'strict')
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()
        if child.stdout:
            child.stdout.close()

if __name__ == '__main__':
    try:
        print(json.dumps({'ok': True, 'text': read_paste()}, ensure_ascii=True))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)[:160]}))
