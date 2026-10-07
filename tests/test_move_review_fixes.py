"""Review regressions: isolated source-only policy, never host storage workers."""
import copy
import os
import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from helper import common, configwriter, maintenance, moves, offline, preparedrive
from helper.common import Common, Failure


@pytest.fixture(autouse=True)
def system_guards(tmp_path, monkeypatch):
    """Guard control/config/recovery paths even when a refusal falls through."""
    monkeypatch.setattr(maintenance, 'STATE', tmp_path / 'state')
    monkeypatch.setattr(Common, 'boot_id', staticmethod(lambda: '22222222-2222-2222-2222-222222222222'))
    monkeypatch.setattr(maintenance, 'LATCH', tmp_path / 'request.json')
    monkeypatch.setattr(maintenance, 'RUNTIME', tmp_path / 'runtime')
    monkeypatch.setattr(offline, 'LOG', tmp_path / 'maintenance.log')
    monkeypatch.setattr(offline, 'QA_CRASH', tmp_path / 'crash-marker')
    monkeypatch.setattr(offline, 'qa_crash', lambda *a: None)
    def forbidden(*args, **kwargs):
        pytest.fail('unguarded privileged/config/recovery operation: ' + repr(args))
    for module in (common, moves, offline):
        monkeypatch.setattr(module, 'run', forbidden)
    monkeypatch.setattr(Common, 'config', forbidden)
    monkeypatch.setattr(configwriter, 'write_owned', forbidden)
    monkeypatch.setattr(moves.MoveManager, 'latch', forbidden)
    monkeypatch.setattr(moves.MoveManager, 'destination_op', forbidden)
    monkeypatch.setattr(offline.Offline, 'restore', forbidden)
    monkeypatch.setattr(moves, 'blocks', lambda: {})
    monkeypatch.setattr(moves, 'mount_rows', lambda: [])
    monkeypatch.setattr(moves, 'probe', forbidden)
    fstab = tmp_path / 'fstab'
    fstab.write_text('# isolated empty configuration\n')
    read = moves.read_regular
    def isolated_read(path, *args):
        target = pathlib.Path(path)
        if str(path) == '/etc/fstab':
            target = fstab
        assert target.is_relative_to(tmp_path), 'unexpected global file read'
        return read(target, *args)
    monkeypatch.setattr(moves, 'read_regular', isolated_read)
    return fstab


@pytest.fixture
def plan(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    source = home / '.config'
    source.mkdir(mode=0o755)
    (source / 'synthetic-token').write_bytes(b'synthetic private source')
    caller = os.getuid() or 1000
    if os.geteuid() == 0:
        # Model an ordinary caller without weakening the root-home refusal.
        # Only owned synthetic fixture paths change their owner.
        for path in (home, source, source / 'synthetic-token'):
            os.chown(path, caller, caller)
    disk = tmp_path / 'disk'
    disk.mkdir()
    c = Common(tmp_path / 'state')
    manager = moves.MoveManager(c, uid=caller)
    move_id = 'b' * 32
    dest = disk / ('drives-' + move_id)
    dest.mkdir(mode=0o755)
    (dest / 'partial').write_bytes(b'retain this flat-layout copy')
    source_mount = {'target': str(tmp_path), 'source': '/dev/source', 'fsroot': '/',
                    'fstype': 'ext4', 'majorMinor': '8:1', 'options': 'rw'}
    mount = {'target': str(disk), 'source': '/dev/mapper/fixture', 'fsroot': '/',
             'fstype': 'btrfs', 'majorMinor': '8:2', 'options': 'rw'}
    source_top = {'supported': True, 'encrypted': False, 'mount': source_mount,
                  'disk': {'serial': 'SOURCE'}, 'chain': [{'uuid': 'SRC'}]}
    top = {'supported': True, 'encrypted': True, 'mount': mount,
           'disk': {'serial': 'DEST'}, 'chain': [{'type': 'crypt', 'uuid': 'DST'}]}
    rows = [source_mount, mount]
    events = []
    def probe(path, block=None, mounts=None, resolve=True):
        assert resolve is False, 'path resolution can trigger an automount'
        events.append(('probe', str(path)))
        return source_top if str(path) in (str(source), str(home)) else top
    monkeypatch.setattr(moves, 'probe', probe)
    monkeypatch.setattr(moves, 'mount_rows', lambda: rows)
    monkeypatch.setattr(moves, 'open_users', lambda *a: [])
    j = {'id': move_id, 'state': 'awaiting-maintenance', 'maintenanceProtocol': 2,
         'source': str(source), 'sourceMount': str(tmp_path), 'sourceUUID': 'SRC',
         'sourceIdentity': moves.identity(source), 'parentIdentity': moves.identity(home),
         'destMount': str(disk), 'dest': str(dest), 'destIdentity': moves.identity(dest),
         'mountIdentity': moves.identity(disk), 'destUUID': 'DST', 'diskSerial': 'DEST',
         'destFSRoot': '/drives-' + move_id, 'backup': str(source) + '.pre-move',
         'uid': caller, 'boot_id': 'old-boot', 'verified': False}
    c.journal('moves', move_id, j)
    return types.SimpleNamespace(m=manager, c=c, j=j, rows=rows, top=top,
                                 source_top=source_top, events=events, home=home)


@pytest.mark.parametrize('operation', ['request', 'resume'])
@pytest.mark.parametrize('state', ['awaiting-maintenance', 'paused'])
def test_legacy_waiting_continue_refused_before_latch(plan, monkeypatch, operation, state):
    a = plan
    a.j['state'] = state
    if state == 'paused':
        a.j['interruptedState'] = 'awaiting-maintenance'
    a.c.journal('moves', a.j['id'], a.j)
    before = a.c.path('moves', a.j['id']).read_bytes()
    calls = []
    monkeypatch.setattr(a.m, 'latch', lambda *args: calls.append(args))
    with pytest.raises(Failure, match='(?i)legacy.*retain.*(?:replan|new plan)'):
        if operation == 'request':
            a.m.request(a.j['id'], 'continue')
        else:
            a.m.resume(a.j['id'])
    assert calls == [], 'privacy refusal must precede latch arming'
    assert a.c.path('moves', a.j['id']).read_bytes() == before
    assert pathlib.Path(a.j['source'], 'synthetic-token').read_bytes() == b'synthetic private source'
    assert pathlib.Path(a.j['dest'], 'partial').read_bytes() == b'retain this flat-layout copy'


@pytest.mark.parametrize('operation', ['execute', 'restart'])
def test_legacy_recopy_aliases_refused_without_recreation_or_planning(plan, monkeypatch, operation):
    a = plan
    a.c.journal('moves', a.j['id'], a.j)
    before = a.c.path('moves', a.j['id']).read_bytes()
    calls = []
    monkeypatch.setattr(a.m, 'destination_op', lambda *args: calls.append(args) or {'identity': a.j['destIdentity']})
    with pytest.raises(Failure, match='(?i)legacy.*retain.*(?:replan|new plan)'):
        if operation == 'execute':
            a.m.execute(copy.deepcopy(a.j))
        else:
            a.m.restart(a.j['id'])
    assert calls == [], 'refuse before deleting/recreating a legacy destination'
    assert a.c.path('moves', a.j['id']).read_bytes() == before
    assert pathlib.Path(a.j['source'], 'synthetic-token').read_bytes() == b'synthetic private source'


def guard_drive_lookups(monkeypatch, a, permitted=False):
    """Observe the real low-level calls, not just high-level policy methods."""
    touched = []
    for name in ('lstat', 'stat', 'open', 'scandir', 'listdir', 'readlink'):
        original = getattr(os, name)
        def observed(path, *args, _name=name, _original=original, **kwargs):
            if isinstance(path, (str, bytes, os.PathLike)):
                path_text = os.fsdecode(path)
                if path_text == a.j['destMount'] or path_text.startswith(a.j['destMount'] + '/'):
                    touched.append((_name, path_text))
                    if not permitted:
                        pytest.fail('uninspected drive path touched: ' + repr(touched[-1]))
                    assert ('probe', a.j['destMount']) in a.events, 'child lookup preceded nonresolving topology observation'
            return _original(path, *args, **kwargs)
        monkeypatch.setattr(os, name, observed)
    return touched


@pytest.mark.parametrize('layout', ['flat', 'private'])
def test_missing_drive_incomplete_cancel_never_touches_child(plan, monkeypatch, layout):
    a = plan
    a.j.pop('destIdentity')
    if layout == 'private':
        a.j['destContainer'] = a.j['dest']
        a.j['dest'] += '/content'
    a.c.journal('moves', a.j['id'], a.j)
    a.rows[:] = [a.rows[0], dict(a.rows[1], fstype='autofs', source='systemd-1')]
    a.top.clear()
    a.top.update(supported=False, mount=a.rows[1])
    target = a.j.get('destContainer', a.j['dest'])
    with monkeypatch.context() as lookups:
        touched = guard_drive_lookups(lookups, a)
        result = a.m.cancel(a.j['id'])
        assert touched == []
    assert result['state'] == 'rolled-back'
    assert result['destinationInspected'] is False
    assert result['retainedDestination'] == target
    assert 'not inspected' in result['message'] and 'may' in result['message']
    saved = a.c.read('moves', a.j['id'])
    assert saved['state'] == 'rolled-back' and saved['destinationInspected'] is False
    assert pathlib.Path(target, 'partial').read_bytes() == b'retain this flat-layout copy'
    assert pathlib.Path(a.j['source'], 'synthetic-token').read_bytes() == b'synthetic private source'


@pytest.mark.parametrize('layout', ['flat', 'private'])
@pytest.mark.parametrize('observation', ['absent', 'only-autofs', 'readonly', 'stacked', 'unsupported',
                                        'unencrypted', 'serial', 'uuid', 'mount-target', 'fsroot', 'fstype'])
def test_unsafe_topology_incomplete_cancel_is_record_only(plan, monkeypatch, layout, observation):
    a = plan
    a.j.pop('destIdentity')
    if layout == 'private':
        a.j['destContainer'] = a.j['dest']
        a.j['dest'] += '/content'
        a.j['destFSRoot'] += '/content'
    a.c.journal('moves', a.j['id'], a.j)
    if observation == 'absent':
        a.rows.pop()
        a.top['mount'] = dict(a.source_top['mount'])
    elif observation == 'only-autofs':
        a.rows[1]['fstype'] = 'autofs'
    elif observation == 'readonly':
        a.rows[1]['options'] = 'ro'
    elif observation == 'stacked':
        a.rows.append(dict(a.rows[1], source='/dev/mapper/other'))
    elif observation == 'unsupported':
        a.top['supported'] = False
    elif observation == 'unencrypted':
        a.top['encrypted'] = False
    elif observation == 'serial':
        a.top['disk']['serial'] = 'FOREIGN'
    elif observation == 'uuid':
        a.top['chain'][-1]['uuid'] = 'FOREIGN'
    elif observation == 'mount-target':
        a.top['mount'] = dict(a.top['mount'], target=str(a.home))
    elif observation == 'fsroot':
        a.top['mount'] = dict(a.top['mount'], fsroot='/foreign')
    else:
        a.rows[1]['fstype'] = 'tmpfs'
    target = a.j.get('destContainer', a.j['dest'])
    with monkeypatch.context() as lookups:
        touched = guard_drive_lookups(lookups, a)
        result = a.m.cancel(a.j['id'])
        assert touched == []
    assert result['state'] == 'rolled-back' and result['destinationInspected'] is False
    assert result['retainedDestination'] == target and 'not inspected' in result['message']
    assert a.c.read('moves', a.j['id'])['state'] == 'rolled-back'
    assert pathlib.Path(target, 'partial').read_bytes() == b'retain this flat-layout copy'


@pytest.mark.parametrize('missing', ['content', 'container', 'both'])
@pytest.mark.parametrize('exists', [False, True])
def test_healthy_incomplete_cancel_retains_unknown_and_reassesses(plan, monkeypatch, missing, exists):
    a = plan
    wrapper = pathlib.Path(a.j['dest'])
    content = wrapper / 'content'
    content.mkdir()
    a.j.update(destContainer=str(wrapper), dest=str(content), destFSRoot=a.j['destFSRoot'] + '/content',
               destIdentity=moves.identity(content), containerIdentity=moves.identity(wrapper),
               preparation={'completed': True})
    if missing in ('content', 'both'):
        a.j.pop('destIdentity')
    if missing in ('container', 'both'):
        a.j.pop('containerIdentity')
    if not exists:
        content.rmdir()
        (wrapper / 'partial').unlink()
        wrapper.rmdir()
    a.c.journal('moves', a.j['id'], a.j)
    # A real filesystem row over its inactive autofs control is normal, not
    # ambiguity. Neither policy admission nor cancellation mounts anything.
    a.rows.insert(1, dict(a.rows[1], fstype='autofs', source='systemd-1'))
    with monkeypatch.context() as lookups:
        touched = guard_drive_lookups(lookups, a, permitted=True)
        result = a.m.cancel(a.j['id'])
        assert touched and all(path == str(wrapper) for _, path in touched), 'only existence of the unowned wrapper may be observed'
    assert result['state'] == 'rolled-back' and result['destinationInspected'] is True
    assert result['retainedDestination'] == (str(wrapper) if exists else None)
    saved = a.c.read('moves', a.j['id'])
    assert saved['preparation']['completed'] is True
    if exists:
        assert (wrapper / 'partial').read_bytes() == b'retain this flat-layout copy'
    real_user = moves.pwd.getpwuid(a.j['uid'])
    monkeypatch.setattr(moves.pwd, 'getpwuid', lambda uid: types.SimpleNamespace(
        pw_dir=str(a.home), pw_name=real_user.pw_name, pw_gid=real_user.pw_gid))
    monkeypatch.setattr(preparedrive, 'check', lambda *args, **kwargs: None)
    monkeypatch.setattr(a.m, 'offline_layout', lambda *args: (a.j['sourceMount'], 'fixture'))
    real_stat = pathlib.Path.stat
    def mount_metadata(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if str(path) == a.j['destMount']:
            values = list(info)
            values[4] = 0
            return os.stat_result(values)
        return info
    monkeypatch.setattr(pathlib.Path, 'stat', mount_metadata)
    fresh = a.m.assess(a.j['source'], a.j['destMount'])
    assert fresh['ok'] is True and fresh['needsPreparation'] is False
    assert fresh['stats']['files'] == 1
    assert pathlib.Path(a.j['source'], 'synthetic-token').read_bytes() == b'synthetic private source'


@pytest.mark.parametrize('observation', ['healthy', 'readonly', 'missing'])
def test_inspect_incomplete_container_identity_never_touches_child(plan, monkeypatch, observation):
    a = plan
    a.j.update(destContainer=a.j['dest'], dest=a.j['dest'] + '/content',
               destFSRoot=a.j['destFSRoot'] + '/content')
    a.c.journal('moves', a.j['id'], a.j)
    if observation == 'readonly':
        a.rows[1]['options'] = 'ro'
    elif observation == 'missing':
        a.top['supported'] = False
    before = a.c.path('moves', a.j['id']).read_bytes()
    with monkeypatch.context() as lookups:
        touched = guard_drive_lookups(lookups, a)
        row = a.m.inspect()[0]
        assert touched == []
    assert row['canCancelIncomplete'] is True and row['bound'] is False
    assert a.c.path('moves', a.j['id']).read_bytes() == before


@pytest.mark.parametrize('observation', ['failure', 'oserror', 'incomplete-topology'])
def test_unobservable_destination_topology_cancels_without_child_lookup(plan, monkeypatch, observation):
    a = plan
    a.j.pop('destIdentity')
    a.c.journal('moves', a.j['id'], a.j)
    real_probe = moves.probe
    def unknown(path, *args, **kwargs):
        if path != a.j['destMount']:
            return real_probe(path, *args, **kwargs)
        assert kwargs.get('resolve') is False
        if observation == 'failure':
            raise Failure('topology unavailable')
        if observation == 'oserror':
            raise OSError('device disappeared')
        return {'supported': True}
    monkeypatch.setattr(moves, 'probe', unknown)
    with monkeypatch.context() as lookups:
        touched = guard_drive_lookups(lookups, a)
        result = a.m.cancel(a.j['id'])
        assert touched == []
    assert result['destinationInspected'] is False
    assert result['retainedDestination'] == a.j['dest']
    assert a.c.read('moves', a.j['id'])['state'] == 'rolled-back'


@pytest.fixture
def returned_plan(plan):
    a = plan
    backup = pathlib.Path(a.j['sourceMount']) / '.drives-quarantine' / a.j['id'] / 'original'
    backup.mkdir(parents=True)
    (backup / 'frozen-original').write_bytes(b'retain frozen original')
    store = pathlib.Path(a.j['sourceMount']) / '.drives-return' / a.j['id']
    store.mkdir(parents=True)
    (store / 'retained-stage').write_bytes(b'retain return stage')
    a.j.update(state='returned', verified=True, backup=str(backup),
               returnedIdentity=moves.identity(a.j['source']), returnStore={'path': str(store)})
    a.c.journal('moves', a.j['id'], a.j)
    return a


def test_returned_local_source_can_be_freshly_assessed_without_deleting_history(returned_plan, monkeypatch):
    a = returned_plan
    before = a.c.path('moves', a.j['id']).read_bytes()
    real_user = moves.pwd.getpwuid(a.j['uid'])
    monkeypatch.setattr(moves.pwd, 'getpwuid', lambda uid: types.SimpleNamespace(
        pw_dir=str(a.home), pw_name=real_user.pw_name, pw_gid=real_user.pw_gid))
    monkeypatch.setattr(preparedrive, 'check', lambda *args, **kwargs: None)
    monkeypatch.setattr(a.m, 'offline_layout', lambda *args: (a.j['sourceMount'], 'fixture'))
    real_stat = pathlib.Path.stat
    def mount_metadata(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if str(path) == a.j['destMount']:
            values = list(info)
            values[4] = 0
            return os.stat_result(values)
        return info
    monkeypatch.setattr(pathlib.Path, 'stat', mount_metadata)
    fresh = a.m.assess(a.j['source'], a.j['destMount'])
    assert fresh['ok'] is True and fresh['stats']['files'] == 1
    assert a.c.path('moves', a.j['id']).read_bytes() == before
    assert pathlib.Path(a.j['backup'], 'frozen-original').read_bytes() == b'retain frozen original'
    assert pathlib.Path(a.j['dest'], 'partial').read_bytes() == b'retain this flat-layout copy'
    assert pathlib.Path(a.j['returnStore']['path'], 'retained-stage').read_bytes() == b'retain return stage'


@pytest.mark.parametrize('retained', ['backup', 'dest', 'returnStore', 'retainedReturnStages'])
@pytest.mark.parametrize('relation', ['exact', 'child', 'parent'])
def test_returned_history_still_blocks_migration_of_all_retained_copies(returned_plan, retained, relation):
    a = returned_plan
    abandoned = pathlib.Path(a.j['returnStore']['path']).with_name('abandoned-stage')
    abandoned.mkdir()
    (abandoned / 'unknown').write_bytes(b'retain abandoned stage')
    a.j['retainedReturnStages'] = [{'path': str(abandoned)}]
    a.c.journal('moves', a.j['id'], a.j)
    path = (a.j[retained] if retained in ('backup', 'dest') else
            a.j['returnStore']['path'] if retained == 'returnStore' else str(abandoned))
    candidate = path if relation == 'exact' else path + '/child' if relation == 'child' else str(pathlib.Path(path).parent)
    before = a.c.path('moves', a.j['id']).read_bytes()
    with pytest.raises(Failure, match='overlaps'):
        a.m.conflicts(candidate)
    assert a.c.path('moves', a.j['id']).read_bytes() == before
    assert (abandoned / 'unknown').read_bytes() == b'retain abandoned stage'


@pytest.mark.parametrize('state', sorted(offline.OFFLINE))
def test_legacy_interrupted_offline_continue_can_still_request_restoration(plan, monkeypatch, state):
    a = plan
    a.j['state'] = state
    a.c.journal('moves', a.j['id'], a.j)
    calls = []
    monkeypatch.setattr(a.m, 'latch', lambda *args: calls.append(args))
    with monkeypatch.context() as lookups:
        touched = guard_drive_lookups(lookups, a)
        result = a.m.resume(a.j['id'])
        assert touched == []
    assert result['action'] == 'continue' and result['state'] == 'restart-required'
    assert calls == [('arm', a.j['id'], 'continue')]
    assert a.c.read('moves', a.j['id'])['state'] == state


def test_legacy_finished_inspect_undo_and_delete_compatibility(plan, monkeypatch):
    a = plan
    a.j.update(state='switched', verified=True, error='old error')
    backup = pathlib.Path(a.j['backup'])
    backup.mkdir()
    (backup / 'original').write_bytes(b'disposable old original')
    a.j['sourceIdentity'] = moves.identity(backup)
    a.rows.append(dict(a.rows[1], target=a.j['source'], fsroot=a.j['destFSRoot']))
    a.c.journal('moves', a.j['id'], a.j)
    real_probe = moves.probe
    monkeypatch.setattr(moves, 'probe', lambda p, *args, **kwargs: a.source_top if p == a.j['backup'] else real_probe(p, *args, **kwargs))
    row = a.m.inspect()[0]
    assert row['bound'] is True and row['state'] == 'rebooted' and row['canDelete'] is True
    assert row['canMoveBack'] is False, 'legacy active copies are not implicitly upgraded'
    a.m.destination(a.j)
    calls = []
    monkeypatch.setattr(a.m, 'latch', lambda *args: calls.append(args))
    assert a.m.rollback(a.j['id'])['action'] == 'rollback'
    assert calls == [('arm', a.j['id'], 'rollback')]
    assert backup.joinpath('original').read_bytes() == b'disposable old original'
    result = a.m.delete_old(a.j['id'])
    assert result['state'] == 'cleaned' and not backup.exists()
    assert pathlib.Path(a.j['dest'], 'partial').read_bytes() == b'retain this flat-layout copy'
    assert pathlib.Path(a.j['source'], 'synthetic-token').read_bytes() == b'synthetic private source'


def test_private_waiting_plan_still_arms_continue(plan, monkeypatch):
    a = plan
    wrapper = pathlib.Path(a.j['dest'])
    wrapper.chmod(0o700)
    content = wrapper / 'content'
    content.mkdir(mode=0o755)
    a.j.update(destContainer=str(wrapper), dest=str(content), destFSRoot=a.j['destFSRoot'] + '/content',
               destIdentity=moves.identity(content), containerIdentity=moves.identity(wrapper))
    a.c.journal('moves', a.j['id'], a.j)
    real_fstat = os.fstat
    def root_wrapper(fd):
        info = real_fstat(fd)
        if info.st_ino == a.j['containerIdentity'][1]:
            values = list(info)
            values[4] = 0
            return os.stat_result(values)
        return info
    monkeypatch.setattr(os, 'fstat', root_wrapper)
    calls = []
    monkeypatch.setattr(a.m, 'latch', lambda *args: calls.append(args))
    assert a.m.request(a.j['id'], 'continue')['state'] == 'restart-required'
    assert calls == [('arm', a.j['id'], 'continue')]
    assert wrapper.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('state', ['awaiting-maintenance', 'paused', 'switched', 'cleaned', *sorted(moves.RETURN_STATES)])
def test_not_yet_returned_history_still_reserves_source(plan, state):
    a = plan
    a.j['state'] = state
    a.c.journal('moves', a.j['id'], a.j)
    with pytest.raises(Failure, match='overlaps'):
        a.m.conflicts(a.j['source'])
