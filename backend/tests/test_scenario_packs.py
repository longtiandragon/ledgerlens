"""Acceptance matrix: three scenario packs x normal / definite anomaly / insufficient / conflicting."""
import pytest
from fastapi.testclient import TestClient

from conftest import reset_test_database
from app.db import Base, engine
from app.main import app
from benchmarks.scenario_packs import CellRun, run_all
from datasets.scenario_packs import CELLS


@pytest.fixture
def client():
    reset_test_database(Base.metadata, engine)
    with TestClient(app) as client:
        yield client


def test_matrix_has_every_pack_and_condition_exactly_once():
    cells = {(cell['pack'], cell['condition']) for cell in CELLS}
    assert cells == {(pack, condition) for pack in ('funds', 'purchase', 'period')
                     for condition in ('normal', 'definite_anomaly', 'insufficient', 'conflicting')}
    assert len(CELLS) == 12


@pytest.mark.parametrize('cell', CELLS, ids=[cell['id'] for cell in CELLS])
def test_scenario_cell_runs_end_to_end(client, cell):
    result = CellRun(client, cell).run()
    failures = [step for step in result['steps'] if not step['passed']]
    assert result['passed'], failures


def test_runner_reports_a_wrong_expectation_as_failed_not_passed(client):
    cell = next(c for c in CELLS if c['id'] == 'purchase-anomaly')
    wrong = {**cell, 'steps': [('check', {'book': 'main', 'expect': [('RECEIPT_GAP', 'insufficient')]})]}
    result = CellRun(client, wrong).run()
    assert not result['passed'] and '预期' in result['steps'][0]['failure']
    report = run_all(client, [wrong])
    assert report['failed'] == ['purchase-anomaly'] and report['passed'] == 0
