"""Re-render saved PDF OCR coordinates and score frozen manual text labels, without inference."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'backend'))
from app.ocr_observations import canonical, normalize, sha  # noqa: E402
from app.ocr_pdf import VERSION, locate_page, page_geometry  # noqa: E402
from benchmarks.ocr_images import digest, evaluate_prediction, resolve_file  # noqa: E402
from scripts.ocr_pdf import SOURCE_FILES  # noqa: E402


def summarize(prediction, dataset, scans):
    import numpy as np
    import pypdfium2 as pdfium
    result = json.loads(prediction.read_text(encoding='utf-8'))
    manifest = json.loads((dataset / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['schema_version'] != 'synthetic-ocr-pdf-v1' or result['schema_version'] != VERSION:
        raise ValueError('Unknown PDF OCR schema')
    if result.get('confirmation') != 'unconfirmed' or result.get('ledger_writes') != 0:
        raise ValueError('OCR must remain unconfirmed and read-only')
    if set(result['source_sha256_by_file']) != set(SOURCE_FILES):
        raise ValueError('Incomplete OCR source hashes')
    for name, expected in result['source_sha256_by_file'].items():
        if digest(ROOT / name) != expected:
            raise ValueError('Changed OCR implementation; retain old prediction and make a new run')
    pdf_path = resolve_file(dataset, manifest['pdf'])
    data = pdf_path.read_bytes()
    if sha(data) != manifest['pdf_sha256'] or result['source_sha256'] != sha(data):
        raise ValueError('PDF source changed')
    if digest(scans / 'manifest.json') != manifest['scan_manifest_sha256']:
        raise ValueError('Independent scan dataset changed')
    scan_manifest = json.loads((scans / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['pages'] != [{'page': i + 1, **case} for i, case in enumerate(scan_manifest['cases'])]:
        raise ValueError('PDF page/truth mapping changed')
    if result['page_count'] != len(manifest['pages']) or len(result['pages']) != result['page_count']:
        raise ValueError('Incomplete page results')
    failed = sum(p['status'] == 'failed' for p in result['pages'])
    expected_status = ('failed' if failed == result['page_count'] else 'completed_with_failures' if failed else
                       'requires_field_verification' if any(p.get('observations') for p in result['pages']) else 'no_text_detected')
    if result['failed_pages'] != failed or result['status'] != expected_status:
        raise ValueError('Failure count or status mismatch')
    scores, hidden_characters = [], 0
    with pdfium.PdfDocument(data) as document:
        if len(document) != result['page_count']:
            raise ValueError('Page count differs from original PDF')
        for index, (saved, case) in enumerate(zip(result['pages'], manifest['pages'])):
            if saved['page'] != index + 1 or saved['confirmation'] != 'unconfirmed':
                raise ValueError('Page order or confirmation mismatch')
            with closing(document[index]) as page:
                with closing(page.get_textpage()) as textpage:
                    hidden_characters += textpage.count_chars()
                if saved['status'] == 'failed':
                    if 'error' not in saved or 'observations' in saved:
                        raise ValueError('Failed page must not look successful')
                else:
                    geometry = page_geometry(page, result['engine']['dpi'] / 72)
                    with closing(page.render(scale=result['engine']['dpi'] / 72,
                                             may_draw_forms=False, draw_annots=True)) as bitmap:
                        image = bitmap.to_pil().convert('RGB')
                        width, height = image.size
                        rendered_sha = sha(canonical({'width': width, 'height': height}) + np.array(image).tobytes())
                        observations = saved['observations']
                        raw = {'res': {'rec_texts': [r['text'] for r in observations],
                                       'rec_scores': [r['confidence'] for r in observations],
                                       'rec_polys': [r['locator']['polygon_px'] for r in observations]}}
                        normalized = normalize(raw, source_hash=rendered_sha, width=width, height=height,
                                               engine=result['engine'])
                        rebuilt = locate_page(normalized, index + 1, geometry, bitmap.get_posconv(page),
                                              sha(data), result['engine'])
                    if rebuilt != saved:
                        raise ValueError('Saved observation, ID or PDF coordinates differ from original render')
            truth_path = resolve_file(scans, case['truth'])
            if digest(truth_path) != case['truth_sha256']:
                raise ValueError('Changed independent truth')
            truth = json.loads(truth_path.read_text(encoding='utf-8'))
            scores.append({'page': index + 1, 'status': saved['status'],
                           **evaluate_prediction(None if saved['status'] == 'failed' else saved, truth)})
    if hidden_characters:
        raise ValueError('Synthetic scan PDF unexpectedly contains text-layer characters')
    totals = {key: sum(p[key] for p in scores) for key in
              ('literal_hits', 'literal_total', 'line_hits', 'line_total', 'blank_correct')}
    return {'schema_version': 'pdf-ocr-validation-summary-v1', 'status': result['status'],
            'prediction_sha256': digest(prediction), 'dataset_sha256': digest(dataset / 'manifest.json'),
            'source_pdf_sha256': sha(data), 'source_sha256_by_file': result['source_sha256_by_file'],
            'engine': result['engine'], 'page_count': result['page_count'], 'failed_pages': failed,
            'hidden_text_characters': hidden_characters, 'all_saved_page_coordinates_revalidated': True,
            'pages': scores, 'totals': totals, 'confirmation': 'unconfirmed', 'ledger_writes': 0,
            'scope': 'synthetic_printed_pdf_not_real_vouchers_or_field_confirmation'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('prediction', 'dataset', 'scans', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    summary = summarize(args.prediction, args.dataset, args.scans)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(summary['totals']))
