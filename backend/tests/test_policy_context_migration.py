"""Forward context migration against actual SQLite and offline PostgreSQL DDL."""
from io import StringIO

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.db import Base
from test_migrations import ROOT, configuration


def test_context_forward_upgrade_keeps_saved_artifacts_and_matches_metadata(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "context-migration.sqlite"}')
    with engine.begin() as connection:
        config = configuration()
        config.attributes['connection'] = connection
        command.upgrade(config, '0002')
        connection.execute(text("INSERT INTO evaluations (id, created_at, result) VALUES ('old', '2026', '{}')"))
        command.upgrade(config, '0003')
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0003'
        assert connection.scalar(text('SELECT count(*) FROM evaluations')) == 1
        uniques = inspect(connection).get_unique_constraints('investigation_policy_contexts')
        assert any(item['column_names'] == ['run_id'] for item in uniques)
        command.upgrade(config, 'head')
        assert connection.scalar(text('SELECT count(*) FROM evaluations')) == 1
        # Later migrations add tables; the forward head must match current models exactly.
        assert compare_metadata(MigrationContext.configure(connection, opts={'compare_type': True}),
                                Base.metadata) == []
    engine.dispose()


def test_context_migration_postgres_offline_does_not_import_application_models():
    script = (ROOT / 'backend/migrations/versions/0003_investigation_policy_context.py').read_text(encoding='utf-8')
    assert 'from app.' not in script and 'metadata.create_all' not in script
    output = StringIO()
    config = configuration()
    config.output_buffer = output
    config.attributes['database_url'] = 'postgresql+psycopg://unused/unused'
    command.upgrade(config, '0002:0003', sql=True)
    sql = output.getvalue()
    assert 'CREATE TABLE investigation_policy_contexts' in sql
    assert 'UNIQUE (run_id)' in sql
    assert 'CREATE INDEX ix_investigation_policy_contexts_book_id' in sql
    assert 'CREATE INDEX ix_investigation_policy_contexts_snapshot_id' in sql
    assert 'DROP TABLE' not in sql
