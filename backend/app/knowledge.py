"""Verified local reference ingestion; search alone is an internal calculation.

Retrieval never creates executable accounting rules. Original-byte reparse and
registry binding happen at the load boundary, not in the pure search helper.
"""
from copy import deepcopy
import json
import re
from pathlib import Path
from uuid import uuid4

from . import reference_sources

DEFAULT_REGISTRY = reference_sources.DEFAULT_REGISTRY


def terms(text):
    text = text.lower()
    latin = set(re.findall(r'[a-z0-9]+', text))
    chinese = ''.join(re.findall(r'[\u4e00-\u9fff]', text))
    return latin | {chinese[i:i + 2] for i in range(max(0, len(chinese) - 1))}


def build_corpus(acquisition_report: Path, output: Path, *, registry=None, source_root=None):
    """Reparse registered originals and atomically write a reference-corpus-v2.

    Answers and component benchmarks are excluded before opening their files.
    A failed rebuild leaves the previous artifact unchanged. The source root
    defaults to the acquisition report's directory; the registry is independent.
    """
    acquisition_report, output = Path(acquisition_report), Path(output)
    registry = Path(registry) if registry is not None else DEFAULT_REGISTRY
    document = reference_sources.build_document(acquisition_report, registry=registry, source_root=source_root)
    destination = reference_sources._no_links(output)
    root = reference_sources._root(source_root if source_root is not None else acquisition_report.parent)
    protected = {reference_sources._no_links(acquisition_report).resolve(),
                 reference_sources._no_links(registry).resolve()}
    records, _ = reference_sources.registry_records(registry)
    if (destination.resolve() in protected or destination.name == 'parsed.json'
            or destination.suffix != '.json'
            or any(destination.resolve().is_relative_to(root / identity) for identity in records)):
        raise ValueError('Corpus output cannot replace registry, acquisition or original source artifacts')
    payload = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    if len(payload) > reference_sources.MAX_JSON_BYTES:
        raise ValueError('Serialized reference corpus exceeds the bounded size')
    destination.parent.mkdir(parents=True, exist_ok=True)
    reference_sources._no_links(destination)
    temporary = destination.with_name('.' + destination.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as handle:
            handle.write(payload)
        reference_sources._no_links(destination)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return deepcopy(document['chunks'])


def load_verified_corpus(path: Path, *, registry=None, source_root=None):
    """Load an in-memory verified copy, rejecting legacy unbound list artifacts."""
    return reference_sources.load_document(path, registry=registry if registry is not None else DEFAULT_REGISTRY,
                                           source_root=source_root)


def freeze_reference(chunk: dict, *, registry=None):
    """Freeze verified text and its eight-field display envelope for a snapshot."""
    return reference_sources.freeze_chunk(chunk, registry=registry if registry is not None else DEFAULT_REGISTRY)


def verify_frozen_references(passages: list[dict], *, registry=None):
    """Verify historical text/bindings without reopening today's source originals.

    Nonempty references without a binding are explicitly unverified legacy data
    and cannot be returned to production model entrypoints by this helper.
    """
    return reference_sources.verify_passages(passages, registry=registry if registry is not None else DEFAULT_REGISTRY)


def search(corpus: list[dict], query: str, framework: str, period: str, limit=6):
    """Rank supplied in-memory chunks; this helper grants no source authenticity."""
    query_terms = terms(query)
    allowed = []
    for chunk in corpus:
        if chunk['framework'] not in [framework, 'BOTH']:
            continue
        if chunk.get('effective_from') and chunk['effective_from'][:7] > period:
            continue
        if chunk.get('effective_to') and chunk['effective_to'][:7] < period:
            continue
        score = len(query_terms & terms(chunk['text'])) / max(1, len(query_terms))
        if score:
            allowed.append({**chunk, 'score': score, 'notice': '参考资料；生效状态未核实的内容不得当作执行规则'})
    return sorted(allowed, key=lambda x: (-x['score'], x['chunk_id']))[:limit]


class LocalEmbeddings:
    """Optional CPU embedding adapter; download only on explicit invocation."""
    def __init__(self, cache_dir: Path):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer('BAAI/bge-small-zh-v1.5', device='cpu', cache_folder=str(cache_dir))
        self.model.max_seq_length = 512

    def encode(self, chunks):
        tokenizer = self.model.tokenizer
        texts, refs = [], []
        for chunk in chunks:
            ids = tokenizer.encode(chunk['text'], add_special_tokens=False)
            for start in range(0, len(ids), 400):
                texts.append(tokenizer.decode(ids[start:start + 480]))
                refs.append({**chunk, 'token_offset': start})
        vectors = self.model.encode(texts, batch_size=8, normalize_embeddings=True, show_progress_bar=False)
        return [{**ref, 'embedding': vector.tolist()} for ref, vector in zip(refs, vectors)]


def parse_scanned_image(path: Path):
    from paddleocr import PaddleOCR
    model = PaddleOCR(use_doc_orientation_classify=False, use_doc_unwarping=False,
                      use_textline_orientation=False, lang='ch', device='cpu')
    # OCR is an observation, never a verified financial field.
    return {'status': 'requires_field_verification', 'pages': [r.json for r in model.predict(str(path))]}
