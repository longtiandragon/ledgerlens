"""Isolated basic storage checks with an independently authored policy result."""
from copy import deepcopy

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db import (Base, Book, Case, CheckRun, Document, InvestigationPolicyContext,
                    PolicyCheckRun, PolicyObservationVersion, Record, Run, Snapshot)
from app.investigation_policy import capture_policy_context, load_policy_context, read_policy_context
from app.policy_context_contract import validate_policy_context
from policy_context_fixtures import frozen_payload
from test_policy_api import seed


@pytest.fixture
def storage(tmp_path):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as database:
        book, snapshot, evidence = seed(database, tmp_path, 'context-book', 'context-snapshot')
        snap = database.get(Snapshot, snapshot)
        record = database.get(Record, book + '-record')
        document = database.get(Document, record.document_id)
        locator = {'evidence_id': evidence, 'record_id': record.id, 'source_id': record.source_id,
            'document_id': document.id, 'document_name': document.name, 'document_sha256': document.sha256,
            'synthetic': True, 'locator': record.locator, 'record_fingerprint': record.fingerprint,
            'source_known_at': record.payload['observed_at'], 'confirmed_import_ids': [book + '-import']}
        payload = frozen_payload(book_id=book, snapshot_id=snapshot, snapshot_digest=snap.digest,
                                 evidence_id=evidence, locator=locator)
        check = payload['checks'][0]
        annotation = check['binding']['annotation_envelope']
        database.add(PolicyObservationVersion(**annotation, digest=check['observation_digest']))
        database.add(PolicyCheckRun(**check, book_id=book, snapshot_id=snapshot))
        database.add(CheckRun(id='context-core-check', book_id=book, snapshot_id=snapshot))
        database.add(Case(id='context-case', book_id=book, snapshot_id=snapshot,
                          check_run_id='context-core-check', finding={}))
        run = Run(id='context-run', case_id='context-case', mode='single', status='queued',
                  created_at='2026-10-05T00:00:00+00:00')
        database.add(run)
        database.commit()
        yield database, run, snap, record
    engine.dispose()


def test_pure_handwritten_contract_preserves_supported_literal_and_input():
    original = frozen_payload()
    before = deepcopy(original)
    checked = validate_policy_context(original, book_id='context-book', snapshot_id='context-snapshot',
                                      snapshot_digest='e' * 64, evidence_ids={'context-evidence'})
    assert checked == before and checked is not original
    assert checked['checks'][0]['result']['expected_treatment'] == 'inventory'
    assert checked['checks'][0]['result']['status'] == 'supported'
    assert original == before


def test_capture_and_repeat_leave_original_ledger_unchanged(storage):
    database, run, snap, record = storage
    original = deepcopy(record.payload)
    original_snapshot = snap.digest
    row = capture_policy_context(database, run, snap)
    assert row.payload['checks'][0]['result']['status'] == 'supported'
    assert row.payload['checks'][0]['binding']['source_cutoff'] == snap.created_at
    assert row.payload['captured_at'] != snap.created_at
    assert capture_policy_context(database, run, snap).id == row.id
    assert database.scalar(select(func.count()).select_from(InvestigationPolicyContext)) == 1
    assert load_policy_context(database, run, snap) == row.payload
    response = read_policy_context(database, run, snap)
    assert response['status'] == 'captured' and response['stale'] is False
    assert record.payload == original and snap.digest == original_snapshot


def test_capture_is_rolled_back_with_callers_transaction(storage):
    database, run, snap, _ = storage
    capture_policy_context(database, run, snap)
    assert database.scalar(select(func.count()).select_from(InvestigationPolicyContext)) == 1
    database.rollback()
    assert database.scalar(select(func.count()).select_from(InvestigationPolicyContext)) == 0
    assert load_policy_context(database, run, snap) is None


def test_uncaptured_legacy_does_not_dynamically_backfill(storage):
    database, run, snap, _ = storage
    assert load_policy_context(database, run, snap) is None
    assert read_policy_context(database, run, snap) == {
        'status': 'legacy_uncaptured', 'payload': None, 'digest': None, 'stale': False,
        'stale_reasons': [], 'notice': '旧调查没有冻结政策检查上下文；不会以当前检查回填历史。'}
    assert database.scalar(select(func.count()).select_from(InvestigationPolicyContext)) == 0


@pytest.mark.parametrize('field', ['payload', 'digest', 'created_at', 'book_id', 'snapshot_id', 'run_id'])
def test_saved_envelope_tamper_is_rejected(storage, field):
    database, run, snap, _ = storage
    row = capture_policy_context(database, run, snap)
    database.commit()
    if field == 'payload':
        row.payload = {**row.payload, 'notice': 'changed'}
    else:
        setattr(row, field, 'changed')
    database.flush()
    with pytest.raises(HTTPException) as caught:
        load_policy_context(database, run, snap)
    assert caught.value.status_code == 409


@pytest.mark.parametrize('version', [True, 1.0])
def test_manifest_version_never_coerces_boolean_or_float(version):
    payload = frozen_payload()
    payload['observation_manifest'][0]['version'] = version
    with pytest.raises(ValueError):
        validate_policy_context(payload, book_id=payload['book_id'])


def test_historical_snapshot_capture_and_stale_read_do_not_replace_scope(storage):
    database, run, snap, _ = storage
    book = database.get(Book, snap.book_id)
    book.revision += 1
    database.commit()
    row = capture_policy_context(database, run, snap)
    assert '历史账簿快照' in row.payload['notice']
    response = read_policy_context(database, run, snap)
    assert response['stale'] is True
    assert 'book_revision_changed' in response['stale_reasons']
    assert load_policy_context(database, run, snap)['snapshot_digest'] == snap.digest
