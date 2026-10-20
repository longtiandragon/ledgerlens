import json

import pytest

from scripts.check_licenses import ROOT, canonical, classify, frontend_entries, main, parse, permitted

POLICY = json.loads((ROOT / 'docs/license-policy.json').read_text(encoding='utf-8'))
ALLOWED = POLICY['allowed']


@pytest.mark.parametrize('expression, expected', [
    ('MIT', True),
    ('Apache-2.0 OR GPL-2.0-only', True),
    ('Apache-2.0 AND GPL-2.0-only', False),
    ('MPL-2.0 AND (Apache-2.0 OR MIT)', True),
    ('(GPL-2.0-only OR LGPL-3.0-only) AND MIT', False),
    ('GPL-2.0-only WITH Classpath-exception-2.0', False),
    ('Apache-2.0 WITH LLVM-exception', True),
    ('mit or apache-2.0', False),  # identifiers are case-sensitive; only operators are normalized
])
def test_spdx_expressions_follow_and_or_semantics(expression, expected):
    assert permitted(parse(expression), ALLOWED) is expected


@pytest.mark.parametrize('expression', [
    'Dual License', 'BSD-3-Clause, Apache-2.0', 'OSI Approved :: MIT License', '(MIT', 'MIT AND', 'AND MIT',
    'MIT WITH', '', 'MIT )'])
def test_non_spdx_metadata_is_rejected_rather_than_guessed(expression):
    with pytest.raises(ValueError):
        parse(expression)


def test_alias_resolution_and_evidence_backed_overrides():
    assert classify('aiosqlite', 'OSI Approved :: MIT License', POLICY)['status'] == 'allowed'
    resolved = classify('amqp', 'BSD', POLICY)
    assert resolved['status'] == 'allowed' and resolved['source'] == 'resolved'
    assert resolved['expression'] == 'BSD-3-Clause'
    # The same ambiguous metadata on a package without recorded evidence is not accepted.
    assert classify('unreviewed-package', 'BSD', POLICY)['status'] == 'violation'
    assert classify('unreviewed-package', 'Dual License', POLICY)['status'] == 'violation'


def test_exceptions_are_scoped_to_the_package_and_license():
    assert classify('psycopg', 'LGPL-3.0-only', POLICY)['status'] == 'exception'
    assert classify('Psycopg_Binary', 'LGPL-3.0-only', POLICY)['status'] == 'exception'
    assert classify('psycopg', 'GPL-3.0-only', POLICY)['status'] == 'violation'
    assert classify('some-other-driver', 'LGPL-3.0-only', POLICY)['status'] == 'violation'


def test_missing_or_malformed_metadata_fails():
    assert classify('nameless', '', POLICY)['status'] == 'violation'
    assert classify('nameless', None, POLICY)['status'] == 'violation'
    assert classify('old-npm-style', {'type': 'MIT'}, POLICY)['status'] == 'violation'


def test_policy_file_is_internally_consistent():
    for key in [*POLICY['resolved'], *POLICY['exceptions']]:
        assert key == canonical(key)
    for target in POLICY['aliases'].values():
        assert permitted(parse(target), ALLOWED)
    for entry in POLICY['resolved'].values():
        assert permitted(parse(entry['expression']), ALLOWED) and entry['evidence']
    for entry in POLICY['exceptions'].values():
        assert not permitted(parse(entry['license']), ALLOWED) and entry['reason'] and entry['scope']


def _lock(tmp_path, packages):
    path = tmp_path / 'package-lock.json'
    path.write_text(json.dumps({'lockfileVersion': 3, 'packages': packages}), encoding='utf-8')
    return path


def test_frontend_lock_entries_use_the_innermost_package_name(tmp_path):
    lock = _lock(tmp_path, {'': {'name': 'app'},
                            'node_modules/@scope/pkg': {'version': '1.0.0', 'license': 'MIT'},
                            'node_modules/a/node_modules/b': {'version': '2.0.0', 'license': 'ISC'}})
    assert frontend_entries(lock) == [('@scope/pkg', '1.0.0', 'MIT'), ('b', '2.0.0', 'ISC')]


def test_main_exit_codes(tmp_path, capsys):
    good = _lock(tmp_path, {'node_modules/x': {'version': '1', 'license': 'MIT'}})
    assert main(['--skip-python', '--lock', str(good)]) == 0
    bad_dir = tmp_path / 'bad'
    bad_dir.mkdir()
    bad = _lock(bad_dir, {'node_modules/y': {'version': '1', 'license': 'GPL-3.0-only'}})
    assert main(['--skip-python', '--lock', str(bad)]) == 1
    assert main(['--skip-python', '--skip-frontend']) == 1  # checking nothing is not a pass
    report = capsys.readouterr().out
    assert 'GPL-3.0-only' in report
