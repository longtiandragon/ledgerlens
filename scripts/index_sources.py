import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.knowledge import build_corpus, load_verified_corpus
parser = argparse.ArgumentParser()
parser.add_argument('--report', type=Path, default=Path('runtime/public-sources/acquisition-report.json'))
parser.add_argument('--output', type=Path, default=Path('runtime/knowledge/corpus-v2.json'))
parser.add_argument('--registry', type=Path, help='Independent trusted source registry (default: docs/data-sources.json)')
parser.add_argument('--source-root', type=Path, help='Original source directory (default while building: report parent)')
parser.add_argument('--verify-only', action='store_true', help='Verify an existing v2 artifact without writing it')
args = parser.parse_args()
if args.verify_only:
    chunks = load_verified_corpus(args.output, registry=args.registry, source_root=args.source_root)
    action = 'Verified'
else:
    chunks = build_corpus(args.report, args.output, registry=args.registry, source_root=args.source_root)
    action = 'Indexed'
print(f'{action} {len(chunks)} reference chunks from registered policy originals; evaluator material excluded.')
