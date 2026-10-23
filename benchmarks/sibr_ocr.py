"""Full SIBR test OCR: all predictions first, independent labels only during scoring.

Primary measure: normalized exact segment text plus axis-aligned bounding-box
IoU >= 0.5, with one-to-one maximum matching. This is stricter than substring
presence and is not semantic entity extraction, table reconstruction or posting.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(text):
    return ''.join(unicodedata.normalize('NFKC', text).split())


def bounds(points):
    return min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)


def iou(a, b):
    x1, y1, x2, y2 = bounds(a)
    u1, v1, u2, v2 = bounds(b)
    intersection = max(0, min(x2, u2) - max(x1, u1)) * max(0, min(y2, v2) - max(y1, v1))
    union = (x2 - x1) * (y2 - y1) + (u2 - u1) * (v2 - v1) - intersection
    return intersection / union if union > 0 else 0


def match_segments(truth, observations, *, localized):
    candidates = {}
    for i, item in enumerate(truth):
        candidates[i] = [j for j, row in enumerate(observations)
                         if normalized(item['text']) == normalized(row['text'])
                         and (not localized or iou(item['box'], row['locator']['polygon_px']) >= .5)]
    assigned = {}

    def assign(i, seen):
        for j in candidates[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in assigned or assign(assigned[j], seen):
                assigned[j] = i
                return True
        return False

    for i in candidates:
        assign(i, set())
    return set(assigned.values())


def score_document(prediction, truth):
    segments = [row for row in truth['form'] if normalized(row['text']) and row['text'] != '###']
    observations = prediction.get('observations', []) if prediction else []
    located = match_segments(segments, observations, localized=True)
    text = match_segments(segments, observations, localized=False)
    labels = {}
    for label in sorted({x['label'] for x in segments}):
        indices = {i for i, row in enumerate(segments) if row['label'] == label}
        labels[label] = dict(total=len(indices), localized_exact=len(indices & located), text_exact=len(indices & text))
    return dict(total=len(segments), localized_exact=len(located), text_exact=len(text), labels=labels,
                contains_han=any('\u4e00' <= c <= '\u9fff' for row in segments for c in row['text']))


def evaluate(dataset, models, output):
    manifest_path = dataset / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    items = manifest['items']
    if len(items) != 400 or len({x['id'] for x in items}) != 400 or manifest['split'] != 'publisher_test':
        raise ValueError('Complete publisher test split required')
    output.mkdir(parents=True, exist_ok=False)
    predictions_dir = output / 'predictions'
    predictions_dir.mkdir()
    report = dict(schema_version='sibr-ocr-full-test-v1', status='running',
                  started_at=datetime.now(timezone.utc).isoformat(), dataset_manifest_sha256=digest(manifest_path),
                  protocol_sha256=digest(ROOT / 'docs/SIBR_OCR_PROTOCOL.md'),
                  code_sha256={p: digest(ROOT / p) for p in ['benchmarks/sibr_ocr.py', 'backend/app/ocr_observations.py',
                                                            'docs/ocr-model-sources.json']},
                  cases=[], external_model_requests=0, ledger_writes=0, forbidden_access_attempts=[])

    def save():
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    scoring = False

    def guard(event, args):
        if event in {'socket.connect', 'socket.getaddrinfo'}:
            report['forbidden_access_attempts'].append(event)
            raise RuntimeError('No network during OCR evaluation')
        if event == 'open' and isinstance(args[0], (str, bytes)) and not scoring:
            if '/truth/' in str(args[0]).replace('\\', '/'):
                report['forbidden_access_attempts'].append('truth_read_before_all_predictions')
                raise RuntimeError('Annotations are evaluator-only')

    save()
    sys.addaudithook(guard)
    from app.ocr_observations import LocalOCR
    ocr = LocalOCR(models, ROOT / '.cache/ocr')
    report['engine'] = ocr.engine
    started = time.perf_counter()
    for index, item in enumerate(items):
        path = dataset / item['image']
        row = dict(id=item['id'], image_sha256=item['image_sha256'])
        begin = time.perf_counter()
        try:
            if digest(path) != item['image_sha256']:
                raise ValueError('Input changed')
            prediction = ocr.parse(path)
            target = predictions_dir / (item['id'] + '.json')
            target.write_text(json.dumps(prediction, ensure_ascii=False), encoding='utf-8')
            row.update(prediction=target.name, prediction_sha256=digest(target), status=prediction['status'])
        except Exception as exc:
            row.update(status='failed', error=dict(type=type(exc).__name__, message=str(exc)[:200]))
        row['seconds'] = round(time.perf_counter() - begin, 3)
        report['cases'].append(row)
        save()
        print(f"{index + 1}/400 {item['id']} {row['status']} {row['seconds']}s", flush=True)
    report['status'] = 'predicted_not_scored'
    save()
    scoring = True
    totals = Counter()
    by_label = {}
    by_source_prefix = {}
    for item, row in zip(items, report['cases'], strict=True):
        truth = dataset / item['truth']
        if digest(truth) != item['truth_sha256']:
            raise ValueError('Annotations changed after preparation')
        prediction = None
        if 'prediction' in row:
            path = predictions_dir / row['prediction']
            if digest(path) != row['prediction_sha256']:
                raise ValueError('Prediction changed')
            prediction = json.loads(path.read_text(encoding='utf-8'))
        score = score_document(prediction, json.loads(truth.read_text(encoding='utf-8')))
        row['score'] = score
        for key in ('total', 'localized_exact', 'text_exact'):
            totals[key] += score[key]
        for label, counts in score['labels'].items():
            by_label.setdefault(label, Counter()).update(counts)
        group = by_source_prefix.setdefault(item['id'].split('-')[0], Counter())
        group.update({k: score[k] for k in ('total', 'localized_exact', 'text_exact')})
        group['documents'] += 1
        group['failed'] += row['status'] == 'failed'
    report.update(status='completed_with_failures' if any(x['status'] == 'failed' for x in report['cases']) else 'completed',
                  finished_at=datetime.now(timezone.utc).isoformat(), seconds=round(time.perf_counter() - started, 3),
                  summary=dict(documents=400, failed=sum(x['status'] == 'failed' for x in report['cases']),
                               **totals, by_label=by_label, by_source_prefix=by_source_prefix),
                  limitations=['Literal OCR segment recovery, not entity linking, field accuracy, or invoice validation.',
                               'Axis-aligned box IoU, not official SIBR semantic VIE score.',
                               'Line merges/splits are strict mismatches; no answer-guided postprocessing.',
                               'No comparison against a deterministic OCR alternative; no relative AI-gain claim.',
                               'Pretraining overlap for the existing OCR model is unknown.',
                               'All images and annotations remain local.'])
    save()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.dataset, args.models, args.output)
    print(json.dumps(result['summary'], indent=2))
