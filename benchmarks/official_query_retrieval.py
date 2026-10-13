"""Expert-authored retrieval queries from archived MOF Q&A, judged by their official citations.

Queries are the official questions (written by practitioners and the MOF, not by this
project). Relevance comes mechanically from the official answers: a corpus standard
named in the answer is a relevant document, and an explicitly cited article ("第十五条")
marks the chunk(s) containing that article heading. Answers are read only here, by the
evaluator, after the query file is frozen; the retrieval function never sees them.
Question and answer text stays in the local archive; outputs keep ids, hashes and counts.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.knowledge import search  # noqa: E402

VERSION = 'official-query-retrieval-v1'
STANDARDS = {'存货': 'cas1-inventory', '固定资产': 'cas4-fixed-assets', '收入': 'cas14-revenue', '租赁': 'cas21-leases'}
NAMED = re.compile(r'《企业会计准则第\d+号——(存货|固定资产|收入|租赁)》|(存货|固定资产|收入|租赁)准则|(小企业会计准则)')
CITED = re.compile(r'(?:《企业会计准则第\d+号——(存货|固定资产|收入|租赁)》|(存货|固定资产|收入|租赁)准则|(小企业会计准则))'
                   r'[^。]{0,90}?第([一二三四五六七八九十百零]+)条')


def source_of(groups) -> str:
    named = next(g for g in groups if g)
    return 'scas-full' if named == '小企业会计准则' else STANDARDS[named]


def build(archive: Path, chunks: list[dict]) -> tuple[list[dict], dict]:
    queries, truth = [], {}
    for path in sorted(glob.glob(str(archive / 'answers' / '*' / '*.json'))):
        answer = json.loads(Path(path).read_text(encoding='utf-8'))
        documents = sorted({source_of(m.groups()) for m in NAMED.finditer(answer['answer'])})
        if not documents:
            continue
        articles = sorted({(source_of(m.groups()[:3]), m.group(4)) for m in CITED.finditer(answer['answer'])})
        observed = json.loads(next((archive / 'observed' / answer['id']).glob('*.json')).read_text(encoding='utf-8'))
        question = re.sub(r'^\s*问[:：]\s*', '', observed['question'])
        cited_chunks = sorted({chunk['chunk_id'] for source, number in articles for chunk in chunks
                               if chunk['source_id'] == source
                               and re.search('第' + number + r'条(?![一二三四五六七八九十百零])', chunk['text'])})
        queries.append({'query_id': answer['id'], 'framework': observed.get('framework', 'CAS'), 'query': question})
        truth[answer['id']] = {'documents': documents, 'articles': [f'{s}#{n}' for s, n in articles],
                               'article_chunks': cited_chunks}
    return queries, truth


def evaluate(queries: list[dict], truth: dict, chunks: list[dict], limit: int = 6) -> dict:
    rows = []
    for item in queries:
        ranked = search(chunks, item['query'], item['framework'], '2026-09', limit=limit)
        sources = [r['source_id'] for r in ranked]
        ids = [r['chunk_id'] for r in ranked]
        want = truth[item['query_id']]
        doc_ranks = [i + 1 for i, s in enumerate(sources) if s in want['documents']]
        art_ranks = [i + 1 for i, c in enumerate(ids) if c in want['article_chunks']]
        rows.append({'query_id': item['query_id'], 'returned': len(ranked),
                     'document_hit_at_1': bool(doc_ranks and doc_ranks[0] == 1),
                     'document_hit_at_6': bool(doc_ranks), 'document_reciprocal_rank': 1 / doc_ranks[0] if doc_ranks else 0,
                     'article_query': bool(want['article_chunks']),
                     'article_hit_at_6': bool(art_ranks) if want['article_chunks'] else None})
    article_rows = [r for r in rows if r['article_query']]
    return {'queries': len(rows), 'document_hit_at_1': sum(r['document_hit_at_1'] for r in rows),
            'document_hit_at_6': sum(r['document_hit_at_6'] for r in rows),
            'document_mrr_at_6': round(sum(r['document_reciprocal_rank'] for r in rows) / len(rows), 4) if rows else None,
            'article_queries': len(article_rows), 'article_hit_at_6': sum(r['article_hit_at_6'] for r in article_rows),
            'rows': rows}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=ROOT / '.local/official-cases')
    parser.add_argument('--corpus', type=Path, default=ROOT / '.local/retrieval-corpus-v1.json')
    parser.add_argument('--freeze', type=Path, help='new directory: write frozen queries and truth, then stop')
    parser.add_argument('--queries', type=Path, help='frozen query file to evaluate')
    parser.add_argument('--truth', type=Path, help='frozen truth file to evaluate')
    parser.add_argument('--output', type=Path)
    options = parser.parse_args(argv)
    corpus = json.loads(options.corpus.read_text(encoding='utf-8'))
    chunks = corpus['chunks']
    if options.freeze:
        options.freeze.mkdir(parents=False, exist_ok=False)
        queries, truth = build(options.archive, chunks)
        for name, value in (('queries.json', queries), ('truth.json', truth)):
            (options.freeze / name).write_text(json.dumps(value, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        print(json.dumps({'queries': len(queries), 'article_queries': sum(bool(t['article_chunks']) for t in truth.values())}))
        return 0
    if not (options.queries and options.truth and options.output) or options.output.exists():
        parser.error('--queries, --truth and a new --output are required to evaluate')
    queries = json.loads(options.queries.read_text(encoding='utf-8'))
    truth = json.loads(options.truth.read_text(encoding='utf-8'))
    result = {'schema_version': VERSION, 'method': 'product keyword search (app.knowledge.search), limit 6',
              'corpus_sha256': hashlib.sha256(options.corpus.read_bytes()).hexdigest(),
              'queries_sha256': hashlib.sha256(options.queries.read_bytes()).hexdigest(),
              'truth_sha256': hashlib.sha256(options.truth.read_bytes()).hexdigest(),
              **evaluate(queries, truth, chunks)}
    options.output.write_text(json.dumps(result, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
