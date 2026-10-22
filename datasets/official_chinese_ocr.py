"""Acquire a pinned official Chinese blank invoice example for local validation.

Source bytes remain local; no open-content license or real-invoice authenticity
is inferred. The labels are manually transcribed from the visible official form
before OCR, and are evaluator data, never supplied to the OCR worker.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from urllib.request import urlopen

URL = 'https://tianjin.chinatax.gov.cn/uploadFiles/file/20201221/20201221113818_870.pdf'
SHA256 = '122543cc1bad3376dbc9e80e5f2ac1ef4566fdd8e731e6c3e6deb35e3f097212'
LABELS = ['增值税电子专用发票', '发票代码', '发票号码', '开票日期', '机器编号', '纳税人识别号',
          '开户行及账号', '项目名称', '规格型号', '数量', '单价', '金额', '税率', '税额', '价税合计']


def collect(output, source=None):
    if source is not None:
        data = source.read_bytes()
    else:
        with urlopen(URL, timeout=45) as response:
            data = response.read(20 * 1024 * 1024 + 1)
    if sha256(data).hexdigest() != SHA256:
        raise ValueError('Official sample bytes changed; review source before changing the pin')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'truth').mkdir()
    (output / 'vat-special-official.pdf').write_bytes(data)
    truth = {'schema_version': 'official-chinese-invoice-labels-v1', 'labels': LABELS,
             'expected_business_records': 0, 'financial_values': 'blank',
             'annotation_basis': 'Manual visible-form transcription before OCR; not engine output'}
    truth_path = output / 'truth/labels.json'
    truth_path.write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding='utf-8')
    manifest = {'schema_version': 'official-chinese-ocr-source-v1', 'publisher': '国家税务总局天津市税务局',
                'source_url': URL, 'source_sha256': SHA256, 'input': 'vat-special-official.pdf',
                'data_nature': 'official_blank_template_not_real_enterprise_invoice',
                'rights': 'Public official example, local evaluation only; no open license inferred; do not redistribute source',
                'truth': 'truth/labels.json', 'truth_sha256': sha256(truth_path.read_bytes()).hexdigest()}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    print(json.dumps(collect(args.output, args.source), ensure_ascii=True))
