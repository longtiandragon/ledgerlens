"""Idempotent HTTP handlers release their write lock before dependency teardown."""
# ruff: noqa: F811 -- pytest discovers the imported fixture by its name.
import sqlite3

from fastapi import BackgroundTasks, Request
from sqlalchemy import func, select

from app.db import Audit, CheckRun, Run, Session, engine
from app.main import CheckInput, InvestigateInput, check, start_investigation
from test_api import book, client  # noqa: F401


def another_writer_can_begin():
    # The first handler's Session is deliberately STILL OPEN. Waiting for its
    # dependency cleanup can deadlock an async investigation on the event loop.
    connection = sqlite3.connect(engine.url.database, timeout=.05)
    try:
        connection.execute('BEGIN IMMEDIATE')
        connection.rollback()
    finally:
        connection.close()


def prepared(client):
    identity = book(client)
    snap = client.post(f'/api/v1/books/{identity}/demo').json()['snapshot_id']
    first = client.post('/api/v1/check-runs', json={'book_id': identity, 'snapshot_id': snap})
    assert first.status_code == 200
    return identity, snap, first.json()


def test_duplicate_check_releases_write_lock_without_new_result_or_audit(client):
    identity, snap, original = prepared(client)
    with Session() as session:
        before_audit = session.scalar(select(func.count()).select_from(Audit))
        result = check(CheckInput(book_id=identity, snapshot_id=snap), s=session)
        assert result == original
        another_writer_can_begin()
        assert session.scalar(select(func.count()).select_from(CheckRun)) == 1
        assert session.scalar(select(func.count()).select_from(Audit)) == before_audit


def test_duplicate_investigation_releases_write_lock_and_never_dispatches(client):
    _, _, checked = prepared(client)
    case_id = checked['cases'][0]['id']
    with Session() as seed:
        active = Run(case_id=case_id, mode='rules', active_slot=case_id)
        seed.add(active)
        seed.commit()
        run_id = active.id
    background = BackgroundTasks()
    request = Request({'type': 'http', 'headers': []})
    with Session() as session:
        result = start_investigation(case_id, InvestigateInput(mode='rules'), background, request, s=session)
        assert result['id'] == run_id
        another_writer_can_begin()
        assert background.tasks == []
        assert session.scalar(select(func.count()).select_from(Run)) == 1
