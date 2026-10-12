"""Stored policy checks use actual source records, not a caller-invented context."""
from copy import deepcopy
import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import (Base, Book, Document, ImportBatch, Record, Snapshot, PolicyObservationVersion,
                    PolicyCheckRun, session)
from app.domain import rule_identity
from app.policy_api import router
from app.schemas import LedgerInput


def seed(s, folder, book_id='policy-book', snapshot_id='policy-snapshot', framework='CAS'):
    book = Book(id=book_id, name='policy source fixture', framework=framework, period='2026-09',
                currency='CNY', version=1, revision=1, accounts=['1405', '1002'], locked_periods=[])
    original = LedgerInput.model_validate({
        'source_id': 'source-condition', 'kind': 'order', 'business_date': '2026-09-02',
        'period': '2026-09', 'amount': '100.00', 'description': 'Original daily resale order'
    }).model_dump(mode='json', exclude_none=True)
    raw = json.dumps([original], ensure_ascii=False).encode()
    source_file = folder / (book_id + '.json')
    source_file.write_bytes(raw)
    doc = Document(id=book_id + '-doc', book_id=book_id, name='order.json',
                   sha256=hashlib.sha256(raw).hexdigest(), storage_path=str(source_file), synthetic=True)
    fingerprint = hashlib.sha256(json.dumps(original, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    record = Record(id=book_id + '-record', book_id=book_id, document_id=doc.id,
                    source_id=original['source_id'], kind='order', evidence_id=book_id + '-evidence',
                    fingerprint=fingerprint, payload={**original, 'id': book_id + '-record',
                    'evidence_id': book_id + '-evidence', 'observed_at': '2026-10-01T00:00:00+00:00'},
                    locator={'row': 1})
    policy = {'framework': framework, 'period': '2026-09', 'execution_rules': rule_identity()}
    manifest = {'records': [(record.id, record.fingerprint)], 'policy': policy}
    snap = Snapshot(id=snapshot_id, book_id=book_id, revision=1, policy=policy,
                    record_ids=[record.id], digest=hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
                    created_at='2026-10-02T00:00:00+00:00')
    batch = ImportBatch(id=book_id + '-import', book_id=book_id, document_id=doc.id,
                        status='confirmed', rows=[{'record': original, 'locator': {'row': 1}}],
                        errors=[], columns=list(original), mapping={}, snapshot_id=snapshot_id,
                        imported=1, duplicate_count=0)
    s.add_all([book, doc, record, snap, batch])
    s.commit()
    return book.id, snap.id, record.evidence_id


@pytest.fixture
def environment(tmp_path):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)

    def isolated_session():
        with sessions() as s:
            yield s

    app.dependency_overrides[session] = isolated_session
    with sessions() as s:
        identity, snap, ref = seed(s, tmp_path)
        other = seed(s, tmp_path, 'other-book', 'other-snapshot')
    with TestClient(app) as client:
        yield client, sessions, identity, snap, ref, other
    engine.dispose()


def body(snapshot, ref, **changes):
    item = {'snapshot_id': snapshot, 'logical_observation_key': 'daily-resale', 'human_confirmation': True,
            'topic': 'resale_inventory', 'conditions': {name: {
                'value': 'yes', 'confirmation': 'human_confirmed', 'evidence_ids': [ref]}
                for name in ('ordinary_course', 'held_for_sale', 'economic_benefits_probable', 'cost_reliably_measurable')},
            'reported_treatment': 'inventory', 'treatment_evidence_ids': [ref]}
    item.update(changes)
    return item


def append(env, **changes):
    client, _, book, snap, ref, _ = env
    return client.post(f'/api/v1/books/{book}/policy-observations', json=body(snap, ref, **changes))


def check(env, observation_id):
    return env[0].post(f'/api/v1/books/{env[2]}/policy-observations/{observation_id}/check')


def test_stored_source_bound_normal_check_export_and_idempotence(environment):
    client, sessions, book, snap, ref, _ = environment
    with sessions() as s:
        original = deepcopy(s.get(Record, book + '-record').payload)
        snapshot_digest = s.get(Snapshot, snap).digest
        original_revision = s.get(Book, book).revision
    response = append(environment)
    assert response.status_code == 201, response.text
    observation = response.json()
    first = check(environment, observation['id'])
    assert first.status_code == 200, first.text
    run = first.json()
    assert run['result']['status'] == 'normal'
    assert run['result']['expected_treatment'] == 'inventory'
    assert run['binding']['ledger_snapshot_id'] == snap
    assert run['binding']['evidence_locators'][0]['evidence_id'] == ref
    assert run['binding']['evidence_locators'][0]['locator'] == {'row': 1}
    assert run['binding']['source_cutoff'] == '2026-10-02T00:00:00+00:00'
    assert run['binding']['observation_digest'] == observation['digest']
    assert check(environment, observation['id']).json()['id'] == run['id']
    exported = client.get(f'/api/v1/books/{book}/policy-checks/{run["id"]}/export')
    assert exported.status_code == 200 and exported.json()['binding'] == run['binding']
    assert 'storage_path' not in exported.text
    with sessions() as s:
        assert s.get(Record, book + '-record').payload == original
        assert s.get(Snapshot, snap).digest == snapshot_digest
        assert s.get(Book, book).revision == original_revision
        assert s.scalar(select(func.count()).select_from(PolicyCheckRun)) == 1


def test_mismatch_and_model_suggestion_never_promoted(environment):
    first = append(environment, reported_treatment='fixed_asset').json()
    assert check(environment, first['id']).json()['result']['status'] == 'supported'
    data = body(environment[3], environment[4])
    data['conditions']['held_for_sale']['confirmation'] = 'model_suggested'
    second = append(environment, parent_version_id=first['id'], conditions=data['conditions']).json()
    result = check(environment, second['id']).json()['result']
    assert result['status'] == 'insufficient'
    assert second['payload']['conditions']['held_for_sale']['value'] == 'yes'
    assert second['payload']['conditions']['held_for_sale']['confirmation'] == 'model_suggested'


@pytest.mark.parametrize('change', [
    {'human_confirmation': False}, {'human_confirmation': 1}, {'human_confirmation': 'true'},
    {'framework': 'SCAS'}, {'book_id': 'other-book'}, {'known_at': '2020-01-01'},
    {'context': {'evidence': []}}, {'conditions': {'invented': {'value': 'yes'}}},
    {'reported_treatment': 'fixed_asset', 'treatment_evidence_ids': []},
])
def test_client_cannot_supply_scope_or_skip_confirmation(environment, change):
    assert append(environment, **change).status_code == 422


def test_cross_book_snapshot_and_evidence_are_rejected(environment):
    client, _, book, _, _, other = environment
    assert append(environment, snapshot_id=other[1]).status_code == 404
    data = body(environment[3], environment[4])
    data['conditions']['held_for_sale']['evidence_ids'] = [other[2]]
    response = append(environment, conditions=data['conditions'])
    assert response.status_code == 422
    assert client.get(f'/api/v1/books/{book}/policy-observations?snapshot_id={other[1]}').status_code == 404


def test_version_chain_is_append_only_and_old_check_readable(environment):
    client, _, book, _, _, other = environment
    first = append(environment).json()
    run = check(environment, first['id']).json()
    assert append(environment).status_code == 409
    second = append(environment, parent_version_id=first['id'], reported_treatment='fixed_asset').json()
    assert second['version'] == 2 and second['parent_version_id'] == first['id']
    assert append(environment, parent_version_id=first['id']).status_code == 409
    assert check(environment, first['id']).status_code == 409
    original = client.get(f'/api/v1/books/{book}/policy-observations/{first["id"]}').json()
    assert not original['latest'] and original['stale']
    historical = client.get(f'/api/v1/books/{book}/policy-checks/{run["id"]}/export')
    assert historical.status_code == 200 and historical.json()['stale']
    assert historical.json()['result'] == run['result']
    assert client.get(f'/api/v1/books/{other[0]}/policy-checks/{run["id"]}').status_code == 404
    assert client.post(f'/api/v1/books/{other[0]}/policy-observations/{first["id"]}/check').status_code == 404


def test_new_ledger_revision_rejects_check_but_retains_historical_export(environment):
    client, sessions, book, _, _, _ = environment
    observation = append(environment).json()
    run = check(environment, observation['id']).json()
    with sessions() as s:
        s.get(Book, book).revision += 1
        s.commit()
    assert check(environment, observation['id']).status_code == 409
    assert append(environment, parent_version_id=observation['id']).status_code == 409
    saved = client.get(f'/api/v1/books/{book}/policy-checks/{run["id"]}/export').json()
    assert saved['stale'] and saved['result'] == run['result']


@pytest.mark.parametrize('tamper', ['future', 'record_id', 'evidence_id', 'book_id', 'fingerprint', 'snapshot_digest', 'missing_record', 'duplicate_membership'])
def test_corrupted_or_future_source_never_becomes_policy_context(environment, tamper):
    _, sessions, book, snap, _, _ = environment
    with sessions() as s:
        record = s.get(Record, book + '-record')
        if tamper == 'future':
            record.payload = {**record.payload, 'observed_at': '2026-10-03T00:00:00+00:00'}
        elif tamper in ('record_id', 'evidence_id', 'book_id'):
            key = 'id' if tamper == 'record_id' else tamper
            record.payload = {**record.payload, key: 'invented'}
        elif tamper == 'fingerprint':
            record.payload = {**record.payload, 'amount': '101.00'}
        elif tamper == 'snapshot_digest':
            s.get(Snapshot, snap).digest = 'changed'
        elif tamper == 'missing_record':
            s.delete(record)
        else:
            s.get(Snapshot, snap).record_ids = [record.id, record.id]
        s.commit()
    assert append(environment).status_code == 409


def test_source_file_tamper_refuses_new_check_and_saved_export_survives(environment):
    client, sessions, book, _, _, _ = environment
    observation = append(environment).json()
    run = check(environment, observation['id']).json()
    with sessions() as s:
        from pathlib import Path
        Path(s.get(Document, book + '-doc').storage_path).write_bytes(b'changed original')
    assert check(environment, observation['id']).status_code == 409
    assert client.get(f'/api/v1/books/{book}/policy-checks/{run["id"]}/export').status_code == 200


def test_pack_changes_do_not_relabel_historical_output(environment, monkeypatch):
    from app import policy_consistency
    client, _, book, _, _, _ = environment
    observation = append(environment).json()
    first = check(environment, observation['id']).json()
    monkeypatch.setattr(policy_consistency, '_SOURCE_SHA256', '1' * 64)
    historical = client.get(f'/api/v1/books/{book}/policy-checks/{first["id"]}').json()
    assert historical['result'] == first['result']
    assert not historical['pack_compatible'] and historical['stale']
    second = check(environment, observation['id']).json()
    assert second['id'] != first['id']
    assert second['binding']['policy_rule_identity']['source_sha256'] == '1' * 64


@pytest.mark.parametrize('part', ['observation', 'result', 'binding', 'pack_key', 'source_metadata'])
def test_saved_content_mutation_is_detected(environment, part):
    client, sessions, book, _, _, _ = environment
    observation = append(environment).json()
    run = check(environment, observation['id']).json()
    with sessions() as s:
        if part == 'observation':
            row = s.get(PolicyObservationVersion, observation['id'])
            row.payload = {**row.payload, 'reported_treatment': 'fixed_asset'}
        elif part == 'result':
            row = s.get(PolicyCheckRun, run['id'])
            row.result = {**row.result, 'status': 'supported'}
        elif part == 'pack_key':
            s.get(PolicyCheckRun, run['id']).pack_key = 'changed-pack'
        elif part == 'binding':
            row = s.get(PolicyCheckRun, run['id'])
            row.binding = {**row.binding, 'framework': 'SCAS'}
        else:
            row = s.get(PolicyCheckRun, run['id'])
            row.result = {**row.result, 'source': {**row.result['source'], 'source_sha256': 'changed-source'}}
        s.commit()
    assert client.get(f'/api/v1/books/{book}/policy-checks/{run["id"]}/export').status_code == 409
    assert check(environment, observation['id']).status_code == 409
    if part == 'observation':
        assert client.get(f'/api/v1/books/{book}/policy-observations/{observation["id"]}').status_code == 409
        assert client.get(f'/api/v1/books/{book}/policy-observations').status_code == 409


@pytest.mark.parametrize('field,value', [('logical_key', 'different-chain'), ('version', 9),
    ('parent_version_id', 'invented-parent'), ('created_at', '2020-01-01T00:00:00+00:00'),
    ('comment', 'changed annotation')])
def test_annotation_metadata_tamper_cannot_promote_earlier_version(environment, field, value):
    client, sessions, book, _, _, _ = environment
    first = append(environment).json()
    second = append(environment, parent_version_id=first['id']).json()
    with sessions() as s:
        setattr(s.get(PolicyObservationVersion, second['id']), field, value)
        s.commit()
    assert check(environment, first['id']).status_code == 409
    assert client.get(f'/api/v1/books/{book}/policy-observations/{first["id"]}').status_code == 409
    assert append(environment, parent_version_id=first['id']).status_code == 409


@pytest.mark.parametrize('tamper', ['wrong_row', 'wrong_document', 'orphan_batch', 'mapping'])
def test_locator_requires_original_confirmed_source_row(environment, tamper):
    _, sessions, book, _, _, other = environment
    with sessions() as s:
        record = s.get(Record, book + '-record')
        if tamper == 'wrong_row':
            record.locator = {'row': 2}
        elif tamper == 'wrong_document':
            record.document_id = other[0] + '-doc'
        elif tamper == 'orphan_batch':
            s.get(ImportBatch, book + '-import').status = 'preview'
        else:
            s.get(ImportBatch, book + '-import').mapping = {'description': 'source_id'}
        s.commit()
    assert append(environment).status_code == 409


def test_catalog_has_separate_pack_identity_and_no_truth_access(environment):
    result = environment[0].get('/api/v1/policy-checks/catalog').json()
    assert len(result['rules']) == 20
    assert result['policy_rule_identity']['version'] == 'policy-consistency-v4'
    assert 'answer' not in result and 'sql' not in result


@pytest.mark.parametrize('membership', [[['nested']], [{'id': 'nested'}], [1], [''], [None]])
def test_malformed_snapshot_membership_is_409_not_server_error(environment, membership):
    with environment[1]() as s:
        s.get(Snapshot, environment[3]).record_ids = membership
        s.commit()
    assert append(environment).status_code == 409


def test_check_history_list_is_scoped_and_bounded(environment):
    client, _, book, snap, _, other = environment
    observation = append(environment).json()
    run = check(environment, observation['id']).json()
    path = f'/api/v1/books/{book}/policy-checks'
    assert client.get(path, params={'observation_id': observation['id'], 'snapshot_id': snap}).json()[0]['id'] == run['id']
    assert client.get(path, params={'offset': 1}).json() == []
    assert client.get(path, params={'snapshot_id': other[1]}).status_code == 404
    assert client.get(f'/api/v1/books/{other[0]}/policy-checks', params={'observation_id': observation['id']}).status_code == 404
    assert client.get(path, params={'limit': 201}).status_code == 422
    assert client.get(path, params={'offset': -1}).status_code == 422
    assert client.get(f'/api/v1/books/{book}/policy-observations', params={'limit': 0}).status_code == 422


@pytest.mark.parametrize('extension', ['csv', 'xlsx'])
def test_confirmed_table_sources_reproduce_original_cell_locator(environment, extension):
    import csv
    from io import StringIO, BytesIO
    from pathlib import Path
    _, sessions, book, _, _, _ = environment
    with sessions() as s:
        record = s.get(Record, book + '-record')
        original = {key: value for key, value in record.payload.items() if key in LedgerInput.model_fields}
        if extension == 'csv':
            output = StringIO()
            writer = csv.DictWriter(output, fieldnames=list(original))
            writer.writeheader()
            writer.writerow(original)
            raw = output.getvalue().encode('utf-8')
            locator = {'row': 2}
        else:
            from openpyxl import Workbook
            workbook = Workbook()
            workbook.active.title = 'Source'
            workbook.active.append(list(original))
            workbook.active.append(list(original.values()))
            output = BytesIO()
            workbook.save(output)
            workbook.close()
            raw = output.getvalue()
            locator = {'sheet': 'Source', 'row': 2}
        doc = s.get(Document, book + '-doc')
        Path(doc.storage_path).write_bytes(raw)
        doc.sha256, doc.name = hashlib.sha256(raw).hexdigest(), 'order.' + extension
        record.locator = locator
        s.get(ImportBatch, book + '-import').rows = [{'record': original, 'locator': locator}]
        s.commit()
    observation = append(environment)
    assert observation.status_code == 201, observation.text
    run = check(environment, observation.json()['id'])
    assert run.status_code == 200, run.text
    assert run.json()['binding']['evidence_locators'][0]['locator'] == locator
