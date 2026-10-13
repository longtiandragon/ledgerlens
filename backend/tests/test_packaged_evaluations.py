"""Exercise historical evaluation routes using only docs copied by the image recipe.

This verifies the literal COPY payload contract, not a Docker build or Linux run.
"""
from pathlib import Path, PurePosixPath
import shlex
import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import numeric_evaluation, retrieval_results, root_cause_results

ROOT = Path(__file__).resolve().parents[2]


def image_docs(destination):
    """Stage the recipe's explicit docs COPY entries; fail on unsupported syntax."""
    copied = set()
    recipe = (ROOT / 'backend/Dockerfile').read_text(encoding='utf-8').replace('\\\n', ' ')
    for line in recipe.splitlines():
        fields = shlex.split(line, comments=True)
        if not fields or fields[0] != 'COPY' or not any('docs' in field for field in fields[1:]):
            continue
        target = PurePosixPath(fields[-1])
        for source in fields[1:-1]:
            relative = PurePosixPath(source)
            assert relative.parts[0] == 'docs' and '..' not in relative.parts
            assert not any(char in source for char in '*?[')
            assert (ROOT / source).is_file(), 'Use explicit public files in the image recipe'
            output = target / relative.name if fields[-1].endswith('/') else target
            assert output.parts[0] == 'docs' and '..' not in output.parts
            assert str(output) not in copied, 'Overlapping COPY destinations'
            copied.add(str(output))
            path = destination / str(output)
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / source, path)
    return destination / 'docs'


@pytest.fixture
def packaged_client(tmp_path, monkeypatch):
    docs = image_docs(tmp_path)
    monkeypatch.setattr(numeric_evaluation, 'REPORTS_DIR', docs)
    monkeypatch.setattr(root_cause_results, 'SUMMARY', docs / 'root-cause-results.json')
    monkeypatch.setattr(retrieval_results, 'DOCS', docs)
    app = FastAPI()
    for module in (numeric_evaluation, root_cause_results, retrieval_results):
        app.include_router(module.router)
    return TestClient(app)


def test_packaged_cause_history_retains_failures_and_negative_conclusions(packaged_client):
    causes = packaged_client.get('/api/v1/evaluations/root-cause').json()
    assert causes['status'] == 'available', 'Image must include root-cause history'
    assert '未获支持' in causes['conclusion']
    final = next(run for run in causes['runs'] if run['id'] == 'v2-sealed-final')
    assert final['summary_status'] == 'verified'
    assert final['methods']['single']['failures'] == 6
    assert final['methods']['single']['samples'] == 18


def test_packaged_numeric_history_retains_failed_and_abstained_counts(packaged_client):
    numeric = packaged_client.get('/api/v1/evaluations/numeric').json()
    assert numeric['issues'] == [], 'Image must include arithmetic replay and all historical model pilots'
    assert numeric['arithmetic_replay']['exact_matches'] == 883
    pilots = {pilot['id']: pilot for pilot in numeric['model_pilots']}
    assert set(pilots) == {'finqa-v1', 'finqa-v2', 'finqa-v2-next32'}
    assert pilots['finqa-v1']['exact_matches'] == 9 and pilots['finqa-v1']['failed'] == 12
    assert pilots['finqa-v2-next32']['exact_matches'] == 18 and pilots['finqa-v2-next32']['abstained'] == 1


def test_packaged_retrieval_history_keeps_failed_gate(packaged_client):
    retrieval = packaged_client.get('/api/v1/evaluations/retrieval').json()
    assert retrieval['baseline']['status'] == retrieval['abstention']['status'] == 'available'
    assert all(row['accepted_unanswerable'] == 2 and row['candidate_meets_gate'] is False
               for row in retrieval['abstention']['rows'])
