import hashlib
import json
from pathlib import Path

import pytest

from scripts import postgres_backup as pg


def stored(app, content: bytes):
    sha = hashlib.sha256(content).hexdigest()
    path = app / 'sources' / sha[:2] / sha
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return sha


def settings(tmp_path):
    return {'project': 'ledgerlens', 'image': 'pgvector/pgvector:pg17', 'user': 'ledgerlens',
            'database': 'ledgerlens', 'password': 'local-only', 'postgres_dir': tmp_path / 'live' / 'postgres',
            'app_dir': tmp_path / 'live' / 'app'}


def test_writers_must_be_stopped_and_postgres_healthy():
    pg.check_states({'postgres': ('running', 'healthy'), 'api': ('exited', ''), 'worker': ('exited', ''),
                     'scheduler': ('exited', ''), 'web': ('running', '')})
    pg.check_states({'postgres': ('running', 'healthy')})  # writer containers never created
    with pytest.raises(RuntimeError, match='api, scheduler'):
        pg.check_states({'postgres': ('running', 'healthy'), 'api': ('running', 'healthy'),
                         'scheduler': ('running', '')})
    for postgres in [('running', 'starting'), ('exited', ''), None]:
        with pytest.raises(RuntimeError, match='postgres'):
            pg.check_states({'postgres': postgres, 'api': ('exited', '')})


@pytest.mark.parametrize('name', ['books"; drop table books; --', 'Books', '1books', 'public.books', ''])
def test_fingerprint_sql_rejects_unexpected_table_names(name):
    with pytest.raises(ValueError):
        pg.fingerprint_sql(name)


def test_fingerprint_sql_is_order_independent_by_construction():
    sql = pg.fingerprint_sql('agent_runs')
    assert 'public."agent_runs"' in sql and "order by h" in sql


def test_copy_excludes_only_transient_entries(tmp_path):
    app = tmp_path / 'app'
    sha = stored(app, b'original upload')
    (app / 'telemetry').mkdir()
    (app / 'telemetry' / 'span.json').write_text('{}', encoding='utf-8')
    (app / 'celerybeat').write_bytes(b'beat')
    (app / 'celerybeat.db').write_bytes(b'beat')
    (app / 'ocr-cache').mkdir()
    (app / 'ocr-cache' / 'cached').write_bytes(b'cache')
    (app / 'process-investigations').mkdir()
    (app / 'process-investigations' / 'run.json').write_text('{}', encoding='utf-8')
    excluded = pg.copy_app(app, tmp_path / 'copy')
    assert excluded == ['celerybeat', 'celerybeat.db', 'ocr-cache']
    assert sorted(pg.inventory(tmp_path / 'copy')) == sorted([
        f'sources/{sha[:2]}/{sha}', 'telemetry/span.json', 'process-investigations/run.json'])


def test_documents_must_match_container_layout_and_content(tmp_path):
    app = tmp_path / 'app'
    sha = stored(app, b'invoice')
    good = ['doc-1', sha, f'/data/sources/{sha[:2]}/{sha}']
    pg.check_documents([good], app)
    with pytest.raises(ValueError, match='layout'):
        pg.check_documents([['doc-1', sha, f'F:/runtime/sources/{sha[:2]}/{sha}']], app)
    with pytest.raises(ValueError, match='layout'):
        pg.check_documents([['doc-1', 'not-a-hash', '/data/sources/no/not-a-hash']], app)
    missing = hashlib.sha256(b'never stored').hexdigest()
    with pytest.raises(ValueError, match='missing'):
        pg.check_documents([['doc-2', missing, f'/data/sources/{missing[:2]}/{missing}']], app)
    (app / 'sources' / sha[:2] / sha).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='does not match'):
        pg.check_documents([good], app)


def test_inventory_ignores_only_root_manifest_and_detects_changes(tmp_path):
    (tmp_path / 'backup.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'app').mkdir()
    (tmp_path / 'app' / 'backup.json').write_text('data file', encoding='utf-8')
    (tmp_path / pg.DUMP).write_bytes(b'dump')
    before = pg.inventory(tmp_path)
    assert sorted(before) == ['app/backup.json', pg.DUMP]
    (tmp_path / pg.DUMP).write_bytes(b'dump changed')
    assert pg.inventory(tmp_path) != before


def test_inventory_accepts_relative_roots(tmp_path, monkeypatch):
    (tmp_path / 'app' / 'telemetry').mkdir(parents=True)
    (tmp_path / 'app' / 'telemetry' / 'span.json').write_text('{}', encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    assert list(pg.inventory(Path('app'))) == ['telemetry/span.json']


def test_manifest_rejects_duplicate_keys_and_other_backup_types(tmp_path):
    path = tmp_path / 'backup.json'
    path.write_text('{"schema_version": "%s", "type": "compose-postgres", "type": "compose-postgres"}'
                    % pg.VERSION, encoding='utf-8')
    with pytest.raises(ValueError, match='Duplicate'):
        pg.read_manifest(tmp_path)
    for manifest in [{'schema_version': pg.VERSION, 'type': 'sqlite-local'},
                     {'schema_version': 'compose-postgres-backup-v0', 'type': 'compose-postgres'}]:
        path.write_text(json.dumps(manifest), encoding='utf-8')
        with pytest.raises(ValueError, match='supported'):
            pg.read_manifest(tmp_path)


def test_destinations_must_not_overlap_protected_directories(tmp_path):
    live = tmp_path / 'runtime' / 'docker' / 'postgres'
    assert pg.outside(tmp_path / 'backups' / 'b1', [live])
    assert not pg.outside(live, [live])
    assert not pg.outside(live / 'inside', [live])
    assert not pg.outside(tmp_path / 'runtime', [live])


def test_backup_refuses_before_any_dump_when_writers_run(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(pg, 'deployment', lambda env=None: settings(tmp_path))
    monkeypatch.setattr(pg, 'compose_states', lambda env=None: {'postgres': ('running', 'healthy'),
                                                                'worker': ('running', '')})
    monkeypatch.setattr(pg, 'run', lambda *args, **kwargs: calls.append(args))
    (tmp_path / 'backups').mkdir()
    with pytest.raises(RuntimeError, match='worker'):
        pg.backup(tmp_path / 'backups' / 'b1')
    assert calls == [] and list((tmp_path / 'backups').iterdir()) == []


def test_backup_refuses_existing_or_live_destinations(tmp_path, monkeypatch):
    live = settings(tmp_path)
    monkeypatch.setattr(pg, 'deployment', lambda env=None: live)
    monkeypatch.setattr(pg, 'compose_states', lambda env=None: pytest.fail('state checked after refusal'))
    existing = tmp_path / 'existing'
    existing.mkdir()
    for destination in [existing, live['app_dir'] / 'backup', tmp_path / 'live']:
        with pytest.raises(ValueError, match='new directory outside'):
            pg.backup(destination)


def valid_backup(tmp_path):
    backup = tmp_path / 'backup'
    app = backup / 'app'
    sha = stored(app, b'original upload')
    (backup / pg.DUMP).write_bytes(b'custom-format dump')
    manifest = {'schema_version': pg.VERSION, 'type': 'compose-postgres', 'image': 'pgvector/pgvector:pg17',
                'documents': [['doc-1', sha, f'/data/sources/{sha[:2]}/{sha}']], 'tables': {},
                'alembic_revision': '0006', 'files': pg.inventory(backup)}
    (backup / 'backup.json').write_text(json.dumps(manifest), encoding='utf-8')
    return backup


def test_restore_refuses_tampered_backup_before_starting_containers(tmp_path, monkeypatch):
    backup = valid_backup(tmp_path)
    monkeypatch.setattr(pg, 'deployment', lambda env=None: pytest.fail('deployment read after refusal'))
    (backup / pg.DUMP).write_bytes(b'custom-format dump, modified')
    with pytest.raises(ValueError, match='inventory or hashes'):
        pg.restore(backup, tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()


def test_restore_refuses_other_image_and_overlapping_destinations(tmp_path, monkeypatch):
    backup = valid_backup(tmp_path)
    live = settings(tmp_path)
    monkeypatch.setattr(pg, 'deployment', lambda env=None: live)
    monkeypatch.setattr(pg, 'open_server', lambda *args: pytest.fail('container started after refusal'))
    with pytest.raises(ValueError, match='overlap'):
        pg.restore(backup, tmp_path / 'live')
    with pytest.raises(ValueError, match='outside the backup'):
        pg.restore(backup, backup / 'restored')
    monkeypatch.setattr(pg, 'deployment', lambda env=None: {**live, 'image': 'postgres:16'})
    with pytest.raises(ValueError, match='image differs'):
        pg.restore(backup, tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()
