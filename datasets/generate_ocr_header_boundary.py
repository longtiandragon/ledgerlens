"""Handwritten mixed-header scan; truth is not an OCR input or model correction."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def generate(output, font):
    output.mkdir(parents=True, exist_ok=False)
    (output / 'images').mkdir()
    (output / 'truth').mkdir()
    rows = [['日期', '编号', '摘要', '金额'], ['2026-09-03', 'A101', '办公采购', '10.00'],
            ['日期', '编号', '收入', '支出'], ['2026-09-10', 'B102', '900.00', '800.00']]
    image = Image.new('RGB', (1600, 1000), 'white')
    draw, face = ImageDraw.Draw(image), ImageFont.truetype(str(font), 36)
    draw.text((60, 35), '教学合成表格，不可作为真实凭证', font=face, fill='black')
    columns = [60, 460, 730, 1170, 1530]
    for row_index, values in enumerate(rows):
        for index, value in enumerate(values):
            left, _, right, _ = draw.textbbox((0, 0), value, font=face)
            x = (columns[index] + columns[index + 1] - (right - left)) / 2
            draw.text((x, 180 + row_index * 125), value, font=face, fill='black')
    for x in columns:
        draw.line((x, 150, x, 650), fill='#555555', width=2)
    for y in range(150, 651, 125):
        draw.line((60, y, 1530, y), fill='#555555', width=2)
    draw.text((60, 740), '备注：表头发生变化，所有字段仍需人工核实。', font=face, fill='black')
    path = output / 'images/mixed-header.png'
    image.save(path)
    truth = {'synthetic': True, 'printed_rows': rows, 'expected_candidates': [
        {'business_date': '2026-09-03', 'source_id': 'A101', 'description': '办公采购', 'amount': '10.00'}],
        'unsupported_header': ['日期', '编号', '收入', '支出'],
        'unassigned_later_record': ['2026-09-10', 'B102', '900.00', '800.00'],
        'notice': 'No universal amount can be inferred for the income/expense table.'}
    truth_path = output / 'truth/mixed-header.json'
    truth_path.write_text(json.dumps(truth, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    manifest = {'schema_version': 'ocr-header-boundary-fixture-v1', 'synthetic': True,
        'image_sha256': sha256(path.read_bytes()).hexdigest(), 'truth_sha256': sha256(truth_path.read_bytes()).hexdigest(),
        'font_sha256': sha256(font.read_bytes()).hexdigest(), 'generator_sha256': sha256(Path(__file__).read_bytes()).hexdigest()}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--font', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(generate(args.output, args.font)))
