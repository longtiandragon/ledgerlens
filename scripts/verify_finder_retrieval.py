"""Verify completed evidence; never open relevance labels for a running trial."""
import argparse
import hashlib
import json
from math import isclose
from pathlib import Path

METHODS = ('keyword', 'bm25', 'tfidf', 'bge', 'rrf')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(inputs, run, root):
    report = json.loads((run / 'report.json').read_text(encoding='utf-8'))
    if report['status'] not in {'completed', 'completed_with_failures'}:
        return {'status': 'pending', 'reason': 'Predictions not complete; labels were not opened'}
    manifest = json.loads((inputs / 'manifest.json').read_text(encoding='utf-8'))
    if manifest != report['manifest']:
        raise ValueError('Input manifest changed')
    for name, expected in manifest['files'].items():
        if digest(inputs / name) != expected:
            raise ValueError('Input hash changed: ' + name)
    for name, expected in report['code_sha256'].items():
        if digest(root / name) != expected:
            raise ValueError('Frozen code changed: ' + name)
    if digest(run / 'predictions.jsonl') != report['predictions_sha256']:
        raise ValueError('Prediction hash changed')
    queries = json.loads((inputs / 'queries.json').read_text(encoding='utf-8'))
    labels = json.loads((inputs / 'labels.json').read_text(encoding='utf-8'))
    parents = {r['id'] for r in json.loads((inputs / 'corpus.json').read_text(encoding='utf-8'))}
    predictions = [json.loads(line) for line in (run / 'predictions.jsonl').read_text(encoding='utf-8').splitlines()]
    ids = [r['id'] for r in queries]
    if len(ids) != 5703 or len(set(ids)) != 5703 or len(parents) != 5832:
        raise ValueError('Unexpected input coverage')
    if [r['id'] for r in predictions] != ids or [r['id'] for r in labels] != ids:
        raise ValueError('Prediction coverage mismatch')
    totals = {m: [0., 0., 0., 0.] for m in METHODS}
    for row, label in zip(predictions, labels, strict=True):
        truth = set(label['references'])
        if not truth or not truth <= parents:
            raise ValueError('Invalid relevance label')
        for method in METHODS:
            hits = row[method]
            if len(hits) > 6 or len(set(hits)) != len(hits) or not set(hits) <= parents:
                raise ValueError('Invalid prediction list')
            relevant = set(hits) & truth
            first = next((rank for rank, hit in enumerate(hits, 1) if hit in truth), None)
            values = [len(relevant) / len(truth), float(first == 1), float(first is not None),
                      1 / first if first else 0]
            totals[method] = [a + b for a, b in zip(totals[method], values, strict=True)]
    for method in METHODS:
        for metric, value in zip(('recall_at_6', 'hit_at_1', 'hit_at_6', 'mrr_at_6'),
                                 totals[method], strict=True):
            if not isclose(value / len(ids), report['summary'][method][metric], abs_tol=1e-12):
                raise ValueError('Metric mismatch')
    failures = sum('error' in row for row in predictions)
    if failures != report['failures'] or report['forbidden_access_attempts'] or report['external_model_calls']:
        raise ValueError('Failure or isolation accounting mismatch')
    strongest = max(METHODS[:3], key=lambda m: report['summary'][m]['recall_at_6'])
    if strongest != report['strongest_deterministic']:
        raise ValueError('Wrong comparator')
    for method in ('bge', 'rrf'):
        comparison = report['comparison'][method]
        delta = report['summary'][method]['recall_at_6'] - report['summary'][strongest]['recall_at_6']
        if not isclose(delta, comparison['delta'], abs_tol=1e-12):
            raise ValueError('Wrong delta')
        supported = delta >= .05 and comparison['adjusted_interval'][0] > 0 and failures == 0
        if supported != comparison['supported']:
            raise ValueError('Incorrect support decision')
    return {'status': 'verified', 'queries': len(ids), 'parents': len(parents), 'failures': failures,
            'report_sha256': digest(run / 'report.json'),
            'scope': 'Byte consistency and independent descriptive metric recount; not execution attestation',
            'bootstrap': 'Intervals not independently recomputed by this verifier'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.inputs, args.run, Path(__file__).resolve().parents[1]), indent=2))
