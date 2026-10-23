import pytest
from benchmarks.finlongeval_refusal import score
from datasets.finlongeval import canonical


def test_execution_failure_is_never_credited_as_correct_refusal():
    rows = [{'error': {'type': 'MissingPDF'}},
            {'keyword': {'accepted': True, 'score': .8}, 'bge': {'accepted': False, 'score': .1}}]
    result = score(rows, ['陷阱问题', '信息提取'])
    assert result['keyword']['trap_failed'] == 1
    assert result['keyword'].get('trap_rejected', 0) == 0
    assert result['keyword']['other_accepted'] == 1
    assert result['bge']['other_rejected'] == 1


def test_no_trap_cannot_establish_refusal():
    with pytest.raises(ValueError, match='trap'):
        score([{}], ['信息提取'])


def test_filename_normalization_is_not_fuzzy_answer_matching():
    assert canonical('报告（2023）.PDF') == canonical('报告(2023).pdf')
    assert canonical('报告2023.pdf') != canonical('报告2022.pdf')
