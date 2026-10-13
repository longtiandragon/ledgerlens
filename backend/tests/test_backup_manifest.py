from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from backend.tests import test_restore
from scripts.backup import backup
from scripts.backup_manifest import VERSION, files, read_metadata, verify
from scripts.restore_local import restore


@pytest.fixture
def source_runtime(tmp_path):
    return test_restore.backup.__wrapped__(tmp_path)


@pytest.mark.parametrize('with_checkpoints', [True, False])
def test_new_backup_manifest_roundtrip_and_optional_checkpoint_explicit(source_runtime, tmp_path, with_checkpoints):
    if not with_checkpoints:
        (source_runtime / 'checkpoints.sqlite').unlink()
    (source_runtime / '.env').write_text('not-part-of-backup', encoding='utf-8')
    before = files(source_runtime)
    saved = backup(source_runtime, tmp_path / 'saved')
    metadata = read_metadata(saved)
    assert metadata['schema_version'] == VERSION and metadata['checkpoints_included'] is with_checkpoints
    assert not (saved / '.env').exists()
    verify(saved, metadata)
    result = restore(saved, tmp_path / 'restored')
    assert (result / 'checkpoints.sqlite').exists() is with_checkpoints
    assert files(source_runtime) == before
    with closing(sqlite3.connect(result / 'ledgerlens.sqlite')) as c, c:
        assert c.execute('SELECT amount FROM original_ledger').fetchone() == ('100.01',)
        assert Path(c.execute('SELECT storage_path FROM documents').fetchone()[0]).is_relative_to(result)


@pytest.mark.parametrize('fault', ['missing_checkpoint', 'changed_checkpoint', 'changed_ledger', 'missing_source',
                                  'extra_source', 'hash', 'size', 'checkpoint_flag', 'unknown_version', 'lost_version', 'duplicate_key'])
def test_v2_rejects_incomplete_and_changed_backup_before_publishing(source_runtime, tmp_path, fault):
    saved = backup(source_runtime, tmp_path / 'saved')
    metadata = read_metadata(saved)
    if fault == 'missing_checkpoint':
        (saved / 'checkpoints.sqlite').unlink()
    elif fault in ('changed_checkpoint', 'changed_ledger'):
        filename, table = ('checkpoints.sqlite', 'checkpoints') if fault == 'changed_checkpoint' else ('ledgerlens.sqlite', 'original_ledger')
        with closing(sqlite3.connect(saved / filename)) as c, c:
            c.execute(f'DELETE FROM {table}')
    elif fault == 'missing_source':
        next((saved / 'sources').glob('*/*')).unlink()
    elif fault == 'extra_source':
        (saved / 'sources' / 'unexpected').write_bytes(b'extra')
    elif fault == 'hash':
        metadata['files']['ledgerlens.sqlite']['sha256'] = '0' * 64
    elif fault == 'size':
        metadata['files']['ledgerlens.sqlite']['bytes'] += 1
    elif fault == 'checkpoint_flag':
        metadata['checkpoints_included'] = False
    elif fault == 'unknown_version':
        metadata['schema_version'] = 'future'
    elif fault == 'lost_version':
        del metadata['schema_version']
    (saved / 'backup.json').write_text(json.dumps(metadata), encoding='utf-8')
    if fault == 'duplicate_key':
        (saved / 'backup.json').write_text('{"type":"sqlite-local","type":"sqlite-local"}', encoding='utf-8')
    with pytest.raises(ValueError):
        restore(saved, tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()
    assert not list(tmp_path.glob('.restored-restore-*'))


@pytest.mark.parametrize('fault', ['missing_source', 'bad_source', 'bad_checkpoint', 'copy_failure'])
def test_backup_failure_never_publishes_partial_directory(source_runtime, tmp_path, monkeypatch, fault):
    source_file = next((source_runtime / 'sources').glob('*/*'))
    if fault == 'missing_source':
        source_file.unlink()
    elif fault == 'bad_source':
        source_file.write_bytes(b'bad')
    elif fault == 'bad_checkpoint':
        (source_runtime / 'checkpoints.sqlite').write_bytes(b'not sqlite')
    else:
        def fail(*args, **kwargs):
            raise OSError('simulated disk full')
        monkeypatch.setattr('scripts.backup.shutil.copytree', fail)
    with pytest.raises((ValueError, OSError, sqlite3.DatabaseError)):
        backup(source_runtime, tmp_path / 'saved')
    assert not (tmp_path / 'saved').exists()
    assert not list(tmp_path.glob('.saved-backup-*'))


def test_corruption_during_restore_copy_rejected_even_after_precheck(source_runtime, tmp_path, monkeypatch):
    saved = backup(source_runtime, tmp_path / 'saved')
    import shutil
    original = shutil.copy2

    def changed_copy(source, destination, *args, **kwargs):
        result = original(source, destination, *args, **kwargs)
        if Path(source).name == 'checkpoints.sqlite':
            with closing(sqlite3.connect(destination)) as c, c:
                c.execute('DELETE FROM checkpoints')
        return result

    monkeypatch.setattr('scripts.restore_local.shutil.copy2', changed_copy)
    with pytest.raises(ValueError, match='hashes differ'):
        restore(saved, tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()
    verify(saved, read_metadata(saved))


@pytest.mark.parametrize('target', ['backup', 'backup/child', 'existing'])
def test_backup_never_overwrites_or_copies_into_source(source_runtime, tmp_path, target):
    (tmp_path / 'existing').mkdir()
    with pytest.raises(ValueError, match='new directory outside'):
        backup(source_runtime, tmp_path / target)
