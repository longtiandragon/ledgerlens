import json

from scripts.verify_finder_retrieval import verify


def test_running_trial_does_not_require_or_open_labels(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    (run / 'report.json').write_text(json.dumps({'status': 'running'}))
    assert verify(tmp_path / 'missing-inputs', run, tmp_path)['status'] == 'pending'


def test_unscored_predictions_do_not_open_labels(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    (run / 'report.json').write_text(json.dumps({'status': 'predicted_not_scored'}))
    assert verify(tmp_path / 'missing-inputs', run, tmp_path)['status'] == 'pending'
