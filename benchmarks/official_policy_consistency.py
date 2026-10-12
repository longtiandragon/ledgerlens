"""Evaluate archived Q&A consistency, with source verification outside runtime.

This program may read the evaluator answer zone to verify the archived pairs.
Only structured observations and trusted synthetic context go to the engine.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.policy_contract import PolicyObservation, PolicyCheckContext  # noqa: E402
from benchmarks import official_policy_cases  # noqa: E402
from datasets.official_cases import verify_local_collection  # noqa: E402


VERSION = 'official-policy-consistency-evaluation-v1'
_FIXTURE_SOURCE = Path(official_policy_cases.__file__).read_bytes().replace(b'\r\n', b'\n')
_EVALUATOR_SOURCE = Path(__file__).read_bytes().replace(b'\r\n', b'\n')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def evaluate(*, manifest: Path = ROOT / 'docs/official-case-sources.json',
             collection: Path = ROOT / '.local/official-cases') -> dict:
    from app.policy_consistency import evaluate_policy, policy_rule_identity, policy_rule_catalog
    verification = verify_local_collection(manifest, collection)
    reports = json.loads((collection / 'acquisition-report.json').read_text(encoding='utf-8'))
    sources = {item['id']: item for item in reports if item['status'] == 'fetched'}
    verified = {item['id'] for item in verification['results'] if item['passed']}
    projections = official_policy_cases.cases()
    selected_sources = {item['source_id'] for item in projections}
    catalog = {item['source']['source_id']: item['source'] for item in policy_rule_catalog()}
    identity = policy_rule_identity()
    results = []
    for case in projections:
        item = {'id': case['id'], 'group': case['group'], 'source_id': case['source_id'],
                'framework': case['context']['framework'], 'expected': case['expected'], 'passed': False}
        before = sha(encoded({'observation': case['observation'], 'context': case['context']}))
        try:
            source_id = case['source_id']
            if source_id not in verified or source_id not in catalog:
                raise ValueError('Selected archived source did not pass independent verification')
            archived, compiled = sources[source_id], catalog[source_id]
            if (archived['sha256'] != compiled['source_sha256'] or archived['url'] != compiled['url']
                    or archived['published_at'] != compiled['publication_date']):
                raise ValueError('Compiled rule is not bound to the verified archived source')
            observation = PolicyObservation.model_validate_json(encoded(case['observation']))
            context = PolicyCheckContext.model_validate_json(encoded(case['context']))
            parsed_before = encoded({'observation': observation.model_dump(mode='json'),
                                     'context': context.model_dump(mode='json')})
            output = evaluate_policy(observation, context)
            references = set(observation.treatment_evidence_ids) | set(observation.beneficiary_evidence_ids)
            for condition in observation.conditions.values():
                references.update(condition.evidence_ids)
            item['actual'] = {key: output[key] for key in case['expected']}
            item['source_sha256'] = archived['sha256']
            item['question_sha256'] = archived['question_sha256']
            item['answer_sha256'] = archived['answer_sha256']
            item['input_sha256'] = before
            item['output_sha256'] = sha(encoded(output))
            item['checks'] = {
                'literal_expected_result': item['actual'] == item['expected'],
                'archived_source_binding': output['source'] == compiled,
                'fixed_pack_identity': output['policy_rule_identity'] == identity,
                'snapshot_citations': (set(output['evidence_ids']) == references
                                      and len(output['evidence_ids']) == len(references)
                                      and references <= {e.evidence_id for e in context.evidence}),
                'original_projection_unchanged': before == sha(encoded({
                    'observation': case['observation'], 'context': case['context']})),
                'parsed_inputs_unchanged': parsed_before == encoded({
                    'observation': observation.model_dump(mode='json'), 'context': context.model_dump(mode='json')}),
            }
            item['passed'] = all(item['checks'].values())
        except Exception as exc:
            item['error'] = {'type': type(exc).__name__, 'message': str(exc)[:250]}
        results.append(item)
    groups = {group: {'tasks': sum(item['group'] == group for item in results),
                      'passed': sum(item['group'] == group and item['passed'] for item in results)}
              for group in ('anchor', 'contrast')}
    frameworks = {framework: {'tasks': sum(item['framework'] == framework for item in results),
                              'passed': sum(item['framework'] == framework and item['passed'] for item in results)}
                  for framework in ('CAS', 'SCAS')}
    return {
        'schema_version': VERSION, 'generated_at': datetime.now(timezone.utc).isoformat(),
        'policy_rule_identity': identity, 'fixture_version': official_policy_cases.VERSION,
        'fixture_source_sha256': sha(_FIXTURE_SOURCE),
        'evaluator_source_sha256': sha(_EVALUATOR_SOURCE),
        'source_verification': verification, 'selected_official_source_count': len(selected_sources),
        'collected_source_count': len(sources), 'unimplemented_source_count': len(set(sources) - selected_sources),
        'tasks': len(results), 'passed': sum(item['passed'] for item in results),
        'failed': sum(not item['passed'] for item in results), 'groups': groups, 'frameworks': frameworks,
        'expected_status_distribution': dict(Counter(item['expected']['status'] for item in results)),
        'model_requests': 0, 'actual_ledger_records_read': 0, 'ledger_writes': 0,
        'scope': 'hand_authored_structured_archived_QA_developer_consistency_not_business_accuracy',
        'limitations': [
            'Selected official sources yield hand-authored projections; projection counts are not official case counts.',
            'Conditions and reported treatments are explicitly supplied, not independently extracted from documents.',
            'The benchmark is public development material, not the sealed synthetic test set.',
            'Publication availability is checked; legal effective intervals and later replacements remain unreviewed.',
            'No real enterprise, model investigation, simulation, professional review or full standards coverage is proven.',
            'Answer-zone separation is a software contract; no OS permission boundary is asserted.',
        ], 'results': results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'docs/official-case-sources.json')
    parser.add_argument('--collection', type=Path, default=ROOT / '.local/official-cases')
    parser.add_argument('--output', type=Path, required=True, help='New report path; existing runs are never replaced')
    args = parser.parse_args()
    report = evaluate(manifest=args.manifest, collection=args.collection)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({key: report[key] for key in ('schema_version', 'tasks', 'passed', 'failed', 'groups',
                                                  'frameworks', 'selected_official_source_count', 'model_requests')},
                     ensure_ascii=True))
    return int(bool(report['failed'] or report['source_verification']['failed']))


if __name__ == '__main__':
    raise SystemExit(main())
