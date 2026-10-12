"""Reproducible offline transport measurement, never model accuracy or billing.

The handwritten synthetic notes and policy text below are transport payloads,
not accounting sources, rule answers, or an investigation evaluation dataset.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.context_transport import serialize_context, deserialize_context  # noqa: E402
from app.investigation import EvidenceMemory, ToolContext  # noqa: E402
from app.model_gateway import RunBudget  # noqa: E402


def measure() -> dict:
    common = {'currency': 'CNY', 'book_id': 'offline-context-probe', 'kind': 'journal',
              'business_date': '2026-09-01', 'period': '2026-09', 'observed_at': '2026-09-02T00:00:00Z'}
    rows = [{**common, 'id': f'row-{i}', 'evidence_id': f'evidence-{i}', 'voucher_id': f'voucher-{i//2}',
             'account': '1403' if i % 2 == 0 else '2202', 'debit': '100.00' if i % 2 == 0 else '0.00',
             'credit': '0.00' if i % 2 == 0 else '90.00',
             'description': f'合成单据 {i} 仅供传输测量。' + '原件应按资料范围核实，未核验事项不能作为事实。' * 12}
            for i in range(96)]
    policy = {'framework': 'CAS', 'version': 'synthetic-probe-v1', 'period': '2026-09',
              'book_id': common['book_id'], 'snapshot_cutoff': '2026-09-30T23:59:59Z', 'accounts': ['1403', '2202'],
              'reference_passages': [{'source_id': f'synthetic-policy-{i}', 'framework': 'CAS',
                 'title': f'合成条款 {i}', 'text': '合成条款，仅测传输，不是官方准则。' * 48,
                 'url': f'https://example.invalid/synthetic-policy/{i}', 'effective_from': '2026-01-01'}
                 for i in range(12)]}
    finding = {'task_type': 'review', 'record_ids': [], 'evidence_ids': []}
    context = ToolContext(rows, policy, finding, RunBudget())
    memory = EvidenceMemory()
    memory.execute(context, 'query_journal_lines', {'limit': 100})
    focus = memory.execute(context, 'query_journal_lines', {'voucher_id': 'voucher-0'})['query_id']
    memory.execute(context, 'retrieve_policy', {'query': '合成条款'})
    memory.execute(context, 'retrieve_policy', {'rule_id': 'VOUCHER_BALANCE'})
    memory.execute(context, 'query_journal_lines', {'reference': 'absent'})
    memory.execute(context, 'request_evidence', {'description': '未取得签收单', 'reason': '保持未决事项'})
    before = deepcopy(memory.stored())
    measurements = []
    for name, selected in [('full_internal_view', None), ('role_manifest_only', set()),
                           ('role_one_voucher_query', {focus}), ('role_all_queries', set(memory.queries))]:
        view = memory.brief(selected)
        packet = {'shared_evidence': view}
        serialized = serialize_context(packet)
        if deserialize_context(serialized) != packet or view['program_observations'] != before['program_observations']:
            raise ValueError('Transport or program-observation roundtrip failed')
        measurements.append({'view': name, 'serialized_context_bytes': len(serialized.encode()),
                             'records_in_view': len(view['records']), 'query_payloads_in_view': len(view['query_results']),
                             'manifest_entries': len(view.get('query_manifest', [])),
                             'policy_passages_in_view': len(view['reference_passages'])})
    if memory.stored() != before:
        raise ValueError('Read-only measurement changed the durable evidence')
    return {'schema_version': 'evidence-context-bytes-v1', 'created_at': datetime.now(UTC).isoformat(),
            'synthetic': True, 'model_calls': 0, 'hidden_truth_read': False, 'records': 96,
            'cached_queries': 5, 'program_observations': 1,
            'snapshot_scope': memory.scope, 'measurements': measurements,
            'source_hashes': {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in [
                'backend/app/investigation.py', 'backend/app/model_contract.py', 'backend/app/context_transport.py',
                'benchmarks/evidence_context_probe.py']},
            'limits': ['Measures shared-evidence transport only, excludes system protocol/native tool schema/history.',
                       'Synthetic notes and clauses are not accounting sources or independent answers.',
                       'No Token, billing, accuracy, complete-model-run or multi-agent-gain inference.']}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.local/reports/evidence-context-v19-repro.json')
    options = parser.parse_args(argv)
    if options.output.exists():
        raise ValueError('Existing measurement retained; choose a fresh output name')
    report = measure()
    options.output.parent.mkdir(parents=True, exist_ok=True)
    with options.output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'model_calls': 0, 'measurements': report['measurements'],
                      'output': str(options.output)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
