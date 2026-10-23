import pytest

from benchmarks.finder_retrieval import METHODS, analyse, metrics


def test_recall_counts_all_references_and_empty_predictions():
    labels = [{'id': 'q', 'references': ['a', 'b']}]
    row = {'id': 'q', **{m: ['x', 'a'] for m in METHODS}}
    row['bge'] = []
    result = metrics([row], labels)
    assert result['keyword'] == [[.5, 0, 1, .5]]
    assert result['bge'] == [[0, 0, 0, 0]]


def test_duplicates_and_misalignment_rejected():
    labels = [{'id': 'q', 'references': ['a']}]
    row = {'id': 'q', **{m: ['a', 'a'] for m in METHODS}}
    with pytest.raises(ValueError, match='Repeated'):
        metrics([row], labels)
    row['id'] = 'wrong'
    with pytest.raises(ValueError, match='differ'):
        metrics([row], labels)


def test_strongest_baseline_not_keyword_and_shared_labels_cluster():
    labels = [{'id': str(i), 'references': ['a']} for i in range(3)]
    rows = [{'id': str(i), 'keyword': [], 'bm25': ['a'], 'tfidf': [],
             'bge': ['a'], 'rrf': ['a']} for i in range(3)]
    result = analyse(rows, labels)
    assert result['clusters'] == 1
    assert result['strongest_deterministic'] == 'bm25'
    assert result['comparison']['bge']['delta'] == 0
    assert result['comparison']['bge']['supported'] is False


def test_execution_failure_blocks_positive_support():
    labels = [{'id': 'q', 'references': ['a']}]
    rows = [{'id': 'q', 'keyword': [], 'bm25': [], 'tfidf': [],
             'bge': ['a'], 'rrf': ['a'], 'error': {'type': 'failure'}}]
    assert analyse(rows, labels)['comparison']['bge']['supported'] is False
