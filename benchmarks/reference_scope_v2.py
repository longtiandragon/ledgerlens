"""Frozen synthetic evidence-scope protocol, without model, database or labels in runtime."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save_new(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def run(plan_path, output):
    from app.reference_contract import ReferenceRequirement
    from app.reference_scope import assess_support

    plan = read(plan_path)
    if plan['schema_version'] != 'reference-scope-plan-v2':
        raise ValueError('Unknown scope protocol')
    for name, expected in plan['sha256'].items():
        if sha(ROOT / name) != expected:
            raise ValueError('Frozen protocol input or code changed: ' + name)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    corpus = read(ROOT / plan['corpus'])
    parents = {p['source_id']: p for p in corpus['parents']}
    predictions = {}
    # Persist BOTH splits before opening any expected result file.
    for split, paths in plan['splits'].items():
        queries = read(ROOT / paths['queries'])['queries']
        rows = []
        for query in queries:
            if set(query) != {'id', 'question', 'candidate_ids', 'requirements'}:
                raise ValueError('Unexpected runtime input field')
            requirements = [ReferenceRequirement.model_validate(r) for r in query['requirements']]
            result = assess_support(requirements, [parents[i] for i in query['candidate_ids']])
            rows.append({'id': query['id'], 'result': result})
        if len({r['id'] for r in rows}) != len(rows):
            raise ValueError('Duplicate runtime case')
        predictions[split] = rows
        save_new(output / f'{split}-predictions.json', rows)
    summary = {'schema_version': 'reference-scope-validation-v2', 'status': 'completed',
               'scope': 'synthetic_contract_regression_not_semantic_answerability',
               'independent_data': False, 'plan_sha256': sha(plan_path), 'splits': {},
               'model_requests': 0, 'ledger_writes': 0, 'previous_v1_results_replaced': False}
    for split, paths in plan['splits'].items():
        labels = read(ROOT / paths['truth'])['labels']
        expected = {r['id']: r for r in labels}
        if len(expected) != len(labels) or set(expected) != {r['id'] for r in predictions[split]}:
            raise ValueError('Expected cases do not match predictions')
        decisions = []
        for row in predictions[split]:
            result, label = row['result'], expected[row['id']]
            observed = {'decision': result['decision'], 'statuses': [r['status'] for r in result['requirements']],
                        'match_parent_ids': [[m['parent_id'] for m in r['matches']] for r in result['requirements']]}
            passed = observed == {k: label[k] for k in observed} and result['answer_status'] == 'not_answered'
            decisions.append({'id': row['id'], 'passed': passed, 'observed': observed})
        summary['splits'][split] = {'cases': len(decisions), 'passed': sum(d['passed'] for d in decisions),
                                    'decisions': decisions,
                                    'predictions_sha256': sha(output / f'{split}-predictions.json')}
    summary['contract_gate_passed'] = all(s['cases'] == s['passed'] for s in summary['splits'].values())
    summary['limitation'] = ('All data and expected results are hand-authored by the implementer. The heldout split '
                             'is a protocol partition, not an independent unseen evaluation. No natural-language '
                             'answerability, ranking, accountant review or real private evidence validation is claimed.')
    save_new(output / 'summary.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.plan, args.output), ensure_ascii=False, indent=2))
