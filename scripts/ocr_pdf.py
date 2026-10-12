"""Local bounded scanned-PDF OCR. No network, ledger import or evaluator truth."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
SOURCE_FILES = ('backend/app/ocr_pdf.py', 'backend/app/ocr_observations.py',
                'scripts/ocr_pdf.py', 'docs/ocr-model-sources.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('input', 'models', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--cache', type=Path, default=ROOT / '.cache/ocr')
    parser.add_argument('--dpi', type=int, default=144)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump({'status': 'running'}, handle)

    def offline(event, arguments):
        if event in {'socket.connect', 'socket.getaddrinfo'}:
            raise RuntimeError('Network access prohibited during local PDF OCR')
    sys.addaudithook(offline)
    try:
        from app.ocr_observations import LocalOCR
        from app.ocr_pdf import parse_pdf
        result = parse_pdf(args.input, LocalOCR(args.models, args.cache), dpi=args.dpi)
    except Exception as exc:
        result = {'status': 'failed', 'error': {'type': type(exc).__name__, 'message': str(exc)[:250]}}
    result['source_sha256_by_file'] = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({'status': result['status'], 'pages': len(result.get('pages', [])),
                      'failed_pages': result.get('failed_pages')}))
    return int(result['status'] in {'failed', 'completed_with_failures'})


if __name__ == '__main__':
    raise SystemExit(main())
