import pathlib,sys,os,fcntl,pytest
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def test_secret_fd_requires_sealed_memfd():
    assert (ROOT/'helper/service.py').exists(),'secret FD boundary is not implemented'
    from helper.service import read_secret_fd
    from helper.common import Failure
    fd=os.memfd_create('test',os.MFD_ALLOW_SEALING)
    try:
        os.write(fd,b'example-passphrase')
        with pytest.raises(Failure,match='sealed'):read_secret_fd(fd)
        fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
        assert read_secret_fd(fd)==b'example-passphrase'
    finally:os.close(fd)

def test_each_mutator_has_separate_auth_admin_policy():
    assert (ROOT/'packaging/io.github.zeus-deus.drives.policy').exists(),'polkit actions not implemented'
    import xml.etree.ElementTree as ET
    policy=ET.parse(ROOT/'packaging/io.github.zeus-deus.drives.policy')
    actions=policy.findall('action')
    assert len(actions)==8
    assert all(a.find('defaults/allow_active').text=='auth_admin' for a in actions)
    assert len({a.attrib['id'] for a in actions})==8
