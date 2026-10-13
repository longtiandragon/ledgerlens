"""OCR on real receipts: CORD v2 test split (CC BY 4.0), frozen local CPU models, network denied.

Every prediction is written before any truth file is opened; failures stay in the denominator.
Scores are literal recovery only — ground-truth words, the receipt total and the item prices
appearing in the recognised text — not field extraction, posting or verified money. CORD
receipts are Indonesian (Latin script), so this measures the shared OCR engine on real
documents, not the project's Chinese table-header extraction.
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


def compact(text):
    return ''.join(str(text).split())


def truth_targets(truth):
    words = [compact(word['text']) for line in truth.get('valid_line', []) for word in line.get('words', [])
             if compact(word.get('text', ''))]
    parse = truth.get('gt_parse', {})
    total = compact((parse.get('total') or {}).get('total_price', '')) if isinstance(parse.get('total'), dict) else ''
    menu = parse.get('menu') or []
    menu = menu if isinstance(menu, list) else [menu]
    prices = [compact(item.get('price', '')) for item in menu if isinstance(item, dict) and compact(item.get('price', ''))]
    return words, total, prices


def score(prediction, truth):
    words, total, prices = truth_targets(truth)
    observed = compact(''.join(row['text'] for row in prediction['observations'])) if prediction else ''
    return {'word_hits': sum(word in observed for word in words), 'word_total': len(words),
            'total_present': bool(total), 'total_hit': bool(total) and total in observed,
            'price_hits': sum(price in observed for price in prices), 'price_total': len(prices)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, default=ROOT / '.cache/ocr')
    options = parser.parse_args()
    manifest = json.loads((options.dataset / 'manifest.json').read_text(encoding='utf-8'))
    report = {'schema_version': 'cord-ocr-evaluation-v1', 'status': 'running',
              'started_at': datetime.now(timezone.utc).isoformat(),
              'dataset': {k: manifest[k] for k in ('dataset', 'revision', 'split', 'license', 'parquet_sha256')},
              'manifest_sha256': digest(options.dataset / 'manifest.json'),
              'source_sha256': {str(p.relative_to(ROOT)): digest(p) for p in
                                (Path(__file__), ROOT / 'backend/app/ocr_observations.py', ROOT / 'docs/ocr-model-sources.json')},
              'cases': [], 'network_attempts_blocked': 0, 'external_model_requests': 0, 'ledger_writes': 0,
              'scope': 'real_receipts_literal_text_recovery_not_field_extraction'}
    options.output.parent.mkdir(parents=True, exist_ok=True)
    with options.output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle)

    def save():
        options.output.write_text(json.dumps(report, ensure_ascii=False, indent=1, allow_nan=False), encoding='utf-8')

    def offline(event, args):
        if event in {'socket.connect', 'socket.getaddrinfo'}:
            report['network_attempts_blocked'] += 1
            raise RuntimeError('Network access prohibited during OCR evaluation')

    sys.addaudithook(offline)
    started = perf_counter()
    try:
        from app.ocr_observations import LocalOCR
        ocr = LocalOCR(options.models, options.cache)
        report['engine'] = ocr.engine
        for item in manifest['items']:
            path = options.dataset / item['image']
            row = {'id': item['id'], 'image_sha256': item['image_sha256']}
            begin = perf_counter()
            try:
                if digest(path) != item['image_sha256']:
                    raise ValueError('Changed input image')
                row['prediction'] = ocr.parse(path)
            except Exception as exc:
                row['error'] = {'type': type(exc).__name__, 'message': str(exc)[:250]}
            row['duration_seconds'] = round(perf_counter() - begin, 2)
            report['cases'].append(row)
            save()
        report['status'] = 'predicted_not_scored'
        save()
        for item, row in zip(manifest['items'], report['cases']):
            truth_path = options.dataset / 'truth' / f"{item['id']}.json"
            raw = truth_path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != item['truth_sha256']:
                raise ValueError('Changed evaluation truth')
            row['scores'] = score(row.get('prediction'), json.loads(raw))
        cases = report['cases']
        report['failed_cases'] = sum('error' in row for row in cases)
        report['totals'] = {key: sum(row['scores'][key] for row in cases)
                            for key in ('word_hits', 'word_total', 'total_present', 'total_hit', 'price_hits', 'price_total')}
        report['status'] = 'completed_with_failures' if report['failed_cases'] else 'completed'
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)[:350]}
    finally:
        report['duration_seconds'] = round(perf_counter() - started, 1)
        save()
    print(json.dumps({k: report.get(k) for k in ('status', 'failed_cases', 'totals', 'duration_seconds')}))


if __name__ == '__main__':
    main()
