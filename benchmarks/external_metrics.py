"""Independent fixed-label scoring; abstentions and missing predictions never disappear."""
from collections import Counter
from decimal import Decimal


def classification_metrics(expected, predictions, labels):
    if not expected or len(labels) != len(set(labels)) or not labels:
        raise ValueError('Nonempty truth and unique declared labels required')
    if set(expected) != set(predictions):
        raise ValueError('Missing or foreign predictions; retain failures explicitly as None')
    if not set(expected.values()) <= set(labels):
        raise ValueError('Unknown truth label')
    if not set(predictions.values()) <= set(labels) | {None}:
        raise ValueError('Unknown prediction label')
    actual = Counter(expected.values())
    predicted = Counter(predictions.values())
    correct = Counter(expected[k] for k in expected if expected[k] == predictions[k])
    rows = {}
    for label in labels:
        tp = correct[label]
        fp, fn = predicted[label] - tp, actual[label] - tp
        denominator = 2 * tp + fp + fn
        rows[label] = dict(tp=tp, fp=fp, fn=fn, support=actual[label],
                           f1=str(Decimal(2 * tp) / denominator if denominator else Decimal(0)))
    macro = sum((Decimal(x['f1']) for x in rows.values()), Decimal(0)) / len(labels)
    return dict(cases=len(expected), correct=sum(correct.values()), abstentions=predicted[None],
                accuracy=str(Decimal(sum(correct.values())) / len(expected)), macro_f1=str(macro),
                maximum_possible_f1_gain=str(Decimal(1) - macro), per_class=rows)
