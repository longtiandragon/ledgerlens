import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.connectors import fetch_erpnext_gl

parser = argparse.ArgumentParser(
    description='Read-only ERPNext GL export for a CNY company; token needs Company and GL Entry read access.',
    epilog='Pause source posting during export. Amounts with missing values or sub-cent precision are rejected; '
           'stock/order/invoice evidence is not inferred. The result must pass import preview before confirmation.')
parser.add_argument('--url', required=True)
parser.add_argument('--company', required=True)
parser.add_argument('--from-date', required=True)
parser.add_argument('--to-date', required=True)
parser.add_argument('--account-mapping', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
result = asyncio.run(fetch_erpnext_gl(args.url, os.environ['ERPNEXT_API_KEY'], os.environ['ERPNEXT_API_SECRET'],
    args.company, args.from_date, args.to_date, json.loads(args.account_mapping.read_text(encoding='utf-8'))))
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'Exported {len(result["records"])} GL records in verified company currency {result["currency"]}; '
      'import through preview for validation.')
