"""Offline checks for the external-condition substitutes: official query retrieval,
public-ledger checks and CORD receipt scoring. Synthetic fixtures only; no downloads."""
import json

from benchmarks import cord_ocr_eval, official_query_retrieval as retrieval, public_ledger_checks as ledger


def chunk(chunk_id, source, text, framework='CAS'):
    return {'chunk_id': chunk_id, 'source_id': source, 'framework': framework, 'text': text}


def test_query_truth_comes_from_named_standards_and_cited_articles(tmp_path):
    for folder in ('answers/batch', 'observed/q1', 'observed/q2'):
        (tmp_path / folder).mkdir(parents=True)
    answers = {'q1': '根据《企业会计准则第1号——存货》第十五条的规定，应计提跌价准备。',
               'q2': '本问题与准则无关，按税法处理。'}
    for qid, text in answers.items():
        (tmp_path / 'answers/batch' / f'{qid}.json').write_text(json.dumps({'id': qid, 'answer': text}), encoding='utf-8')
        (tmp_path / 'observed' / qid / 'page.json').write_text(
            json.dumps({'question': '问：存货跌价如何计提？', 'framework': 'CAS'}), encoding='utf-8')
    chunks = [chunk('a', 'cas1-inventory', '第十五条 资产负债表日，存货应当按照成本与可变现净值孰低计量。'),
              chunk('b', 'cas1-inventory', '第十六条 已计提的跌价准备可以转回。'),
              chunk('c', 'cas14-revenue', '第十五条 收入')]   # same article number, other standard
    queries, truth = retrieval.build(tmp_path, chunks)
    assert queries == [{'query_id': 'q1', 'framework': 'CAS', 'query': '存货跌价如何计提？'}]
    assert truth == {'q1': {'documents': ['cas1-inventory'], 'articles': ['cas1-inventory#十五'],
                            'article_chunks': ['a']}}


def test_retrieval_scores_document_and_article_ranks_with_any_ranker():
    chunks = [chunk('x', 'cas14-revenue', ''), chunk('a', 'cas1-inventory', ''), chunk('b', 'cas1-inventory', '')]
    queries = [{'query_id': 'q1', 'framework': 'CAS', 'query': 'q'}, {'query_id': 'q2', 'framework': 'CAS', 'query': 'q'}]
    truth = {'q1': {'documents': ['cas1-inventory'], 'articles': ['cas1-inventory#十五'], 'article_chunks': ['b']},
             'q2': {'documents': ['cas21-leases'], 'articles': [], 'article_chunks': []}}
    result = retrieval.evaluate(queries, truth, chunks, ranker=lambda item, limit: chunks[:limit])
    assert (result['document_hit_at_1'], result['document_hit_at_6'], result['document_mrr_at_6']) == (0, 1, 0.25)
    assert (result['article_queries'], result['article_hit_at_6']) == (1, 1)
    assert result['rows'][1]['article_hit_at_6'] is None and result['rows'][1]['document_reciprocal_rank'] == 0


def test_bge_ranker_scores_chunks_by_best_window_within_the_framework(monkeypatch, tmp_path):
    import sys
    import types

    import numpy

    class Tokenizer:
        def encode(self, text, add_special_tokens=False):
            return [ord(c) for c in text]

        def decode(self, ids):
            return ''.join(chr(i) for i in ids)

    class Model:
        def __init__(self, path, device):
            self.tokenizer = Tokenizer()

        def encode(self, texts, **options):
            return numpy.array([[1.0, 0.0] if '存货' in t else [0.0, 1.0] if '收入' in t else [0.0, 0.0] for t in texts])

    monkeypatch.setitem(sys.modules, 'sentence_transformers', types.SimpleNamespace(SentenceTransformer=Model))
    chunks = [chunk('late', 'cas1-inventory', 'x' * 500 + '存货跌价'),   # only the second window matches
              chunk('early', 'cas1-inventory', '存货'), chunk('rev', 'cas14-revenue', '收入'),
              chunk('small', 'scas-full', '存货', framework='SCAS'), chunk('both', 'cas21-leases', '租赁', framework='BOTH')]
    rank = retrieval.bge_ranker(chunks, tmp_path)
    ranked = rank({'query_id': 'q', 'framework': 'CAS', 'query': '存货如何计量'}, 6)
    assert [(r['chunk_id'], r['score']) for r in ranked] == [('early', 1.0), ('late', 1.0), ('both', 0.0), ('rev', 0.0)]


def test_public_ledger_reports_rejections_balance_scope_and_encoding(tmp_path):
    header = 'BUSINESS_UNIT,LEDGER,FISCAL_YEAR,ACCOUNTING_PERIOD,FUND_CODE,ACCOUNT,SUM(L.POSTED_TOTAL_AMT)\r\n'
    rows = ['100,ACTUALS,2024,10,F1,1100,250.00', '100,ACTUALS,2024,10,F1,4100,-250.00',
            '200,ACTUALS,2024,11,Fé,5100,10.005', '200,ACTUALS,2024,11,Fé,1100,-9.99',
            '300,BUDGET,2024,11,F3,5100,7']
    path = tmp_path / 'gl.csv'
    path.write_bytes((header + '\r\n'.join(rows) + '\r\n').encode('cp1252'))   # é is not valid UTF-8
    result = ledger.check(path)
    assert result['encoding'] == 'cp1252' and result['utf8_decode_failed_at_byte'] is not None
    assert result['money_rejected'] == 1 and result['rejected_examples'][0]['independent_parse'] == '10.005'
    assert result['independent_parse_disagreements'] == 0
    assert (result['actuals_groups'], result['actuals_unbalanced_groups']) == (2, 1)
    assert result['largest_imbalances'][0]['net'] == '-9.99'   # the rejected sub-cent row is not rounded in
    assert result['trial_balance_testable'] is False          # no liability or fund-balance class


def test_cord_scores_literal_recovery_and_counts_missing_predictions():
    truth = {'valid_line': [{'words': [{'text': 'NASI'}, {'text': 'GORENG'}]}, {'words': [{'text': '25.000'}]}],
             'gt_parse': {'menu': {'nm': 'NASI GORENG', 'price': '25.000'}, 'total': {'total_price': '27.500'}}}
    prediction = {'observations': [{'text': 'NASI GORENG'}, {'text': '25. 000'}]}
    assert cord_ocr_eval.score(prediction, truth) == {
        'word_hits': 3, 'word_total': 3, 'total_present': True, 'total_hit': False, 'price_hits': 1, 'price_total': 1}
    assert cord_ocr_eval.score(None, truth)['word_hits'] == 0
