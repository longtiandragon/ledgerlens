"""Read-only pre-publication audit of every blob reachable from any ref (Apache-2.0).

Run before making the repository public. Blocking findings: credential patterns,
committed local data (.env, SQLite, .local/, runtime/), blobs over 1 MB and
third-party originals by extension. E-mail addresses other than the maintainer's
and GitHub/bot noreply addresses are listed for review but do not block.
Never prints a matched secret; only the path, commit-independent blob id and kind.

Usage: python scripts/audit_publication.py [--output docs/publication-audit.json]
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 1_000_000
SECRETS = {
    'api key (sk-)': re.compile(rb'sk-[A-Za-z0-9]{32,}'),
    'github token': re.compile(rb'gh[pousr]_[A-Za-z0-9]{36,}'),
    'aws access key': re.compile(rb'AKIA[0-9A-Z]{16}'),
    'private key': re.compile(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
    'deepseek key assignment': re.compile(rb'DEEPSEEK_API_KEY\s*=\s*["\']?[A-Za-z0-9-]{20,}'),
}
# Test fixtures use obvious placeholder values; anything else matching a pattern blocks.
PLACEHOLDER = re.compile(rb'(fixture|test|fake|dummy|offline|invalid|example|placeholder|xxxx)', re.I)
LOCAL_DATA = re.compile(r'(^|/)(\.env$|\.local/|runtime/)|\.sqlite3?$|\.db$')
ORIGINALS = re.compile(r'\.(pdf|zip|xes|gz|ofd|png|jpe?g|tiff?|xlsx)$', re.I)
EMAIL = re.compile(rb'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
ALLOWED_EMAIL = re.compile(rb'(240200079@mail\.szpu\.edu\.cn|noreply@anthropic\.com|@users\.noreply\.github\.com|'
                           rb'noreply@github\.com|@example\.(com|org|invalid)|@ethicalsource\.dev)$')


def git(*args, root: Path = ROOT) -> bytes:
    return subprocess.run(['git', *args], cwd=root, capture_output=True, check=True).stdout


def blobs(root: Path = ROOT):
    seen = {}
    for line in git('rev-list', '--objects', '--all', root=root).decode('utf-8', 'replace').splitlines():
        sha, _, path = line.partition(' ')
        if path:
            seen.setdefault(sha, path)
    batch = subprocess.run(['git', 'cat-file', '--batch-check=%(objectname) %(objecttype) %(objectsize)'],
                           cwd=root, input='\n'.join(seen).encode(), capture_output=True, check=True).stdout
    for line in batch.decode().splitlines():
        sha, kind, size = line.split()
        if kind == 'blob':
            yield sha, seen[sha], int(size)


def audit(root: Path = ROOT) -> dict:
    blocking, review, scanned = [], [], 0
    for sha, path, size in blobs(root):
        scanned += 1
        if LOCAL_DATA.search(path):
            blocking.append({'kind': 'local data path', 'path': path, 'blob': sha})
        if ORIGINALS.search(path):
            blocking.append({'kind': 'third-party original extension', 'path': path, 'blob': sha})
        if size > MAX_BYTES:
            blocking.append({'kind': 'blob over 1 MB', 'path': path, 'blob': sha, 'bytes': size})
            continue
        data = git('cat-file', 'blob', sha, root=root)
        for kind, pattern in SECRETS.items():
            real = [m for m in pattern.finditer(data) if not PLACEHOLDER.search(m.group(0))]
            if real:
                blocking.append({'kind': kind, 'path': path, 'blob': sha, 'matches': len(real)})
        others = sorted({m.decode() for m in EMAIL.findall(data) if not ALLOWED_EMAIL.search(m)})
        if others:
            review.append({'kind': 'e-mail address', 'path': path, 'count': len(others)})
    authors = sorted(set(git('log', '--all', '--format=%an <%ae>', root=root).decode('utf-8', 'replace').splitlines()))
    return {'schema_version': 'publication-audit-v1', 'created_at': datetime.now(UTC).isoformat(),
            'head': git('rev-parse', 'HEAD', root=root).decode().strip(), 'blobs_scanned': scanned,
            'commit_authors': authors, 'blocking': blocking, 'review': review,
            'passed': not blocking}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path)
    options = parser.parse_args(argv)
    report = audit()
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if options.output:
        options.output.write_text(text, encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('head', 'blobs_scanned', 'passed')} |
                     {'blocking': len(report['blocking']), 'review': len(report['review'])}, ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
