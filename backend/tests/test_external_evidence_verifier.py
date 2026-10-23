import json
from scripts.verify_external_evidence import check_hash, sibr
import pytest


def test_changed_artifact_is_rejected(tmp_path):
    path = tmp_path / 'prediction.json'
    path.write_text('{}')
    with pytest.raises(ValueError, match='Changed artifact'):
        check_hash(path, '0' * 64)


def test_pending_ocr_does_not_open_annotations(tmp_path):
    run = tmp_path / 'sibr-ocr-full-v1'
    run.mkdir()
    (run / 'report.json').write_text(json.dumps({'status': 'running', 'code_sha256': {}, 'cases': []}))
    # No dataset or annotation directory exists. Verification must return pending.
    assert sibr(tmp_path) == {'status': 'pending', 'predictions': 0, 'expected': 400}
