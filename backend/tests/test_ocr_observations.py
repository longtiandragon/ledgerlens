"""Hand-authored OCR transport boundaries; no model or network needed."""
from copy import deepcopy
from types import SimpleNamespace
import json

from PIL import Image
import pytest

from app import ocr_observations as ocr
from app.imports import parse_file
from benchmarks.ocr_images import evaluate_prediction
from datasets.generate_ocr import generate
from scripts.summarize_ocr import SOURCE_FILES, ROOT, summarize, digest


def raw():
    return {'res': {'rec_texts': ['金额: 9007199254740993.01'], 'rec_scores': [.999],
                    'rec_polys': [[[1, 2], [90, 2], [90, 18], [1, 18]]]}}


def normalized(value=None, **kwargs):
    return ocr.normalize(raw() if value is None else value, source_hash=kwargs.get('source_hash', 'a' * 64),
                         width=100, height=50, engine={'models_sha256': 'b' * 64})


def test_high_confidence_text_remains_unconfirmed_and_cannot_import_as_ledger():
    result = normalized()
    assert result['status'] == 'requires_field_verification'
    assert result['observations'][0]['confirmation'] == 'unconfirmed'
    assert result['observations'][0]['text'] == '金额: 9007199254740993.01'
    rows, errors, *_ = parse_file(json.dumps(result).encode(), 'ocr.json')
    assert rows == [] and errors
    assert 'records' not in result and result['ledger_writes'] == 0


def test_ids_bind_image_and_coordinates_and_no_mutation():
    value = raw()
    before = deepcopy(value)
    first = normalized(value)
    assert value == before
    assert first == normalized(value)
    other = normalized(value, source_hash='c' * 64)
    assert first['observations'][0]['observation_id'] != other['observations'][0]['observation_id']
    value['res']['rec_polys'][0][0][0] = 0
    assert first['observations'][0]['locator']['polygon_px'][0][0] == 1
    assert normalized(value)['observations'][0]['observation_id'] != first['observations'][0]['observation_id']


@pytest.mark.parametrize('change', [
    lambda r: r['res'].pop('rec_polys'),
    lambda r: r['res']['rec_scores'].clear(),
    lambda r: r['res']['rec_scores'].__setitem__(0, float('nan')),
    lambda r: r['res']['rec_scores'].__setitem__(0, True),
    lambda r: r['res']['rec_polys'][0][0].__setitem__(0, -1),
    lambda r: r['res']['rec_polys'][0][0].__setitem__(1, 51),
    lambda r: r['res']['rec_polys'].__setitem__(0, [[0, 0]] * 4),
    lambda r: r['res']['rec_texts'].__setitem__(0, ''),
])
def test_malformed_or_outside_image_output_is_failure_not_no_text(change):
    result = raw()
    change(result)
    with pytest.raises(ValueError):
        normalized(result)


def test_valid_empty_arrays_explicitly_mean_no_text():
    result = normalized({'res': {'rec_texts': [], 'rec_scores': [], 'rec_polys': []}})
    assert result['status'] == 'no_text_detected' and result['confirmation'] == 'unconfirmed'


def test_image_is_immutable_pixels_not_reopened_by_engine(tmp_path):
    path = tmp_path / 'scan.png'
    Image.new('RGB', (100, 50), 'white').save(path)
    digest = ocr.sha(path.read_bytes())

    def predict(pixels):
        assert pixels.shape == (50, 100, 3)
        assert pixels[0, 0].tolist() == [255, 255, 255]
        path.write_bytes(b'changed-after-read')
        return [SimpleNamespace(json=raw())]

    adapter = ocr.LocalOCR.__new__(ocr.LocalOCR)
    adapter.engine = {'test': True}
    adapter.model = SimpleNamespace(predict=predict)
    result = adapter.parse(path)
    assert result['source_sha256'] == digest


def test_invalid_input_rejected_before_inference(tmp_path, monkeypatch):
    adapter = ocr.LocalOCR.__new__(ocr.LocalOCR)
    adapter.model = SimpleNamespace(predict=lambda *_: pytest.fail('invalid file reached inference'))
    path = tmp_path / 'scan.png'
    Image.new('RGB', (100, 50), 'white').save(path)
    monkeypatch.setattr(ocr, 'MAX_PIXELS', 4999)
    with pytest.raises(ValueError, match='pixel'):
        adapter.parse(path)
    with pytest.raises(ValueError, match='local PNG'):
        adapter.parse(tmp_path / 'truth.json')
    monkeypatch.setattr(ocr, 'MAX_BYTES', 10)
    with pytest.raises(ValueError, match='byte'):
        adapter.parse(path)


def test_missing_weights_fail_before_optional_dependency_import(tmp_path):
    with pytest.raises(ValueError, match='Missing'):
        ocr.LocalOCR(tmp_path, tmp_path / 'cache')


def test_tampered_weights_fail_before_import(tmp_path):
    for name in ocr.MODEL_NAMES:
        directory = tmp_path / (name + '_infer')
        directory.mkdir()
        for filename in ('inference.json', 'inference.pdiparams', 'inference.yml'):
            (directory / filename).write_bytes(b'unregistered-content')
    with pytest.raises(ValueError, match='registered official'):
        ocr.LocalOCR(tmp_path, tmp_path / 'cache')


def test_eval_keeps_digit_errors_and_does_not_generate_truth_from_ocr():
    truth = {'lines': ['金额: 108.01'], 'literal_tokens': ['108.01']}
    prediction = {'observations': [{'text': '金额: 108.10'}]}
    assert evaluate_prediction(prediction, truth)['literal_hits'] == 0
    assert evaluate_prediction(None, truth)['literal_total'] == 1
    assert evaluate_prediction({'observations': []}, {'lines': [], 'literal_tokens': []})['blank_correct']


def test_dataset_refuses_overwriting_existing_images(tmp_path):
    (tmp_path / 'keep').write_text('original')
    with pytest.raises(ValueError, match='new empty'):
        generate(tmp_path, tmp_path / 'absent-font')


@pytest.mark.parametrize('corruption', ['none', 'score', 'failure_count', 'empty_hashes', 'auto_confirmed', 'missing_prediction'])
def test_summary_verifies_predictions_counts_sources_and_truth(tmp_path, corruption):
    image, truth = tmp_path / 'input.png', tmp_path / 'truth.json'
    image.write_bytes(b'fixture-original')
    truth.write_text(json.dumps({'lines': ['金额: 9007199254740993.01'], 'literal_tokens': ['9007199254740993.01']}))
    case = {'case_id': 'fixture', 'image': image.name, 'truth': truth.name,
            'image_sha256': digest(image), 'truth_sha256': digest(truth)}
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'cases': [case]}))
    prediction = normalized(source_hash=digest(image))
    scores = evaluate_prediction(prediction, json.loads(truth.read_text()))
    report = {'dataset_sha256': digest(manifest), 'status': 'completed', 'failed_cases': 0,
              'source_sha256': {name: digest(ROOT / name) for name in SOURCE_FILES},
              'cases': [{'case_id': 'fixture', 'prediction': prediction, 'scores': scores}],
              'totals': scores, 'engine': {}, 'duration_seconds': 1, 'network_attempts_blocked': 0,
              'external_model_requests': 0, 'ledger_writes': 0, 'scope': 'fixture'}
    if corruption == 'score':
        report['cases'][0]['scores']['literal_hits'] = 0
    elif corruption == 'failure_count':
        report['failed_cases'] = 1
    elif corruption == 'empty_hashes':
        report['source_sha256'] = {}
    elif corruption == 'auto_confirmed':
        prediction['observations'][0]['confirmation'] = 'human_confirmed'
    elif corruption == 'missing_prediction':
        report['cases'][0].pop('prediction')
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(report))
    if corruption == 'none':
        assert summarize(path, tmp_path)['totals']['literal_hits'] == 1
    else:
        with pytest.raises(ValueError):
            summarize(path, tmp_path)
