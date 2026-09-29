import pathlib,sys,pytest
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def test_erase_confirmation_fails_before_any_disk_action():
    assert (ROOT/'helper/provisioning.py').exists(),'provisioning validation is not implemented'
    from helper.provisioning import validate_request
    from helper.common import Failure
    req={'byId':'/dev/disk/by-id/test','serial':'TESTNEW0002','name':'data2','mountpoint':'/data2','erase':False,'confirmation':'Y003','autoUnlock':True}
    with pytest.raises(Failure,match='serial confirmation'):validate_request(req)

def test_secret_cannot_be_submitted_as_string_request_field():
    assert (ROOT/'helper/provisioning.py').exists(),'provisioning request boundary is not implemented'
    from helper.provisioning import validate_request
    from helper.common import Failure
    with pytest.raises(Failure,match='fields'):
        validate_request({'password':'should never reach request payload'})
