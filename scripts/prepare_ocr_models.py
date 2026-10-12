"""Explicit one-time download of hash-pinned official OCR weights; no inference."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def prepare(output):
    manifest = json.loads((ROOT / 'docs/ocr-model-sources.json').read_text(encoding='utf-8'))
    if output.exists() and any(output.iterdir()):
        raise ValueError('Choose a new empty model directory')
    output.mkdir(parents=True, exist_ok=True)
    for model in manifest['models']:
        if not model['url'].startswith('https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/'):
            raise ValueError('Unregistered model origin')
        archive = output / (model['name'] + '.tar')
        with urllib.request.urlopen(model['url'], timeout=60) as response, archive.open('xb') as handle:
            length = 0
            while part := response.read(1024 * 1024):
                length += len(part)
                if length > model['bytes']:
                    raise ValueError('Model download exceeds pinned length')
                handle.write(part)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != model['archive_sha256']:
            raise ValueError('Downloaded model hash differs; archive retained, not loaded')
        with tarfile.open(archive) as source:
            files = [item for item in source.getmembers() if not item.isdir()]
            if any(not item.isfile() for item in files) or {item.name for item in files} != set(model['files']):
                raise ValueError('Unexpected model archive contents')
            if sum(item.size for item in files) > 100 * 1024 * 1024:
                raise ValueError('Expanded model exceeds limit')
            source.extractall(output, filter='data')
        for filename, expected in model['files'].items():
            if hashlib.sha256((output / filename).read_bytes()).hexdigest() != expected:
                raise ValueError('Extracted model differs from registry')
    return len(manifest['models'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--download', action='store_true', help='Explicitly permit free official model downloads')
    args = parser.parse_args()
    if not args.download:
        parser.error('Downloading weights requires --download; inference never downloads them')
    print(json.dumps({'models_prepared': prepare(args.output)}))
