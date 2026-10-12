"""Create a labelled synthetic book and verify the live policy version workflow.

No model request, enterprise accounting judgement or ledger adjustment occurs.
The fixture assumes human review conditions; that assertion is not an oracle
for the underlying source semantics. This is an API/integrity acceptance run.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def verify(base_url):
    report = {'schema_version': 'policy-runtime-acceptance-v1',
              'started_at': datetime.now(timezone.utc).isoformat(), 'checks': {},
              'model_requests': 0, 'synthetic': True,
              'scope': 'live_API_version_and_integrity_workflow_not_professional_or_document_semantics'}
    with httpx.Client(base_url=base_url, timeout=30, follow_redirects=False, trust_env=False) as client:
        def get(path):
            response = client.get('/api/v1' + path)
            response.raise_for_status()
            return response.json()

        def post(path, value=None):
            response = client.post('/api/v1' + path, json=value)
            response.raise_for_status()
            return response.json()

        try:
            catalog = get('/policy-checks/catalog')
            report['policy_rule_identity'] = catalog['policy_rule_identity']
            book = post('/books', {'name': '政策条件接口验收 · 合成演示', 'framework': 'CAS',
                                  'period': '2026-09', 'accounts': ['1002', '1405', '2202'], 'locked_periods': []})
            report['book_id'] = book['id']
            base = '/books/' + book['id']
            imported = post(base + '/demo')
            report['snapshot_id'] = imported['snapshot_id']
            records = get(base + '/records')
            before = digest(records)
            snap_before = get('/snapshots/' + imported['snapshot_id'])
            before_revision = get('/books')
            before_revision = next(item['revision'] for item in before_revision if item['id'] == book['id'])
            ref = next(row['evidence_id'] for row in records if row['kind'] == 'order')
            body = {'snapshot_id': imported['snapshot_id'], 'logical_observation_key': 'synthetic-resale-review',
                    'human_confirmation': True, 'topic': 'resale_inventory',
                    'conditions': {name: {'value': 'yes', 'confirmation': 'human_confirmed', 'evidence_ids': [ref]}
                                   for name in ('ordinary_course', 'held_for_sale', 'economic_benefits_probable',
                                                'cost_reliably_measurable')},
                    'reported_treatment': 'inventory', 'treatment_evidence_ids': [ref],
                    'comment': '合成接口验收假设；人工确认是测试输入，不构成专业结论。'}
            body['conditions']['ordinary_course']['value'] = 'unknown'
            body['conditions']['ordinary_course']['confirmation'] = 'unconfirmed'
            first = post(base + '/policy-observations', body)
            first_check = post(base + '/policy-observations/' + first['id'] + '/check')
            report['observation_v1'] = first['id']
            report['check_v1'] = first_check['id']
            report['checks']['unknown_condition_reserved'] = (
                first_check['result']['status'] == 'insufficient'
                and first_check['result']['expected_treatment'] is None
                and 'ordinary_course' in first_check['result']['missing_conditions'])
            body['parent_version_id'] = first['id']
            body['conditions']['ordinary_course'].update(value='yes', confirmation='human_confirmed')
            second = post(base + '/policy-observations', body)
            second_check = post(base + '/policy-observations/' + second['id'] + '/check')
            report['observation_v2'] = second['id']
            report['check_v2'] = second_check['id']
            report['checks']['confirmed_conditions_normal'] = (second_check['result']['status'] == 'normal'
                                                               and second_check['result']['expected_treatment'] == 'inventory')
            repeated = post(base + '/policy-observations/' + second['id'] + '/check')
            report['checks']['idempotent_result'] = repeated['id'] == second_check['id']
            report['checks']['append_only_versions'] = second['version'] == 2 and second['parent_version_id'] == first['id']
            first_read = get(base + '/policy-observations/' + first['id'])
            old_result = get(base + '/policy-checks/' + first_check['id'])
            report['checks']['old_version_preserved_and_stale'] = (first_read['payload'] == first['payload']
                                                                  and first_read['stale'] and old_result['stale']
                                                                  and old_result['result'] == first_check['result'])
            rejected = client.post('/api/v1' + base + '/policy-observations/' + first['id'] + '/check')
            report['checks']['superseded_version_reexecution_rejected'] = rejected.status_code == 409
            exported = get(base + '/policy-checks/' + second_check['id'] + '/export')
            report['checks']['export_matches_saved_result'] = exported == second_check
            report['checks']['source_locators_present'] = (
                second_check['result']['evidence_ids'] == [ref]
                and len(second_check['binding']['evidence_locators']) == 1
                and second_check['binding']['evidence_locators'][0]['evidence_id'] == ref
                and bool(second_check['binding']['evidence_locators'][0]['confirmed_import_ids']))
            source = second_check['binding']['evidence_locators'][0]
            original = client.get('/api/v1/documents/' + source['document_id'] + '/file')
            original.raise_for_status()
            report['checks']['original_file_hash_matches'] = hashlib.sha256(original.content).hexdigest() == source['document_sha256']
            report['checks']['ledger_records_unchanged'] = digest(get(base + '/records')) == before
            report['ledger_records_sha256'] = before
            report['checks']['snapshot_unchanged'] = get('/snapshots/' + imported['snapshot_id']) == snap_before
            after_book = next(item for item in get('/books') if item['id'] == book['id'])
            report['checks']['book_revision_unchanged'] = after_book['revision'] == before_revision
            report['checks']['fixed_policy_identity'] = second_check['result']['policy_rule_identity'] == catalog['policy_rule_identity']
            report['checks']['historical_core_identity_preserved'] = (
                second_check['binding']['ledger_snapshot_digest'] == snap_before['digest'])
            report['record_count'] = len(records)
        except Exception as exc:
            report['error'] = {'type': type(exc).__name__, 'message': str(exc)[:250]}
    report['completed_at'] = datetime.now(timezone.utc).isoformat()
    report['passed'] = bool(report['checks']) and all(report['checks'].values()) and 'error' not in report
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    origin = urlsplit(args.base_url)
    if (origin.scheme != 'http' or origin.hostname not in {'127.0.0.1', 'localhost'}
            or origin.username or origin.password or origin.path not in {'', '/'} or origin.query or origin.fragment):
        raise SystemExit('Only an uncredentialed local HTTP origin is accepted')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the report before any mutation. Partial failures remain recorded;
    # repeating this command cannot silently create a second acceptance book.
    with args.output.open('x', encoding='utf-8') as handle:
        report = verify(args.base_url.rstrip('/'))
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({key: report[key] for key in ('schema_version', 'passed', 'checks', 'model_requests')}, ensure_ascii=True))
    return int(not report['passed'])


if __name__ == '__main__':
    raise SystemExit(main())
