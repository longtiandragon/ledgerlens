"""Continue an interrupted full split without repeating completed predictions.

The original report remains untouched. This is a disclosed two-process run,
not a replacement claim that the original uninterrupted run completed.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]
from benchmarks.sibr_ocr import digest, score_document  # noqa: E402


def validate_prefix(prior, manifest):
    cases = prior['cases']
    if prior['status'] != 'running' or not 0 < len(cases) < 400:
        raise ValueError('Only an incomplete prediction run can continue')
    if len(manifest['items']) != 400 or manifest['split'] != 'publisher_test':
        raise ValueError('Wrong population')
    if [row['id'] for row in cases] != [row['id'] for row in manifest['items'][:len(cases)]]:
        raise ValueError('Original predictions are not an exact prefix')
    if any('score' in row for row in cases) or prior['forbidden_access_attempts']:
        raise ValueError('Prior scoring/access violation prevents continuation')
    return len(cases)


def run(dataset, models, prior_dir, output):
    prior_path = prior_dir / 'report.json'
    prior = json.loads(prior_path.read_text(encoding='utf-8'))
    manifest = json.loads((dataset / 'manifest.json').read_text(encoding='utf-8'))
    count = validate_prefix(prior, manifest)
    if digest(dataset / 'manifest.json') != prior['dataset_manifest_sha256']:
        raise ValueError('Dataset changed')
    for relative, expected in prior['code_sha256'].items():
        if digest(ROOT / relative) != expected:
            raise ValueError('Frozen implementation changed')
    if digest(ROOT / 'docs/SIBR_OCR_PROTOCOL.md') != prior['protocol_sha256']:
        raise ValueError('Original protocol changed')
    expected_files = {row['prediction'] for row in prior['cases'] if 'prediction' in row}
    if {p.name for p in (prior_dir / 'predictions').iterdir()} != expected_files:
        raise ValueError('Unrecorded prediction exists; investigate before continuation')
    output.mkdir(parents=True, exist_ok=False)
    predictions = output / 'predictions'
    predictions.mkdir()
    for row in prior['cases']:
        if 'prediction' in row:
            path = prior_dir / 'predictions' / row['prediction']
            if digest(path) != row['prediction_sha256']:
                raise ValueError('Original prediction changed')
            shutil.copyfile(path, predictions / path.name)
    report = prior.copy()
    report['continuation'] = {
        'prior_report_sha256': digest(prior_path), 'preserved_cases': count,
        'prior_directory': prior_dir.name,
        'script_path': 'scripts/continue_sibr_ocr_v2.py',
        'started_at': datetime.now(timezone.utc).isoformat(),
        'reason': 'Original process absent after interruption; exact prefix retained, no completed-case retry.',
        'script_sha256': digest(Path(__file__)),
        'protocol_path': 'docs/SIBR_OCR_CONTINUATION_V2.md',
        'protocol_sha256': digest(ROOT / 'docs/SIBR_OCR_CONTINUATION_V2.md')}

    def save():
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    scoring = False

    def guard(event, args):
        if event in {'socket.connect', 'socket.getaddrinfo'}:
            report['forbidden_access_attempts'].append(event)
            raise RuntimeError('Network unavailable')
        if event == 'open' and isinstance(args[0], (str, bytes)) and not scoring:
            if '/truth/' in str(args[0]).replace('\\', '/'):
                report['forbidden_access_attempts'].append('truth_read_before_all_predictions')
                raise RuntimeError('Annotations unavailable until all predictions complete')

    save()
    sys.addaudithook(guard)
    from app.ocr_observations import LocalOCR
    ocr = LocalOCR(models, ROOT / '.cache/ocr')
    if ocr.engine != prior['engine']:
        raise ValueError('OCR engine differs from original process')
    for item in manifest['items'][count:]:
        begin = time.perf_counter()
        row = {'id': item['id'], 'image_sha256': item['image_sha256']}
        try:
            path = dataset / item['image']
            if digest(path) != item['image_sha256']:
                raise ValueError('Input changed')
            prediction = ocr.parse(path)
            target = predictions / (item['id'] + '.json')
            target.write_text(json.dumps(prediction, ensure_ascii=False), encoding='utf-8')
            row.update(prediction=target.name, prediction_sha256=digest(target), status=prediction['status'])
        except Exception as exc:
            row.update(status='failed', error={'type': type(exc).__name__, 'message': str(exc)[:200]})
        row['seconds'] = round(time.perf_counter() - begin, 3)
        report['cases'].append(row)
        save()
        print(f"{len(report['cases'])}/400 {item['id']} {row['status']} {row['seconds']}s", flush=True)
    report['status'] = 'predicted_not_scored'
    save()
    scoring = True
    totals, labels, groups = Counter(), {}, {}
    for item, row in zip(manifest['items'], report['cases'], strict=True):
        truth_path = dataset / item['truth']
        if digest(truth_path) != item['truth_sha256']:
            raise ValueError('Truth changed')
        prediction = None
        if 'prediction' in row:
            path = predictions / row['prediction']
            if digest(path) != row['prediction_sha256']:
                raise ValueError('Prediction changed')
            prediction = json.loads(path.read_text(encoding='utf-8'))
        actual = score_document(prediction, json.loads(truth_path.read_text(encoding='utf-8')))
        row['score'] = actual
        counts = {key: actual[key] for key in ('total', 'localized_exact', 'text_exact')}
        totals.update(counts)
        for label, values in actual['labels'].items():
            labels.setdefault(label, Counter()).update(values)
        group = groups.setdefault(item['id'].split('-')[0], Counter())
        group.update(counts)
        group['documents'] += 1
        group['failed'] += row['status'] == 'failed'
    failed = sum(row['status'] == 'failed' for row in report['cases'])
    report.update(status='completed_with_failures' if failed else 'completed',
                  finished_at=datetime.now(timezone.utc).isoformat(),
                  summary={'documents': 400, 'failed': failed, **totals,
                           'by_label': labels, 'by_source_prefix': groups},
                  limitations=['Interrupted two-process run; retained exact original prefix, no retry.',
                               'Strict segment recovery, not semantic VIE, table accuracy or AI gain.',
                               'Pretraining contamination unknown; no enterprise accounting validation.'])
    save()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('dataset', 'models', 'prior-dir', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.dataset, args.models, args.prior_dir, args.output)['summary'], indent=2))
