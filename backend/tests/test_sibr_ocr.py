from benchmarks.sibr_ocr import iou, score_document


def box(x=0):
    return [[x, 0], [x + 10, 0], [x + 10, 10], [x, 10]]


def truth(text, x=0):
    return {'text': text, 'box': box(x), 'label': 'answer'}


def observation(text, x=0):
    return {'text': text, 'locator': {'polygon_px': box(x)}}


def test_wrong_location_does_not_prove_field_recovery():
    result = score_document({'observations': [observation('100', 30)]}, {'form': [truth('100')]})
    assert result['text_exact'] == 1
    assert result['localized_exact'] == 0


def test_duplicate_numbers_need_distinct_observations():
    result = score_document({'observations': [observation('100')]}, {'form': [truth('100'), truth('100')]})
    assert result['localized_exact'] == 1
    assert result['total'] == 2


def test_substring_and_failures_are_not_success():
    assert score_document({'observations': [observation('100')]}, {'form': [truth('10')]})['text_exact'] == 0
    assert score_document(None, {'form': [truth('100')]})['total'] == 1


def test_normalization_and_geometry():
    assert iou(box(), box()) == 1
    result = score_document({'observations': [observation('１２ ３')]}, {'form': [truth('123')]})
    assert result['localized_exact'] == 1
