"""Prepare a file-backed local SQLite database using frozen forward migrations.

Unversioned databases are adopted only when their complete schema matches one
frozen revision. Before any change to an existing schema, take a SQLite backup
while holding a writer reservation. Unknown or drifted schemas fail unchanged.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def configuration(connection):
    config = Config(str(ROOT / 'alembic.ini'))
    config.set_main_option('script_location', str(ROOT / 'backend/migrations'))
    config.attributes['connection'] = connection
    return config


def schema(connection):
    """Compare columns, keys, constraints and indexes, not just table names."""
    inspector = inspect(connection)
    if inspector.get_view_names() or connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='trigger'").all():
        raise ValueError('Views or triggers are outside the supported local schema')
    result = {}
    for table in sorted(set(inspector.get_table_names()) - {'alembic_version'}):
        columns = []
        for column in inspector.get_columns(table):
            columns.append({k: str(v) if k == 'type' else v for k, v in column.items()})
        result[table] = {
            'columns': sorted(columns, key=lambda c: c['name']),
            'primary_key': inspector.get_pk_constraint(table),
            'unique': sorted(inspector.get_unique_constraints(table), key=lambda c: str(c['column_names'])),
            'foreign_keys': sorted(inspector.get_foreign_keys(table), key=lambda c: str(c['constrained_columns'])),
            'checks': sorted(inspector.get_check_constraints(table), key=lambda c: c['sqltext']),
            'indexes': sorted(inspector.get_indexes(table), key=lambda c: c['name']),
        }
    # Index dialect options can contain a SQL expression object; retain its text.
    return json.loads(json.dumps(result, default=str, sort_keys=True))


def frozen_schemas():
    engine = create_engine('sqlite:///:memory:')
    try:
        with engine.begin() as connection:
            config = configuration(connection)
            scripts = ScriptDirectory.from_config(config)
            head = scripts.get_current_head()
            revisions = list(reversed(list(scripts.walk_revisions())))
            result = {}
            for revision in revisions:
                command.upgrade(config, revision.revision)
                result[revision.revision] = schema(connection)
            return head, result
    finally:
        engine.dispose()


def prepare(database: Path):
    database = database.absolute()
    if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()) for p in [database, *database.parents]):
        raise ValueError('Database path cannot traverse filesystem links')
    if database.is_dir():
        raise ValueError('SQLite database must be a regular file path')
    database.parent.mkdir(parents=True, exist_ok=True)
    head, known = frozen_schemas()
    engine = create_engine('sqlite:///' + database.as_posix(), connect_args={'timeout': 2})
    backup_path = None
    try:
        with engine.connect() as connection:
            # Explicit BEGIN also makes SQLite DDL transactional on Python 3.12.
            # IMMEDIATE permits readers for the backup but excludes other writers.
            connection.exec_driver_sql('BEGIN IMMEDIATE')
            actual = schema(connection)
            versioned = 'alembic_version' in inspect(connection).get_table_names()
            if versioned:
                inspector = inspect(connection)
                columns = inspector.get_columns('alembic_version')
                if (len(columns) != 1 or columns[0]['name'] != 'version_num'
                        or str(columns[0]['type']) != 'VARCHAR(32)' or columns[0]['nullable']
                        or inspector.get_pk_constraint('alembic_version')['constrained_columns'] != ['version_num']):
                    raise ValueError('Migration version table has an unexpected schema')
            current = connection.exec_driver_sql('SELECT version_num FROM alembic_version').scalars().all() if versioned else []
            if len(current) > 1 or (current and current[0] not in known):
                raise ValueError('Unknown or branched local migration revision')
            if current:
                previous = current[0]
                if actual != known[previous]:
                    raise ValueError('Versioned database schema has drifted; no migration was attempted')
            elif actual:
                matches = [revision for revision, expected in known.items() if actual == expected]
                if len(matches) != 1:
                    raise ValueError('Unversioned database does not match a frozen schema; no adoption was attempted')
                previous = matches[0]
            else:
                previous = None
            if current == [head]:
                connection.rollback()
                return {'status': 'current', 'revision': head, 'adopted': False, 'backup': None}
            if actual or versioned:
                backup_dir = database.parent / 'schema-backups'
                if backup_dir.is_symlink() or (hasattr(backup_dir, 'is_junction') and backup_dir.is_junction()):
                    raise ValueError('Backup directory cannot be a filesystem link')
                backup_dir.mkdir(exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                backup_path = backup_dir / f'{database.stem}-{stamp}-{uuid4().hex}.sqlite'
                # Reserve a unique output; never replace an earlier backup.
                with backup_path.open('xb'):
                    pass
                with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source:
                    with closing(sqlite3.connect(backup_path)) as backup:
                        source.backup(backup)
                        if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                            raise ValueError('Pre-migration backup failed integrity checking')
            config = configuration(connection)
            if previous and not current:
                command.stamp(config, previous)
            command.upgrade(config, head)
            if schema(connection) != known[head]:
                raise ValueError('Migrated schema differs from frozen head')
            connection.commit()
        return {'status': 'upgraded', 'from_revision': previous, 'revision': head, 'adopted': bool(previous and not current),
                'backup': str(backup_path) if backup_path else None,
                'backup_sha256': hashlib.sha256(backup_path.read_bytes()).hexdigest() if backup_path else None}
    finally:
        engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, help='Override the configured local SQLite file for an isolated rehearsal')
    args = parser.parse_args()
    if args.database is None:
        from app.config import settings
        url = make_url(settings.database_url)
        if url.get_backend_name() != 'sqlite' or not url.database or url.database == ':memory:' or url.query:
            raise SystemExit('Local preparation requires a plain file SQLite URL. Use the PostgreSQL deployment migration workflow otherwise.')
        args.database = Path(url.database)
    print(json.dumps(prepare(args.database), ensure_ascii=False))
