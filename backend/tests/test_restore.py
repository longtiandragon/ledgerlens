import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.restore_local import restore


@pytest.fixture
def backup(tmp_path):
    root = tmp_path / 'backup'
    root.mkdir()
    (root / 'backup.json').write_text(json.dumps({'type': 'sqlite-local'}), encoding='utf-8')
    content = b'original,source\n100.01,evidence\n'
    sha = hashlib.sha256(content).hexdigest()
    document = root / 'sources' / sha[:2] / sha
    document.parent.mkdir(parents=True)
    document.write_bytes(content)
    with sqlite3.connect(root / 'ledgerlens.sqlite') as database:
        database.execute('CREATE TABLE documents (id TEXT PRIMARY KEY, sha256 TEXT, storage_path TEXT)')
        database.execute('INSERT INTO documents VALUES (?, ?, ?)', ('proof-1', sha, 'F:/old/runtime/source.csv'))
        database.execute('CREATE TABLE original_ledger (amount TEXT)')
        database.execute("INSERT INTO original_ledger VALUES ('100.01')")
    with sqlite3.connect(root / 'checkpoints.sqlite') as database:
        database.execute('CREATE TABLE checkpoints (id TEXT)')
        database.execute("INSERT INTO checkpoints VALUES ('saved-run')")
    return root


def test_restore_verifies_evidence_relocates_paths_and_preserves_original(backup, tmp_path):
    original = (backup / 'ledgerlens.sqlite').read_bytes()
    (backup / '.env').write_text('DO_NOT_COPY=private', encoding='utf-8')
    result = restore(backup, tmp_path / 'restored')
    assert result == tmp_path / 'restored'
    assert (backup / 'ledgerlens.sqlite').read_bytes() == original
    with sqlite3.connect(result / 'ledgerlens.sqlite') as database:
        sha, path = database.execute('SELECT sha256, storage_path FROM documents').fetchone()
        assert Path(path) == result / 'sources' / sha[:2] / sha
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == sha
        assert database.execute('SELECT amount FROM original_ledger').fetchone() == ('100.01',)
        assert database.execute('PRAGMA integrity_check').fetchone() == ('ok',)
    with sqlite3.connect(result / 'checkpoints.sqlite') as database:
        assert database.execute('SELECT id FROM checkpoints').fetchone() == ('saved-run',)
    assert not (result / '.env').exists()
    assert not list(tmp_path.glob('.restored-restore-*'))


@pytest.mark.parametrize('target', ['backup', 'backup/child', 'existing'])
def test_restore_never_overwrites_or_recurses_inside_backup(backup, tmp_path, target):
    (tmp_path / 'existing').mkdir()
    with pytest.raises(ValueError, match='new directory outside'):
        restore(backup, tmp_path / target)


@pytest.mark.parametrize('fault', ['missing', 'content', 'hash', 'database', 'checkpoints', 'type'])
def test_invalid_backup_is_rejected_before_creating_destination(backup, tmp_path, fault):
    if fault in {'missing', 'content'}:
        document = next((backup / 'sources').glob('*/*'))
        if fault == 'missing':
            document.unlink()
        else:
            document.write_bytes(b'modified-source')
    elif fault == 'hash':
        with sqlite3.connect(backup / 'ledgerlens.sqlite') as database:
            database.execute("UPDATE documents SET sha256='../outside'")
    elif fault in {'database', 'checkpoints'}:
        name = 'ledgerlens.sqlite' if fault == 'database' else 'checkpoints.sqlite'
        (backup / name).write_bytes(b'broken database')
    else:
        (backup / 'backup.json').write_text('{"type":"postgres"}', encoding='utf-8')
    destination = tmp_path / 'restored'
    with pytest.raises(ValueError):
        restore(backup, destination)
    assert not destination.exists()


def test_copy_failure_leaves_no_partial_restore(backup, tmp_path, monkeypatch):
    def failing_copy(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr('scripts.restore_local.shutil.copy2', failing_copy)
    with pytest.raises(OSError, match='disk full'):
        restore(backup, tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()
    assert not list(tmp_path.glob('.restored-restore-*'))
    assert (backup / 'ledgerlens.sqlite').exists()
