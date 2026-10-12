"""The installed schema, not the live metadata, must be produced by migrations."""
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.db import Base


ROOT = Path(__file__).resolve().parents[2]


def configuration():
    config = Config(str(ROOT / 'alembic.ini'))
    config.set_main_option('script_location', str(ROOT / 'backend' / 'migrations'))
    return config


def test_empty_upgrade_has_exact_tables_columns_constraints_and_indexes(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "migration.sqlite"}')
    with engine.begin() as connection:
        config = configuration()
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0004'
        context = MigrationContext.configure(connection, opts={'compare_type': True})
        assert compare_metadata(context, Base.metadata) == []
        assert set(inspect(connection).get_table_names()) == set(Base.metadata.tables) | {'alembic_version'}
        # An upgrade retry is idempotent and preserves persisted evidence.
        connection.execute(text("INSERT INTO evaluations (id, created_at, result) VALUES ('saved', '2026', '{}')"))
        command.upgrade(config, 'head')
        assert connection.scalar(text('SELECT count(*) FROM evaluations')) == 1
    engine.dispose()


def test_frozen_initial_migration_generates_postgres_sql_without_application_models():
    initial = (ROOT / 'backend/migrations/versions/0001_initial.py').read_text(encoding='utf-8')
    assert 'from app.' not in initial
    assert 'metadata.create_all' not in initial
    output = StringIO()
    config = configuration()
    config.output_buffer = output
    config.attributes['database_url'] = 'postgresql+psycopg://unused/unused'
    command.upgrade(config, 'head', sql=True)
    sql = output.getvalue()
    assert 'CREATE TABLE agent_runs' in sql
    assert 'CREATE INDEX ix_records_book_id' in sql
    assert 'UNIQUE (book_id, kind, source_id)' in sql
    assert 'UNIQUE (active_slot)' in sql
    assert 'CREATE TABLE policy_observation_versions' in sql
    assert 'CREATE TABLE policy_check_runs' in sql
    policy_migration = (ROOT / 'backend/migrations/versions/0002_policy_checks.py').read_text(encoding='utf-8')
    assert 'from app.' not in policy_migration and 'metadata.create_all' not in policy_migration
    assert 'INSERT INTO alembic_version' in sql


def test_destructive_downgrade_is_refused_and_existing_data_survives(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "migration.sqlite"}')
    with engine.begin() as connection:
        config = configuration()
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
        with pytest.raises(RuntimeError, match='destroy evidence'):
            command.downgrade(config, 'base')
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0004'
        assert 'documents' in inspect(connection).get_table_names()
    engine.dispose()


def test_langgraph_checkpoint_tables_are_not_reported_as_drops_but_strays_are(tmp_path):
    from app.db import alembic_include_object
    engine = create_engine(f'sqlite:///{tmp_path / "checkpointer.sqlite"}')
    with engine.begin() as connection:
        config = configuration()
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
        # Shapes as LangGraph's checkpointer creates them after the first run.
        connection.execute(text('CREATE TABLE checkpoints (thread_id TEXT, checkpoint_id TEXT)'))
        connection.execute(text('CREATE INDEX checkpoints_thread_id_idx ON checkpoints (thread_id)'))
        connection.execute(text('CREATE TABLE checkpoint_writes (thread_id TEXT, idx INTEGER)'))
        unfiltered = MigrationContext.configure(connection, opts={'compare_type': True})
        assert any(diff[0] == 'remove_table' for diff in compare_metadata(unfiltered, Base.metadata))
        filtered = MigrationContext.configure(connection, opts={
            'compare_type': True, 'include_object': alembic_include_object})
        assert compare_metadata(filtered, Base.metadata) == []
        connection.execute(text('CREATE TABLE unexpected_stray (id TEXT)'))
        diffs = compare_metadata(filtered, Base.metadata)
        assert [diff[1].name for diff in diffs if diff[0] == 'remove_table'] == ['unexpected_stray']
    engine.dispose()
