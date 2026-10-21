"""Build the competition introduction PDF from docs/submission/intro.template.html (Apache-2.0).

Every number in the template is a placeholder filled from a committed validation summary, so the
document cannot drift from the evidence. A missing value fails the build instead of printing a guess.

Placeholders:
  {{json:<alias>#<dotted.path>}}  value from a summary in SOURCES (list items by index)
  {{meta:<key>}} {{link:<key>}}   values from docs/submission/meta.json
  {{asset:<key>}}                 absolute file URL of an image in ASSETS
  {{lic:summary}}                 live result of scripts/check_licenses.py
  {{gh:<key>}}                    live GitHub counts (health, prs, issues) and the CI job count

Usage: python scripts/build_submission_pdf.py --output .local/submission/账证智核-作品介绍.pdf
"""
import argparse
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'docs/submission/intro.template.html'
META = ROOT / 'docs/submission/meta.json'
SOURCES = {'policy': 'docs/policy-v9-validation.json', 'audit': 'docs/publication-audit.json',
           'rootcause': 'docs/root-cause-results.json', 'v4': 'docs/root-cause-v4-sealed-analysis.json'}
ASSETS = {'architecture': 'docs/images/architecture.svg', **{
    f'shot{n:02d}': f'docs/images/{name}.png' for n, name in enumerate(
        ['01-workbench', '02-investigation', '03-evidence', '04-source-record', '05-simulation', '06-review',
         '07-review-invalidated', '08-policy', '09-evaluation'], 1)}}
PLACEHOLDER = re.compile(r'\{\{(json|meta|link|asset|lic|gh):([^}]+)\}\}')
REPO = 'longtiandragon/ledgerlens'


def dig(data, dotted: str):
    for part in dotted.split('.'):
        data = data[int(part)] if isinstance(data, list) else data[part]
    return data


def licenses() -> str:
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/check_licenses.py')], capture_output=True, text=True,
                            encoding='utf-8', cwd=ROOT)
    report = json.loads(result.stdout)
    if result.returncode:
        raise SystemExit('License gate fails; fix it before building the document')
    py, js = report['python'], report['frontend']
    return (f"Python 依赖 {py['checked']} 个（允许 {py['allowed']}、例外 {py['exception']}、违规 {py['violation']}），"
            f"前端锁文件条目 {js['checked']} 个（违规 {js['violation']}）")


def github() -> dict:
    def gh(*args):
        return subprocess.run(['gh', *args], capture_output=True, text=True, encoding='utf-8', check=True).stdout
    profile = json.loads(gh('api', f'repos/{REPO}/community/profile'))
    prs = len(json.loads(gh('pr', 'list', '--repo', REPO, '--state', 'all', '--limit', '500', '--json', 'number')))
    issues = len(json.loads(gh('issue', 'list', '--repo', REPO, '--state', 'all', '--limit', '500', '--json', 'number')))
    workflow = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
    jobs = len(re.findall(r'^  [a-z][a-z0-9_-]*:\s*$', workflow.split('\njobs:\n', 1)[1], re.M))
    return {'health': profile['health_percentage'], 'prs': prs, 'issues': issues, 'jobs': jobs}


def render(template: str, meta: dict, live: dict) -> str:
    cache: dict[str, object] = {}

    def value(match):
        kind, key = match.groups()
        if kind == 'json':
            alias, _, dotted = key.partition('#')
            if alias not in cache:
                cache[alias] = json.loads((ROOT / SOURCES[alias]).read_text(encoding='utf-8'))
            return html.escape(str(dig(cache[alias], dotted)))
        if kind in ('meta', 'link'):
            text = meta[kind][key]
            return f'<a href="{html.escape(text)}">{html.escape(text)}</a>' if kind == 'link' else html.escape(text)
        if kind == 'asset':
            path = ROOT / ASSETS[key]
            if not path.exists():
                raise KeyError(f'asset {key} missing at {path}')
            return path.resolve().as_uri()
        return html.escape(str(live[kind][key]))

    try:
        return PLACEHOLDER.sub(value, template)
    except (KeyError, IndexError, ValueError) as exc:
        raise SystemExit(f'Unresolved placeholder: {exc}') from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--offline', action='store_true', help='skip live GitHub counts (placeholders must be absent)')
    options = parser.parse_args(argv)
    if options.output.exists():
        parser.error('Refusing to overwrite an existing PDF')
    template = TEMPLATE.read_text(encoding='utf-8')
    meta = json.loads(META.read_text(encoding='utf-8'))
    live = {'lic': {'summary': licenses()} if '{{lic:' in template else {},
            'gh': {} if options.offline else github()}
    document = render(template, meta, live)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    page = options.output.with_suffix('.html')
    page.write_text(document, encoding='utf-8')
    node = shutil.which('node')
    if not node:
        raise SystemExit('Installed Node runtime is required to print the PDF')
    subprocess.run([node, str(ROOT / 'scripts/print_pdf.cjs'), str(page), str(options.output),
                    html.escape(meta['meta']['footer'])], check=True)
    print(json.dumps({'pdf': str(options.output), 'html': str(page)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
