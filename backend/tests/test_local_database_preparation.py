from contextlib import closing
import sqlite3

from alembic import command
import pytest
from sqlalchemy import create_engine, text

from app.db import Base
from scripts import prepare_local_database as preparation


def seeded(path, revision, versioned=True):
    engine = create_engine('sqlite:///' + path.as_posix())
    try:
        with engine.begin() as connection:
            command.upgrade(preparation.configuration(connection), revision)
            connection.execute(text("INSERT INTO evaluations (id, created_at, result) VALUES (:id, :time, :value)"),
                               {'id': 'history', 'time': '2026-09-01', 'value': '{"amount":"0.10","notice":"历史保留"}'})
            if not versioned:
                connection.exec_driver_sql('DROP TABLE alembic_version')
    finally:
        engine.dispose()


def dump(path):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
        return list(connection.iterdump())


@pytest.mark.parametrize('revision', ['0001', '0002', '0003', '0004', '0005', '0006', '0007'])
@pytest.mark.parametrize('versioned', [True, False])
def test_frozen_revisions_preserve_history_and_backup_exact_original(tmp_path, revision, versioned):
    path = tmp_path / 'history.sqlite'
    seeded(path, revision, versioned)
    original = dump(path)
    result = preparation.prepare(path)
    assert result['revision'] == '0007'
    assert result['adopted'] is (not versioned)
    if revision == '0007' and versioned:
        assert result['status'] == 'current' and result['backup'] is None
    else:
        from pathlib import Path
        assert dump(Path(result['backup'])) == original
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute('SELECT version_num FROM alembic_version').fetchall() == [('0007',)]
        assert connection.execute('SELECT * FROM evaluations').fetchall() == [
            ('{"amount":"0.10","notice":"历史保留"}', 'history', '2026-09-01')]
    after = dump(path)
    assert preparation.prepare(path)['status'] == 'current'
    assert dump(path) == after


def test_create_all_legacy_database_can_be_adopted(tmp_path):
    path = tmp_path / 'legacy.sqlite'
    engine = create_engine('sqlite:///' + path.as_posix())
    Base.metadata.create_all(engine)
    engine.dispose()
    result = preparation.prepare(path)
    assert result['adopted'] is True and result['from_revision'] == '0007'


def test_empty_database_uses_frozen_migrations(tmp_path):
    path = tmp_path / 'new' / 'empty.sqlite'
    result = preparation.prepare(path)
    assert result == {'status': 'upgraded', 'from_revision': None, 'revision': '0007',
                      'adopted': False, 'backup': None, 'backup_sha256': None}


@pytest.mark.parametrize('change', [
    'CREATE TABLE unrelated (id TEXT)',
    'ALTER TABLE books ADD COLUMN unknown TEXT',
    'ALTER TABLE alembic_version ADD COLUMN unknown TEXT',
    'DROP INDEX ix_records_book_id',
    "UPDATE alembic_version SET version_num='9999'",
    'CREATE VIEW unknown AS SELECT id FROM books',
    'CREATE TRIGGER unknown AFTER INSERT ON books BEGIN SELECT 1; END',
])
def test_unknown_or_drifted_schemas_are_not_stamped_or_migrated(tmp_path, change):
    path = tmp_path / 'unknown.sqlite'
    seeded(path, '0004')
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(change)
        connection.commit()
    before = dump(path)
    with pytest.raises(ValueError):
        preparation.prepare(path)
    assert dump(path) == before
    assert not (tmp_path / 'schema-backups').exists()


def test_partial_migration_failure_rolls_back_ddl_and_revision(tmp_path, monkeypatch):
    path = tmp_path / 'failure.sqlite'
    seeded(path, '0004', versioned=False)
    before = dump(path)
    original = command.upgrade

    def failed_upgrade(config, revision):
        connection = config.attributes['connection']
        if str(connection.engine.url).endswith('failure.sqlite'):
            connection.exec_driver_sql('CREATE TABLE should_rollback (value TEXT)')
            raise RuntimeError('injected DDL failure')
        return original(config, revision)

    monkeypatch.setattr(command, 'upgrade', failed_upgrade)
    with pytest.raises(RuntimeError, match='injected'):
        preparation.prepare(path)
    assert dump(path) == before
    backups = list((tmp_path / 'schema-backups').glob('*.sqlite'))
    assert len(backups) == 1 and dump(backups[0]) == before


def test_concurrent_writer_prevents_preparation_without_changing_data(tmp_path):
    path = tmp_path / 'locked.sqlite'
    seeded(path, '0004')
    before = dump(path)
    from sqlalchemy.exc import OperationalError
    with closing(sqlite3.connect(path)) as connection:
        connection.execute('BEGIN IMMEDIATE')
        with pytest.raises(OperationalError, match='locked'):
            preparation.prepare(path)
        connection.rollback()
    assert dump(path) == before
    assert not (tmp_path / 'schema-backups').exists()


def test_backup_failure_prevents_stamp_and_upgrade(tmp_path):
    path = tmp_path / 'blocked-backup.sqlite'
    seeded(path, '0004', versioned=False)
    before = dump(path)
    (tmp_path / 'schema-backups').write_text('must not replace', encoding='utf-8')
    with pytest.raises(FileExistsError):
        preparation.prepare(path)
    assert dump(path) == before
    assert (tmp_path / 'schema-backups').read_text(encoding='utf-8') == 'must not replace'
