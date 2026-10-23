"""Observation-only projection for external FinBalance acquisition, not an evaluator.

No answers, case names, asset paths, document roles or scenario metadata cross
this boundary. Inputs are still untrusted document text, not instructions.
"""
import hashlib
import json
import argparse
from decimal import Decimal, InvalidOperation
from pathlib import Path


def opening_balance(value):
    result = {'date': value['date']}
    for category in ('assets', 'liabilities', 'equity'):
        result[category] = {}
        for account, amount in value[category].items():
            if isinstance(amount, (float, bool)):
                raise ValueError('Parse source amounts with Decimal, never binary float')
            try:
                amount = Decimal(amount)
            except (InvalidOperation, TypeError) as exc:
                raise ValueError('Invalid source amount') from exc
            if not amount.is_finite():
                raise ValueError('Non-finite source amount')
            result[category][account] = format(amount, 'f')
    return result


def project(record, ordinal):
    if not isinstance(ordinal, int) or ordinal < 1:
        raise ValueError('Positive neutral ordinal required')
    documents = []
    seen = set()
    for doc in record['documents']:
        original = doc['doc_id']
        if original in seen:
            raise ValueError('Duplicate document identifiers')
        seen.add(original)
        # Neutral identifiers also avoid answer codes embedded in source IDs.
        documents.append({'document_id': f'doc-{len(documents) + 1:03d}',
                          'text': doc['ocr_text'], 'title': doc['title'], 'date': doc['date'],
                          'type': doc['doc_type']})
    return {'case_id': f'case-{ordinal:04d}',
            'period_start': record['period_start'], 'period_end': record['period_end'],
            'opening_balance': opening_balance(record['opening_balance']),
            'allowed_accounts': record['allowed_accounts'],
            'documents': documents}


def prepare(source: Path, output: Path):
    """Fail on reused output; keep ordinal mapping outside observations for scoring."""
    output.mkdir(parents=True, exist_ok=False)
    records = [json.loads(line, parse_float=Decimal)
               for line in source.read_text(encoding='utf-8').splitlines() if line.strip()]
    if len({r['record_id'] for r in records}) != len(records):
        raise ValueError('Duplicate source records')
    observations = [project(record, index) for index, record in enumerate(records, 1)]
    target = output / 'observations.jsonl'
    target.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in observations), encoding='utf-8')
    mapping = [{'case_id': row['case_id'], 'source_record_id': source_row['record_id'],
                'document_mapping': {doc['document_id']: original['doc_id']
                                     for doc, original in zip(row['documents'], source_row['documents'], strict=True)}}
               for row, source_row in zip(observations, records, strict=True)]
    (output / 'evaluator-only-mapping.json').write_text(json.dumps(mapping), encoding='utf-8')
    report = {'records': len(records), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'observations_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
              'status': 'prepared_not_evaluated',
              'limitations': ['Public synthetic data; model pretraining overlap unknown.',
                              'Titles and OCR text may carry semantic clues; no claim of zero leakage.',
                              'Coverage is reused in main: these are not independent development/test splits.',
                              'No model run or accuracy claim.']}
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--split', choices=['main', 'coverage'], default='main')
    args = parser.parse_args()
    pins = json.loads((Path(__file__).resolve().parents[1] / 'docs/external-finbalance-source-pin.json')
                      .read_text(encoding='utf-8'))
    expected = pins['files'][args.split + '-records.jsonl']['sha256']
    if hashlib.sha256(args.source.read_bytes()).hexdigest() != expected:
        raise ValueError('Source differs from pinned author release')
    print(json.dumps(prepare(args.source, args.output), indent=2))
