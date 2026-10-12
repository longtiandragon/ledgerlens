"""Adversarial policy API checks starting from the real import workflow.

The financial fixture is hand-authored: a daily-resale order classified as
inventory under four explicitly confirmed conditions. No production function
generates an expected answer. Corruption tests change isolated test storage,
never the user's ledger, and expect a controlled refusal without new results.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app import db
from app.db import (Base, Book, Document, ImportBatch, Record, Snapshot, PolicyObservationVersion,
                    PolicyCheckRun, session)
from app.main import app as application
from app.policy_api import router


REAL_RESOURCE_PATHS = {
    '/api/v1/books', '/api/v1/books/{book_id}', '/api/v1/imports', '/api/v1/imports/{identity}/confirm',
    '/api/v1/books/{book_id}/records', '/api/v1/snapshots', '/api/v1/snapshots/{identity}',
}
ORDER = {'source_id': 'human-authored-order', 'kind': 'order', 'business_date': '2026-09-10',
         'period': '2026-09', 'amount': '250.00', 'description': 'Daily resale purchase, independently authored'}


def upload_and_confirm(client, book_id, rows, *, filename='source.json'):
    preview = client.post('/api/v1/imports', data={'book_id': book_id},
        files={'file': (filename, json.dumps(rows).encode('utf-8'), 'application/json')})
    assert preview.status_code == 200, preview.text
    assert not preview.json()['errors']
    confirmed = client.post(f'/api/v1/imports/{preview.json()["id"]}/confirm')
    assert confirmed.status_code == 200, confirmed.text
    return preview.json()['id'], confirmed.json()['snapshot_id']


@pytest.fixture
def environment(tmp_path, monkeypatch):
    # Reuse actual book/upload/confirm endpoint objects, while excluding the
    # application's startup recovery and its live database/session factory.
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    for route in application.routes:
        if isinstance(route, APIRoute) and route.path in REAL_RESOURCE_PATHS:
            # Reusing APIRoute itself retains the original app as its override
            # provider. Registering the endpoint constructs an isolated route.
            app.add_api_route(route.path, route.endpoint, methods=list(route.methods),
                              status_code=route.status_code, response_model=route.response_model)
    app.include_router(router)
    monkeypatch.setattr(settings, 'data_dir', tmp_path / 'data')

    def forbid_live_session(*args, **kwargs):
        raise AssertionError('Adversarial fixture attempted a non-isolated database session')

    monkeypatch.setattr(db, 'Session', forbid_live_session)

    def isolated_session():
        with sessions() as database:
            yield database

    app.dependency_overrides[session] = isolated_session
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post('/api/v1/books', json={'name': '独立政策边界测试', 'framework': 'CAS',
                                                    'period': '2026-09'})
        assert response.status_code == 201, response.text
        book_id = response.json()['id']
        import_id, snapshot_id = upload_and_confirm(client, book_id, [ORDER])
        with sessions() as database:
            assert database.get(Book, book_id) is not None
            assert database.get(Snapshot, snapshot_id) is not None
        records = client.get(f'/api/v1/books/{book_id}/records').json()
        assert len(records) == 1
        record = records[0]
        yield {'client': client, 'sessions': sessions, 'book_id': book_id, 'snapshot_id': snapshot_id,
               'record': record, 'import_id': import_id}
    engine.dispose()


def observation_body(environment, **changes):
    reference = environment['record']['evidence_id']
    return {'snapshot_id': environment['snapshot_id'], 'logical_observation_key': 'resale-order-conditions',
            'human_confirmation': True, 'topic': 'resale_inventory',
            'conditions': {name: {'value': 'yes', 'confirmation': 'human_confirmed', 'evidence_ids': [reference]}
                for name in ('ordinary_course', 'held_for_sale', 'economic_benefits_probable',
                             'cost_reliably_measurable')},
            'reported_treatment': 'inventory', 'treatment_evidence_ids': [reference], **changes}


def append(environment, **changes):
    return environment['client'].post(f'/api/v1/books/{environment["book_id"]}/policy-observations',
                                      json=observation_body(environment, **changes))


def create_and_check(environment):
    observation = append(environment)
    assert observation.status_code == 201, observation.text
    check = environment['client'].post(
        f'/api/v1/books/{environment["book_id"]}/policy-observations/{observation.json()["id"]}/check')
    assert check.status_code == 200, check.text
    assert check.json()['result']['status'] == 'normal'
    assert check.json()['result']['expected_treatment'] == 'inventory'
    return observation.json(), check.json()


def count_rows(environment, model):
    with environment['sessions']() as database:
        return database.scalar(select(func.count()).select_from(model))


def test_actual_api_import_creates_source_reproducible_check_without_ledger_changes(environment):
    client, book_id = environment['client'], environment['book_id']
    with environment['sessions']() as database:
        original_record = deepcopy(database.get(Record, environment['record']['id']).payload)
        original_snapshot = deepcopy(database.get(Snapshot, environment['snapshot_id']).policy)
        original_revision = database.get(Book, book_id).revision
    observation, check = create_and_check(environment)
    locator = check['binding']['evidence_locators'][0]
    assert locator['confirmed_import_ids'] == [environment['import_id']]
    assert locator['locator'] == {'row': 1}
    assert locator['evidence_id'] == environment['record']['evidence_id']
    assert locator['record_id'] == environment['record']['id']
    assert check['binding']['observation_digest'] == observation['digest']
    assert check['ledger_writes'] == 0
    assert client.get(f'/api/v1/books/{book_id}/records').json() == [environment['record']]
    with environment['sessions']() as database:
        assert database.get(Record, environment['record']['id']).payload == original_record
        assert database.get(Snapshot, environment['snapshot_id']).policy == original_snapshot
        assert database.get(Book, book_id).revision == original_revision


@pytest.mark.parametrize('membership', [[[]], [{}], [123], [None], ['not-a-record']])
def test_malformed_snapshot_membership_is_409_never_500(environment, membership):
    with environment['sessions']() as database:
        database.get(Snapshot, environment['snapshot_id']).record_ids = membership
        database.commit()
    response = append(environment)
    assert response.status_code == 409, response.text
    assert count_rows(environment, PolicyObservationVersion) == 0
    assert count_rows(environment, PolicyCheckRun) == 0


@pytest.mark.parametrize('evidence_id', ['', 'e' * 201])
def test_corrupt_source_evidence_identity_is_409_never_500(environment, evidence_id):
    with environment['sessions']() as database:
        record = database.get(Record, environment['record']['id'])
        record.evidence_id = evidence_id
        record.payload = {**record.payload, 'evidence_id': evidence_id}
        database.commit()
    response = append(environment)
    assert response.status_code == 409, response.text
    assert count_rows(environment, PolicyObservationVersion) == 0


@pytest.mark.parametrize('field,value', [
    ('version', 72), ('comment', 'edited after confirmation'), ('created_at', '2026-01-01T00:00:00+00:00'),
    ('parent_version_id', 'invented-parent'),
])
def test_edited_existing_annotation_cannot_be_promoted_as_parent(environment, field, value):
    first = append(environment)
    assert first.status_code == 201, first.text
    with environment['sessions']() as database:
        setattr(database.get(PolicyObservationVersion, first.json()['id']), field, value)
        database.commit()
    response = append(environment, parent_version_id=first.json()['id'], reported_treatment='fixed_asset')
    assert response.status_code == 409, response.text
    assert count_rows(environment, PolicyObservationVersion) == 1
    assert count_rows(environment, PolicyCheckRun) == 0


@pytest.mark.parametrize('corruption', ['preview_only', 'mapping_conflict', 'wrong_locator', 'missing_file'])
def test_confirmed_provenance_must_reproduce_source_row_on_every_check(environment, corruption):
    observation, _ = create_and_check(environment)
    with environment['sessions']() as database:
        record = database.get(Record, environment['record']['id'])
        if corruption == 'preview_only':
            database.get(ImportBatch, environment['import_id']).status = 'preview'
        elif corruption == 'mapping_conflict':
            batch = database.get(ImportBatch, environment['import_id'])
            batch.mapping = {**batch.mapping, 'source_id': 'description'}
        elif corruption == 'wrong_locator':
            record.locator = {'row': 2}
        else:
            document = database.get(Document, record.document_id)
            document.storage_path = str(Path(document.storage_path).with_name('nonexistent-source'))
        database.commit()
    response = environment['client'].post(
        f'/api/v1/books/{environment["book_id"]}/policy-observations/{observation["id"]}/check')
    assert response.status_code == 409, response.text
    assert count_rows(environment, PolicyCheckRun) == 1


def test_a_document_from_another_book_cannot_satisfy_an_existing_source_row(environment):
    client = environment['client']
    other_book = client.post('/api/v1/books', json={'name': '另一本账', 'framework': 'CAS',
                                                 'period': '2026-09'}).json()['id']
    foreign_import, _ = upload_and_confirm(client, other_book, [ORDER])
    with environment['sessions']() as database:
        record = database.get(Record, environment['record']['id'])
        record.document_id = database.get(ImportBatch, foreign_import).document_id
        database.commit()
    assert append(environment).status_code == 409
    assert count_rows(environment, PolicyObservationVersion) == 0


def test_later_known_evidence_is_rejected_even_when_record_is_in_snapshot(environment):
    with environment['sessions']() as database:
        snapshot = database.get(Snapshot, environment['snapshot_id'])
        record = database.get(Record, environment['record']['id'])
        future = datetime.fromisoformat(snapshot.created_at) + timedelta(seconds=1)
        record.payload = {**record.payload, 'observed_at': future.isoformat()}
        database.commit()
    assert append(environment).status_code == 409
    assert count_rows(environment, PolicyObservationVersion) == 0


@pytest.mark.parametrize('attempt', [
    {'as_of': '2030-01-01T00:00:00Z'}, {'policy_rule_identity': {'version': 'custom'}},
    {'source_cutoff': '2030-01-01T00:00:00Z'}, {'evidence': [{'evidence_id': 'invented'}]},
    {'created_at': '2000-01-01T00:00:00Z'}, {'version': 50}, {'observation_id': 'invented'},
])
def test_clients_cannot_supply_runtime_version_or_context(environment, attempt):
    assert append(environment, **attempt).status_code == 422
    assert count_rows(environment, PolicyObservationVersion) == 0


def test_new_import_requires_new_snapshot_and_keeps_historical_check_frozen(environment):
    observation, check = create_and_check(environment)
    _, new_snapshot = upload_and_confirm(environment['client'], environment['book_id'],
        [{**ORDER, 'source_id': 'new-source-order', 'amount': '17.00'}])
    assert new_snapshot != environment['snapshot_id']
    client, book_id = environment['client'], environment['book_id']
    assert client.post(f'/api/v1/books/{book_id}/policy-observations/{observation["id"]}/check').status_code == 409
    export = client.get(f'/api/v1/books/{book_id}/policy-checks/{check["id"]}/export')
    assert export.status_code == 200, export.text
    assert export.json()['stale'] is True
    assert export.json()['binding'] == check['binding']
    assert export.json()['result'] == check['result']
    assert len(client.get(f'/api/v1/books/{book_id}/records').json()) == 2


def test_late_record_cannot_be_referenced_as_part_of_old_selected_snapshot(environment):
    # A second period-independent import makes the old snapshot stale. The API
    # refuses it before accepting any new observation or fabricated membership.
    _, _ = upload_and_confirm(environment['client'], environment['book_id'],
        [{**ORDER, 'source_id': 'later-order', 'amount': '33.00'}])
    records = environment['client'].get(f'/api/v1/books/{environment["book_id"]}/records').json()
    late = next(row for row in records if row['source_id'] == 'later-order')
    response = append(environment, treatment_evidence_ids=[late['evidence_id']])
    assert response.status_code == 409
    assert count_rows(environment, PolicyObservationVersion) == 0


def test_foreign_evidence_on_current_snapshot_is_422_not_500(environment):
    client = environment['client']
    other_book = client.post('/api/v1/books', json={'name': '异账套', 'framework': 'SCAS',
                                                 'period': '2026-09'}).json()['id']
    upload_and_confirm(client, other_book, [{**ORDER, 'source_id': 'foreign-order'}])
    foreign = client.get(f'/api/v1/books/{other_book}/records').json()[0]
    assert append(environment, treatment_evidence_ids=[foreign['evidence_id']]).status_code == 422
    assert count_rows(environment, PolicyObservationVersion) == 0


def test_old_pack_results_are_readable_but_never_relabelled_as_current(environment, monkeypatch):
    from app import policy_consistency
    observation, saved = create_and_check(environment)
    client, book_id = environment['client'], environment['book_id']
    monkeypatch.setattr(policy_consistency, '_SOURCE_SHA256', 'f' * 64)
    historical = client.get(f'/api/v1/books/{book_id}/policy-checks/{saved["id"]}')
    assert historical.status_code == 200, historical.text
    assert historical.json()['stale'] and not historical.json()['pack_compatible']
    assert historical.json()['binding'] == saved['binding']
    assert historical.json()['result'] == saved['result']
    updated = client.post(f'/api/v1/books/{book_id}/policy-observations/{observation["id"]}/check')
    assert updated.status_code == 200, updated.text
    assert updated.json()['id'] != saved['id']
    assert updated.json()['result']['policy_rule_identity']['source_sha256'] == 'f' * 64
    assert count_rows(environment, PolicyCheckRun) == 2


def test_corrupted_historical_result_cannot_be_hidden_by_switching_pack(environment, monkeypatch):
    from app import policy_consistency
    observation, saved = create_and_check(environment)
    with environment['sessions']() as database:
        row = database.get(PolicyCheckRun, saved['id'])
        row.result = {**row.result, 'expected_treatment': 'fixed_asset'}
        database.commit()
    monkeypatch.setattr(policy_consistency, '_SOURCE_SHA256', 'f' * 64)
    response = environment['client'].post(
        f'/api/v1/books/{environment["book_id"]}/policy-observations/{observation["id"]}/check')
    assert response.status_code == 409, response.text
    assert count_rows(environment, PolicyCheckRun) == 1


def test_stored_condition_mutation_after_check_rejects_all_saved_check_views(environment):
    observation, check = create_and_check(environment)
    with environment['sessions']() as database:
        row = database.get(PolicyObservationVersion, observation['id'])
        payload = deepcopy(row.payload)
        payload['conditions']['held_for_sale']['value'] = 'no'
        row.payload = payload
        database.commit()
    client, book_id = environment['client'], environment['book_id']
    for suffix in ['', '/export']:
        assert client.get(f'/api/v1/books/{book_id}/policy-checks/{check["id"]}{suffix}').status_code == 409
    assert client.get(f'/api/v1/books/{book_id}/policy-observations/{observation["id"]}').status_code == 409
    assert client.post(f'/api/v1/books/{book_id}/policy-observations/{observation["id"]}/check').status_code == 409
    assert count_rows(environment, PolicyCheckRun) == 1


def test_nonexistent_and_crossbook_resources_remain_404(environment):
    observation, check = create_and_check(environment)
    client, book_id = environment['client'], environment['book_id']
    other_book = client.post('/api/v1/books', json={'name': 'another book', 'framework': 'CAS',
                                                 'period': '2026-09'}).json()['id']
    for path in [f'/api/v1/books/{book_id}/policy-observations/not-found',
                 f'/api/v1/books/{other_book}/policy-observations/{observation["id"]}',
                 f'/api/v1/books/{other_book}/policy-checks/{check["id"]}',
                 f'/api/v1/books/{other_book}/policy-checks/{check["id"]}/export']:
        assert client.get(path).status_code == 404
    assert client.post(f'/api/v1/books/{other_book}/policy-observations/{observation["id"]}/check').status_code == 404
