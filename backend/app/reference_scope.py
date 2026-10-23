"""Bounded evidence-coverage checks, never natural-language answerability (Apache-2.0).

Caller declarations can narrow a request but cannot authorize an answer. Only
literal occurrences in server-verified candidate parents are reported. A match
does not establish relevance, applicability, completeness or a business fact.
"""
from .reference_sources import canonical, digest


NOTICE = ('本检查只核对声明的资料需求及原文片语覆盖，不判断自然语言问题是否可回答。'
          '命中仍需人工判断；没有生成业务答案，检索分数不参与证据充分性判断。')
MISSING = {
    'business_fact': ['当前快照中的原始合同、票据或交易记录', '资料与具体交易的对应关系及人工核验'],
    'applicability': ['当前账套制度、期间及交易事实', '条文适用条件和例外的专业复核'],
    'unspecified': ['明确需要查阅原文、核验企业事实，还是判断条文适用性'],
}


def assess_support(requirements, parents):
    """Pure helper; the API must verify parent provenance and scope first.

    `parents` contains only the actual search candidates, not the whole corpus.
    Offsets are Unicode character positions in the frozen parent text. No truth
    labels, generated answer, semantic similarity or external call is used.
    """
    rows = []
    for requirement in requirements:
        kind, text = requirement.kind, requirement.text
        matches = []
        if kind == 'policy_excerpt':
            for parent in parents:
                start = parent['text'].find(text)
                if start >= 0:
                    matches.append({'parent_id': parent['source_id'], 'start': start, 'end': start + len(text),
                                    'text': text, 'parent_sha256': digest(parent['text'].encode('utf-8'))})
            status = 'literal_match' if matches else 'not_found_in_candidates'
            missing = [] if matches else ['候选父条文中包含该完整片语的原文；可调整检索或人工查阅其他来源']
        else:
            status, missing = 'unsupported_request', MISSING[kind]
        rows.append({'kind': kind, 'text': text, 'status': status, 'matches': matches, 'missing_evidence': missing})
    matched = sum(row['status'] == 'literal_match' for row in rows)
    value = {'schema_version': 'reference-support-v2', 'answer_status': 'not_answered',
             'decision': 'manual_review' if matched == len(rows) else 'insufficient_evidence',
             'requirements': rows, 'literal_covered': matched, 'requirement_count': len(rows),
             'notice': NOTICE}
    return {**value, 'assessment_digest': digest(canonical(value))}


def candidate_boundary(status, has_candidates):
    """Every search carries an explicit answer boundary, even without declarations."""
    return {'answer_status': 'not_answered', 'candidate_status': (
        'available_for_manual_review' if status == 'complete' and has_candidates else
        'no_candidates' if status == 'complete' else 'not_completed'), 'notice': NOTICE}
