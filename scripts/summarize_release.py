"""Summarize the validation runs of one release commit into a single JSON (Apache-2.0).

Inputs are the raw outputs of runs made on that commit: a pytest JUnit XML file,
a Playwright JSON report and the scenario-pack report, plus an optional GitHub
Actions run whose job conclusions are read with the gh CLI. Counts are copied
from those files, never typed in; a missing or failing input is reported as such.
The output path must not exist yet.

    python scripts/summarize_release.py --junit <pytest.xml> --playwright <browser.json> \
        --scenario docs/scenario-acceptance.json [--ci-run <id>] --output docs/release-vX.Y.Z-validation.json
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT)]


def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def junit(path: Path) -> dict:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == 'testsuite' else list(root.iter('testsuite'))
    total = {key: sum(int(s.get(key, 0)) for s in suites) for key in ('tests', 'failures', 'errors', 'skipped')}
    total['passed'] = total['tests'] - total['failures'] - total['errors'] - total['skipped']
    return {**total, 'seconds': round(sum(float(s.get('time', 0)) for s in suites), 1), 'source': path.name}


def playwright(path: Path) -> dict:
    stats = json.loads(path.read_text(encoding='utf-8'))['stats']
    return {'passed': stats['expected'], 'skipped': stats['skipped'], 'failed': stats['unexpected'],
            'flaky': stats['flaky'], 'source': path.name}


def ci_run(run_id: str) -> dict:
    data = json.loads(subprocess.run(['gh', 'run', 'view', run_id, '--json', 'conclusion,headSha,jobs,url'],
                                     cwd=ROOT, capture_output=True, text=True, check=True).stdout)
    return {'run_id': run_id, 'url': data['url'], 'head_sha': data['headSha'], 'conclusion': data['conclusion'],
            'jobs': {job['name']: job['conclusion'] for job in data['jobs']}}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--junit', type=Path, required=True)
    parser.add_argument('--playwright', type=Path, required=True)
    parser.add_argument('--scenario', type=Path, required=True)
    parser.add_argument('--ci-run')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f'Refusing to overwrite {args.output}')
    from app.domain import SCENARIO_RULES, rule_catalog, rule_identity
    from app.treatment import treatment_identity
    scenario = json.loads(args.scenario.read_text(encoding='utf-8'))
    label = {True: '通过', False: '未通过'}
    backend, browser = junit(args.junit), playwright(args.playwright)
    summary = {
        'schema_version': 'release-validation-v1',
        'version': tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version'],
        'generated_at': datetime.now(timezone.utc).isoformat(), 'commit': git('rev-parse', 'HEAD'),
        'working_tree_clean': git('status', '--porcelain') == '',
        'backend': backend, 'browser': browser,
        'scenario_packs': {'passed': scenario['passed'], 'cell_count': scenario['cell_count'],
                           'failed': scenario['failed'], 'report_commit': scenario['commit'],
                           'matrix': {pack: {condition: label[ok] for condition, ok in row.items()}
                                      for pack, row in scenario['matrix'].items()}},
        'rules': {'execution_rules': rule_identity(), 'catalog_rules': len({r['rule_id'] for r in rule_catalog()}),
                  'funds_rules': len(SCENARIO_RULES['funds'])},
        'treatment': treatment_identity(),
        'ci': ci_run(args.ci_run) if args.ci_run else None,
        'all_passed': (backend['failures'] == backend['errors'] == 0 and browser['failed'] == 0
                       and not scenario['failed']),
        'scope': '本机与 CI 在该提交上的实测汇总；场景包为手写合成账套的流程验收，不代表真实企业效果或模型增益。',
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: summary[k] for k in ('version', 'commit', 'all_passed')}, ensure_ascii=False))
    return 0 if summary['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
