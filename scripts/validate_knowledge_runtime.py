"""Verify local reference provenance using a labelled synthetic API book.

No model requests, ledger adjustments or professional judgements occur. The
report contains identities and hashes, not reference text or evaluator answers.
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
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def verify(base_url):
    report = {'schema_version': 'knowledge-runtime-acceptance-v1',
              'started_at': datetime.now(timezone.utc).isoformat(), 'checks': {},
              'model_requests': 0, 'synthetic_book': True,
              'scope': 'reference_identity_integrity_not_accounting_accuracy_or_legal_validity'}
    with httpx.Client(base_url=base_url, timeout=60, follow_redirects=False, trust_env=False) as client:
        def get(path, **kwargs):
            response = client.get('/api/v1' + path, **kwargs)
            response.raise_for_status()
            return response.json()

        def post(path, value=None):
            response = client.post('/api/v1' + path, json=value)
            response.raise_for_status()
            return response.json()

        try:
            book = post('/books', {'name': '知识来源绑定验收 · 合成演示', 'framework': 'CAS', 'period': '2026-09'})
            report['book_id'] = book['id']
            base = '/books/' + book['id']
            demo = post(base + '/demo')
            report['snapshot_id'] = demo['snapshot_id']
            records = get(base + '/records')
            before = digest(records)
            snapshot = get('/snapshots/' + demo['snapshot_id'])
            policy = snapshot['policy']
            passages = policy.get('reference_passages', [])
            response = get('/knowledge/search', params={'book_id': book['id'], 'query': '会计软件凭证'})
            report['checks']['live_search_verified'] = response.get('reference_status') == 'verified' and bool(response['results'])
            report['checks']['snapshot_verified'] = policy.get('reference_integrity') == 'verified' and bool(passages)
            report['checks']['frozen_text_hashes_match'] = bool(passages) and all(
                hashlib.sha256(p['text'].encode()).hexdigest() == p['source_binding']['chunk_sha256'] for p in passages)
            report['checks']['only_registered_policy_types'] = bool(passages) and all(
                p['source_binding']['kind'] in {'policy', 'policy_notice'} for p in passages)
            report['checks']['frozen_reference_identity_matches'] = bool(passages) and all(
                p['source_id'] == p['source_binding']['chunk_id'] for p in passages)
            report['checks']['snapshot_scope_matches'] = all(
                p['framework'] in {'CAS', 'BOTH'} for p in passages) and snapshot['book_id'] == book['id']
            report['checks']['query_references_have_bindings'] = all(
                isinstance(p.get('source_binding'), dict) and p.get('kind') in {'policy', 'policy_notice'}
                for p in response['results'])
            repeated = post('/snapshots', {'book_id': book['id']})
            report['checks']['same_data_and_sources_reuse_snapshot'] = repeated['id'] == snapshot['id']
            checks = post('/check-runs', {'book_id': book['id'], 'snapshot_id': snapshot['id']})
            case = checks['cases'][0]
            run = post('/cases/' + case['id'] + '/investigate', {'mode': 'rules'})
            report['run_id'] = run['id']
            completed = get('/runs/' + run['id'])
            deadline = time.monotonic() + 45
            while completed['status'] in {'queued', 'running'} and time.monotonic() < deadline:
                time.sleep(.2)
                completed = get('/runs/' + run['id'])
            report['checks']['rule_workflow_without_model'] = completed['status'] == 'complete' and completed['usage'].get('calls', 0) == 0
            report['checks']['original_records_unchanged'] = digest(get(base + '/records')) == before
            report['checks']['frozen_snapshot_unchanged'] = get('/snapshots/' + snapshot['id']) == snapshot
            report['record_count'] = len(records)
            report['records_sha256'] = before
            report['reference_count'] = len(passages)
            report['sources'] = sorted({
                (p['source_binding']['original_source_id'], p['source_binding']['raw_sha256'],
                 p['source_binding']['parsed_sha256'], p['source_binding']['registry_sha256'])
                for p in passages})
            report['prompt_version'] = completed.get('result', {}).get('prompt_version')
            report['tool_version'] = completed.get('result', {}).get('tool_version')
            if completed['status'] in {'queued', 'running'}:
                report['error'] = {'type': 'TimeoutError', 'message': 'Rule run did not reach a terminal state within 45 seconds'}
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
    with args.output.open('x', encoding='utf-8') as handle:
        report = verify(args.base_url.rstrip('/'))
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')
    print(json.dumps({key: report[key] for key in ('schema_version', 'passed', 'checks', 'model_requests')}, ensure_ascii=True))
    return int(not report['passed'])


if __name__ == '__main__':
    raise SystemExit(main())
