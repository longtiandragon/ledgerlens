import pytest

from benchmarks.external_metrics import classification_metrics


def test_abstentions_stay_in_denominator():
    result = classification_metrics({'a': 'A', 'b': 'A', 'c': 'B'},
                                    {'a': 'A', 'b': None, 'c': 'B'}, ['A', 'B'])
    assert result['cases'] == 3
    assert result['correct'] == 2
    assert result['abstentions'] == 1
    assert result['per_class']['A']['fn'] == 1
    assert result['macro_f1'].startswith('0.833333')


def test_absent_class_is_not_silently_removed():
    result = classification_metrics({'a': 'A'}, {'a': 'A'}, ['A', 'B'])
    assert result['macro_f1'] == '0.5'


@pytest.mark.parametrize('predictions', [{}, {'a': 'A', 'extra': 'A'}, {'a': 'invented'}])
def test_incomplete_foreign_or_unknown_predictions_rejected(predictions):
    with pytest.raises(ValueError):
        classification_metrics({'a': 'A'}, predictions, ['A'])
