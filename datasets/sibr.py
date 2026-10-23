"""Prepare the entire publisher SIBR test split; keep annotations out of OCR inputs."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PIN = ROOT / 'docs/external-sibr-source-pin.json'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def acquire(raw):
    raw.mkdir(parents=True, exist_ok=True)
    pin = json.loads(PIN.read_text(encoding='utf-8'))
    for name, metadata in pin['files'].items():
        target = raw / name
        if target.exists():
            if digest(target) != metadata['Sha256']:
                raise ValueError('Existing source differs: ' + name)
            continue
        url = 'https://www.modelscope.cn/api/v1/datasets/damo/SIBR/repo?' + urlencode(
            {'Revision': metadata['Revision'], 'FilePath': name})
        partial = target.with_suffix(target.suffix + '.part')
        size = 0
        with urlopen(url, timeout=60) as response, partial.open('xb') as handle:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > metadata['Size']:
                    raise ValueError('Source exceeds published size')
                handle.write(chunk)
        if size != metadata['Size'] or digest(partial) != metadata['Sha256']:
            raise ValueError('Source integrity failed')
        partial.rename(target)


def prepare(raw, output):
    pin = json.loads(PIN.read_text(encoding='utf-8'))
    for name, metadata in pin['files'].items():
        if (raw / name).stat().st_size != metadata['Size'] or digest(raw / name) != metadata['Sha256']:
            raise ValueError('Changed publisher source: ' + name)
    test = (raw / 'test.txt').read_text().splitlines()
    train = (raw / 'train.txt').read_text().splitlines()
    if len(set(test)) != 400 or len(test) != 400 or len(set(train)) != 600 or set(test) & set(train):
        raise ValueError('Invalid publisher split')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'images').mkdir()
    (output / 'truth').mkdir()
    items = []
    with zipfile.ZipFile(raw / 'images.zip') as images, zipfile.ZipFile(raw / 'label.zip') as labels:
        by_stem = {}
        for info in images.infolist():
            if info.is_dir():
                continue
            path = PurePosixPath(info.filename)
            if path.is_absolute() or '..' in path.parts or path.suffix.lower() not in {'.jpg', '.jpeg', '.png'}:
                raise ValueError('Unexpected image archive member')
            if path.stem in by_stem:
                raise ValueError('Duplicate image identity')
            by_stem[path.stem] = info.filename
        for label in test:
            identity = PurePosixPath(label).stem
            if not identity or '/' in identity or '\\' in identity or ':' in identity:
                raise ValueError('Unsafe identity')
            name = by_stem[identity]
            for archive, member, target in [(images, name, output / 'images' / (identity + '.jpg')),
                                            (labels, label, output / 'truth' / (identity + '.json'))]:
                if archive.getinfo(member).file_size > 30_000_000:
                    raise ValueError('Oversized archive member')
                target.write_bytes(archive.read(member))
            image_path, truth_path = f'images/{identity}.jpg', f'truth/{identity}.json'
            items.append(dict(id=identity, image=image_path, image_sha256=digest(output / image_path),
                              truth=truth_path, truth_sha256=digest(output / truth_path)))
    manifest = dict(schema_version='sibr-test-input-v1', source=pin['source'], license=pin['license'],
                    source_pin_sha256=digest(PIN), split='publisher_test', cases=400,
                    selection='all test.txt entries in publisher order, no label or OCR selection', items=items)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    if args.download:
        acquire(args.raw)
    result = prepare(args.raw, args.output)
    print(json.dumps({k: v for k, v in result.items() if k != 'items'}))
