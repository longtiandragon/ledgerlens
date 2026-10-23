"""Frozen pooled-reference retrieval comparison; never a production answer agent."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
METHODS = ('keyword', 'bm25', 'tfidf', 'bge', 'rrf')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metrics(predictions, labels):
    if [r['id'] for r in predictions] != [r['id'] for r in labels]:
        raise ValueError('Predictions and labels differ')
    values = {m: [] for m in METHODS}
    for prediction, label in zip(predictions, labels, strict=True):
        truth = set(label['references'])
        if not truth:
            raise ValueError('Empty relevance labels')
        for method in METHODS:
            hits = prediction[method]
            if len(hits) != len(set(hits)) or len(hits) > 6:
                raise ValueError('Repeated parent or too many hits')
            positions = [i + 1 for i, hit in enumerate(hits) if hit in truth]
            values[method].append([len(positions) / len(truth),
                                   float(bool(positions) and positions[0] == 1),
                                   float(bool(positions)), 1 / positions[0] if positions else 0])
    return values


def analyse(predictions, labels):
    import numpy as np
    values = {m: np.asarray(v) for m, v in metrics(predictions, labels).items()}
    # Connected components of shared relevant parents, rather than treating
    # repeated questions about the same passage as independent observations.
    roots = list(range(len(labels)))

    def find(i):
        while roots[i] != i:
            roots[i] = roots[roots[i]]
            i = roots[i]
        return i

    seen = {}
    for i, row in enumerate(labels):
        for reference in row['references']:
            if reference in seen:
                roots[find(i)] = find(seen[reference])
            seen[reference] = i
    groups = {}
    for i in range(len(labels)):
        groups.setdefault(find(i), []).append(i)
    groups = list(groups.values())
    sizes = np.array([len(g) for g in groups])
    totals = {m: np.array([v[g, 0].sum() for g in groups]) for m, v in values.items()}
    rng = np.random.default_rng(20261024)
    differences = {'bge': [], 'rrf': []}
    for _ in range(2000):
        selection = rng.integers(0, len(groups), len(groups))
        means = {m: float(v[selection].sum() / sizes[selection].sum()) for m, v in totals.items()}
        baseline = max(means[m] for m in METHODS[:3])
        for method in differences:
            differences[method].append(means[method] - baseline)
    summary = {m: dict(zip(['recall_at_6', 'hit_at_1', 'hit_at_6', 'mrr_at_6'], v.mean(axis=0).tolist()))
               for m, v in values.items()}
    strongest = max(METHODS[:3], key=lambda m: summary[m]['recall_at_6'])
    failures = sum('error' in p for p in predictions)
    comparison = {}
    for method, draws in differences.items():
        delta = summary[method]['recall_at_6'] - summary[strongest]['recall_at_6']
        interval = np.quantile(draws, [0.0125, 0.9875]).tolist()
        comparison[method] = {'delta': delta, 'adjusted_interval': interval,
                              'supported': delta >= .05 and interval[0] > 0 and failures == 0}
    return {'summary': summary, 'strongest_deterministic': strongest, 'comparison': comparison,
            'failures': failures, 'clusters': len(groups), 'largest_cluster': int(max(sizes))}


def run(inputs, models, output):
    import numpy as np
    from sklearn.feature_extraction.text import CountVectorizer, TfidfTransformer
    from threadpoolctl import threadpool_limits
    from app.retrieval_vectors import OfflineBGE
    threadpool_limits(limits=4)
    output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((inputs / 'manifest.json').read_text(encoding='utf-8'))
    for name in ['queries.json', 'corpus.json']:
        if digest(inputs / name) != manifest['files'][name]:
            raise ValueError('Changed input')
    queries = json.loads((inputs / 'queries.json').read_text(encoding='utf-8'))
    corpus = json.loads((inputs / 'corpus.json').read_text(encoding='utf-8'))
    if len(queries) != 5703 or len(corpus) != 5832:
        raise ValueError('Incomplete inputs')
    report = {'status': 'running', 'started_at': datetime.now(timezone.utc).isoformat(),
              'scope': 'pooled_reference_english_retrieval_only', 'external_model_calls': 0,
              'manifest': manifest, 'code_sha256': {n: digest(ROOT / n) for n in [
                  'benchmarks/finder_retrieval.py', 'scripts/prepare_finder_retrieval.py',
                  'docs/FINDER_RETRIEVAL_PROTOCOL.md', 'backend/app/retrieval_vectors.py',
                  'docs/retrieval-model-v1.json']}, 'forbidden_access_attempts': []}

    def save():
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')

    scoring = False

    def guard(event, args):
        if event in {'socket.connect', 'socket.getaddrinfo'}:
            report['forbidden_access_attempts'].append(event)
            raise RuntimeError('No network during experiment')
        if event == 'open' and isinstance(args[0], (str, bytes)) and not scoring:
            name = str(args[0])
            if 'labels.json' in name or '.parquet' in name:
                report['forbidden_access_attempts'].append('truth_open')
                raise RuntimeError('Labels unavailable before predictions finish')

    save()
    sys.addaudithook(guard)
    model = OfflineBGE(models)
    report['engine'] = model.engine
    segments = model.segments([{'chunk_id': r['id'], 'source_id': r['id'],
                                'framework': 'BOTH', 'text': r['text']} for r in corpus])
    parent_ids = [r['id'] for r in corpus]
    parent_lookup = {p: i for i, p in enumerate(parent_ids)}
    parent_indices = np.array([parent_lookup[s['parent_chunk_id']] for s in segments])
    report['segments'] = len(segments)
    report['embedded_segments'] = 0
    save()
    vectors = []
    for start in range(0, len(segments), 128):
        batch = model.encode([s['text'] for s in segments[start:start + 128]])
        np.save(output / f'vectors-{start:06d}.npy', batch, allow_pickle=False)
        vectors.append(batch)
        report['embedded_segments'] += len(batch)
        save()
        print('embedded', report['embedded_segments'], '/', len(segments), flush=True)
    vectors = np.concatenate(vectors)
    qvectors = model.encode([q['text'] for q in queries], queries=True)
    np.save(output / 'query-vectors.npy', qvectors, allow_pickle=False)
    tokenizer = CountVectorizer(token_pattern=r'(?u)[a-z0-9]+', lowercase=True, dtype=np.float64)
    counts = tokenizer.fit_transform([s['text'] for s in segments])
    qcounts = tokenizer.transform([q['text'] for q in queries])
    tfidf = TfidfTransformer()
    documents_tfidf = tfidf.fit_transform(counts)
    queries_tfidf = tfidf.transform(qcounts)
    binary = counts.copy()
    binary.data[:] = 1
    qbinary = qcounts.copy()
    qbinary.data[:] = 1
    lengths = np.asarray(counts.sum(axis=1)).ravel()
    frequencies = np.asarray(binary.sum(axis=0)).ravel()
    idf = np.log1p((len(segments) - frequencies + .5) / (frequencies + .5))
    bm25 = counts.copy()
    factors = 1.2 * (1 - .75 + .75 * lengths / lengths.mean())
    bm25.data = (bm25.data * 2.2 / (bm25.data + np.repeat(factors, np.diff(bm25.indptr)))
                 * idf[bm25.indices])

    def parent_order(scores):
        aggregated = np.full(len(corpus), -np.inf)
        np.maximum.at(aggregated, parent_indices, scores)
        return np.argsort(-aggregated, kind='stable'), aggregated

    predictions = []
    report['predicted_queries'] = 0
    save()
    started = time.perf_counter()
    with (output / 'predictions.jsonl').open('x', encoding='utf-8') as target:
        for i, query in enumerate(queries):
            row = {'id': query['id']}
            try:
                scores = {
                    'keyword': (binary @ qbinary[i].T).toarray().ravel(),
                    'bm25': (bm25 @ qbinary[i].T).toarray().ravel(),
                    'tfidf': (documents_tfidf @ queries_tfidf[i].T).toarray().ravel(),
                    'bge': vectors @ qvectors[i],
                }
                rankings = {}
                for method, value in scores.items():
                    order, aggregated = parent_order(value)
                    rankings[method] = order
                    selected = [j for j in order if method != 'keyword' or aggregated[j] > 0][:6]
                    row[method] = [parent_ids[j] for j in selected]
                reciprocal = np.zeros(len(corpus))
                for method in ('bm25', 'bge'):
                    reciprocal[rankings[method]] += 1 / (60 + np.arange(1, len(corpus) + 1))
                row['rrf'] = [parent_ids[j] for j in np.argsort(-reciprocal, kind='stable')[:6]]
            except Exception as exc:
                row.update({m: [] for m in METHODS})
                row['error'] = {'type': type(exc).__name__, 'message': str(exc)[:200]}
            target.write(json.dumps(row) + '\n')
            target.flush()
            predictions.append(row)
            if len(predictions) % 100 == 0:
                report['predicted_queries'] = len(predictions)
                save()
                print('predicted', len(predictions), '/', len(queries), flush=True)
    report.update(status='predicted_not_scored', predicted_queries=len(predictions),
                  predictions_sha256=digest(output / 'predictions.jsonl'),
                  prediction_seconds=time.perf_counter() - started)
    save()
    scoring = True
    if digest(inputs / 'labels.json') != manifest['files']['labels.json']:
        raise ValueError('Changed labels')
    labels = json.loads((inputs / 'labels.json').read_text(encoding='utf-8'))
    result = analyse(predictions, labels)
    report.update(result, status='completed_with_failures' if result['failures'] else 'completed',
                  finished_at=datetime.now(timezone.utc).isoformat())
    save()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['inputs', 'models', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.inputs, args.models, args.output)['comparison'], indent=2))
