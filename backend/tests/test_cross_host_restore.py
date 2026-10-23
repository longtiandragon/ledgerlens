"""Cross-host evidence boundaries, without a provider or a working database."""
from copy import deepcopy
import hashlib

import pytest

from scripts import validate_cross_host_restore as cross


def host(name):
    return {'node_sha256': hashlib.sha256(name.encode()).hexdigest(),
            'host_id_sha256': hashlib.sha256(('machine-' + name).encode()).hexdigest(),
            'identity_method': 'fixture', 'system': 'fixture-os', 'release': 'fixture-release',
            'machine': 'fixture-architecture', 'python': '3.12'}


def expected():
    seeded = dict(zip(('book_id', 'snapshot_id', 'case_id', 'check_id'),
                      (f'00000000-0000-0000-0000-{i:012d}' for i in range(1, 5)), strict=True))
    observed = {'records': deepcopy(cross.ORACLE['records']),
                'documents': [{'id': 'document-id', 'book_id': seeded['book_id'], 'sha256': 'a' * 64}],
                'snapshot': {'id': seeded['snapshot_id'], 'book_id': seeded['book_id']},
                'overview': {'book': {'id': seeded['book_id']}, 'latest_snapshot_id': seeded['snapshot_id'],
                             'counts': {'records': 2}}}
    run = {'id': 'run-id', 'case_id': seeded['case_id'], 'mode': 'rules', 'status': 'complete',
           'result': {'status': 'supported', 'claims': [{'calculation': {'difference': '10.00'}}]}}
    return {'schema_version': cross.VERSION, 'oracle': deepcopy(cross.ORACLE), 'seeded': seeded,
            'views': observed, 'run': run, 'business_rows_sha256': 'b' * 64, 'checkpoint_rows_sha256': 'c' * 64}


def bundle_fixture(tmp_path, monkeypatch):
    bundle = tmp_path / 'bundle'
    (bundle / 'backup').mkdir(parents=True)
    for name in ('ledgerlens.sqlite', 'checkpoints.sqlite'):
        (bundle / 'backup' / name).write_bytes(b'fixture-only-no-database')
    cross.write_json(bundle / 'expected.json', expected())
    cross.write_json(bundle / 'backup/backup.json',
                     {'schema_version': 'sqlite-local-backup-v2', 'type': 'sqlite-local', 'checkpoints_included': True})
    metadata = {'schema_version': cross.VERSION, 'synthetic': True, 'source_host': host('source'),
                'source_sha256': {'fixture.py': 'd' * 64}, 'files': cross.inventory(bundle)}
    cross.write_json(bundle / 'bundle.json', metadata)
    monkeypatch.setattr(cross, 'source_hashes', lambda: {'fixture.py': 'd' * 64})
    # These tests isolate the transfer envelope; real SQLite backup verification
    # has separate tests and the two-host CLI executes it without this stub.
    monkeypatch.setattr(cross, 'verify_backup', lambda *_: None)
    return bundle, metadata


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'C:/escape', r'backup\file',
                                 'backup/../file', './file', 'backup//file', ''])
def test_cross_host_rejects_path_traversal(name):
    with pytest.raises(ValueError, match='Unsafe bundle path'):
        cross.safe_relative(name)


@pytest.mark.parametrize('same', ['node_sha256', 'host_id_sha256', 'both'])
def test_cross_host_rejects_same_execution_host(same):
    source, target = host('source'), host('target')
    for key in ('node_sha256', 'host_id_sha256'):
        if same in (key, 'both'):
            target[key] = source[key]
    with pytest.raises(ValueError, match='Same execution host'):
        cross.require_other_host(source, target)


def test_cross_host_rejects_missing_host_evidence():
    target = host('target')
    target.pop('host_id_sha256')
    with pytest.raises(ValueError, match='Invalid execution host'):
        cross.require_other_host(host('source'), target)


@pytest.mark.parametrize('damage', ['oracle', 'amount', 'empty_records', 'empty_documents', 'wrong_scope',
                                   'wrong_run', 'wrong_difference', 'missing_checkpoints'])
def test_cross_host_rejects_wrong_independent_expectations(damage):
    value = expected()
    cross.validate_expected(value)
    if damage == 'oracle':
        value['oracle']['difference'] = '11.00'
    elif damage == 'amount':
        value['views']['records'][0]['debit'] = '101.00'
    elif damage == 'empty_records':
        value['views']['records'] = []
    elif damage == 'empty_documents':
        value['views']['documents'] = []
    elif damage == 'wrong_scope':
        value['views']['snapshot']['book_id'] = 'wrong'
    elif damage == 'wrong_run':
        value['run']['mode'] = 'single'
    elif damage == 'wrong_difference':
        value['run']['result']['claims'][0]['calculation']['difference'] = '0.00'
    else:
        value.pop('checkpoint_rows_sha256')
    with pytest.raises(ValueError):
        cross.validate_expected(value)


def test_cross_host_valid_transfer_envelope(tmp_path, monkeypatch):
    bundle, _ = bundle_fixture(tmp_path, monkeypatch)
    metadata, value = cross.validate_bundle(bundle, host('target'))
    assert metadata['synthetic'] is True
    assert value['oracle'] == cross.ORACLE


@pytest.mark.parametrize('damage', ['file', 'missing', 'extra', 'path', 'source', 'same_host', 'expected'])
def test_cross_host_rejects_tampered_or_incomplete_bundle(tmp_path, monkeypatch, damage):
    bundle, metadata = bundle_fixture(tmp_path, monkeypatch)
    if damage == 'file':
        (bundle / 'backup/ledgerlens.sqlite').write_bytes(b'changed')
    elif damage == 'missing':
        (bundle / 'backup/checkpoints.sqlite').unlink()
        metadata['files'] = cross.inventory(bundle)
    elif damage == 'extra':
        (bundle / 'unexpected.txt').write_text('unexpected', encoding='utf-8')
    elif damage == 'path':
        metadata['files']['../escape'] = {'sha256': 'e' * 64, 'bytes': 1}
    elif damage == 'source':
        metadata['source_sha256'] = {'other.py': 'e' * 64}
    elif damage == 'same_host':
        metadata['source_host'] = host('target')
    else:
        value = expected()
        value['views']['records'][0]['debit'] = '999.00'
        (bundle / 'expected.json').unlink()
        cross.write_json(bundle / 'expected.json', value)
        metadata['files'] = cross.inventory(bundle)
    (bundle / 'bundle.json').unlink()
    cross.write_json(bundle / 'bundle.json', metadata)
    with pytest.raises(ValueError):
        cross.validate_bundle(bundle, host('target'))


def test_cross_host_json_duplicate_keys_rejected(tmp_path):
    path = tmp_path / 'duplicate.json'
    path.write_text('{"files": {}, "files": {"wrong": 1}}', encoding='utf-8')
    with pytest.raises(ValueError, match='Duplicate JSON key'):
        cross.read_json(path)


@pytest.mark.asyncio
async def test_cross_host_failure_is_retained_without_starting_api(tmp_path, monkeypatch):
    bundle, _ = bundle_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(cross, 'host_metadata', lambda: host('source'))

    def forbidden(*_):
        raise AssertionError('A rejected bundle must not start an API or restore')

    monkeypatch.setattr(cross, 'restore', forbidden)
    monkeypatch.setattr(cross, 'IsolatedServer', forbidden)
    output = tmp_path / 'failed-verification'
    result = await cross.execute('verify', output, bundle)
    assert result['status'] == 'failed'
    assert result['independent_host_verified'] is False
    assert 'Same execution host' in result['error']['message']
    assert cross.read_json(output / 'report.json') == result
    assert not (output / 'restored').exists()


@pytest.mark.asyncio
async def test_cross_host_never_overwrites_existing_output_or_writes_inside_bundle(tmp_path):
    existing = tmp_path / 'existing'
    existing.mkdir()
    with pytest.raises(FileExistsError):
        await cross.execute('prepare', existing)
    with pytest.raises(ValueError, match='disjoint'):
        await cross.execute('verify', existing / 'inside', existing)


def test_cross_host_logical_hash_handles_checkpoint_bytes_portably():
    first = {'table': [(1, b'\x00\xff', None)]}
    second = {'table': [[1, b'\x00\xff', None]]}
    assert cross.canonical(first) == cross.canonical(second)
    assert b'AP8=' in cross.canonical(first)
