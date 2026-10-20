import pytest
import hashlib
import zipfile

from scripts.validate_clean_checkout import (
    archive_working_sources, backend_result, browser_result, isolated_environment,
    validate, verify_archived_sources,
)


def test_clean_process_environment_does_not_inherit_runtime_or_payment_settings(monkeypatch):
    for name in ('DEEPSEEK_API_KEY', 'DATABASE_URL', 'DATA_DIR', 'PYTHONPATH', 'OCR_PYTHON',
                 'REFERENCE_INDEX_CATALOG', 'OCR_TABLE_TESTS', 'PROCESS_DATASET_TESTS',
                 'MIGRATION_POSTGRES_TESTS', 'RETRIEVAL_INDEX_TESTS', 'HTTP_PROXY', 'NPM_CONFIG_REGISTRY'):
        monkeypatch.setenv(name, 'must-not-propagate')
    env = isolated_environment()
    assert 'must-not-propagate' not in env.values()
    assert env['UV_PYTHON_DOWNLOADS'] == 'never'
    assert env['PYTHONUTF8'] == '1'


def test_existing_validation_directory_is_never_replaced(tmp_path):
    evidence = tmp_path / 'report.json'
    evidence.write_text('previous failed evidence', encoding='utf-8')
    with pytest.raises(FileExistsError):
        validate(tmp_path)
    assert evidence.read_text(encoding='utf-8') == 'previous failed evidence'


def test_working_snapshot_keeps_uncommitted_bytes_and_deletions(tmp_path, monkeypatch):
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'modified.py').write_bytes(b'working version\n')
    (root / 'new.py').write_bytes(b'new source\n')
    (root / '.env.example').write_bytes(b'KEY=\n')
    monkeypatch.setattr('scripts.validate_clean_checkout.subprocess.check_output',
                        lambda *args, **kw: b'modified.py\0new.py\0deleted.py\0.env.example\0modified.py\0')
    archive = tmp_path / 'source.zip'
    manifest = archive_working_sources(root, archive)
    assert set(manifest) == {'modified.py', 'new.py', '.env.example'}
    assert manifest['modified.py'] == hashlib.sha256(b'working version\n').hexdigest()
    with zipfile.ZipFile(archive) as packed:
        assert packed.read('new.py') == b'new source\n'
        assert len(packed.namelist()) == 3


@pytest.mark.parametrize('name', ['.env', '.env.local', '.local/raw.xml', 'runtime/app.sqlite',
                                 'frontend/node_modules/index.js', '../outside.txt', '.git/config'])
def test_working_snapshot_rejects_local_state_even_when_git_selects_it(tmp_path, monkeypatch, name):
    monkeypatch.setattr('scripts.validate_clean_checkout.subprocess.check_output',
                        lambda *args, **kw: name.encode() + b'\0')
    with pytest.raises(ValueError, match='Forbidden'):
        archive_working_sources(tmp_path, tmp_path / 'source.zip')


def test_working_snapshot_refuses_empty_source(tmp_path, monkeypatch):
    monkeypatch.setattr('scripts.validate_clean_checkout.subprocess.check_output', lambda *args, **kw: b'')
    with pytest.raises(ValueError, match='empty'):
        archive_working_sources(tmp_path, tmp_path / 'source.zip')


def test_working_snapshot_refuses_replacing_prior_archive(tmp_path, monkeypatch):
    archive = tmp_path / 'source.zip'
    archive.write_bytes(b'prior snapshot')
    monkeypatch.setattr('scripts.validate_clean_checkout.subprocess.check_output', lambda *args, **kw: b'a.py\0')
    with pytest.raises(FileExistsError):
        archive_working_sources(tmp_path, archive)
    assert archive.read_bytes() == b'prior snapshot'


@pytest.mark.parametrize('xml', [
    '<testsuites/>',
    '<testsuites><testsuite tests="0"/></testsuites>',
    '<testsuite tests="1" skipped="1"><testcase><skipped/></testcase></testsuite>',
    '<testsuite tests="1" failures="1"><testcase><failure/></testcase></testsuite>',
    '<testsuite tests="1" errors="1"><testcase><error/></testcase></testsuite>',
    '<testsuite tests="1"/>',
    '<testsuite tests="1"><testcase><failure/></testcase></testsuite>',
    '<testsuite tests="1"><testcase><skipped/></testcase></testsuite>',
])
def test_empty_failed_or_inconsistent_backend_report_is_not_acceptance(tmp_path, xml):
    path = tmp_path / 'backend.xml'
    path.write_text(xml, encoding='utf-8')
    with pytest.raises(ValueError):
        backend_result(path)


def test_backend_report_preserves_optional_skips(tmp_path):
    path = tmp_path / 'backend.xml'
    path.write_text('<testsuites><testsuite tests="2" skipped="1"><testcase/>'
                    '<testcase><skipped message="optional dependency"/></testcase></testsuite></testsuites>',
                    encoding='utf-8')
    result = backend_result(path)
    assert result['passed'] == 1 and result['skipped'] == 1
    assert result['skip_reasons'] == ['optional dependency']


@pytest.mark.parametrize('updates,errors', [
    ({'expected': 0}, []), ({'unexpected': 1}, []), ({'flaky': 1}, []),
    ({'expected': True}, []), ({'skipped': -1}, []), ({}, [{'message': 'runner crashed'}]),
])
def test_empty_failed_or_malformed_browser_report_is_not_acceptance(updates, errors):
    stats = {'expected': 1, 'unexpected': 0, 'flaky': 0, 'skipped': 2} | updates
    with pytest.raises(ValueError):
        browser_result({'stats': stats, 'errors': errors})


def test_browser_report_preserves_optional_skips():
    stats = {'expected': 1, 'unexpected': 0, 'flaky': 0, 'skipped': 2}
    assert browser_result({'stats': stats, 'errors': []}) == stats


@pytest.mark.parametrize('mutation', ['modified', 'missing', 'unchanged'])
def test_source_integrity_rechecks_snapshot_after_execution(tmp_path, mutation):
    archive = tmp_path / 'source.zip'
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    with zipfile.ZipFile(archive, 'x') as packed:
        packed.writestr('main.py', b'original source')
    if mutation != 'missing':
        (checkout / 'main.py').write_bytes(b'modified source' if mutation == 'modified' else b'original source')
    (checkout / 'generated.json').write_text('{}')
    if mutation == 'unchanged':
        assert verify_archived_sources(archive, checkout) == 1
    else:
        with pytest.raises(ValueError, match='changed or is missing'):
            verify_archived_sources(archive, checkout)
