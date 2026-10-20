"""Fail when a dependency's license falls outside docs/license-policy.json (Apache-2.0).

Checks every installed Python distribution and every frontend lock entry. Metadata
is normalized to an SPDX expression: a per-package resolution with recorded evidence
wins, then an exact alias, then the metadata itself if it parses as SPDX. Ambiguous
or missing metadata is a violation until a maintainer records evidence for it.
Standard library only; reads nothing outside the repository and the environment.

Usage: python scripts/check_licenses.py [--skip-python] [--skip-frontend]
"""
import argparse
import importlib.metadata
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r'\s*(?:(\()|(\))|([A-Za-z0-9][A-Za-z0-9.+:-]*))')
OPERATORS = {'AND', 'OR', 'WITH'}


def canonical(name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower()


def raw_license(meta) -> str:
    """Same precedence as scripts/dependency_inventory.py."""
    return meta.get('License-Expression') or meta.get('License') or '; '.join(
        x.removeprefix('License :: ') for x in meta.get_all('Classifier', []) if x.startswith('License :: '))


def tokenize(expression: str) -> list[str]:
    tokens, position = [], 0
    while position < len(expression.rstrip()):
        match = TOKEN.match(expression, position)
        if not match:
            raise ValueError(f'unexpected character at {position}')
        tokens.append(match.group(match.lastindex).upper() if match.group(3) and
                      match.group(3).upper() in OPERATORS else match.group(match.lastindex))
        position = match.end()
    return tokens


def parse(expression: str):
    """SPDX subset: id, id WITH exception, AND, OR, parentheses. AND binds tighter."""
    tokens = tokenize(expression)
    index = 0

    def peek():
        return tokens[index] if index < len(tokens) else None

    def take():
        nonlocal index
        index += 1
        return tokens[index - 1]

    def factor():
        token = take() if peek() is not None else None
        if token == '(':
            node = disjunction()
            if peek() != ')':
                raise ValueError('unbalanced parentheses')
            take()
            return node
        if token is None or token in OPERATORS or token == ')':
            raise ValueError('expected a license identifier')
        if peek() == 'WITH':
            take()
            exception = take() if peek() is not None else None
            if exception is None or exception in OPERATORS or exception in '()':
                raise ValueError('expected an exception identifier after WITH')
        return ('id', token)

    def conjunction():
        node = factor()
        while peek() == 'AND':
            take()
            node = ('and', node, factor())
        return node

    def disjunction():
        node = conjunction()
        while peek() == 'OR':
            take()
            node = ('or', node, conjunction())
        return node

    node = disjunction()
    if index != len(tokens):
        raise ValueError('trailing tokens')
    return node


def permitted(node, allowed) -> bool:
    kind = node[0]
    if kind == 'id':
        return node[1] in allowed
    if kind == 'and':
        return permitted(node[1], allowed) and permitted(node[2], allowed)
    return permitted(node[1], allowed) or permitted(node[2], allowed)


def classify(name: str, raw, policy: dict) -> dict:
    key = canonical(name)
    row = {'package': name, 'metadata': raw}
    resolved = policy['resolved'].get(key)
    if resolved:
        expression, source = resolved['expression'], 'resolved'
    elif not isinstance(raw, str):
        return {**row, 'status': 'violation', 'reason': 'license metadata is not a string; record evidence in resolved'}
    elif not raw.strip():
        return {**row, 'status': 'violation', 'reason': 'missing license metadata'}
    elif raw in policy['aliases']:
        expression, source = policy['aliases'][raw], 'alias'
    else:
        expression, source = raw.strip(), 'metadata'
    try:
        tree = parse(expression)
    except ValueError as exc:
        return {**row, 'status': 'violation', 'reason': f'not an SPDX expression ({exc}); record evidence in resolved'}
    row.update(expression=expression, source=source)
    if permitted(tree, policy['allowed']):
        return {**row, 'status': 'allowed'}
    exception = policy['exceptions'].get(key)
    if exception and exception['license'] == expression:
        return {**row, 'status': 'exception', 'scope': exception['scope']}
    return {**row, 'status': 'violation', 'reason': 'license not in the allowed list and no documented exception'}


def python_entries() -> list[tuple[str, str, str]]:
    seen, rows = set(), []
    for dist in importlib.metadata.distributions():
        name = dist.metadata['Name']
        if not name or (canonical(name), dist.version) in seen:
            continue
        seen.add((canonical(name), dist.version))
        rows.append((name, dist.version, raw_license(dist.metadata)))
    return sorted(rows, key=lambda r: canonical(r[0]))


def frontend_entries(lock_path: Path) -> list[tuple[str, str, object]]:
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    return sorted((path.rsplit('node_modules/', 1)[-1], entry.get('version'), entry.get('license'))
                  for path, entry in lock.get('packages', {}).items() if path)


def check(entries, policy) -> dict:
    rows = [{**classify(name, raw, policy), 'version': version} for name, version, raw in entries]
    counts = {status: sum(r['status'] == status for r in rows) for status in ('allowed', 'exception', 'violation')}
    return {'checked': len(rows), **counts,
            'exceptions': [r for r in rows if r['status'] == 'exception'],
            'violations': [r for r in rows if r['status'] == 'violation']}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--policy', type=Path, default=ROOT / 'docs/license-policy.json')
    parser.add_argument('--lock', type=Path, default=ROOT / 'frontend/package-lock.json')
    parser.add_argument('--skip-python', action='store_true')
    parser.add_argument('--skip-frontend', action='store_true')
    options = parser.parse_args(argv)
    policy = json.loads(options.policy.read_text(encoding='utf-8'))
    report = {'policy': policy['schema_version']}
    if not options.skip_python:
        report['python'] = check(python_entries(), policy)
    if not options.skip_frontend:
        report['frontend'] = check(frontend_entries(options.lock), policy)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    failed = sum(part['violation'] for key, part in report.items() if key != 'policy')
    return 1 if failed or len(report) == 1 else 0


if __name__ == '__main__':
    sys.exit(main())
