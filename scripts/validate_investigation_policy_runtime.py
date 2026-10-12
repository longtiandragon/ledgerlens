"""Unpaid live API acceptance for immutable investigation policy contexts.

Creates a labelled synthetic book. Human confirmations are fixture inputs, not
professional findings or a source-document semantic oracle. No model is called.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def verify(base_url):
    report = {'schema_version': 'investigation-policy-runtime-acceptance-v1',
              'started_at': datetime.now(timezone.utc).isoformat(), 'checks': {},
              'model_requests': 0, 'synthetic': True,
              'scope': 'live_API_frozen_context_and_version_workflow_not_model_or_professional_effectiveness'}
    with httpx.Client(base_url=base_url, timeout=45, trust_env=False, follow_redirects=False) as client:
        def get(path):
            response = client.get('/api/v1' + path)
            response.raise_for_status()
            return response.json()

        def post(path, value=None):
            response = client.post('/api/v1' + path, json=value)
            response.raise_for_status()
            return response.json()

        def run_case(case_id):
            run = post('/cases/' + case_id + '/investigate', {'mode': 'rules'})
            deadline = time.monotonic() + 45
            while run['status'] in {'queued', 'running'} and time.monotonic() < deadline:
                time.sleep(.2)
                run = get('/runs/' + run['id'])
            if run['status'] != 'complete' or run['result'].get('usage', {}).get('calls', 0) != 0:
                raise ValueError('Unpaid rules run did not complete with zero model requests')
            return run, get('/runs/' + run['id'] + '/policy-context')

        try:
            book = post('/books', {'name': '调查政策冻结验收 · 合成演示', 'framework': 'CAS',
                                  'period': '2026-09', 'accounts': ['1002', '1405', '2202'], 'locked_periods': []})
            report['book_id'] = book['id']
            base = '/books/' + book['id']
            imported = post(base + '/demo')
            snapshot_id = imported['snapshot_id']
            report['snapshot_id'] = snapshot_id
            records = get(base + '/records')
            before = digest(records)
            reference = next(row['evidence_id'] for row in records if row['kind'] == 'order')
            request = {'snapshot_id': snapshot_id, 'logical_observation_key': 'synthetic-resale-conditions',
                       'human_confirmation': True, 'topic': 'resale_inventory',
                       'conditions': {name: {'value': 'yes', 'confirmation': 'human_confirmed',
                                            'evidence_ids': [reference]}
                                      for name in ('ordinary_course', 'held_for_sale', 'economic_benefits_probable',
                                                   'cost_reliably_measurable')},
                       'reported_treatment': 'inventory', 'treatment_evidence_ids': [reference],
                       'comment': '合成验收人工条件，仅用于传输和版本边界，不构成专业判断。'}
            observation_v1 = post(base + '/policy-observations', request)
            check_v1 = post(base + '/policy-observations/' + observation_v1['id'] + '/check')
            checks = post('/check-runs', {'book_id': book['id'], 'snapshot_id': snapshot_id})
            case_id = checks['cases'][0]['id']
            report['case_id'] = case_id
            run_v1, context_v1 = run_case(case_id)
            report['run_v1'] = run_v1['id']
            report['policy_check_v1'] = check_v1['id']
            captured_before = digest(context_v1['payload'])
            saved = context_v1['payload']['checks']
            report['checks']['saved_check_captured'] = (context_v1['status'] == 'captured'
                                                        and len(saved) == 1 and saved[0]['id'] == check_v1['id'])
            report['checks']['exact_saved_result_and_binding'] = (saved[0]['result'] == check_v1['result']
                                                                  and saved[0]['binding'] == check_v1['binding'])
            report['checks']['scope_fixed'] = (context_v1['payload']['run_id'] == run_v1['id']
                                              and context_v1['payload']['snapshot_id'] == snapshot_id
                                              and context_v1['payload']['book_id'] == book['id'])
            report['checks']['annotation_time_separate_from_ledger_cutoff'] = (
                saved[0]['binding']['source_cutoff'] != context_v1['payload']['captured_at'])
            request['parent_version_id'] = observation_v1['id']
            request['conditions']['ordinary_course'].update(value='unknown', confirmation='unconfirmed')
            observation_v2 = post(base + '/policy-observations', request)
            context_v1_after = get('/runs/' + run_v1['id'] + '/policy-context')
            report['checks']['new_annotation_marks_old_context_stale'] = context_v1_after['stale']
            report['checks']['frozen_payload_survives_new_annotation'] = digest(context_v1_after['payload']) == captured_before
            run_v2, context_v2 = run_case(case_id)
            report['run_v2'] = run_v2['id']
            report['checks']['unchecked_latest_never_falls_back_to_old_check'] = context_v2['payload']['checks'] == []
            report['checks']['unchecked_annotation_in_manifest'] = (
                [row['id'] for row in context_v2['payload']['observation_manifest']] == [observation_v2['id']])
            check_v2 = post(base + '/policy-observations/' + observation_v2['id'] + '/check')
            report['policy_check_v2'] = check_v2['id']
            context_v2_after = get('/runs/' + run_v2['id'] + '/policy-context')
            report['checks']['later_check_marks_empty_context_stale'] = context_v2_after['stale']
            report['checks']['later_check_not_grafted_into_empty_context'] = context_v2_after['payload']['checks'] == []
            run_v3, context_v3 = run_case(case_id)
            report['run_v3'] = run_v3['id']
            saved_v3 = context_v3['payload']['checks']
            report['checks']['new_run_captures_new_insufficient_result'] = (
                len(saved_v3) == 1 and saved_v3[0]['id'] == check_v2['id']
                and saved_v3[0]['result']['status'] == 'insufficient')
            exported = get('/exports/cases/' + case_id + '?format=json')
            report['checks']['export_includes_each_original_context'] = (
                digest(exported['investigation_policy_contexts'][run_v1['id']]['payload']) == captured_before
                and exported['investigation_policy_contexts'][run_v2['id']]['payload']['checks'] == [])
            markdown = client.get('/api/v1/exports/cases/' + case_id)
            markdown.raise_for_status()
            report['checks']['markdown_contains_run_and_saved_check'] = (run_v1['id'] in markdown.text
                                                                        and check_v1['id'] in markdown.text)
            evidence = get('/cases/' + case_id + '/evidence')
            report['checks']['source_reference_resolves_in_case_browser'] = reference in {row['id'] for row in evidence}
            report['checks']['rules_runs_zero_model_calls'] = all(run['result']['usage'].get('calls', 0) == 0
                                                                 for run in (run_v1, run_v2, run_v3))
            report['checks']['policy_context_does_not_create_financial_fact'] = all(
                claim.get('rule_id') != check_v1['result']['rule_id'] for claim in run_v1['result']['claims'])
            report['checks']['original_records_unchanged'] = digest(get(base + '/records')) == before
            report['record_count'] = len(records)
            report['ledger_records_sha256'] = before
            report['captured_v1_payload_sha256'] = captured_before
            report['prompt_version'] = run_v3['result']['prompt_version']
            report['tool_version'] = run_v3['result']['tool_version']
        except Exception as exc:
            report['error'] = {'type': type(exc).__name__, 'message': str(exc)[:350]}
    report['completed_at'] = datetime.now(timezone.utc).isoformat()
    report['passed'] = bool(report['checks']) and all(report['checks'].values()) and 'error' not in report
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    target = urlsplit(args.base_url)
    if target.scheme != 'http' or target.hostname not in {'127.0.0.1', 'localhost'} or target.username or target.password:
        raise SystemExit('Acceptance must use a local HTTP API')
    output = Path(args.report)
    if output.exists():
        raise SystemExit('Use a new report path; prior acceptance evidence is immutable')
    output.parent.mkdir(parents=True, exist_ok=True)
    result = verify(args.base_url)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps({'passed': result['passed'], 'checks': result['checks'], 'report': str(output)}, ensure_ascii=False))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
