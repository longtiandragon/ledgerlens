"""Publish a verified local probe as an immutable, read-only report snapshot."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import settings  # noqa: E402
from app.statement_store import save_statement_snapshot  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', type=Path, required=True)
    parser.add_argument('--coverage', type=Path, default=ROOT / 'docs/annual-report-coverage.json')
    args = parser.parse_args()
    result = save_statement_snapshot(settings.statement_datasets_dir, args.probe, args.coverage, settings.annual_reports_dir)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
