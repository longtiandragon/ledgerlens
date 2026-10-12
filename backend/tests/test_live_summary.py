import json

from scripts import summarize_live_validation as summary


def test_price_reports_are_not_misclassified_as_extra_model_batches(tmp_path, monkeypatch):
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'runtime').mkdir()
    reports = tmp_path / '.local/reports'
    reports.mkdir(parents=True)
    (tmp_path / 'docs/live-model-validation-summary.json').write_text('{}', encoding='utf-8')
    (reports / 'deepseek-pilot-v15.json').write_text(json.dumps({'cases': [], 'groups': {}}), encoding='utf-8')
    (reports / 'deepseek-pilot-v15-costs-in-progress.json').write_text('{}', encoding='utf-8')
    monkeypatch.setattr(summary, 'ROOT', tmp_path)
    summary.main()
    result = json.loads((tmp_path / 'docs/live-model-validation-summary.json').read_text(encoding='utf-8'))
    assert len(result['batch_history']) == 1
    assert result['batch_history'][0]['report'] == '.local/reports/deepseek-pilot-v15.json'
