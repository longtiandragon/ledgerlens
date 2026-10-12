import json
import httpx
import pytest
from scripts.collect_sources import collect


def sources(tmp_path, identities=('first', 'second')):
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'sources': [{'id': identity, 'url': f'https://www.mof.gov.cn/{identity}',
                                                'framework': 'BOTH', 'effective_from': None} for identity in identities]}), encoding='utf-8')
    return manifest


def test_partial_acquisition_preserves_previous_sources_and_records_failure(tmp_path, monkeypatch):
    monkeypatch.setattr('scripts.collect_sources.time.sleep', lambda _: None)
    manifest = sources(tmp_path)
    transport = httpx.MockTransport(lambda _: httpx.Response(200, text='<html><body>' + 'Valid reference document. ' * 5 + '</body></html>'))
    output = tmp_path / 'output'
    assert collect(manifest, output, ['first'], transport=transport)[0]['status'] == 'fetched'
    fail = httpx.MockTransport(lambda _: httpx.Response(502))
    assert collect(manifest, output, ['second'], transport=fail)[0]['status'] == 'failed'
    report = json.loads((output / 'acquisition-report.json').read_text())
    assert {r['id']: r['status'] for r in report} == {'first': 'fetched', 'second': 'failed'}
    assert len((output / 'acquisition-history.jsonl').read_text().splitlines()) == 2


@pytest.mark.parametrize('identity', ['../escape', 'C:/absolute', 'folder/name'])
def test_manifest_paths_cannot_escape_output(tmp_path, identity):
    with pytest.raises(ValueError, match='safe'):
        collect(sources(tmp_path, [identity]), tmp_path / 'output')
    assert not (tmp_path / 'output').exists()


def test_http_200_error_page_is_not_a_collected_policy(tmp_path, monkeypatch):
    monkeypatch.setattr('scripts.collect_sources.time.sleep', lambda _: None)
    transport = httpx.MockTransport(lambda _: httpx.Response(200, text='<html><body>502 Bad Gateway</body></html>'))
    assert collect(sources(tmp_path, ['first']), tmp_path / 'output', transport=transport)[0]['status'] == 'failed'
