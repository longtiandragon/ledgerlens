import csv

from scripts.build_review_kit import ROOT, SHORT, rows, short_items


def answer_sheet(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return [(row['编号'], row['项目']) for row in csv.DictReader(stream)]


def test_committed_answer_sheets_match_the_current_catalog():
    # Rule renames must reach the printed answer sheet, not only the HTML kit.
    items = rows()
    assert answer_sheet(ROOT / 'docs/review-kit/复核答卷.csv') == [(i['id'], i['title']) for i in items]
    short = short_items(items)
    assert answer_sheet(ROOT / 'docs/review-kit/short/复核答卷.csv') == [(i['id'], i['title']) for i in short]


def test_short_kit_keeps_full_kit_ids_and_sections():
    items = rows()
    short = short_items(items)
    assert len(items) == 47 and len(short) == len(SHORT['policy']) + len(SHORT['cause']) + len(SHORT['simulation']) == 12
    assert [i['id'] for i in short] == ['P05', 'P07', 'P08', 'P09', 'P29', 'P32', 'R03', 'R05', 'R08', 'S1', 'S4', 'S5']
    full_ids = {i['id']: i['title'] for i in items}
    assert all(full_ids[i['id']] == i['title'] for i in short)
