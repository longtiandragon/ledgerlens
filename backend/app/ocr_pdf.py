"""Bounded scanned-PDF OCR with original PDF coordinates, no ledger conversion.

Run in the optional OCR worker process, sequentially. PDFium is not thread-safe;
the lock serializes this module's calls, not arbitrary PDFium users elsewhere.
"""
from copy import deepcopy
from contextlib import closing
from importlib.metadata import version
import math
from pathlib import Path
from threading import Lock

from .ocr_observations import canonical, normalize, sha

VERSION = 'local-pdf-ocr-observations-v1'
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_PAGES = 20
MAX_PAGE_PIXELS = 16_000_000
MAX_TOTAL_PIXELS = 80_000_000
PDFIUM_LOCK = Lock()


def page_geometry(page, scale):
    width, height = page.get_size()
    if any(not math.isfinite(n) or n <= 0 for n in (width, height)):
        raise ValueError('Invalid PDF page size')
    pixels = math.ceil(width * scale) * math.ceil(height * scale)
    if pixels > MAX_PAGE_PIXELS:
        raise ValueError('PDF page exceeds raster pixel budget')
    return {'width_pt': width, 'height_pt': height, 'rotation_degrees': page.get_rotation(),
            'bbox_pdf': list(page.get_bbox()), 'planned_pixels': pixels}


def locate_page(prediction, page_number, geometry, converter, source_hash, engine):
    """Map bitmap coordinates through the renderer's affine transform, incl. crop/rotation."""
    width, height = prediction['image']['width'], prediction['image']['height']
    origin, along_x, along_y = converter.to_page(0, 0), converter.to_page(width, 0), converter.to_page(0, height)
    transform = {'origin': list(origin), 'x_edge': list(along_x), 'y_edge': list(along_y)}
    observations = []
    for observed in prediction['observations']:
        item = deepcopy(observed)
        points = item['locator']['polygon_px']
        normalized = [[x / width, y / height] for x, y in points]
        pdf_points = [[origin[d] + x * (along_x[d] - origin[d]) + y * (along_y[d] - origin[d])
                       for d in (0, 1)] for x, y in normalized]
        if any(not math.isfinite(v) for point in pdf_points for v in point):
            raise ValueError('Non-finite PDF coordinate mapping')
        item['locator'].update(page=page_number, polygon_normalized=normalized,
                               polygon_pdf_user_space=pdf_points)
        item['observation_id'] = 'ocr-pdf-' + sha(canonical({
            'source': source_hash, 'rendered_source': prediction['source_sha256'],
            'engine': engine, 'geometry': geometry, 'transform': transform,
            'text': item['text'], 'locator': item['locator']}))
        observations.append(item)
    return {'page': page_number, 'status': prediction['status'], 'confirmation': 'unconfirmed',
            'rendered_rgb_sha256': prediction['source_sha256'],
            'image': {'width': width, 'height': height}, 'geometry': geometry,
            'bitmap_to_pdf': transform, 'observations': observations,
            # Rendered-bitmap pixels; regions the recogniser detected but could not read.
            'unreadable_regions': deepcopy(prediction.get('unreadable_regions', []))}


def parse_pdf(path, ocr, *, dpi=144):
    """Accept immutable local bytes. A failed page remains failed, never blank success."""
    path = Path(path)
    if path.suffix.lower() != '.pdf':
        raise ValueError('Scanned PDF OCR requires a local PDF')
    if type(dpi) is not int or not 72 <= dpi <= 300:
        raise ValueError('PDF raster DPI must be an integer from 72 to 300')
    with path.open('rb') as handle:
        data = handle.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES or not data.startswith(b'%PDF-'):
        raise ValueError('PDF is oversized or has an invalid header')
    if version('pypdfium2') != '5.14.0':
        raise ValueError('Unvalidated PDF renderer version')
    import pypdfium2 as pdfium
    import numpy as np
    source_hash, scale = sha(data), dpi / 72
    engine = {**deepcopy(ocr.engine), 'pdf_renderer': version('pypdfium2'),
              'pdfium_build': str(pdfium.PDFIUM_INFO), 'dpi': dpi, 'wrapper': VERSION}
    result = {'schema_version': VERSION, 'source_sha256': source_hash, 'engine': engine,
              'confirmation': 'unconfirmed', 'ledger_writes': 0, 'pages': [],
              'notice': '扫描PDF识别仅为待核实文字；原页坐标和置信度不证明字段或业务事实，不能直接入账。'}
    with PDFIUM_LOCK, pdfium.PdfDocument(data) as document:
        count = len(document)
        if not 1 <= count <= MAX_PAGES:
            raise ValueError('PDF page count exceeds the bounded OCR limit')
        if document.get_formtype() != 0:
            raise ValueError('Interactive PDF forms are unsupported; visible values may differ from field data')
        result['page_count'] = count
        geometry, total_pixels = [], 0
        # Preflight ALL pages before invoking the model on any page.
        for index in range(count):
            with closing(document[index]) as page:
                metadata = page_geometry(page, scale)
                total_pixels += metadata['planned_pixels']
                if total_pixels > MAX_TOTAL_PIXELS:
                    raise ValueError('PDF exceeds total raster pixel budget')
                geometry.append(metadata)
        for index in range(count):
            try:
                with closing(document[index]) as page, closing(page.render(
                        scale=scale, may_draw_forms=False, draw_annots=True)) as bitmap:
                    image = bitmap.to_pil().convert('RGB')
                    width, height = image.size
                    if width * height > MAX_PAGE_PIXELS:
                        raise ValueError('Rendered page exceeds pixel limit')
                    rgb = np.array(image)
                    # Bind the exact rendered dimensions and RGB buffer handed to OCR.
                    raster_hash = sha(canonical({'width': width, 'height': height}) + rgb.tobytes())
                    predicted = list(ocr.model.predict(rgb[:, :, ::-1].copy()))
                    if len(predicted) != 1:
                        raise ValueError('OCR returned an unexpected page count')
                    normalized = normalize(predicted[0].json, source_hash=raster_hash,
                                           width=width, height=height, engine=engine)
                    output = locate_page(normalized, index + 1, geometry[index], bitmap.get_posconv(page),
                                         source_hash, engine)
            except Exception as exc:
                output = {'page': index + 1, 'status': 'failed', 'confirmation': 'unconfirmed',
                          'error': {'type': type(exc).__name__, 'message': str(exc)[:250]}}
            result['pages'].append(output)
    failed = sum(page['status'] == 'failed' for page in result['pages'])
    result['failed_pages'] = failed
    result['status'] = ('failed' if failed == count else 'completed_with_failures' if failed
                        else 'requires_field_verification' if any(p['observations'] for p in result['pages'])
                        else 'no_text_detected')
    return result
