"""Offline BGE/pgvector versus unchanged keyword scorer on identical segments.

Truth is opened only after every prediction is saved. No rule engine, ledger,
paid provider, runtime agent or evaluator answer corpus is used here.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
SOURCE_FILES = ('benchmarks/reference_retrieval.py', 'backend/app/retrieval_vectors.py',
                'backend/app/knowledge.py', 'backend/app/reference_sources.py',
                'docs/retrieval-model-v1.json')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def compact(text):
    return ''.join(text.split())


def score_rankings(rankings, label, segments):
    if label['source_id'] is None:
        return {'no_answer': True, 'returned_count': len(rankings), 'empty_result': not rankings}
    relevant = {s['segment_id'] for s in segments if s['source_id'] == label['source_id']
                and compact(label['anchor']) in compact(s['text'])}
    if not relevant:
        raise ValueError('Manual clause anchor absent from reference segments')
    positions = [i for i, row in enumerate(rankings, 1) if row['segment_id'] in relevant]
    first = positions[0] if positions else None
    return {'no_answer': False, 'first_relevant_rank': first,
            'hit_at_1': bool(first and first <= 1), 'hit_at_3': bool(first and first <= 3),
            'hit_at_6': bool(first and first <= 6), 'reciprocal_rank_at_6': 1 / first if first else 0}


def totals(cases):
    result = {}
    for method in ('keyword', 'bge_pgvector'):
        positives = [r for r in cases if not r['no_answer']]
        negatives = [r for r in cases if r['no_answer']]
        result[method] = {'answerable_count': len(positives), 'unanswerable_count': len(negatives),
                          'failed_queries': sum(method + '_error' in r for r in cases)}
        for key in ('hit_at_1', 'hit_at_3', 'hit_at_6', 'reciprocal_rank_at_6'):
            # Failures have empty rankings and score zero, including in denominators.
            result[method][key] = sum(r[method + '_scores'][key] for r in positives) / max(1, len(positives))
        result[method]['no_answer_returned_candidates'] = sum(
            bool(r[method + '_scores']['returned_count']) for r in negatives)
        result[method]['no_answer_successful_empty_results'] = sum(
            r[method + '_scores']['empty_result'] and method + '_error' not in r for r in negatives)
    return result


def run(args):
    from app.knowledge import load_verified_corpus, search
    from app.retrieval_vectors import OfflineBGE, PgVectorIndex, eligible, validate_scope
    import numpy as np
    import psycopg

    inputs = {key: digest(getattr(args, key)) for key in ('queries', 'truth', 'corpus', 'registry')}
    queries = read(args.queries)
    if queries['schema_version'] != 'reference-retrieval-queries-v1':
        raise ValueError('Unknown query contract')
    ids = [q['id'] for q in queries['queries']]
    if len(ids) != len(set(ids)) or not ids:
        raise ValueError('Invalid query IDs')
    for query in queries['queries']:
        if set(query) != {'id', 'framework', 'text'}:
            raise ValueError('Query inputs must not contain labels')
        validate_scope(query['framework'], queries['period'], 6)
    report = {'schema_version': 'reference-retrieval-evaluation-v1', 'status': 'running',
              'started_at': datetime.now(timezone.utc).isoformat(), 'input_sha256': inputs,
              'source_sha256': {name: digest(ROOT / name) for name in SOURCE_FILES},
              'cases': [], 'ledger_writes': 0, 'external_model_requests': 0,
              'scope': 'exploratory_handwritten_queries_not_independent_business_validation',
              'config': {'top_k': 6, 'vector_threshold': None, 'keyword_min_score': 'strictly greater than 0',
                         'same_segments': True, 'exact_search': True}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle)

    def save():
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

    begin = perf_counter()
    try:
        corpus = load_verified_corpus(args.corpus, registry=args.registry, source_root=args.source_root)
        model = OfflineBGE(args.model_root)
        segments = model.segments(corpus)
        report['engine'] = model.engine
        report['parent_chunks'] = len(corpus)
        report['segments'] = segments
        # Keyword uses exactly the same windows; existing score and filter remain unchanged.
        keyword_corpus = [{**s, 'chunk_id': s['segment_id']} for s in segments]
        encoded_at = perf_counter()
        vectors = model.encode([s['text'] for s in segments])
        report['corpus_encoding_seconds'] = perf_counter() - encoded_at
        # Fixed local research database, separate from the product database. Temp table per connection.
        with psycopg.connect(host='127.0.0.1', port=args.port, user='retrieval',
                             password='local-retrieval-only', dbname='retrieval', connect_timeout=5) as conn:
            conn.execute("SET statement_timeout = '30s'")
            conn.execute('CREATE EXTENSION IF NOT EXISTS vector')
            report['postgres_version'] = conn.execute('SHOW server_version').fetchone()[0]
            report['pgvector_version'] = conn.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()[0]
            index = PgVectorIndex(conn, segments, vectors)
            for query in queries['queries']:
                row = {'query_id': query['id'], 'query': query['text'], 'framework': query['framework']}
                for method in ('keyword', 'bge_pgvector'):
                    start = perf_counter()
                    try:
                        if method == 'keyword':
                            hits = search(keyword_corpus, query['text'], query['framework'], queries['period'], 6)
                        else:
                            query_vector = model.encode([query['text']], queries=True)[0]
                            hits = index.search(query_vector, query['framework'], queries['period'], 6)
                            local = [(s['segment_id'], float(np.dot(vectors[i], query_vector)))
                                     for i, s in enumerate(segments)
                                     if eligible(s, query['framework'], queries['period'])]
                            local.sort(key=lambda x: (-x[1], x[0]))
                            lookup = dict(local)
                            row['pg_numpy_max_score_difference'] = max(
                                (abs(h['score'] - lookup[h['segment_id']]) for h in hits), default=0)
                            row['pg_numpy_same_top6'] = [h['segment_id'] for h in hits] == [s[0] for s in local[:6]]
                            if row['pg_numpy_max_score_difference'] > 1e-5 or not row['pg_numpy_same_top6']:
                                raise ValueError('pgvector ranking differs from independent local exact cosine')
                        row[method] = [{'segment_id': h['segment_id'], 'score': h['score']} for h in hits]
                    except Exception as exc:
                        row[method] = []
                        row[method + '_error'] = {'type': type(exc).__name__, 'message': str(exc)[:250]}
                    row[method + '_seconds'] = perf_counter() - start
                report['cases'].append(row)
                save()
        report['status'] = 'predicted_not_scored'
        save()
        for key, expected in inputs.items():
            if digest(getattr(args, key)) != expected:
                raise ValueError('Changed frozen evaluation input')
        truth = read(args.truth)
        if truth['schema_version'] != 'reference-retrieval-truth-v1':
            raise ValueError('Unknown truth contract')
        labels = {label['id']: label for label in truth['labels']}
        if len(labels) != len(truth['labels']) or set(labels) != set(ids):
            raise ValueError('Mismatched labels')
        for row in report['cases']:
            label = labels[row['query_id']]
            row['no_answer'] = label['source_id'] is None
            for method in ('keyword', 'bge_pgvector'):
                row[method + '_scores'] = score_rankings(row[method], label, segments)
        report['totals'] = totals(report['cases'])
        failed = sum(method + '_error' in row for row in report['cases'] for method in ('keyword', 'bge_pgvector'))
        report['status'] = 'completed_with_failures' if failed else 'completed'
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)[:350]}
    report['duration_seconds'] = perf_counter() - begin
    save()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('corpus', 'registry', 'source-root', 'model-root', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--queries', type=Path, default=ROOT / 'benchmarks/retrieval-queries-v1.json')
    parser.add_argument('--truth', type=Path, default=ROOT / 'benchmarks/retrieval-truth-v1.json')
    parser.add_argument('--port', type=int, default=55439)
    result = run(parser.parse_args())
    print(json.dumps({k: result[k] for k in ('status', 'totals', 'error') if k in result}, ensure_ascii=True))
    raise SystemExit(0 if result['status'] == 'completed' else 1)
