"""Recheck persisted synthetic OCR scores and hashes; never run inference."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmarks.ocr_images import digest, evaluate_prediction, resolve_file  # noqa: E402

SOURCE_FILES = ('benchmarks/ocr_images.py', 'backend/app/ocr_observations.py', 'docs/ocr-model-sources.json')
TOTAL_KEYS = ('literal_hits', 'literal_total', 'line_hits', 'line_total', 'blank_correct')


def summarize(report_path, dataset):
    report = json.loads(report_path.read_text(encoding='utf-8'))
    manifest = json.loads((dataset / 'manifest.json').read_text(encoding='utf-8'))
    if report['dataset_sha256'] != digest(dataset / 'manifest.json'):
        raise ValueError('Dataset manifest changed')
    if report['status'] not in {'completed', 'completed_with_failures'}:
        raise ValueError('OCR report did not finish scoring')
    if len(report['cases']) != len(manifest['cases']):
        raise ValueError('Incomplete OCR report')
    hashes = {name.replace('\\', '/'): value for name, value in report['source_sha256'].items()}
    if set(hashes) != set(SOURCE_FILES):
        raise ValueError('Missing or unexpected evaluation source hashes')
    for source in SOURCE_FILES:
        if digest(ROOT / source) != hashes[source]:
            raise ValueError('Evaluation sources changed; retain historical report and make a new run')
    failed = sum('error' in row for row in report['cases'])
    if report['failed_cases'] != failed or report['status'] != ('completed_with_failures' if failed else 'completed'):
        raise ValueError('Failure count and completion status disagree')
    if report['ledger_writes'] != 0 or report['external_model_requests'] != 0:
        raise ValueError('Evaluation has unexpected external side effects')
    for case, row in zip(manifest['cases'], report['cases']):
        if row['case_id'] != case['case_id']:
            raise ValueError('Case order mismatch')
        if ('prediction' in row) == ('error' in row):
            raise ValueError('Each case must retain either a prediction or a failure')
        for field in ('image', 'truth'):
            if digest(resolve_file(dataset, case[field])) != case[field + '_sha256']:
                raise ValueError('Evaluation input changed')
        truth = json.loads(resolve_file(dataset, case['truth']).read_text(encoding='utf-8'))
        if evaluate_prediction(row.get('prediction'), truth) != row['scores']:
            raise ValueError('Saved scores differ from independent truth')
        if 'prediction' in row:
            prediction = row['prediction']
            if (prediction['source_sha256'] != case['image_sha256']
                    or prediction['confirmation'] != 'unconfirmed' or prediction['ledger_writes'] != 0
                    or any(line['confirmation'] != 'unconfirmed' for line in prediction['observations'])):
                raise ValueError('Observation boundary violated')
    totals = {key: sum(row['scores'][key] for row in report['cases']) for key in TOTAL_KEYS}
    if totals != report['totals']:
        raise ValueError('Saved total differs')
    return {'schema_version': 'ocr-synthetic-summary-v1', 'report_sha256': digest(report_path),
            'dataset_sha256': report['dataset_sha256'], 'source_sha256': report['source_sha256'],
            'status': report['status'], 'cases': len(report['cases']), 'failed_cases': report['failed_cases'],
            'totals': totals, 'engine': report['engine'], 'duration_seconds': report['duration_seconds'],
            'network_attempts_blocked': report['network_attempts_blocked'],
            'external_model_requests': report['external_model_requests'], 'ledger_writes': report['ledger_writes'],
            'scope': report['scope'], 'limitations': [
                'Six public synthetic printed scan fixtures, not independent real-document accuracy.',
                'Exact text matching ignores whitespace only; no punctuation or digit error correction.',
                'Literal-token presence does not prove field assignment or financial facts.',
                'No automatic ledger import, human review workflow, scanned PDF or handwriting acceptance.',
                'All text, including high confidence output, remains unconfirmed.',
            ]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.report, args.dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, indent=2)
        handle.write('\n')
    print(json.dumps({'status': result['status'], 'totals': result['totals']}))
