import pytest

from scripts.validate_clean_checkout import isolated_environment, validate


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
