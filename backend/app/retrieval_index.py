"""Versioned, bounded pgvector indexes. No accounting facts or runtime SQL tools.

The caller supplies an independently retained manifest. Reading verifies the full
small index in one repeatable-read transaction before ranking; changed data is
an error, never a silent fallback or successful empty result.
"""
from copy import deepcopy
import hashlib
import re
import struct

from .reference_sources import canonical
from .retrieval_vectors import DIMENSIONS, validate_scope, validate_vectors

VERSION = 'persistent-reference-index-v1'
MAX_SEGMENTS = 20000
SEGMENT_KEYS = {'segment_id', 'parent_chunk_id', 'source_id', 'framework', 'effective_from',
                'effective_to', 'start', 'end', 'text'}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def validate_segments(segments, parents=None):
    if not 1 <= len(segments) <= MAX_SEGMENTS:
        raise ValueError('Persistent index segment count exceeds bounds')
    seen = set()
    parent_map = {p['chunk_id']: p for p in parents} if parents is not None else None
    if parent_map is not None and len(parent_map) != len(parents):
        raise ValueError('Duplicate reference parents')
    for s in segments:
        if set(s) != SEGMENT_KEYS or s['framework'] not in {'CAS', 'SCAS', 'BOTH'}:
            raise ValueError('Invalid reference segment contract')
        if (not isinstance(s['text'], str) or not s['text'].strip() or len(s['text']) > 10000
                or any(type(s[k]) is not int for k in ('start', 'end'))
                or not 0 <= s['start'] < s['end'] or len(s['text']) != s['end'] - s['start']):
            raise ValueError('Invalid reference character range')
        for key in ('parent_chunk_id', 'source_id'):
            if not isinstance(s[key], str) or not 1 <= len(s[key]) <= 300:
                raise ValueError('Invalid reference identity')
        identity = digest(f"{s['parent_chunk_id']}:{s['start']}:{s['end']}".encode())
        if s['segment_id'] != identity or identity in seen:
            raise ValueError('Invalid or duplicate segment identity')
        seen.add(identity)
        for key in ('effective_from', 'effective_to'):
            if s[key] is not None and (not isinstance(s[key], str) or
                                       not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])-\d{2}', s[key])):
                raise ValueError('Invalid reference period metadata')
        if parent_map is not None:
            parent = parent_map.get(s['parent_chunk_id'])
            if (not parent or s['text'] != parent['text'][s['start']:s['end']]
                    or any(s[key] != parent.get(key) for key in ('source_id', 'framework', 'effective_from', 'effective_to'))):
                raise ValueError('Segment differs from verified parent')


def content_hashes(segments, vectors):
    validate_segments(segments)
    validate_vectors(vectors, len(segments))
    ordered = sorted(zip(segments, vectors), key=lambda pair: pair[0]['segment_id'])
    # pgvector stores float32. Hash its exact binary representation, not JSON floats.
    vector_bytes = b''.join(s['segment_id'].encode('ascii') + struct.pack('<512f', *map(float, v))
                            for s, v in ordered)
    return digest(canonical([s for s, _ in ordered])), digest(vector_bytes)


def manifest_for(segments, vectors, *, parents, engine, corpus_sha256, registry_sha256):
    validate_segments(segments, parents)
    segment_hash, vector_hash = content_hashes(segments, vectors)
    body = {'schema_version': VERSION, 'engine': deepcopy(engine), 'corpus_sha256': corpus_sha256,
            'registry_sha256': registry_sha256, 'parents_sha256': digest(canonical(parents)),
            'segments_sha256': segment_hash, 'vectors_sha256': vector_hash,
            'segment_count': len(segments), 'dimensions': DIMENSIONS}
    result = {**body, 'index_id': digest(canonical(body))}
    validate_manifest(result)
    return result


def validate_manifest(manifest):
    keys = {'schema_version', 'engine', 'corpus_sha256', 'registry_sha256', 'parents_sha256',
            'segments_sha256', 'vectors_sha256', 'segment_count', 'dimensions', 'index_id'}
    if set(manifest) != keys or manifest['schema_version'] != VERSION or manifest['dimensions'] != DIMENSIONS:
        raise ValueError('Unknown persistent index manifest')
    if type(manifest['segment_count']) is not int or not 1 <= manifest['segment_count'] <= MAX_SEGMENTS:
        raise ValueError('Invalid persistent index size')
    if not isinstance(manifest['engine'], dict) or not manifest['engine']:
        raise ValueError('Missing persistent index model identity')
    for key in ('corpus_sha256', 'registry_sha256', 'parents_sha256', 'segments_sha256', 'vectors_sha256', 'index_id'):
        if not isinstance(manifest[key], str) or not re.fullmatch('[a-f0-9]{64}', manifest[key]):
            raise ValueError('Invalid persistent index digest')
    if digest(canonical({k: v for k, v in manifest.items() if k != 'index_id'})) != manifest['index_id']:
        raise ValueError('Changed persistent index manifest')


class PersistentIndex:
    """Dedicated autocommit connection. Mutations are explicit, transactional builds."""
    def __init__(self, connection):
        if not connection.autocommit:
            raise ValueError('Dedicated autocommit connection required')
        self.connection = connection

    def initialize(self):
        with self.connection.transaction():
            self.connection.execute('SELECT pg_advisory_xact_lock(18271801)')
            self.connection.execute('CREATE EXTENSION IF NOT EXISTS vector')
            self.connection.execute('CREATE SCHEMA IF NOT EXISTS ledgerlens_retrieval')
            self.connection.execute('CREATE TABLE IF NOT EXISTS ledgerlens_retrieval.versions '
                                    '(id text PRIMARY KEY, manifest jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now())')
            self.connection.execute('CREATE TABLE IF NOT EXISTS ledgerlens_retrieval.segments '
                                    '(index_id text NOT NULL REFERENCES ledgerlens_retrieval.versions(id), '
                                    'id text NOT NULL, payload jsonb NOT NULL, embedding vector(512) NOT NULL, '
                                    'PRIMARY KEY(index_id,id))')

    def _verify(self, expected):
        from pgvector.psycopg import register_vector
        register_vector(self.connection)
        row = self.connection.execute('SELECT manifest FROM ledgerlens_retrieval.versions WHERE id=%s',
                                      (expected['index_id'],)).fetchone()
        if row is None:
            raise ValueError('Persistent index version is missing')
        if canonical(row[0]) != canonical(expected):
            raise ValueError('Stored persistent index manifest changed')
        rows = self.connection.execute('SELECT id,payload,embedding FROM ledgerlens_retrieval.segments '
                                       'WHERE index_id=%s ORDER BY id LIMIT %s',
                                       (expected['index_id'], MAX_SEGMENTS + 1)).fetchall()
        if len(rows) != expected['segment_count'] or any(identity != payload.get('segment_id') for identity, payload, _ in rows):
            raise ValueError('Persistent index rows missing or altered')
        segments, vectors = [r[1] for r in rows], [r[2].to_list() for r in rows]
        hashes = content_hashes(segments, vectors)
        if hashes != (expected['segments_sha256'], expected['vectors_sha256']):
            raise ValueError('Persistent index content digest mismatch')
        return {s['segment_id']: s for s in segments}

    def publish(self, manifest, segments, vectors):
        from psycopg.types.json import Jsonb
        validate_manifest(manifest)
        if len(segments) != manifest['segment_count'] or content_hashes(segments, vectors) != (
                manifest['segments_sha256'], manifest['vectors_sha256']):
            raise ValueError('Build data differs from retained index manifest')
        with self.connection.transaction():
            self.connection.execute('SELECT pg_advisory_xact_lock(18271801)')
            found = self.connection.execute('SELECT id FROM ledgerlens_retrieval.versions WHERE id=%s',
                                            (manifest['index_id'],)).fetchone()
            if found:
                self._verify(manifest)  # Idempotent rebuild, never repair/overwrite damaged rows.
                return False
            self.connection.execute('INSERT INTO ledgerlens_retrieval.versions(id,manifest) VALUES (%s,%s)',
                                    (manifest['index_id'], Jsonb(manifest)))
            with self.connection.cursor() as cursor:
                cursor.executemany('INSERT INTO ledgerlens_retrieval.segments(index_id,id,payload,embedding) '
                                   'VALUES (%s,%s,%s,%s::vector)',
                                   [(manifest['index_id'], s['segment_id'], Jsonb(s), vector_literal(v))
                                    for s, v in zip(segments, vectors)])
            self._verify(manifest)  # Failed publication rolls back the version and every row.
        return True

    def verify(self, manifest):
        validate_manifest(manifest)
        with self.connection.transaction():
            self.connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            return self._verify(manifest)

    def search(self, manifest, vector, *, engine, framework, period, limit=6):
        validate_manifest(manifest)
        validate_scope(framework, period, limit)
        validate_vectors([vector], 1)
        if canonical(engine) != canonical(manifest['engine']):
            raise ValueError('Query model differs from indexed model')
        query = vector_literal(vector)
        with self.connection.transaction():
            self.connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            verified = self._verify(manifest)
            rows = self.connection.execute(
                "SELECT id,1-(embedding <=> %s::vector) AS score FROM ledgerlens_retrieval.segments "
                "WHERE index_id=%s AND payload->>'framework' IN (%s,%s) "
                "AND (payload->>'effective_from' IS NULL OR left(payload->>'effective_from',7)<=%s) "
                "AND (payload->>'effective_to' IS NULL OR left(payload->>'effective_to',7)>=%s) "
                "ORDER BY embedding <=> %s::vector,id LIMIT %s",
                (query, manifest['index_id'], framework, 'BOTH', period, period, query, limit)).fetchall()
            return [{**deepcopy(verified[identity]), 'score': float(score), 'index_id': manifest['index_id'],
                     'notice': '参考候选，相似度不是证据支持；引用必须回到已核验父片段'} for identity, score in rows]


def vector_literal(vector):
    # Internal encoding only; it is still sent as a bound parameter, never SQL text.
    return '[' + ','.join(repr(float(x)) for x in vector) + ']'
