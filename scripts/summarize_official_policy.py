"""Publish source metadata and scoped results after independently rerunning QA checks."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'backend'))
from benchmarks.official_policy_consistency import evaluate  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/official-policy-validation.json')
    args = parser.parse_args()
    raw = args.report.read_bytes()
    saved = json.loads(raw)
    current = evaluate()
    # This is a generated current-state summary. Immutable full reports and
    # earlier failures remain separate; no official answer text is published.
    for key in current:
        if key != 'generated_at' and saved.get(key) != current[key]:
            raise SystemExit('Stored policy report is not reproducible by the current source: ' + key)
    summary = {key: saved[key] for key in ('schema_version', 'generated_at', 'policy_rule_identity',
               'fixture_version', 'fixture_source_sha256', 'evaluator_source_sha256',
               'selected_official_source_count', 'collected_source_count', 'unimplemented_source_count',
               'tasks', 'passed', 'failed', 'groups', 'frameworks', 'expected_status_distribution',
               'model_requests', 'actual_ledger_records_read', 'ledger_writes', 'scope', 'limitations')}
    summary['report_sha256'] = hashlib.sha256(raw).hexdigest()
    summary['source_verification'] = {key: saved['source_verification'][key] for key in ('checked', 'passed', 'failed', 'scope')}
    sources = {}
    for result in saved['results']:
        source = sources.setdefault(result['source_id'], {'source_id': result['source_id'],
            'source_sha256': result.get('source_sha256'), 'question_sha256': result.get('question_sha256'),
            'answer_sha256': result.get('answer_sha256'), 'tasks': 0, 'passed': 0})
        source['tasks'] += 1
        source['passed'] += int(result['passed'])
    summary['sources'] = list(sources.values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                           encoding='utf-8', newline='\n')
    print(json.dumps({key: summary[key] for key in ('selected_official_source_count', 'tasks', 'passed', 'failed')},
                     ensure_ascii=True))
    return int(bool(summary['failed'] or summary['source_verification']['failed']))


if __name__ == '__main__':
    raise SystemExit(main())
