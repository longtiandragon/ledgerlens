"""Wrap frozen synthetic scan images as image-only PDF pages; never run OCR."""
import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate(scans, output):
    from reportlab.pdfgen.canvas import Canvas
    from PIL import Image
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a new empty PDF fixture directory')
    manifest_path = scans / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest['schema_version'] != 'synthetic-ocr-v1':
        raise ValueError('Unknown frozen scan manifest')
    output.mkdir(parents=True, exist_ok=True)
    pdf = output / 'synthetic-scans.pdf'
    canvas = Canvas(str(pdf), pagesize=(700, 500), invariant=1)
    canvas.setTitle('LedgerLens synthetic OCR fixture - not a real voucher')
    pages = []
    for number, case in enumerate(manifest['cases'], 1):
        path = (scans / case['image']).resolve(strict=True)
        if not path.is_relative_to(scans.resolve()) or digest(path) != case['image_sha256']:
            raise ValueError('Changed or escaping scan image')
        with Image.open(path) as image:
            width, height = image.size
        canvas.setPageSize((width / 2, height / 2))
        canvas.drawImage(str(path), 0, 0, width=width / 2, height=height / 2)
        canvas.showPage()
        pages.append({'page': number, **case})
    canvas.save()
    result = {'schema_version': 'synthetic-ocr-pdf-v1', 'synthetic': True,
              'pdf': pdf.name, 'pdf_sha256': digest(pdf), 'pages': pages,
              'scan_manifest_sha256': digest(manifest_path),
              'generator_sha256': digest(Path(__file__))}
    (output / 'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scans', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({'pages': len(generate(args.scans, args.output)['pages'])}))
