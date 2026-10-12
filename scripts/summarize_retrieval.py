"""Validate saved retrieval results against frozen originals and manual labels."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'backend'))
from app.knowledge import load_verified_corpus  # noqa: E402
from app.retrieval_vectors import eligible  # noqa: E402
from benchmarks.reference_retrieval import SOURCE_FILES, digest, read, score_rankings, totals  # noqa: E402


def summarize(args):
    report = read(args.report)
    if report['schema_version'] != 'reference-retrieval-evaluation-v1':
        raise ValueError('Invalid evaluation schema')
    if set(report['source_sha256']) != set(SOURCE_FILES):
        raise ValueError('Incomplete evaluation source provenance')
    for name, expected in report['source_sha256'].items():
        if digest(ROOT / name) != expected:
            raise ValueError('Evaluation implementation changed; keep historical report and make a new run')
    if set(report['input_sha256']) != {'queries', 'truth', 'corpus', 'registry'}:
        raise ValueError('Incomplete input hashes')
    for name, expected in report['input_sha256'].items():
        if digest(getattr(args, name)) != expected:
            raise ValueError('Frozen inputs changed')
    if report['ledger_writes'] != 0 or report['external_model_requests'] != 0:
        raise ValueError('Unexpected evaluation side effects')
    corpus = load_verified_corpus(args.corpus, registry=args.registry, source_root=args.source_root)
    parents = {c['chunk_id']: c for c in corpus}
    segments = {s['segment_id']: s for s in report['segments']}
    if report['parent_chunks'] != len(corpus) or len(segments) != len(report['segments']):
        raise ValueError('Invalid chunk or segment count')
    for segment in segments.values():
        parent = parents[segment['parent_chunk_id']]
        start, end = segment['start'], segment['end']
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(parent['text']):
            raise ValueError('Invalid segment coordinates')
        identity = hashlib.sha256(f'{parent["chunk_id"]}:{start}:{end}'.encode()).hexdigest()
        if identity != segment['segment_id'] or segment['text'] != parent['text'][start:end]:
            raise ValueError('Segment differs from verified original')
        for key in ('source_id', 'framework', 'effective_from', 'effective_to'):
            if segment.get(key) != parent.get(key):
                raise ValueError('Segment metadata differs from original')
    queries = read(args.queries)
    truth = read(args.truth)['labels']
    labels = {r['id']: r for r in truth}
    if len(labels) != len(truth) or set(labels) != {q['id'] for q in queries['queries']}:
        raise ValueError('Labels do not cover query set')
    if len(report['cases']) != len(queries['queries']):
        raise ValueError('Incomplete query results')
    failed = 0
    for query, row in zip(queries['queries'], report['cases']):
        if (row['query_id'], row['query'], row['framework']) != (query['id'], query['text'], query['framework']):
            raise ValueError('Query order or scope mismatch')
        label = labels[query['id']]
        if row['no_answer'] != (label['source_id'] is None):
            raise ValueError('Changed answerability')
        for method in ('keyword', 'bge_pgvector'):
            rankings = row[method]
            error = method + '_error' in row
            failed += error
            if error and rankings:
                raise ValueError('Failure must not masquerade as success')
            if len(rankings) > 6 or len({h['segment_id'] for h in rankings}) != len(rankings):
                raise ValueError('Invalid result count or duplicate segments')
            last_score = math.inf
            for hit in rankings:
                if not math.isfinite(hit['score']) or hit['score'] > last_score:
                    raise ValueError('Invalid or unordered retrieval scores')
                last_score = hit['score']
                if not eligible(segments[hit['segment_id']], query['framework'], queries['period']):
                    raise ValueError('Out-of-scope retrieved segment')
            if score_rankings(rankings, label, list(segments.values())) != row[method + '_scores']:
                raise ValueError('Saved score differs from manual label')
        if ('bge_pgvector_error' not in row and
                (row['pg_numpy_same_top6'] is not True or not 0 <= row['pg_numpy_max_score_difference'] <= 1e-5)):
            raise ValueError('Exact vector comparison did not pass')
    if report['status'] != ('completed_with_failures' if failed else 'completed'):
        raise ValueError('Completion status hides failures')
    if totals(report['cases']) != report['totals']:
        raise ValueError('Saved aggregate differs')
    counts = {s: sum(c['source_id'] == s for c in corpus) for s in sorted({c['source_id'] for c in corpus})}
    return {'schema_version': 'reference-retrieval-summary-v1', 'report_sha256': digest(args.report),
            'status': report['status'], 'input_sha256': report['input_sha256'],
            'source_sha256': report['source_sha256'], 'engine': report['engine'],
            'config': report['config'], 'parent_chunks': len(corpus), 'segments': len(segments),
            'source_chunk_counts': counts, 'postgres_version': report['postgres_version'],
            'pgvector_version': report['pgvector_version'], 'totals': report['totals'],
            'duration_seconds': report['duration_seconds'], 'corpus_encoding_seconds': report['corpus_encoding_seconds'],
            'cases': [{k: r[k] for k in ('query_id', 'no_answer', 'keyword_scores', 'bge_pgvector_scores',
                                       'keyword_seconds', 'bge_pgvector_seconds')}
                      for r in report['cases']],
            'ledger_writes': 0, 'external_model_requests': 0,
            'pg_numpy_verified_queries': sum(r.get('pg_numpy_same_top6') is True for r in report['cases']),
            'limitations': ['Exploratory queries handwritten by implementer; not blind expert or real-user evaluation.',
                            'No answer threshold calibrated; both methods return candidates for absent answers.',
                            'Six verified original sources; two acquisition failures retained.',
                            'Reference effectivity is not certified; no executable rule or business fact is created.',
                            'Connection-local temporary pgvector index; persistent product integration not performed.',
                            'All parameters and labels frozen before results; no tuning on this query set.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('report', 'corpus', 'registry', 'source-root', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--queries', type=Path, default=ROOT / 'benchmarks/retrieval-queries-v1.json')
    parser.add_argument('--truth', type=Path, default=ROOT / 'benchmarks/retrieval-truth-v1.json')
    args = parser.parse_args()
    summary = summarize(args)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'status': summary['status'], 'totals': summary['totals']}, ensure_ascii=True))
