"""Acquire the reviewed FinRCA revision; verify every byte before atomic publication."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def fetch(destination):
    pins = json.loads((ROOT / 'docs/external-finrca-source-pin.json').read_text(encoding='utf-8'))
    base = 'https://raw.githubusercontent.com/PratikGhawate/FinRCA-AI-Bench/' + pins['revision'] + '/'
    destination = destination.resolve()
    for relative, expected in pins['files'].items():
        target = (destination / relative).resolve()
        if not target.is_relative_to(destination):
            raise ValueError('Unsafe destination')
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise ValueError('Existing file differs: ' + relative)
            continue
        upstream = pins.get('local_aliases', {}).get(relative, relative)
        if '..' in Path(upstream).parts or upstream.startswith('/'):
            raise ValueError('Unsafe source')
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.part')
        digest = hashlib.sha256()
        size = 0
        with urlopen(base + quote(upstream, safe='/'), timeout=60) as response, temporary.open('xb') as output:
            while block := response.read(1024 * 1024):
                size += len(block)
                if size > 100 * 1024 * 1024:
                    raise ValueError('Source exceeds acquisition limit')
                digest.update(block)
                output.write(block)
        if digest.hexdigest() != expected:
            raise ValueError('Downloaded bytes differ: ' + relative)
        temporary.replace(target)
        print(relative, size, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    fetch(parser.parse_args().destination)
