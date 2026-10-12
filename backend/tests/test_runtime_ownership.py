"""Isolated worker fencing tests; no Redis, DeepSeek, or persistent user DB."""
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql.dml import Update

from app import investigation, jobs
from app.db import Base, Book, Case, Run, Snapshot, CheckRun
from app.domain import rule_identity, rule_identity_key


@pytest.fixture
def worker_db(tmp_path, monkeypatch):
    # Local observability must be isolated as well as the worker database.
    monkeypatch.setattr(jobs.settings, 'data_dir', tmp_path)
    engine = create_engine(f'sqlite:///{tmp_path / "worker.sqlite"}')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(jobs, 'Session', sessions)

    class Saver:
        async def aget_tuple(self, _):
            return None

    @asynccontextmanager
    async def saver():
        yield Saver()

    monkeypatch.setattr(jobs, 'saver_context', saver)
    yield sessions, engine
    engine.dispose()


def queued(sessions, *, valid=True, wrong_book=False):
    with sessions() as session:
        if valid:
            # A valid owned workflow now also binds its captured/legacy policy
            # context to an existing book. Keep this fixture referentially valid
            # so fencing tests reach their intended lease-race injection.
            if session.get(Book, 'book') is None:
                session.add(Book(id='book', name='isolated worker book', framework='CAS',
                                 period='2026-09', revision=1, accounts=[]))
            snapshot = Snapshot(book_id='other' if wrong_book else 'book', revision=1,
                                policy={'framework': 'CAS', 'period': '2026-09', 'version': '1',
                                        'execution_rules': rule_identity()},
                                record_ids=[], digest='test')
            session.add(snapshot)
            session.flush()
            check = CheckRun(book_id='book', snapshot_id=snapshot.id, rule_version=rule_identity_key())
            session.add(check)
            session.flush()
            case = Case(book_id='book', snapshot_id=snapshot.id, check_run_id=check.id,
                        finding={'rule_id': 'VOUCHER_BALANCE', 'record_ids': [], 'evidence_ids': []})
            session.add(case)
            session.flush()
            case_id = case.id
        else:
            case_id = 'missing-case'
        run = Run(case_id=case_id, mode='rules', active_slot=case_id)
        session.add(run)
        session.commit()
        return run.id


@pytest.mark.asyncio
@pytest.mark.parametrize('bad_context', ['missing_case', 'wrong_book', 'missing_record'])
async def test_loading_failure_releases_active_slot(worker_db, bad_context):
    sessions, _ = worker_db
    identity = queued(sessions, valid=bad_context != 'missing_case', wrong_book=bad_context == 'wrong_book')
    if bad_context == 'missing_record':
        with sessions() as session:
            snapshot = session.scalars(select(Snapshot)).one()
            snapshot.record_ids = ['absent-record']
            session.commit()
    await jobs._execute_owned(identity)
    with sessions() as session:
        run = session.get(Run, identity)
        assert run.status == 'failed'
        assert run.error
        assert run.active_slot is None and run.worker_token is None


@pytest.mark.asyncio
async def test_lease_lost_between_read_and_progress_update_cannot_overwrite(worker_db, monkeypatch):
    sessions, engine = worker_db
    identity = queued(sessions)
    hijacked = False

    def before_execute(connection, clause, *args):
        nonlocal hijacked
        if isinstance(clause, Update) and clause.table.name == 'agent_runs' and not hijacked:
            names = {getattr(key, 'name', str(key)) for key in clause._values}
            if 'steps' in names:
                hijacked = True
                # Lease recovery happens after the worker read, before its write.
                connection.exec_driver_sql('UPDATE agent_runs SET worker_token = ? WHERE id = ?',
                                           ('replacement-worker', identity))

    event.listen(engine, 'before_execute', before_execute)

    async def run(*args, on_step, **kwargs):
        await on_step({'event': 'model_attempt', 'usage': {'calls': 1}})
        raise AssertionError('Lost lease must stop execution before contacting a model')

    monkeypatch.setattr(investigation, 'investigate', run)
    await jobs._execute_owned(identity)
    event.remove(engine, 'before_execute', before_execute)
    # The injected recovery write rolls back with the rejected progress update,
    # so the exception handler may mark the still-current worker as failed. The
    # important property is that no stale progress or paid-attempt state landed.
    assert hijacked
    with sessions() as session:
        result = session.get(Run, identity)
        assert result.steps == [] and result.usage == {}
        assert result.status != 'complete'


@pytest.mark.asyncio
@pytest.mark.parametrize('fail', [False, True])
async def test_lost_lease_cannot_replace_new_worker_result(worker_db, monkeypatch, fail):
    sessions, _ = worker_db
    identity = queued(sessions)

    async def run(*args, **kwargs):
        with sessions() as session:
            session.execute(update(Run).where(Run.id == identity).values(
                worker_token='replacement-worker', status='running', result={'owner': 'new'}))
            session.commit()
        if fail:
            raise RuntimeError('old worker failure')
        return {'status': 'supported', 'usage': {'calls': 99}, 'owner': 'old'}

    monkeypatch.setattr(investigation, 'investigate', run)
    await jobs._execute_owned(identity)
    with sessions() as session:
        result = session.get(Run, identity)
        assert result.worker_token == 'replacement-worker'
        assert result.status == 'running' and result.active_slot
        assert result.result == {'owner': 'new'}
        assert result.usage == {}


@pytest.mark.asyncio
async def test_duplicate_terminal_delivery_is_noop(worker_db, monkeypatch):
    sessions, _ = worker_db
    identity = queued(sessions)
    with sessions() as session:
        session.execute(update(Run).where(Run.id == identity).values(
            status='complete', active_slot=None, result={'owner': 'finished'}))
        session.commit()

    async def forbidden(*args, **kwargs):
        raise AssertionError('Terminal runs cannot be investigated twice')

    monkeypatch.setattr(investigation, 'investigate', forbidden)
    await jobs._execute_owned(identity)
    with sessions() as session:
        assert session.get(Run, identity).result == {'owner': 'finished'}
