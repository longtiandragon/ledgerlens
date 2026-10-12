"""Immutable index checks; optional integration uses a dedicated loopback database."""
from copy import deepcopy
import os
import struct
from uuid import uuid4
from types import SimpleNamespace

import pytest

from app.retrieval_index import PersistentIndex, content_hashes, manifest_for, validate_manifest, validate_segments
from app.retrieval_vectors import segment_chunks


def fixture_data(identity='parent'):
    parents = [{'chunk_id': identity + str(i), 'source_id': 'registered-original', 'text': text,
                'framework': framework, 'effective_from': begin, 'effective_to': end}
               for i, (text, framework, begin, end) in enumerate([
                   ('存货成本参考', 'CAS', None, None), ('期末存货计量', 'BOTH', '2026-09-01', '2026-09-30'),
                   ('小企业规则参考', 'SCAS', None, None), ('未来条文参考', 'CAS', '2027-01-01', None)])]
    def tokenizer(text, **kw):
        return {'offset_mapping': [(i, i + 1) for i in range(len(text))]}
    segments = segment_chunks(parents, tokenizer)
    vectors = [[1.0] + [0.0] * 511, [0.8, 0.6] + [0.0] * 510,
               [0.6, 0.8] + [0.0] * 510, [1.0] + [0.0] * 511]
    manifest = manifest_for(segments, vectors, parents=parents, engine={'fixture': 'not-a-model'},
                            corpus_sha256='a' * 64, registry_sha256='b' * 64)
    return parents, segments, vectors, manifest


def test_content_identity_is_order_independent_and_exact_float32():
    parents, segments, vectors, manifest = fixture_data()
    assert content_hashes(segments, vectors) == content_hashes(segments[::-1], vectors[::-1])
    rounded = [list(struct.unpack('<512f', struct.pack('<512f', *v))) for v in vectors]
    assert content_hashes(segments, vectors) == content_hashes(segments, rounded)
    assert manifest_for(segments[::-1], vectors[::-1], parents=parents, engine=manifest['engine'],
                        corpus_sha256='a' * 64, registry_sha256='b' * 64) == manifest
    changed = deepcopy(vectors)
    changed[0] = [0.0, 1.0] + [0.0] * 510
    assert content_hashes(segments, changed)[1] != manifest['vectors_sha256']


@pytest.mark.parametrize('mutation', ['text', 'parent', 'scope', 'range', 'id', 'duplicate', 'extra', 'period'])
def test_segments_cannot_forge_parent_text_or_metadata(mutation):
    parents, segments, _, _ = fixture_data()
    if mutation == 'text':
        segments[0]['text'] = '伪造成本参考'
    elif mutation == 'parent':
        segments[0]['parent_chunk_id'] = 'foreign'
    elif mutation == 'scope':
        segments[0]['framework'] = 'SCAS'
    elif mutation == 'range':
        segments[0]['start'] = True
    elif mutation == 'id':
        segments[0]['segment_id'] = 'invalid'
    elif mutation == 'duplicate':
        segments.append(segments[0])
    elif mutation == 'extra':
        segments[0]['source_binding'] = {'forged': True}
    else:
        segments[0]['effective_from'] = '2026-13-01'
    with pytest.raises(ValueError):
        validate_segments(segments, parents)


@pytest.mark.parametrize('key,value', [('index_id', 'c' * 64), ('segment_count', True), ('dimensions', 256),
                                      ('engine', {}), ('schema_version', 'unknown'), ('corpus_sha256', 'wrong')])
def test_bad_or_changed_manifests_rejected(key, value):
    manifest = fixture_data()[3]
    manifest[key] = value
    with pytest.raises(ValueError):
        validate_manifest(manifest)


def test_changed_model_or_registry_creates_distinct_version():
    parents, segments, vectors, old = fixture_data()
    for engine, registry in [({'fixture': 'v2'}, 'b' * 64), (old['engine'], 'c' * 64)]:
        new = manifest_for(segments, vectors, parents=parents, engine=engine, corpus_sha256='a' * 64,
                           registry_sha256=registry)
        assert old['index_id'] != new['index_id']


def cli_args(tmp_path):
    for name in ('corpus', 'registry'):
        (tmp_path / (name + '.json')).write_text('{}')
    (tmp_path / 'originals').mkdir()
    (tmp_path / 'models').mkdir()
    return SimpleNamespace(command='build', port=55439, corpus=tmp_path / 'corpus.json',
                           registry=tmp_path / 'registry.json', source_root=tmp_path / 'originals',
                           model_root=tmp_path / 'models', manifest=None, output=tmp_path / 'report.json')


def test_cli_never_overwrites_a_report_or_writes_inside_source_directories(tmp_path):
    from scripts.retrieval_index import run
    args = cli_args(tmp_path)
    args.output.write_text('retained failure')
    with pytest.raises(FileExistsError):
        run(args)
    assert args.output.read_text() == 'retained failure'
    for root in (args.source_root, args.model_root):
        args.output = root / 'new-report.json'
        with pytest.raises(ValueError, match='new report'):
            run(args)
        assert not args.output.exists()


def test_cli_fails_before_embedding_when_segment_budget_exceeded(tmp_path, monkeypatch):
    from app import knowledge, retrieval_vectors
    from scripts.retrieval_index import run
    args = cli_args(tmp_path)
    parents, segments, _, _ = fixture_data()
    monkeypatch.setattr(knowledge, 'load_verified_corpus', lambda *a, **k: parents)
    class FakeModel:
        def __init__(self, path):
            pass
        def segments(self, chunks):
            return [segments[0]] * 20001
        def encode(self, texts):
            pytest.fail('Embedding must not start after exceeding the storage budget')
    monkeypatch.setattr(retrieval_vectors, 'OfflineBGE', FakeModel)
    report = run(args)
    assert report['status'] == 'failed' and report['error']['type'] == 'ValueError'
    assert 'exceeds bounds' in report['error']['message']
    assert report['paid_model_requests'] == 0 and 'manifest' not in report
    assert args.output.exists()


@pytest.fixture
def postgres_index():
    if os.environ.get('RETRIEVAL_INDEX_TESTS') != '1':
        pytest.skip('Explicit RETRIEVAL_INDEX_TESTS=1 requires the local optional pgvector service')
    import psycopg
    def connect():
        return psycopg.connect(host='127.0.0.1', port=55439, user='retrieval', dbname='retrieval',
                                password='local-retrieval-only', autocommit=True, connect_timeout=5)
    with connect() as connection:
        connection.execute("SET statement_timeout='10s'")
        index = PersistentIndex(connection)
        index.initialize()
        data = fixture_data('test-' + uuid4().hex)
        assert index.publish(data[3], data[1], data[2])
        yield connection, index, data, connect


def test_pg_restart_connection_idempotence_scope_and_independent_ranking(postgres_index):
    _, index, (_, segments, vectors, manifest), connect = postgres_index
    assert not index.publish(manifest, segments, vectors)
    with connect() as new_connection:
        reopened = PersistentIndex(new_connection)
        assert len(reopened.verify(manifest)) == 4
        for framework, period, expected in [('CAS', '2026-09', [0, 1]), ('SCAS', '2026-09', [1, 2]),
                                            ('CAS', '2026-10', [0]), ('CAS', '2026-08', [0])]:
            hits = reopened.search(manifest, vectors[0], engine=manifest['engine'], framework=framework, period=period)
            assert [h['segment_id'] for h in hits] == [segments[i]['segment_id'] for i in expected]
            assert [h['score'] for h in hits] == pytest.approx([vectors[i][0] for i in expected], abs=1e-6)
            assert all(h['parent_chunk_id'] in {segments[i]['parent_chunk_id'] for i in expected} for h in hits)
        with pytest.raises(ValueError, match='model differs'):
            reopened.search(manifest, vectors[0], engine={'fixture': 'another'}, framework='CAS', period='2026-09')


@pytest.mark.parametrize('mutation', ['text', 'vector', 'missing', 'manifest', 'numeric_metadata', 'extra'])
def test_pg_modified_storage_is_rejected_not_repaired(postgres_index, mutation):
    connection, index, (_, segments, vectors, manifest), _ = postgres_index
    identity, first = manifest['index_id'], segments[0]['segment_id']
    if mutation == 'text':
        connection.execute("UPDATE ledgerlens_retrieval.segments SET payload=jsonb_set(payload,'{text}','\"篡改成本参考\"') WHERE index_id=%s AND id=%s", (identity, first))
    elif mutation == 'vector':
        from app.retrieval_index import vector_literal
        connection.execute('UPDATE ledgerlens_retrieval.segments SET embedding=%s::vector WHERE index_id=%s AND id=%s',
                           (vector_literal(vectors[2]), identity, first))
    elif mutation == 'missing':
        connection.execute('DELETE FROM ledgerlens_retrieval.segments WHERE index_id=%s AND id=%s', (identity, first))
    elif mutation == 'manifest':
        connection.execute("UPDATE ledgerlens_retrieval.versions SET manifest=jsonb_set(manifest,'{segment_count}','3') WHERE id=%s", (identity,))
    elif mutation == 'numeric_metadata':
        connection.execute("UPDATE ledgerlens_retrieval.versions SET manifest=jsonb_set(manifest,'{dimensions}','512.0') WHERE id=%s", (identity,))
    else:
        connection.execute("INSERT INTO ledgerlens_retrieval.segments SELECT index_id,'unexpected',payload,embedding FROM ledgerlens_retrieval.segments WHERE index_id=%s AND id=%s", (identity, first))
    with pytest.raises(ValueError):
        index.verify(manifest)
    with pytest.raises(ValueError):
        index.search(manifest, vectors[0], engine=manifest['engine'], framework='CAS', period='2026-09')
    with pytest.raises(ValueError):
        index.publish(manifest, segments, vectors)


def test_pg_query_pins_one_snapshot_during_concurrent_change(postgres_index, monkeypatch):
    _, index, (_, segments, vectors, manifest), connect = postgres_index
    original = index._verify
    def concurrent(expected):
        verified = original(expected)
        with connect() as writer:
            writer.execute('DELETE FROM ledgerlens_retrieval.segments WHERE index_id=%s AND id=%s',
                           (manifest['index_id'], segments[0]['segment_id']))
        return verified
    monkeypatch.setattr(index, '_verify', concurrent)
    hits = index.search(manifest, vectors[0], engine=manifest['engine'], framework='CAS', period='2026-09')
    assert hits[0]['segment_id'] == segments[0]['segment_id']
    monkeypatch.setattr(index, '_verify', original)
    with pytest.raises(ValueError, match='missing or altered'):
        index.verify(manifest)


def test_pg_failed_publication_leaves_no_partial_version(postgres_index, monkeypatch):
    connection, index, _, _ = postgres_index
    _, segments, vectors, manifest = fixture_data('rollback-' + uuid4().hex)
    def failure(expected):
        raise RuntimeError('injected verification failure')
    monkeypatch.setattr(index, '_verify', failure)
    with pytest.raises(RuntimeError, match='injected'):
        index.publish(manifest, segments, vectors)
    assert connection.execute('SELECT id FROM ledgerlens_retrieval.versions WHERE id=%s', (manifest['index_id'],)).fetchone() is None
    assert connection.execute('SELECT id FROM ledgerlens_retrieval.segments WHERE index_id=%s', (manifest['index_id'],)).fetchone() is None
