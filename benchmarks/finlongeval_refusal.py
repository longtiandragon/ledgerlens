"""External transfer audit of frozen retrieval thresholds, not answer correctness."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
THRESHOLDS = {'keyword': 0.2857142857142857, 'bge': 0.5847992897033691}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def score(cases, labels):
    if len(cases) != len(labels) or not any(label == '陷阱问题' for label in labels):
        raise ValueError('Missing rows or no publisher trap labels; cannot score refusal')
    results = {}
    for method in THRESHOLDS:
        count = Counter()
        for row, label in zip(cases, labels, strict=True):
            group = 'trap' if label == '陷阱问题' else 'other'
            count[group + '_total'] += 1
            if 'error' in row:
                count[group + '_failed'] += 1
                continue
            count[group + ('_accepted' if row[method]['accepted'] else '_rejected')] += 1
            count[group + '_unthresholded_has_candidate'] += row[method]['score'] is not None
        results[method] = dict(count)
    return results


def evaluate(raw, inputs, models, output):
    import numpy as np
    import pdfplumber
    from app.knowledge import search
    from app.retrieval_vectors import OfflineBGE

    pins = json.loads((inputs / 'source-pin.json').read_text(encoding='utf-8'))
    for name, expected in pins['files'].items():
        path = (raw / name).resolve()
        if not path.is_relative_to(raw.resolve()) or digest(path) != expected['sha256']:
            raise ValueError('Changed source: ' + name)
    queries = json.loads((inputs / 'queries.json').read_text(encoding='utf-8'))
    if len(queries) != 80 or len({q['id'] for q in queries}) != 80:
        raise ValueError('All 80 public samples required')
    for query in queries:
        if set(query) != {'id', 'question', 'document_name', 'source_path'}:
            raise ValueError('Unexpected query field')
    output.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'scope': 'external_threshold_transfer_not_answer_accuracy',
              'started_at': datetime.now(timezone.utc).isoformat(), 'cases': [], 'documents': {},
              'thresholds': THRESHOLDS, 'external_model_requests': 0, 'ledger_writes': 0,
              'inputs_sha256': {p: digest(inputs / p) for p in ['queries.json', 'source-pin.json']},
              'code_sha256': {p: digest(ROOT / p) for p in [
                  'benchmarks/finlongeval_refusal.py', 'backend/app/knowledge.py',
                  'backend/app/retrieval_vectors.py', 'docs/FINLONGEVAL_REFUSAL_PROTOCOL.md']},
              'forbidden_access_attempts': []}

    def save():
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    scoring = False

    def guard(event, args):
        if event in {'socket.connect', 'socket.getaddrinfo'}:
            report['forbidden_access_attempts'].append(event)
            raise RuntimeError('Network disabled')
        if event == 'open' and isinstance(args[0], (str, bytes)) and not scoring:
            if 'sample_data.jsonl' in str(args[0]):
                report['forbidden_access_attempts'].append('truth_open')
                raise RuntimeError('Labels unavailable before predictions finish')

    save()
    sys.addaudithook(guard)
    model = OfflineBGE(models)
    report['engine'] = model.engine
    report['pdfplumber_version'] = pdfplumber.__version__
    cache = {}
    with (output / 'predictions.jsonl').open('x', encoding='utf-8') as predictions:
        for query in queries:
            start = time.perf_counter()
            row = {'id': query['id']}
            try:
                name = query['source_path']
                if name is None:
                    raise ValueError('Publisher PDF unavailable under fixed filename mapping')
                if name not in cache:
                    chunks, empty, pages = [], 0, 0
                    with pdfplumber.open(raw / name) as pdf:
                        for index, page in enumerate(pdf.pages, 1):
                            text = page.extract_text() or ''
                            pages += 1
                            empty += not bool(text.strip())
                            if text.strip():
                                chunks.append({'chunk_id': f'{name}:{index}', 'source_id': name,
                                               'framework': 'BOTH', 'text': text})
                            page.close()
                    segments = model.segments(chunks)
                    if not segments:
                        raise ValueError('No extractable PDF text')
                    vectors = model.encode([s['text'] for s in segments])
                    cache[name] = (segments, vectors)
                    report['documents'][name] = {'pages': pages, 'empty_pages': empty,
                                                'segments': len(segments), 'sha256': digest(raw / name)}
                    save()
                    print('indexed', name, pages, len(segments), flush=True)
                segments, vectors = cache[name]
                keyword = search([{**s, 'chunk_id': s['segment_id']} for s in segments],
                                 query['question'], 'CAS', '2026-10', 6)
                vector = model.encode([query['question']], queries=True)[0]
                scores = np.dot(vectors, vector)
                order = sorted(range(len(segments)), key=lambda i: (-float(scores[i]), segments[i]['segment_id']))[:6]
                rankings = {'keyword': [(h['segment_id'], float(h['score'])) for h in keyword],
                            'bge': [(segments[i]['segment_id'], float(scores[i])) for i in order]}
                for method, hits in rankings.items():
                    top_score = hits[0][1] if hits else None
                    row[method] = {'score': top_score, 'accepted': top_score is not None and top_score >= THRESHOLDS[method],
                                   'top6': [{'segment_id': identity, 'score': value} for identity, value in hits]}
            except Exception as exc:
                row['error'] = {'type': type(exc).__name__, 'message': str(exc)[:200]}
            row['seconds'] = round(time.perf_counter() - start, 3)
            predictions.write(json.dumps(row, ensure_ascii=False) + '\n')
            predictions.flush()
            report['cases'].append(row)
            save()
            print(len(report['cases']), '/80', row['id'], 'failed' if 'error' in row else 'predicted', flush=True)
    report['status'] = 'predicted_not_scored'
    save()
    scoring = True
    truth_path = raw / 'data/sample_data.jsonl'
    truth = [json.loads(line) for line in truth_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    labels = [r['question_type'] for r in truth]
    report.update(status='completed_with_failures' if any('error' in r for r in report['cases']) else 'completed',
                  finished_at=datetime.now(timezone.utc).isoformat(), summary=score(report['cases'], labels),
                  question_types=dict(Counter(labels)), truth_sha256=digest(truth_path),
                  predictions_sha256=digest(output / 'predictions.jsonl'),
                  limitations=['Only publisher public samples, not full dataset.',
                               'Known document retrieval, not discovery or production scope gating.',
                               'Trap/other is a proxy; acceptance is not correct evidence or an answer.',
                               'Public pretraining contamination unknown; no threshold retuning.',
                               'PDF text only; not OCR, visual tables or pgvector runtime.'])
    save()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['raw', 'inputs', 'models', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.raw, args.inputs, args.models, args.output)['summary'], indent=2))
