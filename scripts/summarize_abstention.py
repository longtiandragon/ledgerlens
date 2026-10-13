"""Recheck frozen inputs, saved predictions, development fit and held-out metrics."""
import argparse
import hashlib
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'backend'))
from benchmarks.reference_abstention import METHODS, SOURCE_FILES, calibrate, digest, evaluate, gate, read, save_new  # noqa: E402
from app.knowledge import load_verified_corpus  # noqa: E402
from app.retrieval_vectors import eligible  # noqa: E402


def require(condition, message):
    if not condition:
        raise ValueError(message)


def summarize(args):
    plan = read(args.plan)
    root = args.run
    report = read(root / 'report.json')
    require(report['schema_version'] == 'reference-abstention-result-v1', 'Unexpected report contract')
    require(report['status'] in {'completed', 'completed_with_failures'}, 'Incomplete experiment')
    require(report['plan_sha256'] == digest(args.plan), 'Changed plan')
    require(set(report['source_sha256']) == set(SOURCE_FILES), 'Incomplete source provenance')
    require(report['source_sha256'] == plan['source_sha256'], 'Changed implementation identity')
    for name, expected in plan['source_sha256'].items():
        require(digest(ROOT / name) == expected, 'Changed source file')
    for name, expected in plan['input_sha256'].items():
        require(digest(ROOT / name) == expected, 'Changed input file')
    require(report['model_requests'] == report['ledger_writes'] == 0, 'Unexpected side effects')
    require(report['engine_scope'] == 'Local BGE exact NumPy cosine; no PostgreSQL claim', 'Changed engine scope')
    corpus = load_verified_corpus(ROOT / plan['assets']['corpus'], registry=ROOT / plan['assets']['registry'],
                                  source_root=args.source_root)
    parents = {c['chunk_id']: c for c in corpus}
    segments = report['segments']
    by_id = {s['segment_id']: s for s in segments}
    require(len(parents) == report['parent_chunks'] and len(by_id) == len(segments), 'Invalid corpus counts')
    for s in segments:
        parent = parents[s['parent_chunk_id']]
        start, end = s['start'], s['end']
        require(type(start) is type(end) is int and 0 <= start < end <= len(parent['text']), 'Invalid segment coordinates')
        require(s['text'] == parent['text'][start:end], 'Changed source text')
        require(s['segment_id'] == hashlib.sha256(f'{parent["chunk_id"]}:{start}:{end}'.encode()).hexdigest(), 'Changed segment ID')
        require(all(s[k] == parent.get(k) for k in ('source_id', 'framework', 'effective_from', 'effective_to')), 'Changed source scope')
    predictions, labels = {}, {}
    for split in ('dev', 'heldout'):
        predictions[split] = read(root / (split + '-predictions.json'))
        require(report['cases'][split] == predictions[split], 'Changed saved predictions')
        queries = read(ROOT / plan['splits'][split]['queries'])
        truth = read(ROOT / plan['splits'][split]['truth'])['labels']
        labels[split] = {label['id']: label for label in truth}
        require(len(truth) == len(labels[split]) == len(predictions[split]) == len(queries['queries']), 'Missing queries or labels')
        for query, row in zip(queries['queries'], predictions[split]):
            require((row['query_id'], row['query'], row['framework']) == (query['id'], query['text'], query['framework']), 'Changed query')
            for method in METHODS:
                hits = row[method]
                require(len(hits) <= 6 and len({h['segment_id'] for h in hits}) == len(hits), 'Invalid ranking size')
                require(not hits or method + '_error' not in row, 'Failed query returned successful candidates')
                require(all(type(h['score']) in (float, int) and math.isfinite(h['score']) for h in hits), 'Invalid score')
                require(all(hits[i]['score'] >= hits[i+1]['score'] for i in range(len(hits)-1)), 'Unordered ranking')
                require(all(eligible(by_id[h['segment_id']], query['framework'], queries['period']) for h in hits), 'Out of scope hit')
    thresholds = read(root / 'thresholds.json')
    require(digest(root / 'thresholds.json') == report['thresholds_sha256'], 'Changed thresholds artifact')
    require(thresholds['plan_sha256'] == report['plan_sha256'], 'Thresholds belong to another plan')
    require(thresholds['dev_predictions_sha256'] == digest(root / 'dev-predictions.json'), 'Thresholds use different development predictions')
    methods = {}
    for method in METHODS:
        fitted = calibrate(predictions['dev'], labels['dev'], segments, method)
        require(fitted == thresholds['methods'][method], 'Threshold was not fitted exclusively on development cases')
        baseline, _, _ = evaluate(predictions['heldout'], labels['heldout'], segments, method, None)
        metrics, decisions, _ = evaluate(predictions['heldout'], labels['heldout'], segments, method, fitted['threshold'])
        expected = {**fitted, 'heldout_baseline': baseline, 'heldout': metrics, 'decisions': decisions, 'candidate_meets_gate': gate(metrics)}
        require(expected == report['methods'][method], 'Changed metrics or hidden failed gate')
        methods[method] = expected
    failed = any(methods[m]['heldout']['failures'] for m in METHODS)
    require(report['status'] == ('completed_with_failures' if failed else 'completed'), 'Completion hides failures')
    return {'schema_version': 'reference-abstention-summary-v1', 'status': report['status'], 'date': '2026-10-13',
            'source_commit_before_predictions': '8dc793e', 'plan_sha256': report['plan_sha256'],
            'report_sha256': digest(root / 'report.json'), 'source_sha256': report['source_sha256'],
            'input_sha256': plan['input_sha256'], 'engine_scope': report['engine_scope'],
            'parent_chunks': len(parents), 'segments': len(segments), 'seconds': report['seconds'],
            'methods': methods, 'model_requests': 0, 'ledger_writes': 0, 'default_changed': False,
            'evidence_sha256': {p.name: digest(p) for p in sorted(root.glob('*.json'))},
            'limitations': ['Implementer-authored synthetic queries, not independent expert validation',
                'Top-score threshold is not a semantic answerability guarantee', 'No PostgreSQL run in this experiment',
                'No prompt/model/window changes and no tuning after held-out scores', 'Neither gate pass nor retrieval hit establishes professional applicability']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('plan', 'run', 'source-root', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    summary = summarize(args)
    save_new(args.output, summary)
    print({method: {'gate': summary['methods'][method]['candidate_meets_gate'],
                    'heldout': summary['methods'][method]['heldout']} for method in METHODS})
