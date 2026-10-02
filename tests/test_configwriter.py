import pathlib,sys,pytest
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def test_config_transaction_preserves_unowned_bytes():
    assert (ROOT/'helper/configwriter.py').exists(),'atomic system-config worker is not implemented'
    from helper.configwriter import transform
    id='a'*32;old='old source';new='new source'
    original='# system config\nrootline\n\n# drives-helper '+id+'\n'+old+'\n# unrelated\nkeep\n'
    changed=transform(original,id,new,{old})
    assert '# unrelated\nkeep\n' in changed and 'rootline\n' in changed
    assert old not in changed and new in changed

def test_modified_owned_entry_is_never_silently_deleted():
    assert (ROOT/'helper/configwriter.py').exists(),'owned config validation is not implemented'
    from helper.configwriter import transform
    from helper.common import Failure
    id='b'*32
    with pytest.raises(Failure,match='changed'):
        transform('# drives-helper '+id+'\nadmin-edited\n',id,None,{'our-original'})
