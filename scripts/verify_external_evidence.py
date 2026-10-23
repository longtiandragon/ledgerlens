"""Check retained public experiment artifacts; running jobs remain explicitly pending.

Hashes establish consistency with these retained bytes, not historical execution,
absence of model pretraining contamination, or professional accounting validity.
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmarks.external_metrics import classification_metrics  # noqa: E402


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_hash(path, expected):
    if digest(path) != expected:
        raise ValueError('Changed artifact: ' + str(path))


def check_codes(report):
    for relative, expected in report['code_sha256'].items():
        check_hash(ROOT / relative, expected)


def finrca(root):
    report = read(root / 'finrca-audit-v1/report.json')
    check_codes(report)
    source = root / 'finrca'
    pin_path = ROOT / 'docs/external-finrca-source-pin.json'
    check_hash(pin_path, report['source_manifest_sha256'])
    for relative, expected in read(pin_path)['files'].items():
        check_hash(source / relative, expected)
    predictions_path = root / 'finrca-audit-v1/predictions.jsonl'
    check_hash(predictions_path, report['prediction_sha256'])
    truth_path = source / 'data/benchmark/test/failure_manifest.csv'
    check_hash(truth_path, report['truth_sha256'])
    predictions = [json.loads(line) for line in predictions_path.read_text(encoding='utf-8').splitlines()]
    with truth_path.open(encoding='utf-8', newline='') as stream:
        truth = list(csv.DictReader(stream))
    if len(predictions) != 439 or len({r['case_id'] for r in predictions}) != 439:
        raise ValueError('Wrong FinRCA population')
    result = classification_metrics({r['case_id']: r['failure_type'] for r in truth},
                                    {r['case_id']: r['predicted_failure_type'] for r in predictions},
                                    list(report['metrics']['per_class']))
    if result != report['metrics'] or report['forbidden_inference_reads']:
        raise ValueError('FinRCA metric/access mismatch')
    return {'status': 'verified', 'cases': 439, 'macro_f1': result['macro_f1']}


def sibr(root):
    report = read(root / 'sibr-ocr-full-v1/report.json')
    check_codes(report)
    if report['status'] not in {'completed', 'completed_with_failures'}:
        return {'status': 'pending', 'predictions': len(report['cases']), 'expected': 400}
    # Annotation reads are deliberately unreachable until the full run has ended.
    from benchmarks.sibr_ocr import score_document
    dataset = root / 'sibr-test-v1'
    manifest = read(dataset / 'manifest.json')
    check_hash(dataset / 'manifest.json', report['dataset_manifest_sha256'])
    if len(report['cases']) != 400 or len({r['id'] for r in report['cases']}) != 400:
        raise ValueError('Wrong SIBR population')
    totals = Counter()
    for item, row in zip(manifest['items'], report['cases'], strict=True):
        if item['id'] != row['id']:
            raise ValueError('SIBR routing changed')
        check_hash(dataset / item['image'], item['image_sha256'])
        check_hash(dataset / item['truth'], item['truth_sha256'])
        prediction = None
        if 'prediction' in row:
            path = root / 'sibr-ocr-full-v1/predictions' / row['prediction']
            check_hash(path, row['prediction_sha256'])
            prediction = read(path)
        actual = score_document(prediction, read(dataset / item['truth']))
        if actual != row['score']:
            raise ValueError('SIBR per-case scoring changed')
        totals.update({key: actual[key] for key in ['total', 'localized_exact', 'text_exact']})
    if any(report['summary'][key] != value for key, value in totals.items()) or report['forbidden_access_attempts']:
        raise ValueError('SIBR summary/access mismatch')
    return {'status': 'verified', 'documents': 400, **totals}


def refusal(root):
    report = read(root / 'finlongeval-refusal-v1/report.json')
    check_codes(report)
    if report['status'] not in {'completed', 'completed_with_failures'}:
        return {'status': 'pending', 'predictions': len(report['cases']), 'expected': 80}
    from benchmarks.finlongeval_refusal import score
    inputs = root / 'finlongeval-input-v1'
    for name, expected in report['inputs_sha256'].items():
        check_hash(inputs / name, expected)
    for name, metadata in read(inputs / 'source-pin.json')['files'].items():
        check_hash(root / 'finlongeval' / name, metadata['sha256'])
    path = root / 'finlongeval-refusal-v1/predictions.jsonl'
    check_hash(path, report['predictions_sha256'])
    cases = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    if len(cases) != 80 or len({r['id'] for r in cases}) != 80 or cases != report['cases']:
        raise ValueError('Wrong FinLongEval population')
    truth = root / 'finlongeval/data/sample_data.jsonl'
    check_hash(truth, report['truth_sha256'])
    labels = [json.loads(line)['question_type'] for line in truth.read_text(encoding='utf-8').splitlines() if line.strip()]
    if score(cases, labels) != report['summary'] or report['forbidden_access_attempts']:
        raise ValueError('FinLongEval scoring/access mismatch')
    return {'status': 'verified', 'cases': 80, 'summary': report['summary']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT / '.local/external-research')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = {}
    for name, verify in [('finrca', finrca), ('sibr', sibr), ('finlongeval', refusal)]:
        try:
            result[name] = verify(args.root)
        except Exception as exc:
            result[name] = {'status': 'failed', 'error': str(exc)}
    result['status'] = ('failed' if any(r['status'] == 'failed' for r in result.values()) else
                        'pending' if any(r['status'] == 'pending' for r in result.values()) else 'verified')
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result['status'] == 'verified' else 2)
