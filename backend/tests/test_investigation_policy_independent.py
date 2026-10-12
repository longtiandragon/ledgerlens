"""Independent adversarial tests for investigation policy capture and transport.

All application endpoints are newly registered on an isolated FastAPI instance.
The source order and unbalanced voucher are hand-authored. The sole accounting
oracle is the explicit daily-resale inventory expectation, never a production
calculation. Worker fakes measure transport only and never call a paid model.
"""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db, jobs, policy_runtime
from app.config import settings
from app.db import (Audit, Base, Book, Case, Document, InvestigationPolicyContext, PolicyCheckRun,
                    PolicyObservationVersion, Record, Run, Snapshot, session, serialize)
from app.investigation_policy import capture_policy_context, load_policy_context, read_policy_context
from app.main import app as production_application
from app.policy_api import router as policy_router
from app.policy_context_contract import validate_policy_context


PATHS = {
    '/api/v1/books', '/api/v1/books/{book_id}/records', '/api/v1/imports',
    '/api/v1/imports/{identity}/confirm', '/api/v1/check-runs',
    '/api/v1/cases/{identity}/investigate', '/api/v1/runs/{identity}',
    '/api/v1/runs/{identity}/policy-context', '/api/v1/exports/cases/{identity}',
}
ORDER = {'source_id': 'independent-resale-order', 'kind': 'order', 'business_date': '2026-09-10',
         'period': '2026-09', 'amount': '250.00', 'description': 'Daily resale order, hand-authored'}
JOURNAL = {'source_id': 'independent-unbalanced-journal', 'kind': 'journal',
           'business_date': '2026-09-10', 'period': '2026-09', 'voucher_id': 'HAND-001',
           'account': '1405', 'debit': '250.00', 'credit': '0.00',
           'description': 'Hand-authored single debit; test transport, not an adjustment oracle'}


def canonical(value):
    """Independent integrity encoding; this is not an accounting oracle."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def upload(client, book_id, rows, filename='hand-source.json'):
    result = client.post('/api/v1/imports', data={'book_id': book_id},
                         files={'file': (filename, json.dumps(rows).encode(), 'application/json')})
    assert result.status_code == 200, result.text
    assert not result.json()['errors'], result.text
    confirmed = client.post(f'/api/v1/imports/{result.json()["id"]}/confirm')
    assert confirmed.status_code == 200, confirmed.text
    return result.json()['id'], confirmed.json()['snapshot_id']


@pytest.fixture
def environment(tmp_path, monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    application = FastAPI()
    # Copy endpoint functions, NOT production APIRoute objects or their override
    # provider. This fixture must never invoke production startup/lifespan.
    for route in production_application.routes:
        if isinstance(route, APIRoute) and route.path in PATHS:
            application.add_api_route(route.path, route.endpoint, methods=list(route.methods),
                                      status_code=route.status_code, response_model=route.response_model)
    application.include_router(policy_router)
    monkeypatch.setattr(settings, 'data_dir', tmp_path / 'data')

    def forbidden_session(*args, **kwargs):
        raise AssertionError('Independent policy tests attempted a non-isolated session')

    from app import main
    monkeypatch.setattr(db, 'Session', forbidden_session)
    monkeypatch.setattr(main, 'Session', forbidden_session)
    monkeypatch.setattr(jobs, 'Session', forbidden_session)
    dispatched = []
    monkeypatch.setattr(jobs, 'dispatch', lambda run_id, background: dispatched.append(run_id))

    def isolated_session():
        with sessions() as database:
            yield database

    application.dependency_overrides[session] = isolated_session
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.post('/api/v1/books', json={'name': 'Isolated investigation policy test',
                                                    'framework': 'CAS', 'period': '2026-09'})
        assert response.status_code == 201, response.text
        book_id = response.json()['id']
        import_id, snapshot_id = upload(client, book_id, [ORDER, JOURNAL])
        response = client.post('/api/v1/check-runs', json={'book_id': book_id, 'snapshot_id': snapshot_id})
        assert response.status_code == 200, response.text
        assert response.json()['cases'], 'Hand-written debit must produce a transport case'
        case_id = response.json()['cases'][0]['id']
        records = client.get(f'/api/v1/books/{book_id}/records').json()
        order = next(row for row in records if row['source_id'] == ORDER['source_id'])
        with sessions() as database:
            assert database.get(Book, book_id) is not None
            assert database.get(Case, case_id).snapshot_id == snapshot_id
            assert database.get(Snapshot, snapshot_id).book_id == book_id
            assert database.scalar(select(func.count()).select_from(Record)) == 2
        yield {'client': client, 'sessions': sessions, 'book_id': book_id, 'snapshot_id': snapshot_id,
               'case_id': case_id, 'order': order, 'import_id': import_id, 'dispatch': dispatched}
    engine.dispose()


def append(environment, **changes):
    reference = environment['order']['evidence_id']
    body = {'snapshot_id': environment['snapshot_id'], 'logical_observation_key': 'resale-conditions',
            'human_confirmation': True, 'topic': 'resale_inventory',
            'conditions': {name: {'value': 'yes', 'confirmation': 'human_confirmed', 'evidence_ids': [reference]}
                           for name in ('ordinary_course', 'held_for_sale', 'economic_benefits_probable',
                                        'cost_reliably_measurable')},
            'reported_treatment': 'inventory', 'treatment_evidence_ids': [reference], **changes}
    response = environment['client'].post(f'/api/v1/books/{environment["book_id"]}/policy-observations',
                                          json=body)
    assert response.status_code == 201, response.text
    return response.json()


def checked(environment, observation=None):
    observation = observation or append(environment)
    response = environment['client'].post(
        f'/api/v1/books/{environment["book_id"]}/policy-observations/{observation["id"]}/check')
    assert response.status_code == 200, response.text
    result = response.json()
    # Literal expectation from the public daily-resale inventory answer.
    assert result['result']['status'] == 'normal'
    assert result['result']['expected_treatment'] == 'inventory'
    return observation, result


def start(environment):
    return environment['client'].post(f'/api/v1/cases/{environment["case_id"]}/investigate',
                                      json={'mode': 'rules'})


def captured(environment):
    response = start(environment)
    assert response.status_code == 200, response.text
    identity = response.json()['id']
    context = environment['client'].get(f'/api/v1/runs/{identity}/policy-context')
    assert context.status_code == 200, context.text
    return identity, context.json()


def count(environment, model):
    with environment['sessions']() as database:
        return database.scalar(select(func.count()).select_from(model))


def ledger_state(environment):
    with environment['sessions']() as database:
        return {model.__tablename__: [serialize(row) for row in database.scalars(select(model).order_by(model.id))]
                for model in (Book, Document, Record, Snapshot)}


def scoped_run(environment, database, *, identity=None):
    run = Run(id=identity or db.uid(), case_id=environment['case_id'], mode='rules')
    database.add(run)
    database.flush()
    return run, database.get(Snapshot, environment['snapshot_id'])


def test_real_start_atomically_captures_independent_answer_and_export_locators(environment):
    before = ledger_state(environment)
    observation, check = checked(environment)
    run_id, response = captured(environment)
    assert response['status'] == 'captured' and not response['stale']
    payload = response['payload']
    assert payload['run_id'] == run_id
    assert payload['snapshot_id'] == environment['snapshot_id']
    assert payload['book_id'] == environment['book_id']
    assert payload['observation_manifest'] == [{'id': observation['id'], 'digest': observation['digest'],
                                               'logical_key': 'resale-conditions', 'version': 1}]
    assert len(payload['checks']) == 1
    saved = payload['checks'][0]
    assert set(saved) == {'id', 'created_at', 'observation_id', 'pack_key', 'observation_digest',
                          'artifact_digest', 'binding', 'result'}
    assert saved['id'] == check['id']
    assert saved['result']['expected_treatment'] == 'inventory'
    assert saved['result']['status'] == 'normal'
    with environment['sessions']() as database:
        snap = database.get(Snapshot, environment['snapshot_id'])
        assert saved['binding']['source_cutoff'] == snap.created_at
        assert datetime.fromisoformat(payload['captured_at']) >= datetime.fromisoformat(check['created_at'])
        assert database.get(Run, run_id) is not None
        audit = database.scalars(select(Audit).where(Audit.action == 'investigation.policy_context.captured')).one()
        assert audit.details == {'run_id': run_id, 'snapshot_id': snap.id, 'context_digest': response['digest']}
    export = environment['client'].get(f'/api/v1/exports/cases/{environment["case_id"]}?format=json')
    assert export.status_code == 200, export.text
    report = export.json()
    assert report['investigation_policy_contexts'][run_id] == response
    assert environment['order']['evidence_id'] in {row['id'] for row in report['evidence']}
    assert environment['dispatch'] == [run_id]
    assert ledger_state(environment) == before


def test_latest_unchecked_annotation_is_manifested_without_old_check_fallback(environment):
    first, old = checked(environment)
    newest = append(environment, parent_version_id=first['id'], comment='Latest version awaiting a check')
    _, result = captured(environment)
    assert result['payload']['checks'] == []
    assert result['payload']['observation_manifest'] == [
        {'id': newest['id'], 'digest': newest['digest'], 'logical_key': 'resale-conditions', 'version': 2}]
    assert old['id'] not in canonical(result['payload']).decode()
    assert '1' in result['notice'] and '旧版' in result['notice']


def test_capture_is_idempotent_and_later_annotation_is_separate_staleness(environment):
    observation, _ = checked(environment)
    run_id, original = captured(environment)
    newest = append(environment, parent_version_id=observation['id'], comment='Later confirmed version')
    checked(environment, newest)
    with environment['sessions']() as database:
        run = database.get(Run, run_id)
        snap = database.get(Snapshot, environment['snapshot_id'])
        loaded = load_policy_context(database, run, snap)
        assert loaded == original['payload']
        loaded['notice'] = 'Mutating caller copy cannot change saved context'
        again = capture_policy_context(database, run, snap)
        assert again.payload == original['payload']
        assert again.digest == original['digest']
        database.commit()
    response = environment['client'].get(f'/api/v1/runs/{run_id}/policy-context')
    assert response.status_code == 200, response.text
    assert response.json()['payload'] == original['payload']
    assert response.json()['digest'] == original['digest']
    assert response.json()['stale']
    assert 'policy_observations_changed' in response.json()['stale_reasons']
    assert count(environment, InvestigationPolicyContext) == 1
    assert count(environment, PolicyCheckRun) == 2


def test_later_check_for_previously_unchecked_manifest_marks_stale_without_backfill(environment):
    observation = append(environment)
    run_id, original = captured(environment)
    assert original['payload']['checks'] == []
    checked(environment, observation)
    result = environment['client'].get(f'/api/v1/runs/{run_id}/policy-context').json()
    assert result['payload'] == original['payload']
    assert result['stale_reasons'] == ['policy_checks_changed']


def test_new_ledger_source_marks_history_stale_and_excludes_future_records(environment):
    checked(environment)
    run_id, original = captured(environment)
    _, future_snapshot = upload(environment['client'], environment['book_id'],
                               [{**ORDER, 'source_id': 'later-order', 'amount': '75.00'}], 'later.json')
    assert future_snapshot != environment['snapshot_id']
    result = environment['client'].get(f'/api/v1/runs/{run_id}/policy-context')
    assert result.status_code == 200, result.text
    assert result.json()['payload'] == original['payload']
    assert 'book_revision_changed' in result.json()['stale_reasons']
    export = environment['client'].get(f'/api/v1/exports/cases/{environment["case_id"]}?format=json')
    assert export.status_code == 200, export.text
    assert 'later-order' not in {row['source_id'] for row in export.json()['evidence']}


def test_historical_snapshot_capture_remains_explicit_and_bound(environment):
    checked(environment)
    upload(environment['client'], environment['book_id'], [{**ORDER, 'source_id': 'future-record'}], 'future.json')
    _, result = captured(environment)
    assert result['payload']['snapshot_id'] == environment['snapshot_id']
    assert 'book_revision_changed' in result['stale_reasons']
    assert '历史' in result['notice']
    assert len(result['payload']['checks']) == 1


def test_rollback_removes_run_and_context_capture_does_not_commit(environment):
    checked(environment)
    with environment['sessions']() as database:
        run, snap = scoped_run(environment, database)
        identity = run.id
        captured_row = capture_policy_context(database, run, snap)
        context_id = captured_row.id
        database.rollback()
    with environment['sessions']() as database:
        assert database.get(Run, identity) is None
        assert database.get(InvestigationPolicyContext, context_id) is None
    assert count(environment, PolicyCheckRun) == 1


def test_legacy_run_remains_explicitly_uncaptured_even_with_current_checks(environment):
    with environment['sessions']() as database:
        run, snap = scoped_run(environment, database)
        identity = run.id
        database.commit()
        assert load_policy_context(database, run, snap) is None
    checked(environment)
    result = environment['client'].get(f'/api/v1/runs/{identity}/policy-context')
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'legacy_uncaptured'
    assert result.json()['payload'] is None and result.json()['digest'] is None
    assert not result.json()['stale']
    assert count(environment, InvestigationPolicyContext) == 0


@pytest.mark.parametrize('mutation', ['raw_file', 'locator', 'check_result', 'check_binding', 'check_digest'])
def test_start_refuses_changed_source_or_saved_check_without_partial_run(environment, mutation):
    _, check = checked(environment)
    before_audits = count(environment, Audit)
    with environment['sessions']() as database:
        if mutation == 'raw_file':
            record = database.get(Record, environment['order']['id'])
            document = database.get(Document, record.document_id)
            Path(document.storage_path).write_bytes(b'Changed independent source')
        elif mutation == 'locator':
            database.get(Record, environment['order']['id']).locator = {'row': 99}
        else:
            row = database.get(PolicyCheckRun, check['id'])
            if mutation == 'check_result':
                row.result = {**row.result, 'expected_treatment': 'management_expense'}
            elif mutation == 'check_binding':
                row.binding = {**row.binding, 'source_cutoff': '2099-01-01T00:00:00+00:00'}
            else:
                row.artifact_digest = '0' * 64
        database.commit()
    response = start(environment)
    assert response.status_code == 409, response.text
    assert count(environment, Run) == 0
    assert count(environment, InvestigationPolicyContext) == 0
    assert count(environment, Audit) == before_audits
    assert environment['dispatch'] == []


def test_genuine_future_check_timestamp_is_refused_not_merely_bad_digest(environment, monkeypatch):
    observation = append(environment)
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with monkeypatch.context() as scoped_patch:
        scoped_patch.setattr(policy_runtime, 'now', lambda: future)
        _, check = checked(environment, observation)
    assert check['created_at'] == future
    # The saved check passed its own API verification; refusal is temporal.
    response = start(environment)
    assert response.status_code == 409, response.text
    assert count(environment, Run) == 0
    assert count(environment, InvestigationPolicyContext) == 0
    assert environment['dispatch'] == []


@pytest.mark.parametrize('field,value', [
    ('payload', None), ('payload', []), ('payload', 'malformed'),
    ('digest', '0' * 64), ('book_id', 'foreign-book'), ('snapshot_id', 'foreign-snapshot'),
    ('created_at', '2099-01-01T00:00:00+00:00'),
])
def test_saved_context_corruption_is_controlled_refusal(environment, field, value):
    checked(environment)
    run_id, _ = captured(environment)
    with environment['sessions']() as database:
        row = database.scalars(select(InvestigationPolicyContext)).one()
        setattr(row, field, value)
        database.commit()
    response = environment['client'].get(f'/api/v1/runs/{run_id}/policy-context')
    assert response.status_code == 409, response.text
    assert count(environment, InvestigationPolicyContext) == 1


@pytest.mark.parametrize('scope', ['book', 'snapshot'])
def test_direct_capture_rejects_foreign_case_scope(environment, scope):
    checked(environment)
    with environment['sessions']() as database:
        run, snap = scoped_run(environment, database)
        if scope == 'book':
            snap.book_id = 'another-book'
        else:
            snap.id = 'another-snapshot'
        with pytest.raises(HTTPException) as error:
            capture_policy_context(database, run, snap)
        assert error.value.status_code == 409
        database.rollback()
    assert count(environment, InvestigationPolicyContext) == 0


@pytest.mark.parametrize('scope', ['book', 'snapshot', 'snapshot_digest', 'evidence'])
def test_pure_context_validator_rejects_trusted_caller_scope_mismatch(environment, scope):
    checked(environment)
    _, response = captured(environment)
    payload = response['payload']
    arguments = {'book_id': environment['book_id'], 'snapshot_id': environment['snapshot_id'],
                 'snapshot_digest': payload['snapshot_digest'],
                 'evidence_ids': {environment['order']['evidence_id']}}
    arguments[{'book': 'book_id', 'snapshot': 'snapshot_id', 'snapshot_digest': 'snapshot_digest',
               'evidence': 'evidence_ids'}[scope]] = set() if scope == 'evidence' else 'foreign-value'
    before = deepcopy(payload)
    with pytest.raises(ValueError):
        validate_policy_context(payload, **arguments)
    assert payload == before


@pytest.mark.parametrize('mutation', [
    'duplicate_check', 'duplicate_manifest_id', 'duplicate_logical_key', 'foreign_manifest',
    'missing_manifest', 'future_capture', 'naive_time', 'invented_result_ref', 'missing_result_ref',
    'duplicate_locator', 'extra_payload_field', 'missing_nested_field', 'float_version',
])
def test_pure_context_rejects_internal_corruption_without_mutating_input(environment, mutation):
    checked(environment)
    _, response = captured(environment)
    payload = deepcopy(response['payload'])
    item = payload['checks'][0]
    if mutation == 'duplicate_check':
        payload['checks'].append(deepcopy(item))
    elif mutation == 'duplicate_manifest_id':
        payload['observation_manifest'].append(deepcopy(payload['observation_manifest'][0]))
    elif mutation == 'duplicate_logical_key':
        payload['observation_manifest'].append({**payload['observation_manifest'][0], 'id': 'other'})
    elif mutation == 'foreign_manifest':
        payload['observation_manifest'][0]['id'] = 'other-annotation'
    elif mutation == 'missing_manifest':
        payload['observation_manifest'] = []
    elif mutation == 'future_capture':
        payload['captured_at'] = '2000-01-01T00:00:00+00:00'
    elif mutation == 'naive_time':
        payload['captured_at'] = '2026-10-09T00:00:00'
    elif mutation == 'invented_result_ref':
        item['result']['evidence_ids'].append('invented-evidence')
    elif mutation == 'missing_result_ref':
        item['result']['evidence_ids'] = []
    elif mutation == 'duplicate_locator':
        item['binding']['evidence_locators'].append(deepcopy(item['binding']['evidence_locators'][0]))
    elif mutation == 'extra_payload_field':
        payload['ledger_writes'] = 1
    elif mutation == 'missing_nested_field':
        del item['binding']['observation']['beneficiary_evidence_ids']
    else:
        payload['observation_manifest'][0]['version'] = 1.0
    before = deepcopy(payload)
    with pytest.raises(ValueError):
        validate_policy_context(payload, book_id=environment['book_id'],
                                snapshot_id=environment['snapshot_id'],
                                evidence_ids={environment['order']['evidence_id']})
    assert payload == before


def test_pure_context_is_independent_of_current_rules_and_returns_deep_copy(environment, monkeypatch):
    checked(environment)
    _, response = captured(environment)
    payload = response['payload']

    def forbidden_current_state(*args, **kwargs):
        raise AssertionError('Pure validation fetched current policy rules')

    from app import policy_consistency
    monkeypatch.setattr(policy_consistency, 'policy_rule_identity', forbidden_current_state)
    original = deepcopy(payload)
    validated = validate_policy_context(payload, book_id=environment['book_id'],
                                        evidence_ids={environment['order']['evidence_id']})
    validated['checks'][0]['result']['reason'] = 'Caller changes a returned copy'
    assert payload == original


def test_context_capacity_refuses_61_checks_atomically_instead_of_truncating(environment):
    for index in range(61):
        observation = append(environment, logical_observation_key=f'independent-resale-{index:03}')
        checked(environment, observation)
    assert count(environment, PolicyCheckRun) == 61
    response = start(environment)
    assert response.status_code == 409, response.text
    assert '60' in response.text
    assert count(environment, Run) == 0
    assert count(environment, InvestigationPolicyContext) == 0
    assert environment['dispatch'] == []


def test_worker_uses_saved_context_after_later_versions_and_redelivery_is_noop(environment, monkeypatch):
    observation, _ = checked(environment)
    run_id, original = captured(environment)
    checked(environment, append(environment, parent_version_id=observation['id']))
    seen = []

    async def fake_investigate(records, policy, finding, **kwargs):
        seen.append(deepcopy(policy['investigation_policy_context']))
        assert kwargs['gateway'] is None
        assert {row['source_id'] for row in records} == {ORDER['source_id'], JOURNAL['source_id']}
        return {'status': 'normal', 'usage': {}, 'transport_test_only': True}

    class Saver:
        async def aget_tuple(self, configuration):
            return None

    @asynccontextmanager
    async def fake_saver():
        yield Saver()

    from app import investigation
    monkeypatch.setattr(jobs, 'Session', environment['sessions'])
    monkeypatch.setattr(jobs, 'saver_context', fake_saver)
    monkeypatch.setattr(investigation, 'investigate', fake_investigate)
    before = ledger_state(environment)
    asyncio.run(jobs._execute_owned(run_id))
    asyncio.run(jobs._execute_owned(run_id))
    assert seen == [original['payload']]
    with environment['sessions']() as database:
        run = database.get(Run, run_id)
        assert run.status == 'complete', run.error
        assert run.active_slot is None and run.worker_token is None
    assert ledger_state(environment) == before


def test_worker_legacy_context_stays_none_without_dynamic_capture(environment, monkeypatch):
    checked(environment)
    with environment['sessions']() as database:
        run, _ = scoped_run(environment, database)
        identity = run.id
        database.commit()
    seen = []

    async def fake_investigate(records, policy, finding, **kwargs):
        seen.append(policy['investigation_policy_context'])
        return {'status': 'insufficient', 'transport_test_only': True}

    class Saver:
        async def aget_tuple(self, configuration):
            return None

    @asynccontextmanager
    async def fake_saver():
        yield Saver()

    from app import investigation
    monkeypatch.setattr(jobs, 'Session', environment['sessions'])
    monkeypatch.setattr(jobs, 'saver_context', fake_saver)
    monkeypatch.setattr(investigation, 'investigate', fake_investigate)
    asyncio.run(jobs._execute_owned(identity))
    assert seen == [None]
    assert count(environment, InvestigationPolicyContext) == 0


def test_current_annotation_corruption_does_not_replace_intact_frozen_history(environment):
    observation, _ = checked(environment)
    run_id, original = captured(environment)
    with environment['sessions']() as database:
        row = database.get(PolicyObservationVersion, observation['id'])
        row.comment = 'Changed current annotation without updating its digest'
        database.commit()
        run = database.get(Run, run_id)
        snap = database.get(Snapshot, environment['snapshot_id'])
        assert load_policy_context(database, run, snap) == original['payload']
        response = read_policy_context(database, run, snap)
    assert response['payload'] == original['payload']
    assert response['stale_reasons'] == ['current_policy_state_invalid']


@pytest.mark.parametrize('value', [None, [], 'malformed', {'unexpected': 'incomplete'}])
def test_coherently_hashed_malformed_context_still_has_controlled_api_refusal(environment, value):
    """Hashes describe content; they cannot replace structural validation."""
    checked(environment)
    run_id, _ = captured(environment)
    with environment['sessions']() as database:
        row = database.scalars(select(InvestigationPolicyContext)).one()
        row.payload = value
        row.digest = digest({name: getattr(row, name) for name in
                             ('id', 'created_at', 'run_id', 'book_id', 'snapshot_id', 'payload')})
        database.commit()
    response = environment['client'].get(f'/api/v1/runs/{run_id}/policy-context')
    assert response.status_code == 409, response.text


def repair_check_integrity(payload):
    """Update only content hashes to reach the independent semantic guards."""
    for item in payload['checks']:
        item['binding']['result_sha256'] = digest(item['result'])
        annotation = item['binding']['annotation_envelope']
        item['observation_digest'] = digest(annotation)
        item['binding']['observation_digest'] = item['observation_digest']
        for entry in payload['observation_manifest']:
            if entry['id'] == item['observation_id']:
                entry['digest'] = item['observation_digest']
        artifact = {name: item[name] for name in ('id', 'created_at', 'observation_id', 'pack_key',
                                                'observation_digest', 'binding', 'result')}
        artifact.update(book_id=payload['book_id'], snapshot_id=payload['snapshot_id'])
        item['artifact_digest'] = digest(artifact)


@pytest.mark.parametrize('mutation', ['normal_wrong_expected', 'future_source', 'future_evidence',
                                     'fake_refs', 'duplicate_refs', 'reported_mismatch'])
def test_coherent_content_hashes_do_not_override_reference_and_temporal_guards(environment, mutation):
    checked(environment)
    _, response = captured(environment)
    payload = deepcopy(response['payload'])
    item = payload['checks'][0]
    if mutation == 'normal_wrong_expected':
        item['result']['expected_treatment'] = 'management_expense'
    elif mutation == 'future_source':
        item['result']['source']['publication_date'] = '2099-01-01'
    elif mutation == 'future_evidence':
        item['binding']['evidence_locators'][0]['source_known_at'] = '2099-01-01T00:00:00+00:00'
    elif mutation == 'fake_refs':
        item['result']['evidence_ids'] = ['fake-evidence']
    elif mutation == 'duplicate_refs':
        item['result']['evidence_ids'] *= 2
    else:
        item['result']['reported_treatment'] = 'management_expense'
    repair_check_integrity(payload)
    before = deepcopy(payload)
    with pytest.raises(ValueError):
        validate_policy_context(payload, book_id=environment['book_id'],
                                snapshot_id=environment['snapshot_id'],
                                snapshot_digest=payload['snapshot_digest'],
                                evidence_ids={environment['order']['evidence_id']})
    assert payload == before
