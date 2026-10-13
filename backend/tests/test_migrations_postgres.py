"""Opt-in live PostgreSQL migration checks, isolated by a rolled-back schema."""
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema

from app.db import Base, Document, Snapshot, OcrRun, OcrReview, ReferenceSearch, ReferenceChoice
from test_migrations import configuration


@pytest.mark.parametrize('starting_revision', ['base', '0004'])
def test_live_postgres_forward_migrations_preserve_evidence_and_constraints(starting_revision):
    if os.environ.get('MIGRATION_POSTGRES_TESTS') != '1':
        pytest.skip('Explicit MIGRATION_POSTGRES_TESTS=1 requires the dedicated local PostgreSQL service')
    engine = create_engine('postgresql+psycopg://retrieval:local-retrieval-only@127.0.0.1:55439/retrieval',
                           connect_args={'connect_timeout': 5})
    # This generated identifier is never derived from user input. DDL and data
    # stay in one outer transaction; no table in public or the index is touched.
    schema = 'migration_check_' + uuid4().hex
    saved_schema = None
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            saved_schema = connection.dialect.default_schema_name
            try:
                connection.execute(CreateSchema(schema))
                connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                connection.execute(text("SET LOCAL statement_timeout='20s'"))
                assert connection.scalar(text('SELECT current_schema()')) == schema
                connection.dialect.default_schema_name = schema
                config = configuration()
                config.attributes['connection'] = connection
                if starting_revision == 'base':
                    command.upgrade(config, 'head')
                else:
                    command.upgrade(config, starting_revision)
                    connection.execute(Document.__table__.insert().values(
                        id='source', created_at='2026-10-13', book_id='synthetic-book', name='scan.pdf',
                        sha256='original-hash', storage_path='not-a-real-file', synthetic=True,
                        media_type='application/pdf', extracted=[]))
                    connection.execute(Snapshot.__table__.insert().values(
                        id='snapshot', created_at='2026-10-13', book_id='synthetic-book', revision=1,
                        policy={'framework': 'CAS', 'version': 1, 'amount': '1394.99'},
                        record_ids=['preserved-record'], digest='snapshot-hash'))
                    before = {table: connection.execute(text(f'SELECT * FROM {table}')).all()
                              for table in ('documents', 'snapshots')}
                    command.upgrade(config, '0005')
                    connection.execute(OcrRun.__table__.insert().values(
                        id='ocr', created_at='2026-10-13', book_id='synthetic-book', document_id='source',
                        book_version=1, period='2026-09', status='complete',
                        result={'amount': '1394.99', 'confidence': 1.0}, result_digest='ocr-hash'))
                    connection.execute(OcrReview.__table__.insert().values(
                        id='review', created_at='2026-10-13', book_id='synthetic-book', run_id='ocr',
                        version=1, book_revision=1, book_version=1, parent_id=None,
                        payload={'confirmed': True, 'amount': '1394.99'}, digest='review-hash', import_id='import'))
                    before.update({table: connection.execute(text(f'SELECT * FROM {table}')).all()
                                   for table in ('ocr_runs', 'ocr_reviews')})
                    command.upgrade(config, 'head')
                    for table, rows in before.items():
                        assert connection.execute(text(f'SELECT * FROM {table}')).all() == rows
                assert transaction.is_active  # Alembic must not commit our enclosing transaction.
                assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0006'
                assert set(inspect(connection).get_table_names(schema=schema)) == set(Base.metadata.tables) | {'alembic_version'}
                assert compare_metadata(MigrationContext.configure(connection, opts={'compare_type': True}), Base.metadata) == []
                connection.execute(ReferenceSearch.__table__.insert().values(
                    id='query', created_at='2026-10-13', book_id='synthetic-book', book_version=1,
                    book_revision=1, framework='CAS', period='2026-09', query='handwritten migration fixture',
                    registry_key='fixture-only', manifest={'fixture': True}, status='failed',
                    result={}, error='intentionally not executed'))
                choice = {'created_at': '2026-10-13', 'book_id': 'synthetic-book', 'book_version': 1,
                          'payload': {'mode': 'keyword'}, 'digest': 'fixture-only'}
                connection.execute(ReferenceChoice.__table__.insert().values(**choice, id='first', version=1))
                connection.execute(ReferenceChoice.__table__.insert().values(**choice, id='second', version=2, parent_id='first'))
                for values in ({'id': 'duplicate-version', 'version': 2},
                               {'id': 'duplicate-parent', 'version': 3, 'parent_id': 'first'}):
                    with pytest.raises(IntegrityError):
                        with connection.begin_nested():
                            connection.execute(ReferenceChoice.__table__.insert().values(**choice, **values))
                command.upgrade(config, 'head')
                assert connection.scalar(text('SELECT count(*) FROM reference_choices')) == 2
                assert connection.scalar(text('SELECT count(*) FROM reference_searches')) == 1
            finally:
                transaction.rollback()
                connection.dialect.default_schema_name = saved_schema
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT count(*) FROM pg_namespace WHERE nspname=:schema'), {'schema': schema}) == 0
    finally:
        engine.dispose()
