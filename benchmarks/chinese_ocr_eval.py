"""Score already persisted OCR against independent labels/rows, without model calls.

The runtime OCR scripts do not import this file or receive either truth path.
Official blank template label coverage and synthetic row extraction are reported
separately; neither is a real enterprise invoice accuracy estimate.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.ocr_tables import candidates  # noqa: E402
from app.ocr_workflow import validate_result  # noqa: E402


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def evaluate(official, wrapped):
    # Load both persisted predictions before opening any evaluator truth.
    official_result = read(official / 'prediction.json')
    wrapped_result = read(wrapped / 'prediction.json')
    official_manifest = read(official / 'manifest.json')
    wrapped_manifest = read(wrapped / 'manifest.json')
    for directory, manifest, prediction, input_key, hash_key in [
            (official, official_manifest, official_result, 'input', 'source_sha256'),
            (wrapped, wrapped_manifest, wrapped_result, 'image', 'image_sha256')]:
        actual = digest(directory / manifest[input_key])
        if actual != manifest[hash_key]:
            raise ValueError('Input changed since source collection')
        validate_result(prediction, actual)
        if prediction['status'] != 'requires_field_verification':
            raise ValueError('OCR did not complete successfully')
        if digest(directory / manifest['truth']) != manifest['truth_sha256']:
            raise ValueError('Evaluator truth changed after collection')
    labels = read(official / official_manifest['truth'])
    pages = official_result['pages']
    if len(pages) != 1 or pages[0]['status'] == 'failed':
        raise ValueError('Official sample must have exactly one successful page')
    observed = pages[0]['observations']
    coverage = [{'label': label, 'observation_ids': [row['observation_id'] for row in observed
                 if label in re.sub(r'\s+', '', row['text'])]} for label in labels['labels']]
    official_tables = candidates(official_result, 1)
    official_rows = [r for t in official_tables['tables'] for r in t['rows']]
    wrapped_tables = candidates(wrapped_result, 1)
    wrapped_rows = [r for t in wrapped_tables['tables'] for r in t['rows']]
    truth = read(wrapped / wrapped_manifest['truth'])
    actual_rows = [row['record'] for row in wrapped_rows]
    cases = {'official_blank_has_zero_records': len(official_rows) == labels['expected_business_records'],
             'wrapped_rows_exact': actual_rows == truth['expected_rows'],
             'wrapped_multiple_real_references': bool(wrapped_rows) and len(
                 wrapped_rows[0]['field_evidence'].get('description', [])) == 2,
             'wrapped_explicit_review': bool(wrapped_rows) and wrapped_rows[0]['issues'] == [
                 'wrapped_description_requires_manual_review']}
    membership = [identity for table in wrapped_tables['tables'] for identity in (
        *[h['observation_id'] for h in table['headers']],
        *[identity for row in table['rows'] for identity in row['observation_ids']])]
    membership += [identity for group in wrapped_tables['unassigned'] for identity in group['observation_ids']]
    cases['wrapped_observations_unique_and_complete'] = sorted(membership) == sorted(
        row['observation_id'] for row in wrapped_result['observations'])
    report = {'schema_version': 'chinese-public-ocr-validation-v1',
              'evaluated_at_utc': datetime.now(timezone.utc).isoformat(),
              'table_schema_version': wrapped_tables['schema_version'], 'checks': cases,
              'official': {'source_url': official_manifest['source_url'],
                  'source_sha256': official_manifest['source_sha256'],
                  'prediction_sha256': digest(official / 'prediction.json'),
                  'truth_sha256': official_manifest['truth_sha256'],
                  'data_nature': official_manifest['data_nature'], 'observations': len(observed),
                  'labels_matched': sum(bool(row['observation_ids']) for row in coverage),
                  'labels_total': len(coverage), 'label_coverage': coverage,
                  'table_candidates': len(official_rows), 'engine': official_result['engine']},
              'wrapped': {'synthetic': True, 'image_sha256': wrapped_manifest['image_sha256'],
                  'prediction_sha256': digest(wrapped / 'prediction.json'),
                  'truth_sha256': wrapped_manifest['truth_sha256'], 'observations': len(wrapped_result['observations']),
                  'expected_rows': truth['expected_rows'], 'actual_rows': actual_rows,
                  'candidates': wrapped_rows},
              'code_sha256': {p: digest(ROOT / p) for p in [
                  'backend/app/ocr_tables.py', 'backend/app/ocr_observations.py', 'backend/app/ocr_pdf.py',
                  'datasets/official_chinese_ocr.py', 'datasets/generate_ocr_wrapped.py', 'benchmarks/chinese_ocr_eval.py']},
              'limitations': ['Official blank invoice tests visible Chinese labels, not amount extraction or authenticity.',
                  'One synthetic wrapped table does not establish general complex-table accuracy.',
                  'No handwritten, merged-cell, split-amount or headerless cross-page support.',
                  'Every candidate remains unconfirmed and requires explicit human verification.',
                  'Source PDFs remain local; no open-content redistribution license is inferred.']}
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--official', type=Path, required=True)
    parser.add_argument('--wrapped', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args.official, args.wrapped)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'checks': report['checks'], 'labels_matched': report['official']['labels_matched'],
                      'labels_total': report['official']['labels_total']}, ensure_ascii=True))
    raise SystemExit(0 if all(report['checks'].values()) else 1)
