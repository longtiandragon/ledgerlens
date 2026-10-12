from copy import deepcopy
from contextlib import closing
from types import SimpleNamespace
import json

import pypdfium2 as pdfium
import pytest

from app import ocr_pdf
from app.ocr_observations import sha


def fixture_pdf(path, *, count=2, rotation=0, crop=False, width=100, height=60):
    with pdfium.PdfDocument.new() as doc:
        for _ in range(count):
            with closing(doc.new_page(width, height)) as page:
                if crop:
                    page.set_cropbox(10, 5, width - 10, height - 5)
                page.set_rotation(rotation)
        doc.save(path)


def fake_ocr(predict=None):
    def default(pixels):
        height, width = pixels.shape[:2]
        return [SimpleNamespace(json={'res': {'rec_texts': ['金额: 108.01'], 'rec_scores': [0.999],
                  'rec_polys': [[[2, 2], [width - 2, 2], [width - 2, height - 2], [2, height - 2]]]}})]
    return SimpleNamespace(engine={'fixture': True}, model=SimpleNamespace(predict=predict or default))


@pytest.mark.parametrize('rotation,crop', [(0, False), (90, False), (180, True), (270, True)])
def test_pdf_page_and_rotated_crop_coordinates_round_trip(tmp_path, rotation, crop):
    path = tmp_path / 'fixture.pdf'
    fixture_pdf(path, rotation=rotation, crop=crop)
    result = ocr_pdf.parse_pdf(path, fake_ocr())
    assert result['status'] == 'requires_field_verification'
    assert result['page_count'] == 2 and result['failed_pages'] == 0
    assert result['source_sha256'] == sha(path.read_bytes())
    with pdfium.PdfDocument(path) as doc:
        for number, observed_page in enumerate(result['pages'], 1):
            assert observed_page['page'] == number
            with closing(doc[number - 1]) as page, closing(page.render(scale=2)) as bitmap:
                converter = bitmap.get_posconv(page)
                observation = observed_page['observations'][0]
                assert observation['confirmation'] == 'unconfirmed'
                assert observation['locator']['page'] == number
                for pixel, point in zip(observation['locator']['polygon_px'],
                                         observation['locator']['polygon_pdf_user_space']):
                    assert list(converter.to_bitmap(*point)) == pixel
    first, second = [p['observations'][0]['observation_id'] for p in result['pages']]
    assert first != second  # Same pixels on different pages cannot collide.
    assert result['ledger_writes'] == 0 and 'records' not in result


def test_one_failed_page_retained_and_following_page_is_not_lost(tmp_path):
    path = tmp_path / 'fixture.pdf'
    fixture_pdf(path, count=3)
    calls = []

    def predict(pixels):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError('synthetic page failure')
        return fake_ocr().model.predict(pixels)
    result = ocr_pdf.parse_pdf(path, fake_ocr(predict))
    assert len(calls) == len(result['pages']) == 3
    assert result['status'] == 'completed_with_failures' and result['failed_pages'] == 1
    assert result['pages'][1]['status'] == 'failed' and 'observations' not in result['pages'][1]
    assert result['pages'][2]['observations'][0]['locator']['page'] == 3


def test_all_failed_pages_are_failed_not_blank(tmp_path):
    path = tmp_path / 'fixture.pdf'
    fixture_pdf(path)
    result = ocr_pdf.parse_pdf(path, fake_ocr(lambda _: []))
    assert result['status'] == 'failed' and result['failed_pages'] == 2


def test_valid_blank_document_is_no_text_and_unconfirmed(tmp_path):
    path = tmp_path / 'blank.pdf'
    fixture_pdf(path)
    result = ocr_pdf.parse_pdf(path, fake_ocr(lambda _: [SimpleNamespace(json={
        'res': {'rec_texts': [], 'rec_scores': [], 'rec_polys': []}})]))
    assert result['status'] == 'no_text_detected' and result['confirmation'] == 'unconfirmed'
    assert all(p['observations'] == [] for p in result['pages'])


@pytest.mark.parametrize('budget,value', [('MAX_PAGES', 1), ('MAX_PAGE_PIXELS', 10),
                                         ('MAX_TOTAL_PIXELS', 30000), ('MAX_PDF_BYTES', 10)])
def test_budget_preflight_never_invokes_model(tmp_path, monkeypatch, budget, value):
    path = tmp_path / 'too-large.pdf'
    fixture_pdf(path)
    monkeypatch.setattr(ocr_pdf, budget, value)
    with pytest.raises(ValueError):
        ocr_pdf.parse_pdf(path, fake_ocr(lambda _: pytest.fail('preflight failed to stop inference')))


@pytest.mark.parametrize('dpi', [True, 71, 301, 144.5])
def test_invalid_dpi(tmp_path, dpi):
    with pytest.raises(ValueError, match='DPI'):
        ocr_pdf.parse_pdf(tmp_path / 'nonexistent.pdf', fake_ocr(), dpi=dpi)


def test_source_bytes_frozen_before_first_page_inference(tmp_path):
    path = tmp_path / 'fixture.pdf'
    fixture_pdf(path)
    before = sha(path.read_bytes())

    def predict(pixels):
        path.write_bytes(b'changed-during-inference')
        return fake_ocr().model.predict(pixels)
    result = ocr_pdf.parse_pdf(path, fake_ocr(predict))
    assert result['source_sha256'] == before and len(result['pages']) == 2
    assert result['failed_pages'] == 0


def test_geometry_and_engine_are_not_aliased(tmp_path):
    path = tmp_path / 'fixture.pdf'
    fixture_pdf(path)
    ocr = fake_ocr()
    result = ocr_pdf.parse_pdf(path, ocr)
    before = deepcopy(result)
    ocr.engine['fixture'] = False
    assert result == before


@pytest.mark.parametrize('corruption', ['none', 'coordinate', 'page', 'status', 'missing_page', 'truth'])
def test_pdf_summary_reopens_original_pixels_and_rejects_changed_evidence(tmp_path, corruption):
    from scripts.summarize_ocr_pdf import summarize
    from scripts.ocr_pdf import SOURCE_FILES, ROOT
    dataset, scans = tmp_path / 'pdf', tmp_path / 'scans'
    dataset.mkdir()
    scans.mkdir()
    pdf = dataset / 'synthetic.pdf'
    fixture_pdf(pdf, count=1)
    truth = scans / 'truth.json'
    truth.write_text(json.dumps({'lines': ['金额: 108.01'], 'literal_tokens': ['108.01']}), encoding='utf-8')
    case = {'case_id': 'handwritten', 'image': 'not-needed-for-scoring.png', 'image_sha256': 'unused',
            'truth': 'truth.json', 'truth_sha256': sha(truth.read_bytes())}
    scan_manifest = scans / 'manifest.json'
    scan_manifest.write_text(json.dumps({'cases': [case]}), encoding='utf-8')
    (dataset / 'manifest.json').write_text(json.dumps({
        'schema_version': 'synthetic-ocr-pdf-v1', 'pdf': pdf.name, 'pdf_sha256': sha(pdf.read_bytes()),
        'scan_manifest_sha256': sha(scan_manifest.read_bytes()), 'pages': [{'page': 1, **case}]}), encoding='utf-8')
    result = ocr_pdf.parse_pdf(pdf, fake_ocr())
    result['source_sha256_by_file'] = {name: sha((ROOT / name).read_bytes()) for name in SOURCE_FILES}
    if corruption == 'coordinate':
        result['pages'][0]['observations'][0]['locator']['polygon_pdf_user_space'][0][0] += 1
    elif corruption == 'page':
        result['pages'][0]['observations'][0]['locator']['page'] = 2
    elif corruption == 'status':
        result['status'] = 'no_text_detected'
    elif corruption == 'missing_page':
        result['pages'] = []
    elif corruption == 'truth':
        truth.write_text('{}', encoding='utf-8')
    output = dataset / 'prediction.json'
    output.write_text(json.dumps(result), encoding='utf-8')
    if corruption == 'none':
        summary = summarize(output, dataset, scans)
        assert summary['totals']['literal_hits'] == 1
        assert summary['all_saved_page_coordinates_revalidated'] is True
        assert summary['hidden_text_characters'] == 0
    else:
        with pytest.raises(ValueError):
            summarize(output, dataset, scans)
