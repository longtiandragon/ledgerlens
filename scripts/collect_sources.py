"""Bounded public-source acquisition; no crawling, no answers fed to agents."""
import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import httpx
from bs4 import BeautifulSoup

ALLOWED = {'kjs.mof.gov.cn', 'www.mof.gov.cn', 'research.tue.nl', 'finqasite.github.io',
           'static.cninfo.com.cn', 'www.sse.com.cn'}


def collect(manifest: Path, output: Path, identifiers=None, *, transport=None):
    source_list = json.loads(manifest.read_text(encoding='utf-8'))['sources']
    ids = [source['id'] for source in source_list]
    if len(ids) != len(set(ids)) or any(not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', identity) for identity in ids):
        raise ValueError('Source IDs must be unique, safe lowercase identifiers')
    if identifiers and set(identifiers) - set(ids):
        raise ValueError('Requested source ID is not in the manifest')
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / 'acquisition-report.json'
    previous = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else []
    merged = {item['id']: item for item in previous}
    reports = []
    with httpx.Client(timeout=45, follow_redirects=False, transport=transport,
                      headers={'User-Agent': 'LedgerLens-research/0.1 (local reproducible acquisition)'}) as client:
        for source in source_list:
            if identifiers and source['id'] not in identifiers:
                continue
            item = {**source, 'retrieved_at': datetime.now(timezone.utc).isoformat(), 'status': 'failed'}
            try:
                url = source['url']
                for _ in range(4):
                    parsed = urlparse(url)
                    if parsed.scheme != 'https' or parsed.hostname not in ALLOWED:
                        raise ValueError('URL is outside the HTTPS source allowlist')
                    with client.stream('GET', url) as response:
                        if response.is_redirect:
                            from urllib.parse import urljoin
                            url = urljoin(url, response.headers['location'])
                            continue
                        response.raise_for_status()
                        parts, size = [], 0
                        for part in response.iter_bytes():
                            size += len(part)
                            if size > 25 * 1024 * 1024:
                                raise ValueError('Source exceeds 25 MB')
                            parts.append(part)
                        data = b''.join(parts)
                        content_type = response.headers.get('content-type', '')
                    break
                else:
                    raise ValueError('Redirect limit exceeded')
                sha = hashlib.sha256(data).hexdigest()
                folder = output / source['id']
                folder.mkdir(exist_ok=True)
                suffix = '.pdf' if data.startswith(b'%PDF') else '.html'
                raw = folder / f'{sha}{suffix}'
                raw.write_bytes(data)
                pages = []
                if suffix == '.pdf':
                    import pdfplumber
                    with pdfplumber.open(raw) as pdf:
                        if len(pdf.pages) > 300:
                            raise ValueError('Source PDF exceeds 300 pages')
                        pages = [{'page': n, 'text': p.extract_text() or ''} for n, p in enumerate(pdf.pages, 1)]
                else:
                    soup = BeautifulSoup(data, 'html.parser')
                    for tag in soup(['script', 'style', 'nav', 'header', 'footer']):
                        tag.decompose()
                    pages = [{'page': 1, 'text': soup.get_text('\n', strip=True)}]
                text = '\n'.join(page['text'] for page in pages)
                if len(text.strip()) < 30 or re.search(r'^\s*(502 Bad Gateway|404 Not Found|Access Denied)', text, re.I):
                    raise ValueError('Source is empty or an error page, not usable reference text')
                if source.get('required_text') and source['required_text'] not in text:
                    raise ValueError('Source does not contain its declared document title')
                (folder / 'parsed.json').write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding='utf-8')
                item.update(status='fetched', sha256=sha, bytes=len(data), content_type=content_type,
                            final_url=url, raw_path=str(raw), parsed_path=str(folder / 'parsed.json'),
                            parser_version='collector-2', temporal_status='unverified' if not source.get('effective_from') else 'declared')
            except Exception as exc:
                item['error'] = f'{type(exc).__name__}: {exc}'
            reports.append(item)
            merged[item['id']] = item
            with (output / 'acquisition-history.jsonl').open('a', encoding='utf-8') as history:
                history.write(json.dumps(item, ensure_ascii=False) + '\n')
            time.sleep(.25)
    temporary = report_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(list(merged.values()), ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(report_path)
    return reports


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, default=Path('docs/data-sources.json'))
    parser.add_argument('--output', type=Path, default=Path('runtime/public-sources'))
    parser.add_argument('--ids', nargs='*')
    args = parser.parse_args()
    result = collect(args.manifest, args.output, args.ids)
    print(json.dumps({'fetched': sum(x['status'] == 'fetched' for x in result), 'failed': [x['id'] for x in result if x['status'] != 'fetched']}, ensure_ascii=False))
