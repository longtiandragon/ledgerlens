"""Independent printed table fixtures; answer sidecars never enter runtime OCR."""
import argparse
import json
from pathlib import Path
from hashlib import sha256

from PIL import Image, ImageDraw, ImageFont

ROWS = [
    ['2026-09-03', 'T101', '办公采购', '1,250.50'],
    ['2026-09-10', 'T102', '预付订金', '0.00'],
    ['2026-09-21', 'T103', '设备维修', '2,040.07'],
]


def generate(output, font):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'images').mkdir()
    (output / 'truth').mkdir()
    cases = []
    for identity, headers in [('clear-table', ['日期', '编号', '摘要', '金额']),
                              ('unsupported-columns', ['日期', '编号', '收入', '支出'])]:
        image = Image.new('RGB', (1600, 1000), 'white')
        draw = ImageDraw.Draw(image)
        face = ImageFont.truetype(str(font), 36)
        draw.text((60, 35), '教学合成表格，不可作为真实凭证', font=face, fill='black')
        columns = [60, 460, 730, 1170, 1530]
        rows = [headers, *ROWS]
        for row_index, values in enumerate(rows):
            y = 180 + row_index * 125
            for index, value in enumerate(values):
                left, _, right, _ = draw.textbbox((0, 0), value, font=face)
                x = (columns[index] + columns[index + 1] - (right - left)) / 2
                draw.text((x, y), value, font=face, fill='black')
        for x in columns:
            draw.line((x, 150, x, 650), fill='#555555', width=2)
        for y in range(150, 651, 125):
            draw.line((60, y, 1530, y), fill='#555555', width=2)
        draw.text((60, 740), '备注：仅用于离线工程验收，所有字段仍需人工核实。', font=face, fill='black')
        file = output / 'images' / (identity + '.png')
        image.save(file)
        truth = {'case_id': identity, 'synthetic': True, 'headers': headers, 'printed_rows': ROWS,
                 'expected_supported': identity == 'clear-table', 'expected_record_count': 3 if identity == 'clear-table' else 0}
        truth_path = output / 'truth' / (identity + '.json')
        truth_path.write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding='utf-8')
        cases.append({'case_id': identity, 'image': file.relative_to(output).as_posix(),
                      'image_sha256': sha256(file.read_bytes()).hexdigest(), 'truth': truth_path.relative_to(output).as_posix(),
                      'truth_sha256': sha256(truth_path.read_bytes()).hexdigest()})
    manifest = {'schema_version': 'synthetic-ocr-tables-v1', 'synthetic': True, 'cases': cases,
                'font_sha256': sha256(Path(font).read_bytes()).hexdigest(), 'generator_sha256': sha256(Path(__file__).read_bytes()).hexdigest()}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--font', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps({'cases': len(generate(args.output, args.font)['cases'])}))
