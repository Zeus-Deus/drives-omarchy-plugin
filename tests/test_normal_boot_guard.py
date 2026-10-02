"""The persistent normal-boot barrier must not import the storage helper."""
import ast,importlib.util,os,pathlib,subprocess
import pytest

ROOT=pathlib.Path(__file__).resolve().parents[1]


def implementation():
    path=ROOT/'helper/normal_boot_guard.py'
    assert path.is_file(),'standalone normal-boot guard is missing'
    spec=importlib.util.spec_from_file_location('normal_boot_guard',path)
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_only_absent_controls_allow_normal_boot(tmp_path,monkeypatch):
    guard=implementation();latch=tmp_path/'latch';runtime=tmp_path/'runtime'
    monkeypatch.setattr(guard,'CONTROLS',(latch,runtime))
    assert guard.check() is None
    latch.write_bytes(b'malformed controls must still block normal boot')
    with pytest.raises(RuntimeError,match='recovery'):guard.check()
    latch.unlink();latch.symlink_to(tmp_path/'missing')
    with pytest.raises(RuntimeError,match='recovery'):guard.check()
    latch.unlink();runtime.mkdir()
    with pytest.raises(RuntimeError,match='recovery'):guard.check()


def test_persistent_sysinit_barrier_is_packaged():
    unit=ROOT/'packaging/drives-normal-boot-guard.service'
    assert unit.is_file(),'persistent guard unit is missing'
    text=unit.read_text()
    assert 'DefaultDependencies=no' in text and 'Before=sysinit.target' in text
    assert 'Type=oneshot' in text and 'RemainAfterExit=yes' in text
    assert 'ExecStart=/usr/bin/python3 -I -B /usr/lib/drives-helper/normal_boot_guard.py' in text
    assert '[Install]' not in text and 'ConditionPath' not in text and 'ExecCondition' not in text
    drop=ROOT/'packaging/drives-sysinit-guard.conf'
    assert drop.read_text()=='[Unit]\nRequires=drives-normal-boot-guard.service\nAfter=drives-normal-boot-guard.service\n'
    install=(ROOT/'helper/install.sh').read_text()
    assert 'normal_boot_guard.py' in install and 'drives-normal-boot-guard.service' in install
    assert '/etc/systemd/system/sysinit.target.d' in install and 'drives-sysinit-guard.conf' in install


def test_guard_has_no_helper_import_and_accepts_no_cli_override(tmp_path):
    path=ROOT/'helper/normal_boot_guard.py'
    imports={name.name for node in ast.walk(ast.parse(path.read_text())) if isinstance(node,ast.Import) for name in node.names}
    assert imports=={'os','sys'}
    assert not any(isinstance(node,ast.ImportFrom) for node in ast.walk(ast.parse(path.read_text())))
    fake=tmp_path/'helper';fake.mkdir();(fake/'__init__.py').write_text('raise RuntimeError("broken helper package")')
    result=subprocess.run(['/usr/bin/python3','-I','-B',str(path)],cwd=tmp_path,capture_output=True,timeout=10)
    assert result.returncode==0 and result.stdout==b'' and result.stderr==b''
    result=subprocess.run(['/usr/bin/python3','-I','-B',str(path),'--latch=/missing'],capture_output=True,timeout=10)
    assert result.returncode==1 and b'accepts no parameters' in result.stderr


def test_stat_failure_is_not_treated_as_absence(tmp_path,monkeypatch):
    guard=implementation();control=tmp_path/'unreadable'
    monkeypatch.setattr(guard,'CONTROLS',(control,))
    original=os.lstat
    def unavailable(path,*args,**kwargs):
        if path==control:raise PermissionError('fixture error must not leak the path')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(guard.os,'lstat',unavailable)
    with pytest.raises(RuntimeError,match='state is unavailable'):guard.check()
