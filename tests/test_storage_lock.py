"""The storage transaction lease must serialize independent root processes."""
import json,pathlib,subprocess,sys
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from helper.common import Common


def test_storage_lock_excludes_another_process_and_releases(tmp_path):
    c=Common(tmp_path)
    assert callable(getattr(c,'storage_lock',None)), 'no cross-process storage transaction lock'
    script='''import json,sys
from helper.common import Common,Failure
try:
    with Common(sys.argv[1]).storage_lock():print(json.dumps({'acquired':True}))
except Failure as error:print(json.dumps({'acquired':False,'error':str(error)}))
'''
    def child():
        result=subprocess.run([sys.executable,'-B','-c',script,str(tmp_path)],cwd=ROOT,capture_output=True,timeout=10)
        assert result.returncode==0,result.stderr
        return json.loads(result.stdout)
    with c.storage_lock():
        result=child()
        assert result['acquired'] is False
        assert 'another storage operation' in result['error']
    assert child()=={'acquired':True}
    lock=tmp_path/'storage.lock'
    assert lock.stat().st_mode&0o777==0o600
