"""Optional offline reference retrieval. Scores are not accounting conclusions.

Segments retain exact character slices of verified parent chunks. They are not
new reference bindings: callers must cite/freeze the original parent chunk.
"""
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import re

MODEL_MANIFEST = Path(__file__).resolve().parents[2] / 'docs/retrieval-model-v1.json'
QUERY_PREFIX = '为这个句子生成表示以用于检索相关文章：'
DIMENSIONS = 512
VERSIONS = {'sentence-transformers': '6.1.0', 'torch': '2.14.1',
            'transformers': '5.19.0', 'tokenizers': '0.23.3'}


def validate_scope(framework, period, limit):
    if framework not in {'CAS', 'SCAS'} or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period):
        raise ValueError('Invalid reference scope')
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError('Invalid retrieval limit')


def eligible(segment, framework, period):
    return (segment['framework'] in {framework, 'BOTH'}
            and (not segment.get('effective_from') or segment['effective_from'][:7] <= period)
            and (not segment.get('effective_to') or segment['effective_to'][:7] >= period))


def segment_chunks(chunks, tokenizer, *, size=480, stride=400):
    if type(size) is not int or type(stride) is not int or not 1 <= stride <= size <= 480:
        raise ValueError('Invalid token window')
    segments, seen = [], set()
    for chunk in chunks:
        identity = chunk['chunk_id']
        if identity in seen:
            raise ValueError('Duplicate parent chunk')
        seen.add(identity)
        text = chunk['text']
        offsets = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)['offset_mapping']
        previous = 0
        for begin, end in offsets:
            if not 0 <= begin < end <= len(text) or begin < previous:
                raise ValueError('Invalid tokenizer character offsets')
            previous = begin
        for token_start in range(0, len(offsets), stride):
            window = offsets[token_start:token_start + size]
            begin, end = window[0][0], window[-1][1]
            segment_id = hashlib.sha256(f'{identity}:{begin}:{end}'.encode()).hexdigest()
            segments.append({'segment_id': segment_id, 'parent_chunk_id': identity,
                             'source_id': chunk['source_id'], 'framework': chunk['framework'],
                             'effective_from': chunk.get('effective_from'),
                             'effective_to': chunk.get('effective_to'),
                             'start': begin, 'end': end, 'text': text[begin:end]})
            if token_start + size >= len(offsets):
                break
    return segments


def validate_vectors(vectors, count):
    if len(vectors) != count:
        raise ValueError('Embedding count mismatch')
    for vector in vectors:
        if len(vector) != DIMENSIONS or any(not math.isfinite(float(x)) for x in vector):
            raise ValueError('Invalid embedding dimensions or values')
        norm = sum(float(x) ** 2 for x in vector)
        if abs(norm - 1) > 0.001:
            raise ValueError('Embedding is not normalized')


class OfflineBGE:
    def __init__(self, model_root):
        root = Path(model_root).resolve(strict=True)
        manifest = json.loads(MODEL_MANIFEST.read_text(encoding='utf-8'))
        for item in manifest['files']:
            path = (root / item['file']).resolve(strict=True)
            if not path.is_relative_to(root) or path.stat().st_size != item['bytes']:
                raise ValueError('Invalid pinned model file')
            if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('Changed pinned model file')
        for package, expected in VERSIONS.items():
            if version(package) != expected:
                raise ValueError(f'Expected {package}=={expected}')
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
        os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
        from sentence_transformers import SentenceTransformer
        import torch
        torch.set_num_threads(4)
        self.model = SentenceTransformer(str(root), device='cpu', local_files_only=True,
                                         trust_remote_code=False)
        self.model.max_seq_length = 512
        if not self.model.tokenizer.is_fast:
            raise ValueError('Fast tokenizer required for original character offsets')
        self.engine = {'model': manifest['model'], 'revision': manifest['revision'],
                       'manifest_sha256': hashlib.sha256(MODEL_MANIFEST.read_bytes()).hexdigest(),
                       'packages': VERSIONS.copy(), 'dimensions': DIMENSIONS,
                       'window_tokens': 480, 'stride_tokens': 400, 'query_prefix': QUERY_PREFIX,
                       'device': 'cpu', 'normalized': True}

    def segments(self, chunks):
        return segment_chunks(chunks, self.model.tokenizer)

    def encode(self, texts, *, queries=False):
        inputs = [(QUERY_PREFIX if queries else '') + text for text in texts]
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError('Empty retrieval text')
        if any(len(self.model.tokenizer.encode(text)) > 512 for text in inputs):
            raise ValueError('Embedding input would be silently truncated')
        result = self.model.encode(inputs, batch_size=8, normalize_embeddings=True, show_progress_bar=False)
        validate_vectors(result, len(texts))
        return result


class PgVectorIndex:
    """Connection-local exact cosine index; never overwrites an existing index.

    The supplied connection is dedicated to this index. Only fixed parameterized
    statements are exposed; runtime agents never receive a SQL tool.
    """
    def __init__(self, connection, segments, vectors):
        from pgvector.psycopg import register_vector
        validate_vectors(vectors, len(segments))
        self.connection = connection
        register_vector(connection)
        self.segments = {s['segment_id']: dict(s) for s in segments}
        if len(self.segments) != len(segments):
            raise ValueError('Duplicate segment ID')
        connection.execute('CREATE TEMP TABLE retrieval_segments '
                           '(id text PRIMARY KEY, framework text NOT NULL, '
                           'effective_from text, effective_to text, embedding vector(512) NOT NULL)')
        with connection.cursor() as cursor:
            cursor.executemany('INSERT INTO retrieval_segments VALUES (%s,%s,%s,%s,%s)',
                               [(s['segment_id'], s['framework'],
                                 s.get('effective_from', '')[:7] if s.get('effective_from') else None,
                                 s.get('effective_to', '')[:7] if s.get('effective_to') else None, vector)
                                for s, vector in zip(segments, vectors)])

    def search(self, vector, framework, period, limit=6):
        validate_scope(framework, period, limit)
        validate_vectors([vector], 1)
        rows = self.connection.execute(
            'SELECT id, 1 - (embedding <=> %s) AS score FROM retrieval_segments '
            'WHERE framework IN (%s, %s) AND (effective_from IS NULL OR effective_from <= %s) '
            'AND (effective_to IS NULL OR effective_to >= %s) '
            'ORDER BY embedding <=> %s, id LIMIT %s',
            (vector, framework, 'BOTH', period, period, vector, limit)).fetchall()
        return [{**self.segments[identity], 'score': float(score)} for identity, score in rows]
