import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import retrieval_vectors as retrieval
from benchmarks.reference_retrieval import score_rankings, totals


def chunk(text='甲乙丙丁戊己庚辛', **kw):
    return {'chunk_id': 'parent', 'text': text, 'source_id': 'original', 'framework': 'CAS', **kw}


def characters(text, **kwargs):
    return {'offset_mapping': [(i, i + 1) for i, c in enumerate(text) if not c.isspace()]}


def test_character_slices_preserve_original_whitespace_and_parent():
    parent = chunk(' 甲乙\n丙丁 戊己庚辛 ')
    segments = retrieval.segment_chunks([parent], characters, size=4, stride=3)
    assert [s['text'] for s in segments] == ['甲乙\n丙丁', '丁 戊己庚', '庚辛']
    for segment in segments:
        assert segment['text'] == parent['text'][segment['start']:segment['end']]
        assert segment['parent_chunk_id'] == 'parent'
        assert 'source_binding' not in segment
    assert parent['text'] == ' 甲乙\n丙丁 戊己庚辛 '


@pytest.mark.parametrize('offsets', [[(-1, 1)], [(1, 1)], [(0, 100)], [(3, 4), (0, 1)]])
def test_invalid_token_offsets_rejected(offsets):
    with pytest.raises(ValueError, match='character offsets'):
        retrieval.segment_chunks([chunk()], lambda *a, **k: {'offset_mapping': offsets})


def test_duplicate_parents_and_bad_window_rejected():
    with pytest.raises(ValueError, match='Duplicate'):
        retrieval.segment_chunks([chunk(), chunk()], characters)
    with pytest.raises(ValueError, match='window'):
        retrieval.segment_chunks([chunk()], characters, size=481)


@pytest.mark.parametrize('framework,period,expected', [
    ('CAS', '2026-09', True), ('CAS', '2026-10', True), ('CAS', '2026-08', False),
    ('CAS', '2026-11', False), ('SCAS', '2026-09', False)])
def test_scope_and_inclusive_date_bounds(framework, period, expected):
    segment = chunk(effective_from='2026-09-01', effective_to='2026-10-31')
    assert retrieval.eligible(segment, framework, period) == expected


def test_both_and_unknown_effectivity_are_reference_only():
    assert retrieval.eligible(chunk(framework='BOTH'), 'SCAS', '2026-10')
    for scope in [('BOTH', '2026-10', 6), ('CAS', '2026-13', 6), ('CAS', '2026-10', True)]:
        with pytest.raises(ValueError):
            retrieval.validate_scope(*scope)


@pytest.mark.parametrize('vector', [[0] * 512, [1] * 511, [float('nan')] + [0] * 511,
                                     [float('inf')] + [0] * 511])
def test_invalid_embeddings_fail(vector):
    with pytest.raises(ValueError):
        retrieval.validate_vectors([vector], 1)


def test_silent_model_truncation_is_rejected():
    model = object.__new__(retrieval.OfflineBGE)
    model.model = SimpleNamespace(tokenizer=SimpleNamespace(encode=lambda s: [1] * 513))
    with pytest.raises(ValueError, match='truncated'):
        model.encode(['长查询'], queries=True)


def test_changed_model_rejected_before_optional_import(tmp_path, monkeypatch):
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'files': [{'file': 'model.safetensors', 'bytes': 3, 'sha256': 'wrong'}]}))
    (tmp_path / 'model.safetensors').write_bytes(b'abc')
    monkeypatch.setattr(retrieval, 'MODEL_MANIFEST', manifest)
    with pytest.raises(ValueError, match='Changed pinned'):
        retrieval.OfflineBGE(tmp_path)


def test_manual_anchor_requires_correct_source_and_whole_clause():
    segments = [{'segment_id': 'wrong-source', 'source_id': 'wrong', 'text': '应计提折旧'},
                {'segment_id': 'incomplete', 'source_id': 'right', 'text': '计提折旧'},
                {'segment_id': 'valid', 'source_id': 'right', 'text': '应\n计提折旧'}]
    label = {'source_id': 'right', 'anchor': '应计提折旧'}
    scored = score_rankings([{'segment_id': s['segment_id']} for s in segments], label, segments)
    assert not scored['hit_at_1'] and scored['hit_at_3']
    assert scored['reciprocal_rank_at_6'] == 1 / 3
    with pytest.raises(ValueError, match='absent'):
        score_rankings([], label, segments[:2])


def test_failures_remain_in_denominator_and_are_not_abstentions():
    hit = {'no_answer': False, 'hit_at_1': True, 'hit_at_3': True, 'hit_at_6': True,
           'reciprocal_rank_at_6': 1.0}
    miss = {**hit, 'hit_at_1': False, 'hit_at_3': False, 'hit_at_6': False, 'reciprocal_rank_at_6': 0}
    empty = {'no_answer': True, 'empty_result': True, 'returned_count': 0}
    rows = []
    for scores, error in [(hit, False), (miss, True), (empty, True)]:
        row = {'no_answer': scores['no_answer']}
        for method in ('keyword', 'bge_pgvector'):
            row[method + '_scores'] = scores
            if error:
                row[method + '_error'] = 'retained failure'
        rows.append(row)
    result = totals(rows)['bge_pgvector']
    assert result['answerable_count'] == 2 and result['hit_at_6'] == 0.5
    assert result['failed_queries'] == 2 and result['no_answer_successful_empty_results'] == 0


def test_frozen_query_inputs_cannot_contain_answer_labels():
    root = Path(__file__).resolve().parents[2]
    query_path = root / 'benchmarks/retrieval-queries-v1.json'
    queries = json.loads(query_path.read_text(encoding='utf-8'))['queries']
    assert len(queries) == 20
    assert all(set(q) == {'id', 'framework', 'text'} for q in queries)


@pytest.fixture
def summary_fixture(tmp_path, monkeypatch):
    from scripts import summarize_retrieval as summary
    from benchmarks.reference_retrieval import SOURCE_FILES, digest
    import hashlib
    parent = chunk('应计提折旧')
    segment = retrieval.segment_chunks([parent], characters)[0]
    label = {'id': 'q1', 'source_id': 'original', 'anchor': '应计提折旧'}
    args = SimpleNamespace(**{k: tmp_path / (k + '.json')
                             for k in ('report', 'queries', 'truth', 'corpus', 'registry')}, source_root=tmp_path)
    query = {'id': 'q1', 'text': '设备如何折旧？', 'framework': 'CAS'}
    args.queries.write_text(json.dumps({'period': '2026-10', 'queries': [query]}), encoding='utf-8')
    args.truth.write_text(json.dumps({'labels': [label]}), encoding='utf-8')
    args.corpus.write_text('[]', encoding='utf-8')
    args.registry.write_text('{}', encoding='utf-8')
    rankings = [{'segment_id': segment['segment_id'], 'score': 0.9}]
    scores = score_rankings(rankings, label, [segment])
    row = {'query_id': 'q1', 'query': query['text'], 'framework': 'CAS', 'no_answer': False,
           'pg_numpy_same_top6': True, 'pg_numpy_max_score_difference': 1e-7}
    for method in ('keyword', 'bge_pgvector'):
        row.update({method: rankings, method + '_scores': scores, method + '_seconds': 0.1})
    report = {'schema_version': 'reference-retrieval-evaluation-v1', 'status': 'completed',
              'source_sha256': {name: digest(summary.ROOT / name) for name in SOURCE_FILES},
              'input_sha256': {k: digest(getattr(args, k)) for k in ('queries', 'truth', 'corpus', 'registry')},
              'ledger_writes': 0, 'external_model_requests': 0, 'segments': [segment], 'parent_chunks': 1,
              'cases': [row], 'totals': totals([row]), 'engine': {}, 'config': {},
              'postgres_version': 'fixture', 'pgvector_version': 'fixture', 'duration_seconds': 1,
              'corpus_encoding_seconds': 0.5}
    monkeypatch.setattr(summary, 'load_verified_corpus', lambda *a, **kw: [parent])
    assert segment['segment_id'] == hashlib.sha256(b'parent:0:5').hexdigest()
    return summary, args, report


@pytest.mark.parametrize('tamper', ['none', 'aggregate', 'text', 'scope', 'omit', 'error', 'labels'])
def test_saved_report_rechecks_scores_coordinates_scope_and_failures(summary_fixture, tamper):
    summary, args, report = summary_fixture
    if tamper == 'aggregate':
        report['totals']['keyword']['hit_at_1'] = 0
    elif tamper == 'text':
        report['segments'][0]['text'] = '假造原文'
    elif tamper == 'scope':
        report['segments'][0]['framework'] = 'SCAS'
    elif tamper == 'omit':
        report['cases'] = []
    elif tamper == 'error':
        report['cases'][0]['keyword_error'] = 'failed but ranked'
    elif tamper == 'labels':
        args.truth.write_text('{"labels": []}', encoding='utf-8')
    args.report.write_text(json.dumps(report), encoding='utf-8')
    if tamper == 'none':
        assert summary.summarize(args)['totals']['keyword']['hit_at_1'] == 1
    else:
        with pytest.raises(ValueError):
            summary.summarize(args)
