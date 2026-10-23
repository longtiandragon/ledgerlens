"""Reproduce an external Rules baseline, then independently score all 439 test cases.

The upstream MIT implementation is downloaded separately, pinned and checked
before import. It is a comparison method, not LedgerLens production behavior.
No provider, database, model weights, pickle or external shell is invoked.
"""
import argparse
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmarks.external_metrics import classification_metrics  # noqa: E402

REVISION = '76c93ec32536e29826e2ab9e68b58e0351410bc7'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(source, output):
    pins_path = ROOT / 'docs/external-finrca-source-pin.json'
    pins = json.loads(pins_path.read_text(encoding='utf-8'))
    if pins['revision'] != REVISION:
        raise ValueError('Wrong reviewed revision')
    for name, expected_hash in pins['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source.resolve()) or digest(path) != expected_hash:
            raise ValueError('External source changed: ' + name)
    output.mkdir(parents=True, exist_ok=False)
    # Project only routing fields in acquisition; questions contain answers and
    # must never be sent to a model or used for predictions.
    routes = {}
    for line in (source / 'data/benchmark/test/benchmark_questions.jsonl').read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        route = {'case_id': row['case_id'], 'primary_entity_type': row['primary_entity']['type'],
                 'primary_entity_id': row['primary_entity']['id']}
        if routes.setdefault(row['case_id'], route) != route:
            raise ValueError('Inconsistent routing')
    ids = (source / 'data/benchmark/test/case_ids.txt').read_text().splitlines()
    if len(ids) != 439 or len(set(ids)) != 439 or set(ids) != set(routes):
        raise ValueError('Wrong fixed test population')
    (output / 'routes.json').write_text(json.dumps([routes[x] for x in ids]), encoding='utf-8')
    scoring = False
    forbidden_reads = []

    def guard(event, args):
        if event in {'socket.connect', 'socket.getaddrinfo', 'subprocess.Popen', 'os.system'}:
            raise RuntimeError('External process/network disabled during audit')
        if event == 'open' and isinstance(args[0], (str, bytes)) and not scoring:
            name = str(args[0]).replace('\\', '/')
            if any(x in name for x in ('rca_ground_truth', 'failure_manifest', 'mutation_log',
                                      'causal_edges', 'benchmark_questions', 'case_entity_records', 'raw_clean')):
                forbidden_reads.append(name)
                raise RuntimeError('Evaluator/answer data read during inference')

    sys.addaudithook(guard)
    sys.path.insert(0, str(source.resolve()))
    from src.rules_sql.engine import F01Parameters, FrozenRulesBaseline
    from src.direct_llm.config import ALL_CLASSES
    # Published validation-only selection, fixed before opening held-out truth.
    engine = FrozenRulesBaseline(source / 'data/benchmark/full', F01Parameters(7, Decimal('1.00')))
    predictions_path = output / 'predictions.jsonl'
    predictions = {}
    with predictions_path.open('x', encoding='utf-8') as handle:
        for case_id in ids:
            row = engine.predict_case(**routes[case_id]).serializable()
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
            predictions[case_id] = row['predicted_failure_type']
    # Scoring starts only after the entire predictions artifact is closed.
    scoring = True
    truth_path = source / 'data/benchmark/test/failure_manifest.csv'
    with truth_path.open(encoding='utf-8', newline='') as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != len({r['case_id'] for r in rows}):
        raise ValueError('Duplicate external truth')
    truth = {row['case_id']: row['failure_type'] for row in rows}
    metrics = classification_metrics(truth, predictions, list(ALL_CLASSES))
    upstream_path = source / 'results/rules_sql/phase2_rules_sql_v1_0_20260808T185000Z_auditfix1/metrics.json'
    upstream = json.loads(upstream_path.read_text())
    reproduced = abs(Decimal(metrics['macro_f1']) - Decimal(str(upstream['macro_f1_16_classes']))) < Decimal('1e-12')
    report = dict(schema_version='external-finrca-baseline-audit-v1',
                  evaluated_at=datetime.now(timezone.utc).isoformat(), upstream_revision=REVISION,
                  scope='external_rules_baseline_reproduction_not_project_ai_gain',
                  external_model_requests=0, test_cases=439, metrics=metrics,
                  reproduced_published_macro_f1=reproduced,
                  gain_005_mathematically_possible=Decimal(metrics['maximum_possible_f1_gain']) >= Decimal('0.05'),
                  prediction_sha256=digest(predictions_path), truth_sha256=digest(truth_path),
                  source_manifest_sha256=digest(pins_path), forbidden_inference_reads=forbidden_reads,
                  code_sha256={p: digest(ROOT / p) for p in ['scripts/audit_finrca_baseline.py', 'benchmarks/external_metrics.py']},
                  limitations=['Independent-author synthetic data, not real enterprise evidence.',
                               'Published public test; model pretraining contamination cannot be excluded.',
                               '15 failure classes plus normal differ from project v4 taxonomy.',
                               'No paid model comparison executed. No new project AI claim established.',
                               'Macro F1 ceiling is specific to this split and this comparator.'])
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.source, args.output)
    print(json.dumps({k: v for k, v in result.items() if k not in {'code_sha256', 'limitations', 'metrics'}}, indent=2))
    print(json.dumps(result['metrics'], indent=2))
    raise SystemExit(0 if result['reproduced_published_macro_f1'] else 1)
