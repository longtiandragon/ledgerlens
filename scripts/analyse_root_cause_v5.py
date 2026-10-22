"""Validate paired v5 reports and score only after every prediction exists."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]

from benchmarks.root_cause_v5_protocol import analyse, read_json  # noqa: E402


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--plans', type=Path, required=True)
    parser.add_argument('--selective', type=Path, required=True)
    parser.add_argument('--comparator', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite results')
    paths = {name: getattr(args, name) for name in ('selective', 'comparator')}
    plans = {name: read_json(args.plans / f'{name}.json') for name in paths}
    result = analyse(args.dataset, {name: read_json(path) for name, path in paths.items()}, plans)
    result['reports'] = {name: {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                         for name, path in paths.items()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(result['preregistered_targets'], ensure_ascii=False, indent=2))
