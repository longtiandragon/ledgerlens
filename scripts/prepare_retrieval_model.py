"""Explicitly acquire pinned BGE safetensors from its official model repository."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def prepare(output):
    manifest = json.loads((ROOT / 'docs/retrieval-model-v1.json').read_text(encoding='utf-8'))
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Use a new empty model directory; existing artifacts are retained')
    output.mkdir(parents=True, exist_ok=True)
    root = output.resolve()
    for item in manifest['files']:
        target = (root / item['file']).resolve()
        if not target.is_relative_to(root):
            raise ValueError('Invalid model manifest path')
        url = f'https://huggingface.co/{manifest["model"]}/resolve/{manifest["revision"]}/{item["file"]}'
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + '.partial')
        digest, count = hashlib.sha256(), 0
        with urlopen(url, timeout=90) as response, temp.open('xb') as handle:
            while data := response.read(1024 * 1024):
                count += len(data)
                if count > item['bytes']:
                    raise ValueError('Model download exceeds frozen size')
                digest.update(data)
                handle.write(data)
        if count != item['bytes'] or digest.hexdigest() != item['sha256']:
            raise ValueError('Model download differs from frozen official revision')
        temp.replace(target)
    return {'model': manifest['model'], 'revision': manifest['revision'], 'files': len(manifest['files'])}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true', required=True,
                        help='Explicit authorization to download public model files; no paid inference')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output)))
