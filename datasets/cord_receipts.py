"""Extract the CORD v2 test split (real Indonesian receipts, CC BY 4.0) for offline OCR evaluation.

Source: huggingface.co/datasets/naver-clova-ix/cord-v2 at revision 7f0115a4b758a71d6473b8d085751692da2fef98,
file data/test-00000-of-00001-9c204eb3f4e11791.parquet (LFS SHA-256 below). Images and ground truth are
written to separate directories; OCR runs read only images, the evaluator reads truth afterwards.
Run with an interpreter that has pyarrow (the isolated .local/cord-env), reading in small batches.
"""
import argparse
import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq

REVISION = '7f0115a4b758a71d6473b8d085751692da2fef98'
TEST_SHA256 = '51c65f1788faff392abe2a0b55b023eb23e9be551c509138eaa3a832514224e7'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parquet', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='new directory')
    options = parser.parse_args()
    digest = hashlib.sha256()
    with options.parquet.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    if digest.hexdigest() != TEST_SHA256:
        raise SystemExit('Parquet hash differs from the pinned CORD v2 test split')
    options.output.mkdir(parents=False, exist_ok=False)
    (options.output / 'images').mkdir()
    (options.output / 'truth').mkdir()
    entries, index = [], 0
    for batch in pq.ParquetFile(options.parquet).iter_batches(batch_size=8, columns=['image', 'ground_truth']):
        for image, truth in zip(batch.column('image').to_pylist(), batch.column('ground_truth').to_pylist()):
            data = image['bytes']
            suffix = '.png' if data[:8] == b'\x89PNG\r\n\x1a\n' else '.jpg' if data[:3] == b'\xff\xd8\xff' else '.bin'
            name = f'cord-test-{index:03d}'
            (options.output / 'images' / (name + suffix)).write_bytes(data)
            (options.output / 'truth' / (name + '.json')).write_text(truth, encoding='utf-8')
            entries.append({'id': name, 'image': f'images/{name}{suffix}', 'image_sha256': hashlib.sha256(data).hexdigest(),
                            'truth_sha256': hashlib.sha256(truth.encode('utf-8')).hexdigest()})
            index += 1
    manifest = {'dataset': 'naver-clova-ix/cord-v2', 'revision': REVISION, 'split': 'test', 'license': 'cc-by-4.0',
                'parquet_sha256': TEST_SHA256, 'items': entries}
    (options.output / 'manifest.json').write_text(json.dumps(manifest, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({'items': len(entries), 'formats': sorted({Path(e['image']).suffix for e in entries})}))


if __name__ == '__main__':
    main()
