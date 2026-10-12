"""Synthetic printed scans, explicitly labelled; no production OCR used for truth."""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

CASES = [
    ('clear', '合成采购单 A-017', ['日期: 2026-09-12', '供应方: 模拟文具公司',
      '未税金额: 1,234.50', '税额: 160.49', '价税合计: 1,394.99'], ['A-017', '2026-09-12', '1,234.50', '160.49', '1,394.99']),
    ('skew', '合成收款单 B-028', ['日期: 2026-09-18', '付款方: 模拟商贸公司',
      '本次收款: 8,008.08', '尚欠金额: 0.00', '币种: CNY'], ['B-028', '2026-09-18', '8,008.08', '0.00', 'CNY']),
    ('faint', '合成费用单 C-039', ['日期: 2026-09-23', '报销人: 测试甲',
      '交通费: 108.01', '办公费: 80.10', '合计: 188.11'], ['C-039', '2026-09-23', '108.01', '80.10', '188.11']),
    ('small', '合成对账单 D-041', ['期间: 2026-09', '往来方: 模拟设备公司',
      '期初余额: -2,000.00', '本期发生: 12,000.00', '期末余额: 10,000.00'],
     ['D-041', '2026-09', '-2,000.00', '12,000.00', '10,000.00']),
    ('confusable', '合成核对单 E-052', ['编号: O0-I1-0856', '金额一: 0.10', '金额二: 0.01',
      '备注: 这两个金额不得互换', '结论: 尚未人工复核'], ['E-052', 'O0-I1-0856', '0.10', '0.01']),
    ('blank', '', [], []),
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def generate(output, font_path):
    output, font_path = Path(output), Path(font_path)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a new empty output directory')
    (output / 'images').mkdir(parents=True, exist_ok=True)
    (output / 'truth').mkdir()
    manifest = {'schema_version': 'synthetic-ocr-v1', 'synthetic': True,
                'font_sha256': sha(font_path), 'generator_sha256': sha(__file__), 'cases': []}
    for index, (variant, title, body, tokens) in enumerate(CASES, 1):
        identity = f'scan-{index:03}'
        image = Image.new('RGB', (1400, 1000), 'white')
        draw = ImageDraw.Draw(image)
        size = 28 if variant == 'small' else 44
        font = ImageFont.truetype(str(font_path), size)
        lines = [] if variant == 'blank' else [title, '教学合成样本，不可作为真实凭证', *body]
        for row, line in enumerate(lines):
            draw.text((90, 60 + row * 105), line, font=font, fill='black')
        if variant == 'skew':
            image = image.rotate(2, Image.Resampling.BICUBIC, fillcolor='white')
        if variant == 'faint':
            image = ImageEnhance.Contrast(image.filter(ImageFilter.GaussianBlur(.7))).enhance(.25)
        image_path = output / 'images' / (identity + '.png')
        image.save(image_path)
        truth_path = output / 'truth' / (identity + '.json')
        truth_path.write_text(json.dumps({'case_id': identity, 'lines': lines, 'literal_tokens': tokens,
                                         'variant': variant}, ensure_ascii=False, indent=2), encoding='utf-8')
        manifest['cases'].append({'case_id': identity, 'image': image_path.relative_to(output).as_posix(),
                                  'image_sha256': sha(image_path), 'truth': truth_path.relative_to(output).as_posix(),
                                  'truth_sha256': sha(truth_path)})
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--font', type=Path, required=True, help='Local licensed CJK font; font bytes are not distributed')
    args = parser.parse_args()
    print(json.dumps({'cases': len(generate(args.output, args.font)['cases'])}))
