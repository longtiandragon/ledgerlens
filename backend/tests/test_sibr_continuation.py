import pytest
from scripts.continue_sibr_ocr import validate_prefix


def inputs():
    manifest = {'split': 'publisher_test', 'items': [{'id': str(i)} for i in range(400)]}
    report = {'status': 'running', 'cases': [{'id': '0'}], 'forbidden_access_attempts': []}
    return report, manifest


def test_only_exact_unscored_prefix_can_continue():
    report, manifest = inputs()
    assert validate_prefix(report, manifest) == 1
    report['cases'][0]['id'] = '1'
    with pytest.raises(ValueError, match='prefix'):
        validate_prefix(report, manifest)


def test_previous_scoring_prevents_continuation():
    report, manifest = inputs()
    report['cases'][0]['score'] = {}
    with pytest.raises(ValueError, match='scoring'):
        validate_prefix(report, manifest)
