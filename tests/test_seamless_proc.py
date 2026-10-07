"""Real, owned child process metadata only; no host-wide proc census."""
import os,pathlib,select,shutil,subprocess,sys,time
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from helper.moves import open_users

@pytest.mark.parametrize('kind',['fd','cwd','mmap-only','exe'])
def test_owned_live_process_use_and_later_retry(tmp_path,kind):
    source=tmp_path/'profile';source.mkdir();file=source/'fixture';file.write_bytes(b'x'*4096)
    proc=tmp_path/'proc';proc.mkdir()
    if kind=='exe':
        executable=source/'fixture-sleep';shutil.copyfile('/usr/bin/sleep',executable);executable.chmod(0o700)
        child=subprocess.Popen([str(executable),'30'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    else:
        code="import os,mmap,sys,time; p=sys.argv[1]; k=sys.argv[2]; f=os.open(p,os.O_RDONLY) if k!='cwd' else None; "
        if kind=='cwd':code+="os.chdir(os.path.dirname(p)); "
        elif kind=='mmap-only':code+="m=mmap.mmap(f,0,access=mmap.ACCESS_READ,trackfd=False); os.close(f); "
        code+="print('READY',flush=True); time.sleep(30)"
        child=subprocess.Popen([sys.executable,'-c',code,str(file),kind],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    (proc/str(child.pid)).symlink_to('/proc/'+str(child.pid),target_is_directory=True)
    try:
        if kind!='exe':
            assert select.select([child.stdout],[],[],5)[0], 'child never became ready'
            assert child.stdout.readline()==b'READY\n'
        else:
            end=time.monotonic()+5
            while os.readlink('/proc/'+str(child.pid)+'/exe')!=str(executable):
                assert time.monotonic()<end;time.sleep(.01)
        if kind=='mmap-only':
            targets=[]
            for fd in pathlib.Path('/proc',str(child.pid),'fd').iterdir():
                try:targets.append(os.readlink(fd))
                except FileNotFoundError:pass
            assert str(file) not in targets,'fixture must truly have no source fd'
        result=open_users(str(source),proc_root=proc)
        assert [p['pid'] for p in result]==[child.pid]
    finally:
        child.terminate()
        try:child.wait(timeout=5)
        except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
        child.stdout.close();child.stderr.close()
    assert open_users(str(source),proc_root=proc)==[]
