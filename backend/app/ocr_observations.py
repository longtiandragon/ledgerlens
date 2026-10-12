"""Bounded local image OCR. Output is unconfirmed text, never ledger records.

No evaluator import, remote URL, automatic weight download, or financial parser.
Only the caller's already acquired model files and immutable input pixels enter
PaddleOCR; answer files are not accepted by this interface.
"""
from copy import deepcopy
import hashlib
from importlib.metadata import version
import io
import json
import math
import os
from pathlib import Path

from PIL import Image

VERSION = 'local-ocr-observations-v1'
MODEL_NAMES = ('PP-OCRv5_mobile_det', 'PP-OCRv5_mobile_rec')
MAX_BYTES, MAX_PIXELS, MAX_LINES = 20 * 1024 * 1024, 16_000_000, 2000
MODEL_MANIFEST = Path(__file__).resolve().parents[2] / 'docs/ocr-model-sources.json'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def model_files(root):
    root = Path(root).resolve(strict=True)
    files = {}
    for name in MODEL_NAMES:
        directory = root / (name + '_infer')
        for filename in ('inference.json', 'inference.pdiparams', 'inference.yml'):
            path = directory / filename
            if not path.is_file() or path.is_symlink() or directory.is_symlink() or directory.is_junction():
                raise ValueError('Missing or linked local OCR model file')
            files[path.relative_to(root).as_posix()] = sha(path.read_bytes())
    return files


def normalize(raw, *, source_hash, width, height, engine):
    if not isinstance(raw, dict) or not isinstance(raw.get('res'), dict):
        raise ValueError('Invalid OCR response envelope')
    result = raw['res']
    texts, scores, polygons = (result.get(key) for key in ('rec_texts', 'rec_scores', 'rec_polys'))
    if not all(isinstance(value, list) for value in (texts, scores, polygons)):
        raise ValueError('OCR text, confidence and polygon arrays are required')
    if len(texts) > MAX_LINES or len(texts) != len(scores) or len(texts) != len(polygons):
        raise ValueError('OCR arrays disagree or exceed limit')
    lines = []
    for index, (text, score, polygon) in enumerate(zip(texts, scores, polygons)):
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError('Invalid OCR text')
        if type(score) not in (float, int) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError('Invalid OCR confidence')
        if not isinstance(polygon, list) or len(polygon) != 4:
            raise ValueError('Invalid OCR polygon')
        for point in polygon:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError('Invalid OCR point')
            if any(type(n) not in (int, float) or not math.isfinite(n) for n in point):
                raise ValueError('Invalid OCR coordinate')
            if not (0 <= point[0] <= width and 0 <= point[1] <= height):
                raise ValueError('OCR polygon outside original image')
        area = abs(sum(polygon[i][0] * polygon[(i + 1) % 4][1]
                       - polygon[(i + 1) % 4][0] * polygon[i][1] for i in range(4)))
        if area == 0:
            raise ValueError('OCR polygon has zero area')
        item = {'text': text, 'confidence': score, 'confirmation': 'unconfirmed',
                'locator': {'page': 1, 'line': index + 1, 'polygon_px': deepcopy(polygon)}}
        item['observation_id'] = 'ocr-' + sha(canonical({'source': source_hash, 'engine': engine, 'line': item}))
        lines.append(item)
    return {'schema_version': VERSION, 'status': 'requires_field_verification' if lines else 'no_text_detected',
            'confirmation': 'unconfirmed', 'ledger_writes': 0, 'source_sha256': source_hash,
            'image': {'width': width, 'height': height, 'page_count': 1}, 'engine': deepcopy(engine), 'observations': lines,
            'notice': 'OCR文字为待核实观察；置信度不证明金额、字段归属或业务事实，不能直接入账。'}


class LocalOCR:
    def __init__(self, model_root, cache_dir):
        root = Path(model_root).resolve(strict=True)
        files = model_files(root)  # Fail before importing Paddle if weights are absent.
        registered = json.loads(MODEL_MANIFEST.read_text(encoding='utf-8'))
        expected_files = {name: value for model in registered['models'] for name, value in model['files'].items()}
        if files != expected_files:
            raise ValueError('Local OCR weights differ from registered official files')
        for name, expected in (('paddleocr', '3.7.0'), ('paddlepaddle', '3.3.1'), ('paddlex', '3.7.2')):
            if version(name) != expected:
                raise ValueError('Unvalidated OCR dependency version: ' + name)
        os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
        os.environ['PADDLE_PDX_CACHE_HOME'] = str(Path(cache_dir).resolve())
        from paddleocr import PaddleOCR
        self.engine = {'paddleocr': version('paddleocr'), 'paddlepaddle': version('paddlepaddle'),
                       'paddlex': version('paddlex'), 'models_sha256': sha(canonical(files)),
                       'model_files': files, 'device': 'cpu', 'document_transforms': False}
        self.model = PaddleOCR(text_detection_model_name=MODEL_NAMES[0],
                               text_detection_model_dir=str(root / (MODEL_NAMES[0] + '_infer')),
                               text_recognition_model_name=MODEL_NAMES[1],
                               text_recognition_model_dir=str(root / (MODEL_NAMES[1] + '_infer')),
                               use_doc_orientation_classify=False, use_doc_unwarping=False,
                               use_textline_orientation=False, device='cpu', enable_mkldnn=False,
                               cpu_threads=4)

    def parse(self, path):
        path = Path(path)
        if path.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
            raise ValueError('OCR accepts only a local PNG or JPEG image')
        with path.open('rb') as handle:
            data = handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError('OCR image exceeds byte limit')
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {'PNG', 'JPEG'} or getattr(source, 'n_frames', 1) != 1:
                raise ValueError('Only single-frame PNG/JPEG images are accepted')
            width, height = source.size
            if width * height > MAX_PIXELS:
                raise ValueError('OCR image exceeds pixel limit')
            image = source.convert('RGB')
        import numpy as np
        # Array input ensures OCR sees the exact hashed bytes, with no path race.
        results = list(self.model.predict(np.array(image)[:, :, ::-1].copy()))
        if len(results) != 1:
            raise ValueError('OCR returned an unexpected page count')
        return normalize(results[0].json, source_hash=sha(data), width=width, height=height, engine=self.engine)
