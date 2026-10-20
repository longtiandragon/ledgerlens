import subprocess

from scripts.audit_publication import audit


def repo(tmp_path, files):
    root = tmp_path / 'repo'
    root.mkdir()
    run = lambda *args: subprocess.run(['git', *args], cwd=root, check=True, capture_output=True)  # noqa: E731
    run('init', '-q')
    run('config', 'user.email', 'maintainer@example.com')
    run('config', 'user.name', 'Maintainer')
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        run('add', '-f', name)
        run('commit', '-q', '-m', f'add {name}')
    return root


def kinds(report):
    return sorted(item['kind'] for item in report['blocking'])


def test_real_looking_key_in_any_revision_blocks(tmp_path):
    key = b'sk-' + b'0123456789abcdef' * 2
    root = repo(tmp_path, {'app.py': b'KEY = "' + key + b'"\n'})
    # Removing the key later does not remove it from history.
    (root / 'app.py').write_bytes(b'KEY = None\n')
    subprocess.run(['git', 'commit', '-qam', 'remove key'], cwd=root, check=True, capture_output=True)
    report = audit(root)
    assert not report['passed'] and kinds(report) == ['api key (sk-)']
    assert key.decode() not in str(report)  # the matched secret is never echoed


def test_fixture_placeholders_and_clean_files_pass(tmp_path):
    root = repo(tmp_path, {'tests/test_x.py': b'env = "DEEPSEEK_API_KEY=fixture-file-key-0000000000000000"\n',
                           'README.md': b'contact: maintainer@example.com\n'})
    report = audit(root)
    assert report['passed'] and report['blocking'] == [] and report['blobs_scanned'] == 2


def test_local_data_originals_and_large_blobs_block(tmp_path):
    root = repo(tmp_path, {'.env': b'X=1\n', 'runtime/app.sqlite': b'db', 'docs/invoice.pdf': b'%PDF',
                           'big.txt': b'a' * 1_000_001})
    assert kinds(audit(root)) == ['blob over 1 MB', 'local data path', 'local data path',
                                  'third-party original extension']


def test_own_screenshots_pass_but_images_elsewhere_block(tmp_path):
    root = repo(tmp_path, {'docs/images/01-workbench.png': b'\x89PNG own capture',
                           'docs/raw/receipt.png': b'\x89PNG third-party scan'})
    report = audit(root)
    assert [item['path'] for item in report['blocking']] == ['docs/raw/receipt.png']


def test_unknown_email_addresses_are_listed_for_review_only(tmp_path):
    root = repo(tmp_path, {'notes.md': b'ask someone@university.edu.cn\n'})
    report = audit(root)
    assert report['passed'] and report['review'] == [{'kind': 'e-mail address', 'path': 'notes.md', 'count': 1}]
