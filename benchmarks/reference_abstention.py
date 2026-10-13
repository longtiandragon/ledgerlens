"""Frozen offline top-score abstention experiment; no product default changes.

Predict both splits without labels, calibrate only on developer labels, save the
threshold artifact, then score held-out labels once. No paid model or database.
"""
import argparse
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'backend'))
from benchmarks.reference_retrieval import score_rankings  # noqa: E402

METHODS = ('keyword', 'bge_numpy')
SOURCE_FILES = ('benchmarks/reference_abstention.py', 'benchmarks/reference_retrieval.py',
                'backend/app/retrieval_vectors.py', 'backend/app/knowledge.py',
                'backend/app/reference_sources.py', 'docs/retrieval-model-v1.json')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save_new(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def accepts(row, method, threshold):
    hits = row[method]
    return method + '_error' not in row and bool(hits) and (threshold is None or hits[0]['score'] >= threshold)


def evaluate(cases, labels, segments, method, threshold):
    if len(cases) != len(labels) or {c['query_id'] for c in cases} != set(labels):
        raise ValueError('Incomplete or duplicate predictions')
    result = {'answerable': 0, 'unanswerable': 0, 'accepted_answerable': 0,
              'rejected_unanswerable': 0, 'accepted_unanswerable': 0,
              'correct_hit_at_6_kept': 0, 'correct_hit_at_6_lost': 0, 'failures': 0}
    decisions = []
    for row in cases:
        label = labels[row['query_id']]
        no_answer = label['source_id'] is None
        result['unanswerable' if no_answer else 'answerable'] += 1
        failure = method + '_error' in row
        keep = accepts(row, method, threshold)
        scores = score_rankings(row[method], label, segments)
        hit = bool(scores.get('hit_at_6'))
        result['failures'] += failure
        # Failures are not correct abstentions and remain in all denominators.
        if not failure:
            if no_answer:
                result['accepted_unanswerable' if keep else 'rejected_unanswerable'] += 1
            else:
                result['accepted_answerable'] += keep
                result['correct_hit_at_6_kept' if keep else 'correct_hit_at_6_lost'] += hit
        decisions.append({'query_id': row['query_id'], 'no_answer': no_answer, 'accepted': keep,
                          'failed': failure, 'correct_hit_at_6': hit, 'kind': label['kind']})
    if not result['answerable'] or not result['unanswerable']:
        raise ValueError('Both answerable and absent examples are required')
    balanced = (Fraction(result['accepted_answerable'], result['answerable'])
                + Fraction(result['rejected_unanswerable'], result['unanswerable'])) / 2
    result['balanced_answerability_accuracy'] = float(balanced)
    return result, decisions, balanced


def calibrate(cases, labels, segments, method):
    if any(method + '_error' in row for row in cases):
        raise ValueError('Cannot calibrate on failed development queries')
    scores = sorted({row[method][0]['score'] for row in cases if row[method]})
    if not scores or any(not math.isfinite(s) for s in scores):
        raise ValueError('No finite development scores')
    candidates = [*scores, math.nextafter(scores[-1], math.inf)]
    evaluated = []
    for threshold in candidates:
        metrics, _, balanced = evaluate(cases, labels, segments, method, threshold)
        evaluated.append((balanced, -metrics['accepted_unanswerable'], metrics['correct_hit_at_6_kept'],
                          -threshold, threshold, metrics))
    selected = max(evaluated, key=lambda item: item[:4])
    return {'threshold': selected[4], 'development': selected[5], 'candidates': len(candidates)}


def gate(metrics):
    # Pre-registered for this exact 12-positive/12-negative held-out split.
    return (metrics['answerable'] == 12 and metrics['unanswerable'] == 12 and metrics['failures'] == 0
            and metrics['accepted_unanswerable'] <= 1 and metrics['correct_hit_at_6_kept'] >= 9
            and metrics['correct_hit_at_6_lost'] <= 1)


def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    plan = read(args.plan)
    if plan.get('schema_version') != 'reference-abstention-plan-v1':
        raise ValueError('Unknown frozen plan')
    for key in ('corpus', 'registry'):
        if digest(getattr(args, key)) != plan['input_sha256'][plan['assets'][key]]:
            raise ValueError('Command input differs from frozen plan: ' + key)
    for name, expected in plan['input_sha256'].items():
        if digest(ROOT / name) != expected:
            raise ValueError('Frozen input changed: ' + name)
    for name, expected in plan['source_sha256'].items():
        if digest(ROOT / name) != expected:
            raise ValueError('Frozen implementation changed: ' + name)
    report = {'schema_version': 'reference-abstention-result-v1', 'status': 'running',
              'plan_sha256': digest(args.plan), 'cases': {}, 'model_requests': 0, 'ledger_writes': 0,
              'engine_scope': 'Local BGE exact NumPy cosine; no PostgreSQL claim',
              'source_sha256': plan['source_sha256']}
    started = time.monotonic()
    try:
        from app.knowledge import load_verified_corpus, search
        from app.retrieval_vectors import OfflineBGE, eligible
        import numpy as np
        corpus = load_verified_corpus(args.corpus, registry=args.registry, source_root=args.source_root)
        model = OfflineBGE(args.model_root)
        segments = model.segments(corpus)
        report.update(engine=model.engine, parent_chunks=len(corpus), segments=segments)
        vectors = model.encode([s['text'] for s in segments])
        keyword_corpus = [{**s, 'chunk_id': s['segment_id']} for s in segments]
        for split in ('dev', 'heldout'):
            query_file = ROOT / plan['splits'][split]['queries']
            queries = read(query_file)
            predictions = []
            for query in queries['queries']:
                if set(query) != {'id', 'framework', 'text'}:
                    raise ValueError('Query has non-runtime fields')
                row = {'query_id': query['id'], 'query': query['text'], 'framework': query['framework']}
                for method in METHODS:
                    try:
                        if method == 'keyword':
                            hits = search(keyword_corpus, query['text'], query['framework'], queries['period'], 6)
                        else:
                            vector = model.encode([query['text']], queries=True)[0]
                            hits = [{'segment_id': s['segment_id'], 'score': float(np.dot(vectors[i], vector))}
                                    for i, s in enumerate(segments) if eligible(s, query['framework'], queries['period'])]
                            hits.sort(key=lambda h: (-h['score'], h['segment_id']))
                        row[method] = [{'segment_id': h.get('segment_id', h.get('chunk_id')), 'score': float(h['score'])}
                                       for h in hits[:6]]
                    except Exception as exc:
                        row[method] = []
                        row[method + '_error'] = {'type': type(exc).__name__, 'message': str(exc)[:250]}
                predictions.append(row)
            report['cases'][split] = predictions
            save_new(output / (split + '-predictions.json'), predictions)
            print(split + ': predictions saved before labels', flush=True)
        # Every ranking is persisted before the first label JSON is opened.
        dev = read(ROOT / plan['splits']['dev']['truth'])['labels']
        dev_labels = {label['id']: label for label in dev}
        if len(dev_labels) != len(dev):
            raise ValueError('Duplicate development labels')
        fitted = {method: calibrate(report['cases']['dev'], dev_labels, segments, method) for method in METHODS}
        thresholds = {'plan_sha256': report['plan_sha256'], 'methods': fitted,
                      'dev_predictions_sha256': digest(output / 'dev-predictions.json')}
        save_new(output / 'thresholds.json', thresholds)
        report['thresholds_sha256'] = digest(output / 'thresholds.json')
        # The thresholds cannot use held-out labels; they are now saved separately.
        heldout = read(ROOT / plan['splits']['heldout']['truth'])['labels']
        labels = {label['id']: label for label in heldout}
        if len(labels) != len(heldout):
            raise ValueError('Duplicate held-out labels')
        report['methods'] = {}
        for method in METHODS:
            baseline, _, _ = evaluate(report['cases']['heldout'], labels, segments, method, None)
            metrics, decisions, _ = evaluate(report['cases']['heldout'], labels, segments, method, fitted[method]['threshold'])
            report['methods'][method] = {**fitted[method], 'heldout_baseline': baseline, 'heldout': metrics,
                                         'decisions': decisions, 'candidate_meets_gate': gate(metrics)}
        for name, expected in plan['input_sha256'].items():
            if digest(ROOT / name) != expected:
                raise ValueError('Frozen input changed during evaluation')
        report['status'] = 'completed_with_failures' if any(
            report['methods'][method]['heldout']['failures'] for method in METHODS) else 'completed'
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    finally:
        report['seconds'] = round(time.monotonic() - started, 2)
        save_new(output / 'report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('plan', 'corpus', 'registry', 'source-root', 'model-root', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    report = run(parser.parse_args())
    print(json.dumps({k: v for k, v in report.items() if k in {'status', 'error', 'seconds'}}, ensure_ascii=False))
    raise SystemExit(0 if report['status'] == 'completed' else 1)
