"""Public envelope validation at real import routes; synthetic XML fixture, no download."""
from copy import deepcopy
import hashlib
import json

import pytest

from test_api import client as client  # conftest owns isolation; the fixture checks the exact DB before reset.
from app.imports import parse_file
from datasets import mof_vouchers
from test_mof_vouchers import example


@pytest.fixture
def pinned_fixture(monkeypatch):
    # Isolated test trust anchor, never written into the production pin list.
    raw = example()
    monkeypatch.setattr(mof_vouchers, 'SAMPLES', {'fixture_123.xml': hashlib.sha256(raw).hexdigest()})
    return mof_vouchers.project(raw, 'fixture_123.xml')


def upload(client, package, **fields):
    identity = client.post('/api/v1/books', json={
        'name': '公开包入口边界（独立合成测试）', 'framework': 'CAS', 'period': '2023-12'}).json()['id']
    raw = json.dumps(package, ensure_ascii=False).encode()
    response = client.post('/api/v1/imports', data={'book_id': identity, **fields},
                           files={'file': ('example.json', raw, 'application/json')})
    return identity, raw, response


@pytest.mark.parametrize('mark', [None, 'false', 'true'])
def test_verified_example_always_retains_teaching_nature(client, pinned_fixture, mark):
    identity, raw, response = upload(client, pinned_fixture, **({} if mark is None else {'synthetic': mark}))
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview['record_count'] == 2 and preview['errors'] == []
    document = client.get(f'/api/v1/books/{identity}/documents').json()[0]
    assert document['synthetic'] is True
    assert client.get(f'/api/v1/documents/{document["id"]}/file').content == raw
    assert client.post(f'/api/v1/imports/{preview["id"]}/confirm').json()['imported'] == 2
    records = client.get(f'/api/v1/books/{identity}/records').json()
    assert sorted((r['debit'], r['credit']) for r in records) == [('0.00', '0.10'), ('0.10', '0.00')]


@pytest.mark.parametrize('change', ['amount', 'xml', 'entity', 'pointer', 'schema', 'nature', 'pin_flag', 'missing_source'])
def test_altered_claimed_example_is_rejected_before_saving(client, pinned_fixture, change):
    package = deepcopy(pinned_fixture)
    if change == 'amount':
        package['records'][0]['debit'] = '999.00'
    elif change == 'xml':
        package['source']['original_xml_utf8'] = package['source']['original_xml_utf8'].replace('0.10', '999.00')
    elif change == 'entity':
        package['accounting_entity']['id'] = 'OTHER'
    elif change == 'pointer':
        package['field_provenance'][0]['xml_path'] = './wrong'
    elif change == 'schema':
        package['schema_version'] = 'mof-einv-example-projection-v999'
    elif change == 'nature':
        package['data_nature'] = 'real_enterprise_invoice'
    elif change == 'pin_flag':
        package['source']['matches_pinned_official_example'] = 1  # True == 1 must not bypass strict type checks
    else:
        del package['source']
    identity, _, response = upload(client, package)
    assert response.status_code == 422 and '固定原件不一致' in response.text
    assert client.get(f'/api/v1/books/{identity}/documents').json() == []
    assert client.get(f'/api/v1/books/{identity}/records').json() == []


def test_public_source_cannot_be_reinterpreted_by_mapping(client, pinned_fixture):
    identity, _, response = upload(client, pinned_fixture, mapping=json.dumps({'debit': 'credit'}))
    assert response.status_code == 422 and '不允许重新映射' in response.text
    assert client.get(f'/api/v1/books/{identity}/documents').json() == []
    _, _, accepted = upload(client, pinned_fixture)
    preview = accepted.json()
    remap = client.put(f'/api/v1/imports/{preview["id"]}/mapping', json={'debit': 'credit'})
    assert remap.status_code == 422
    same = client.put(f'/api/v1/imports/{preview["id"]}/mapping', json=preview['mapping'])
    assert same.status_code == 200 and same.json()['rows'] == preview['rows']


def test_unpinned_xml_is_not_an_official_example_even_with_teaching_flag(client):
    package = mof_vouchers.project(example(), 'fixture_123.xml')
    _, _, response = upload(client, package, synthetic='true')
    assert response.status_code == 422


def test_ordinary_json_keeps_explicit_user_choice(client, pinned_fixture):
    identity, _, response = upload(client, {'records': pinned_fixture['records']})
    assert response.status_code == 200
    assert client.get(f'/api/v1/books/{identity}/documents').json()[0]['synthetic'] is False


def test_duplicate_envelope_key_cannot_disable_gate(pinned_fixture):
    raw = json.dumps(pinned_fixture)
    raw = raw[:-1] + ', "data_nature": "ordinary"}'
    with pytest.raises(ValueError, match='字段重复'):
        parse_file(raw.encode(), 'example.json')
