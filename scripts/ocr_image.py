"""Read a local scan with frozen CPU models, exporting only unconfirmed observations."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, default=ROOT / '.cache/ocr')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve output before expensive work. Failure is retained, never empty success.
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump({'status': 'running'}, handle)

    def offline(event, arguments):
        if event in {'socket.connect', 'socket.getaddrinfo'}:
            raise RuntimeError('Network access prohibited during local OCR')

    sys.addaudithook(offline)
    try:
        from app.ocr_observations import LocalOCR
        result = LocalOCR(args.models, args.cache).parse(args.input)
    except Exception as exc:
        result = {'status': 'failed', 'error': {'type': type(exc).__name__, 'message': str(exc)[:250]}}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({'status': result['status'], 'observations': len(result.get('observations', []))}))
    return int(result['status'] == 'failed')


if __name__ == '__main__':
    raise SystemExit(main())
