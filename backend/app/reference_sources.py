"""Local reference provenance checks, without network or evaluator access.

Bindings detect changed configuration and artifacts. They are not signatures:
a local actor able to rewrite the registry and every hash is outside this trust
boundary. Historical frozen text is verified in memory, not against today's
original files. Only current corpus loading reparses the originals.
"""
from copy import deepcopy
from datetime import date
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

DEFAULT_REGISTRY = Path(__file__).resolve().parents[2] / 'docs' / 'data-sources.json'
CORPUS_VERSION = 'reference-corpus-v2'
PARSER_VERSION = 'reference-parser-v1'
FROZEN_VERSION = 'frozen-reference-v1'
ALLOWED_KINDS = {'policy', 'policy_notice'}
EXCLUDED_KINDS = {
    'official_answer', 'case_index', 'official_implementation_qa', 'evaluator',
    'evaluator_answer', 'component_benchmark', 'dataset_description',
}
META_KEYS = ('id', 'kind', 'url', 'framework', 'title', 'effective_from')
OPTIONAL_REGISTRY_KEYS = ('required_text', 'expected_article_title')
PASSAGE_KEYS = ('source_id', 'text', 'url', 'framework', 'title', 'effective_from', 'effective_to', 'status')
MAX_RAW_BYTES = 25 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_PAGES = 300
MAX_CHUNKS = 50000
CHUNK_STEP = 600
CHUNK_LENGTH = 800


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value: bytes):
    return hashlib.sha256(value).hexdigest()


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('Invalid reference SHA-256')
    return value


def _no_links(path):
    path = Path(path).absolute()
    for component in (path, *path.parents):
        if component.is_symlink() or getattr(component, 'is_junction', lambda: False)():
            raise ValueError('Reference path contains a symbolic link or junction')
    return path


def _read(path, limit=MAX_JSON_BYTES):
    path = _no_links(path)
    if not path.is_file():
        raise ValueError('Reference artifact is not a regular file')
    with path.open('rb') as handle:
        content = handle.read(limit + 1)
    if len(content) > limit:
        raise ValueError('Reference artifact exceeds the bounded size')
    # Do not return bytes read through a path that changed to a link mid-read.
    _no_links(path)
    return content


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate reference JSON field')
        value[key] = item
    return value


def _constant(value):
    raise ValueError('Nonfinite reference JSON value')


def _json(path, limit=MAX_JSON_BYTES):
    try:
        return json.loads(_read(path, limit).decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError('Reference JSON encoding or nesting is invalid') from exc


def _official_url(value):
    if not isinstance(value, str):
        raise ValueError('Missing registered reference URL')
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or parsed.hostname not in {'www.mof.gov.cn', 'kjs.mof.gov.cn'}
                or parsed.username or parsed.password or parsed.port not in {None, 443} or parsed.fragment):
            raise ValueError('Policy reference URL is outside registered official HTTPS origins')
    except (ValueError, TypeError) as exc:
        raise ValueError('Invalid official policy reference URL') from exc


def registry_records(registry=None):
    """Return normalized independent configuration and its semantic hash."""
    document = _json(registry if registry is not None else DEFAULT_REGISTRY, 1024 * 1024)
    if not isinstance(document, dict) or not isinstance(document.get('sources'), list):
        raise ValueError('Reference registry must contain a source list')
    records = {}
    for item in document['sources']:
        if not isinstance(item, dict) or any(key not in item for key in META_KEYS):
            raise ValueError('Registered reference metadata is incomplete')
        identity = item['id']
        if (not isinstance(identity, str) or not re.fullmatch('[a-z0-9][a-z0-9-]{0,79}', identity)
                or identity in records):
            raise ValueError('Duplicate or unsafe registered reference ID')
        kind = item['kind']
        if not isinstance(kind, str) or kind not in ALLOWED_KINDS | EXCLUDED_KINDS:
            raise ValueError('Unknown registered reference kind')
        if not isinstance(item['title'], str) or not item['title'].strip():
            raise ValueError('Missing registered reference title')
        if item.get('effective_to') is not None:
            raise ValueError('Registered reference expiry dates are not supported by this corpus version')
        if item['effective_from'] is not None:
            if not isinstance(item['effective_from'], str):
                raise ValueError('Invalid registered effective date')
            if date.fromisoformat(item['effective_from']).isoformat() != item['effective_from']:
                raise ValueError('Registered effective date is not canonical ISO format')
        if kind in ALLOWED_KINDS:
            if not isinstance(item['framework'], str) or item['framework'] not in {'CAS', 'SCAS', 'BOTH'}:
                raise ValueError('Invalid registered accounting framework')
            _official_url(item['url'])
        normalized = {key: item[key] for key in META_KEYS}
        for key in OPTIONAL_REGISTRY_KEYS:
            if key in item:
                if not isinstance(item[key], str) or not item[key].strip():
                    raise ValueError('Invalid optional registered reference metadata')
                normalized[key] = item[key]
        records[identity] = normalized
    normalized_registry = {'version': document.get('version'), 'sources': [records[key] for key in sorted(records)]}
    return records, digest(canonical(normalized_registry))


def _metadata(registered):
    return {key: registered[key] for key in META_KEYS}


def _root(path):
    path = _no_links(path)
    if not path.is_dir():
        raise ValueError('Reference source root is missing')
    return path.resolve()


def _bound_path(root, relative):
    if (not isinstance(relative, str) or '\\' in relative
            or any(part in {'', '.', '..'} for part in relative.split('/'))
            or PurePosixPath(relative).is_absolute()):
        raise ValueError('Reference artifact path is not canonical and scoped')
    path = _no_links(root / relative)
    if not path.resolve().is_relative_to(root):
        raise ValueError('Reference artifact path is outside the source root')
    return path


def _declared_path(value, expected, root):
    if not isinstance(value, str):
        raise ValueError('Missing acquisition artifact path')
    declared = Path(value)
    candidates = [declared] if declared.is_absolute() else [declared, root / declared]
    for path in candidates:
        checked = _no_links(path)
        if checked.resolve() == expected.resolve():
            return
    raise ValueError('Acquisition path does not match its registered source directory')


def parse_original(content: bytes, suffix: str, *, registered=None):
    """Parse the already-hashed bytes once, using the collector's text semantics."""
    if suffix == '.pdf':
        if not content.startswith(b'%PDF'):
            raise ValueError('Declared PDF does not contain PDF bytes')
        import pdfplumber
        from pdfminer.psexceptions import PSException
        from pdfplumber.utils.exceptions import PdfminerException
        try:
            with pdfplumber.open(io.BytesIO(content)) as document:
                if not 1 <= len(document.pages) <= MAX_PAGES:
                    raise ValueError('Reference PDF page count is outside limits')
                pages = [{'page': number, 'text': page.extract_text() or ''}
                         for number, page in enumerate(document.pages, 1)]
        except (PSException, PdfminerException) as exc:
            raise ValueError('Original reference PDF cannot be parsed') from exc
    elif suffix == '.html':
        if content.startswith(b'%PDF'):
            raise ValueError('PDF bytes cannot be declared as HTML')
        soup = BeautifulSoup(content, 'html.parser')
        article_titles = [tag.get('content', '') for tag in soup.select('meta[name="ArticleTitle"]')]
        if registered and registered.get('expected_article_title'):
            if article_titles != [registered['expected_article_title']]:
                raise ValueError('Original HTML article title differs from its independently reviewed identity')
        headings = article_titles[:]
        headings += [tag.get_text(' ', strip=True) for tag in soup.select('h1,h2')]
        editor = soup.select_one('.TRS_Editor')
        body = editor.get_text(' ', strip=True) if editor else soup.get_text(' ', strip=True)
        if (any(re.match(r'^\s*(?:问|问题|答|答案|参考答案)\s*[:：]', text) for text in headings)
                or re.match(r'^\s*(?:答|答案|参考答案)\s*[:：]', body)
                or re.search(r'\b(?:evaluator_only|official_answer)\b', body)):
            raise ValueError('Answer-bearing document cannot become a policy reference')
        for tag in soup(['script', 'style', 'nav', 'header', 'footer']):
            tag.decompose()
        pages = [{'page': 1, 'text': soup.get_text('\n', strip=True)}]
    else:
        raise ValueError('Only registered HTML and PDF originals are supported')
    if not any(page['text'].strip() for page in pages):
        raise ValueError('Reference original contains no usable text')
    if len(canonical(pages)) > MAX_JSON_BYTES // 2:
        raise ValueError('Normalized reference text exceeds limits')
    return pages


def _source(root, identity, sha, suffix, registered):
    raw_file = f'{identity}/{sha}{suffix}'
    parsed_file = f'{identity}/parsed.json'
    raw = _read(_bound_path(root, raw_file), MAX_RAW_BYTES)
    if digest(raw) != sha:
        raise ValueError('Original reference bytes do not match their source SHA-256')
    pages = parse_original(raw, suffix, registered=registered)
    parsed = _json(_bound_path(root, parsed_file), MAX_JSON_BYTES // 2)
    # Canonical JSON preserves int/bool/float distinctions that Python equality
    # would erase (True == 1), including the original page identity.
    if canonical(parsed) != canonical(pages):
        raise ValueError('Stored parsed reference differs from original byte reparse')
    if registered.get('required_text') and registered['required_text'] not in '\n'.join(p['text'] for p in pages):
        raise ValueError('Original reference does not contain its registered required text')
    return {'id': identity, 'metadata': _metadata(registered), 'raw_file': raw_file, 'parsed_file': parsed_file,
            'raw_sha256': sha, 'parsed_sha256': digest(canonical(pages))}, pages


def _chunk_identity(binding):
    return digest(canonical({key: binding[key] for key in (
        'schema_version', 'registry_sha256', 'original_source_id', 'kind', 'metadata',
        'raw_sha256', 'parsed_sha256', 'chunk_sha256', 'page', 'offset', 'end')}))


def _passage(chunk):
    return {'source_id': chunk['chunk_id'], 'text': chunk['text'], 'url': chunk['url'],
            'framework': chunk['framework'], 'title': chunk['title'], 'effective_from': chunk['effective_from'],
            'effective_to': None, 'status': chunk['temporal_status']}


def make_chunks(sources, registry_sha):
    chunks = []
    for source, pages in sources:
        metadata = source['metadata']
        for page in pages:
            for start in range(0, len(page['text']), CHUNK_STEP):
                text = page['text'][start:start + CHUNK_LENGTH]
                chunk = {'source_id': source['id'], 'framework': metadata['framework'], 'url': metadata['url'],
                         'title': metadata['title'], 'kind': metadata['kind'], 'text': text, 'page': page['page'],
                         'offset': start, 'end': start + len(text), 'effective_from': metadata['effective_from'],
                         'effective_to': None, 'temporal_status': 'declared' if metadata['effective_from'] else 'unverified',
                         'sha256': source['raw_sha256'], 'parsed_sha256': source['parsed_sha256'],
                         'text_sha256': digest(text.encode()), 'parser_version': PARSER_VERSION}
                binding = {'schema_version': FROZEN_VERSION, 'registry_sha256': registry_sha,
                           'original_source_id': source['id'], 'kind': metadata['kind'], 'metadata': metadata,
                           'raw_sha256': source['raw_sha256'], 'parsed_sha256': source['parsed_sha256'],
                           'chunk_sha256': chunk['text_sha256'], 'page': page['page'], 'offset': start, 'end': chunk['end']}
                chunk['chunk_id'] = _chunk_identity(binding)
                binding['chunk_id'] = chunk['chunk_id']
                binding['passage_sha256'] = digest(canonical(_passage(chunk)))
                binding['binding_sha256'] = digest(canonical(binding))
                chunk['source_binding'] = deepcopy(binding)
                chunks.append(chunk)
                if len(chunks) > MAX_CHUNKS:
                    raise ValueError('Reference corpus exceeds chunk limit')
    return chunks


def build_document(report, *, registry=None, source_root=None):
    report = Path(report)
    registry_map, registry_sha = registry_records(registry)
    root = _root(source_root if source_root is not None else report.parent)
    rows = _json(report, 4 * 1024 * 1024)
    if not isinstance(rows, list) or len(rows) > 1000 or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Acquisition report is not a bounded source list')
    ids = [row.get('id') for row in rows]
    if any(not isinstance(identity, str) for identity in ids) or len(ids) != len(set(ids)):
        raise ValueError('Acquisition source identities are missing or duplicated')
    accepted = []
    for row in rows:
        kind = row.get('kind')
        if not isinstance(kind, str) or kind not in ALLOWED_KINDS | EXCLUDED_KINDS:
            raise ValueError('Acquisition reference kind is missing or unknown')
        registered = registry_map.get(row['id'])
        if registered is not None and any(row.get(key) != registered[key] for key in META_KEYS):
            raise ValueError('Acquisition metadata conflicts with its independent registered source')
        if registered is not None and any(key in row and row[key] != registered.get(key)
                                          for key in OPTIONAL_REGISTRY_KEYS):
            raise ValueError('Acquisition optional metadata conflicts with its independent registered source')
        if kind in EXCLUDED_KINDS:
            continue
        if registered is None:
            raise ValueError('Policy source ID is absent from the independent registry')
        if row.get('status') != 'fetched':
            continue
        if row.get('effective_to') is not None:
            raise ValueError('Reference expiry dates are not supported by this corpus version')
        if 'final_url' in row and row['final_url'] != registered['url']:
            raise ValueError('Acquisition final URL differs from its independently registered source')
        sha = _hash(row.get('sha256'))
        if not isinstance(row.get('raw_path'), str):
            raise ValueError('Missing acquisition original reference path')
        raw_path = Path(row['raw_path'])
        suffix = raw_path.suffix.lower()
        if suffix not in {'.html', '.pdf'}:
            raise ValueError('Unsupported original reference suffix')
        expected_raw = _bound_path(root, f'{row["id"]}/{sha}{suffix}')
        expected_parsed = _bound_path(root, f'{row["id"]}/parsed.json')
        _declared_path(row.get('raw_path'), expected_raw, root)
        _declared_path(row.get('parsed_path'), expected_parsed, root)
        accepted.append((row['id'], sha, suffix, registered))
    verified_sources = [_source(root, identity, sha, suffix, registered)
                        for identity, sha, suffix, registered in sorted(accepted)]
    return {'schema_version': CORPUS_VERSION, 'parser_version': PARSER_VERSION, 'registry_sha256': registry_sha,
            'sources': [source for source, _ in verified_sources], 'chunks': make_chunks(verified_sources, registry_sha)}


def load_document(path, *, registry=None, source_root=None):
    path = Path(path)
    document = _json(path)
    if not isinstance(document, dict) or document.get('schema_version') != CORPUS_VERSION:
        raise ValueError('Legacy or unsupported reference corpus is unverified; rebuild the v2 corpus explicitly')
    if set(document) != {'schema_version', 'parser_version', 'registry_sha256', 'sources', 'chunks'}:
        raise ValueError('Unexpected reference corpus fields')
    registry_map, registry_sha = registry_records(registry)
    if document['parser_version'] != PARSER_VERSION or document['registry_sha256'] != registry_sha:
        raise ValueError('Reference corpus parser or independent registry identity changed')
    if not isinstance(document['sources'], list) or len(document['sources']) > 1000:
        raise ValueError('Invalid reference corpus source list')
    root = _root(source_root if source_root is not None else path.parent.parent / 'public-sources')
    sources, identities = [], set()
    for stored in document['sources']:
        if not isinstance(stored, dict):
            raise ValueError('Invalid reference corpus source')
        identity = stored.get('id')
        if not isinstance(identity, str) or identity in identities:
            raise ValueError('Duplicate or absent reference corpus source ID')
        identities.add(identity)
        registered = registry_map.get(identity)
        if registered is None or registered['kind'] not in ALLOWED_KINDS:
            raise ValueError('Unregistered or excluded reference source in persisted corpus')
        if stored.get('metadata') != _metadata(registered):
            raise ValueError('Persisted reference metadata conflicts with its independent registry')
        sha = _hash(stored.get('raw_sha256'))
        raw_file = stored.get('raw_file')
        suffix = Path(raw_file).suffix if isinstance(raw_file, str) else ''
        if (suffix not in {'.html', '.pdf'} or raw_file != f'{identity}/{sha}{suffix}'
                or stored.get('parsed_file') != f'{identity}/parsed.json'):
            raise ValueError('Persisted source paths are not canonical and scoped')
        source, pages = _source(root, identity, sha, suffix, registered)
        if canonical(stored) != canonical(source):
            raise ValueError('Persisted source provenance differs from verified original and parsed content')
        sources.append((source, pages))
    if [source['id'] for source, _ in sources] != sorted(identities):
        raise ValueError('Persisted source ordering is not canonical')
    chunks = make_chunks(sources, registry_sha)
    if not isinstance(document['chunks'], list) or canonical(document['chunks']) != canonical(chunks):
        raise ValueError('Persisted reference chunks differ from the verified originals')
    return deepcopy(chunks)


def verify_passages(passages, *, registry=None):
    if not isinstance(passages, list) or len(passages) > 5000:
        raise ValueError('Frozen references must be a bounded passage list')
    if not passages:
        return []
    registry_map, registry_sha = registry_records(registry)
    identities = set()
    for passage in passages:
        if not isinstance(passage, dict) or set(passage) != set(PASSAGE_KEYS) | {'source_binding'}:
            raise ValueError('Legacy or malformed frozen references lack verified source bindings')
        binding = passage['source_binding']
        keys = {'schema_version', 'registry_sha256', 'original_source_id', 'kind', 'metadata', 'raw_sha256',
                'parsed_sha256', 'chunk_sha256', 'page', 'offset', 'end', 'chunk_id', 'passage_sha256', 'binding_sha256'}
        if not isinstance(binding, dict) or set(binding) != keys or binding['schema_version'] != FROZEN_VERSION:
            raise ValueError('Frozen reference binding is missing or malformed')
        if not isinstance(binding['original_source_id'], str):
            raise ValueError('Frozen reference source identity is invalid')
        registered = registry_map.get(binding['original_source_id'])
        if registered is None or registered['kind'] not in ALLOWED_KINDS:
            raise ValueError('Frozen reference source is absent or excluded from the registry')
        if (binding['registry_sha256'] != registry_sha or binding['metadata'] != _metadata(registered)
                or binding['kind'] != registered['kind']):
            raise ValueError('Frozen reference independent registry binding changed')
        for key in ('registry_sha256', 'raw_sha256', 'parsed_sha256', 'chunk_sha256', 'chunk_id', 'passage_sha256', 'binding_sha256'):
            _hash(binding[key])
        if (not isinstance(passage['text'], str) or not 1 <= len(passage['text']) <= CHUNK_LENGTH
                or any(type(binding[key]) is not int for key in ('page', 'offset', 'end'))
                or not 1 <= binding['page'] <= MAX_PAGES or binding['offset'] < 0
                or binding['offset'] % CHUNK_STEP or binding['end'] != binding['offset'] + len(passage['text'])):
            raise ValueError('Frozen reference text or source location is invalid')
        core = {key: value for key, value in binding.items() if key != 'binding_sha256'}
        if (digest(canonical(core)) != binding['binding_sha256']
                or digest(passage['text'].encode()) != binding['chunk_sha256']
                or _chunk_identity(binding) != binding['chunk_id'] or passage['source_id'] != binding['chunk_id']
                or digest(canonical({key: passage[key] for key in PASSAGE_KEYS})) != binding['passage_sha256']):
            raise ValueError('Frozen reference text, envelope or binding hash changed')
        metadata = binding['metadata']
        if (passage['url'] != metadata['url'] or passage['framework'] != metadata['framework']
                or passage['title'] != metadata['title'] or passage['effective_from'] != metadata['effective_from']
                or passage['effective_to'] is not None
                or passage['status'] != ('declared' if metadata['effective_from'] else 'unverified')):
            raise ValueError('Frozen reference display envelope conflicts with its source')
        if passage['source_id'] in identities:
            raise ValueError('Frozen reference chunk identity is duplicated')
        identities.add(passage['source_id'])
    return deepcopy(passages)


def freeze_chunk(chunk, *, registry=None):
    if not isinstance(chunk, dict) or 'source_binding' not in chunk:
        raise ValueError('Unverified reference chunk cannot be frozen')
    try:
        passage = {**_passage(chunk), 'source_binding': deepcopy(chunk['source_binding'])}
    except (KeyError, TypeError) as exc:
        raise ValueError('Reference chunk is incomplete') from exc
    return verify_passages([passage], registry=registry)[0]
