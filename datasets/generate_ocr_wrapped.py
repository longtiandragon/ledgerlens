"""New teaching scan with independent expectations; never overwrite old samples."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def generate(output, font):
    output.mkdir(parents=True, exist_ok=False)
    (output / 'images').mkdir()
    (output / 'truth').mkdir()
    image = Image.new('RGB', (1600, 850), 'white')
    draw = ImageDraw.Draw(image)
    face = ImageFont.truetype(str(font), 32)
    draw.text((70, 30), '教学合成扫描，仅验证换行摘要，所有字段需人工核实', font=face, fill='black')
    for y, texts in [(150, ['日期', '编号', '摘要', '金额']),
                     (240, ['2026-09-03', 'A101', '设备维护', '1250.50']),
                     (380, ['2026-09-04', 'A102', '耗材采购', '40.00'])]:
        for x, text in zip([100, 500, 800, 1280], texts):
            draw.text((x, y), text, font=face, fill='black')
    draw.text((800, 278), '更换主机配件', font=face, fill='black')
    for y in [120, 210, 350, 460]:
        draw.line((70, y, 1510, y), fill='#555555', width=2)
    for x in [70, 440, 700, 1170, 1510]:
        draw.line((x, 120, x, 460), fill='#555555', width=2)
    draw.text((70, 550), '备注：本图不是真实企业票据，不构成会计事实。', font=face, fill='black')
    path = output / 'images/wrapped-description.png'
    image.save(path)
    truth = {'schema_version': 'independent-wrapped-table-truth-v1', 'synthetic': True, 'expected_rows': [
        {'business_date': '2026-09-03', 'source_id': 'A101', 'description': '设备维护\n更换主机配件', 'amount': '1250.50'},
        {'business_date': '2026-09-04', 'source_id': 'A102', 'description': '耗材采购', 'amount': '40.00'}]}
    truth_path = output / 'truth/wrapped-description.json'
    truth_path.write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding='utf-8')
    manifest = {'synthetic': True, 'image': 'images/wrapped-description.png',
                'image_sha256': sha256(path.read_bytes()).hexdigest(),
                'truth': 'truth/wrapped-description.json', 'truth_sha256': sha256(truth_path.read_bytes()).hexdigest(),
                'font_sha256': sha256(font.read_bytes()).hexdigest(),
                'generator_sha256': sha256(Path(__file__).read_bytes()).hexdigest()}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--font', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(generate(args.output, args.font), ensure_ascii=True))
