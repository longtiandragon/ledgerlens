"""Frozen synthetic-scan evaluation, with all network access denied in-process.

All predictions are persisted before opening evaluator truth. Literal substring
recovery is NOT field extraction or verified money, and errors stay in denominator.
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


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resolve_file(root, relative):
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Dataset file outside its root')
    return path


def compact(text):
    return ''.join(text.split())


def evaluate_prediction(prediction, truth):
    if not prediction:
        return {'literal_hits': 0, 'literal_total': len(truth['literal_tokens']),
                'line_hits': 0, 'line_total': len(truth['lines']), 'blank_correct': False}
    observed = compact(''.join(row['text'] for row in prediction['observations']))
    return {'literal_hits': sum(compact(token) in observed for token in truth['literal_tokens']),
            'literal_total': len(truth['literal_tokens']),
            'line_hits': sum(compact(line) in observed for line in truth['lines']),
            'line_total': len(truth['lines']),
            'blank_correct': not truth['lines'] and not prediction['observations']}


def run(dataset, model_root, output, cache_dir):
    manifest_path = dataset / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest['schema_version'] != 'synthetic-ocr-v1':
        raise ValueError('Unknown scan dataset')
    cases = manifest['cases']
    images = []
    for case in cases:
        path = resolve_file(dataset, case['image'])
        if digest(path) != case['image_sha256']:
            raise ValueError('Changed input image')
        images.append(path)
    report = {'schema_version': 'ocr-synthetic-evaluation-v1', 'status': 'running',
              'started_at': datetime.now(timezone.utc).isoformat(), 'dataset_sha256': digest(manifest_path),
              'source_sha256': {str(p.relative_to(ROOT)): digest(p) for p in
                                (Path(__file__), ROOT / 'backend/app/ocr_observations.py',
                                 ROOT / 'docs/ocr-model-sources.json')},
              'cases': [], 'external_model_requests': 0, 'ledger_writes': 0,
              'network_attempts_blocked': 0, 'scope': 'synthetic_printed_scans_not_real_documents_or_verified_fields'}
    output.parent.mkdir(parents=True, exist_ok=True)
    # Claim this run path before initializing any model. Never replace older runs.
    with output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle)

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

    def offline(event, args):
        if event in {'socket.connect', 'socket.getaddrinfo'}:
            report['network_attempts_blocked'] += 1
            raise RuntimeError('Network access prohibited during OCR evaluation')

    sys.addaudithook(offline)
    started = perf_counter()
    try:
        from app.ocr_observations import LocalOCR
        ocr = LocalOCR(model_root, cache_dir)
        report['engine'] = ocr.engine
        for case, path in zip(cases, images):
            row = {'case_id': case['case_id'], 'image_sha256': case['image_sha256']}
            begin = perf_counter()
            try:
                row['prediction'] = ocr.parse(path)
                if digest(path) != case['image_sha256']:
                    raise ValueError('Input changed during inference')
            except Exception as exc:
                row.pop('prediction', None)
                row['error'] = {'type': type(exc).__name__, 'message': str(exc)[:250]}
            row['duration_seconds'] = perf_counter() - begin
            report['cases'].append(row)
            save()
        report['status'] = 'predicted_not_scored'
        save()
        # Only this evaluator section can open truth, after every model output.
        for case, row in zip(cases, report['cases']):
            truth = resolve_file(dataset, case['truth'])
            if digest(truth) != case['truth_sha256']:
                raise ValueError('Changed evaluation truth')
            row['scores'] = evaluate_prediction(row.get('prediction'), json.loads(truth.read_text(encoding='utf-8')))
        report['failed_cases'] = sum('error' in row for row in report['cases'])
        report['status'] = 'completed_with_failures' if report['failed_cases'] else 'completed'
        report['totals'] = {key: sum(row['scores'][key] for row in report['cases'])
                            for key in ('literal_hits', 'literal_total', 'line_hits', 'line_total', 'blank_correct')}
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)[:350]}
    finally:
        report['duration_seconds'] = perf_counter() - started
        save()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, default=ROOT / '.cache/ocr')
    args = parser.parse_args()
    result = run(args.dataset, args.models, args.output, args.cache)
    print(json.dumps({key: result.get(key) for key in ('status', 'failed_cases', 'totals', 'error', 'network_attempts_blocked')}))
    raise SystemExit(int(result['status'] != 'completed'))
