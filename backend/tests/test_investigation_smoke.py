import copy
import json

import httpx
import pytest

from scripts.run_investigation_smoke import canonical_records, main, run_investigation_smoke


def fixture_data():
    rows = [{"id": f"r{i}", "evidence_id": f"e{i}", "source_id": f"source-{i}",
             "kind": "journal", "period": "2026-09", "debit": "0.10", "credit": "0.00"}
            for i in (1, 2)]
    case = {"id": "case-1", "book_id": "book-1", "snapshot_id": "snapshot-1", "stale": False}
    snapshot = {"id": "snapshot-1", "book_id": "book-1", "record_ids": ["r1", "r2"]}
    evidence = [{"id": f"e{i}", "record_id": f"r{i}", "source_id": f"source-{i}", "kind": "journal",
                 "record": rows[i - 1], "document_id": "document-1", "locator": {"row": i + 1}}
                for i in (1, 2)]
    usage = {"calls": 3, "tokens": 100, "tools": 2, "reserved_tokens": 0}
    result = {"status": "supported", "mode": "single", "usage": usage,
              "claims": [{"text": "Program checked", "evidence_ids": ["e1"]}],
              "proposed_claims": [{"text": "Model proposed", "evidence_ids": ["e2"]}],
              "hypotheses": [{"status": "unresolved", "evidence_ids": []},
                             {"status": "supported", "evidence_ids": ["e1"]}]}
    run = {"id": "run-1", "case_id": "case-1", "mode": "single", "status": "complete",
           "result": result, "usage": usage, "error": None,
           "steps": [{"event": "agent_started"}, {"event": "model_usage", "usage": usage}]}
    return {"rows": rows, "case": case, "snapshot": snapshot, "evidence": evidence, "run": run}


class ApiFixture:
    def __init__(self, data=None, *, changed_after=False, queued_forever=False, missing_export_locator=False,
                 complete_on_first_get=False):
        self.data = data or fixture_data()
        self.posts = 0
        self.polls = 0
        self.records_reads = 0
        self.changed_after = changed_after
        self.queued_forever = queued_forever
        self.missing_export_locator = missing_export_locator
        self.complete_on_first_get = complete_on_first_get

    def handler(self, request):
        path = request.url.path
        data = self.data
        if request.method == "POST":
            self.posts += 1
            assert path == "/api/v1/cases/case-1/investigate"
            assert json.loads(request.content) == {"mode": "single"}
            return httpx.Response(200, json={**data["run"], "status": "queued", "steps": [], "result": {},
                                           "usage": {"calls": 0, "tokens": 0, "tools": 0, "reserved_tokens": 0}})
        if path == "/api/v1/cases/case-1":
            return httpx.Response(200, json=data["case"])
        if path == "/api/v1/snapshots/snapshot-1":
            return httpx.Response(200, json=data["snapshot"])
        if path == "/api/v1/books/book-1/records":
            self.records_reads += 1
            rows = copy.deepcopy(data["rows"])
            if self.changed_after and self.records_reads > 1:
                rows[0]["debit"] = "0.11"
            return httpx.Response(200, json=rows)
        if path == "/api/v1/runs/run-1":
            self.polls += 1
            if self.queued_forever:
                return httpx.Response(200, json={**data["run"], "status": "running", "steps": [], "result": {}})
            if self.polls == 1 and not self.complete_on_first_get:
                return httpx.Response(200, json={**data["run"], "status": "running", "result": {}, "steps": [
                    {"event": "agent_started", "reasoning_content": "PRIVATE_REASONING_MARKER",
                     "arguments": {"api_key": "sk-12345678901234567890"}},
                ]})
            return httpx.Response(200, json=data["run"])
        if path == "/api/v1/cases/case-1/evidence":
            return httpx.Response(200, json=data["evidence"])
        if path == "/api/v1/exports/cases/case-1":
            if request.url.params["format"] == "markdown":
                return httpx.Response(200, text="# Workpaper\nsnapshot-1 run-1 e1 e2\n")
            evidence = copy.deepcopy(data["evidence"])
            if self.missing_export_locator:
                evidence[1]["locator"] = {}
            return httpx.Response(200, json={**data["case"], "snapshot": data["snapshot"],
                                           "runs": [data["run"]], "evidence": evidence})
        raise AssertionError(f"Unexpected API request: {request.method} {path}")


async def execute(api, *, report_path=None, **kwargs):
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(api.handler)) as client:
        return await run_investigation_smoke(client, case_id="case-1", mode="single",
                                             report_path=report_path, poll_seconds=0, **kwargs)


@pytest.mark.asyncio
async def test_complete_investigation_checks_original_ledger_references_and_workpapers(tmp_path):
    api = ApiFixture()
    progress = []
    path = tmp_path / "acceptance.json"
    report = await execute(api, report_path=path, on_progress=progress.append)
    assert report["status"] == "passed", report
    assert api.posts == report["launch_posts"] == 1
    assert report["run_id"] == "run-1" and report["polls"] == 2
    assert report["records_before"] == report["records_after"]
    assert report["verifications"]["original_records_unchanged"] is True
    refs = report["verifications"]["references"]
    assert refs["reference_instances"] == 3 and refs["unique_references"] == 2
    assert refs["unreferenced_hypotheses"] == 1
    assert report["verifications"]["exports"]["reference_location_rate"] == 1.0
    assert (tmp_path / "acceptance.workpaper.json").exists()
    assert (tmp_path / "acceptance.workpaper.md").exists()
    text = json.dumps(report) + json.dumps(progress) + path.read_text(encoding="utf-8")
    assert "PRIVATE_REASONING_MARKER" not in text and "sk-12345678901234567890" not in text
    assert json.loads(path.read_text(encoding="utf-8")) == report


@pytest.mark.asyncio
async def test_old_payloads_resolve_record_ids_through_actual_evidence():
    data = fixture_data()
    data["rows"] = [{key: value for key, value in row.items() if key not in {"id", "evidence_id"}}
                    for row in data["rows"]]
    report = await execute(ApiFixture(data))
    assert report["status"] == "passed", report
    assert report["verifications"]["references"]["mapped_snapshot_records"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["outside_snapshot", "missing_locator", "changed_ledger", "wrong_run", "failed_run",
                                     "false_evidence_mapping", "no_model_calls"])
async def test_model_completion_cannot_mask_scope_export_binding_or_ledger_failures(failure):
    data = fixture_data()
    if failure == "outside_snapshot":
        data["run"]["result"]["proposed_claims"][0]["evidence_ids"] = ["future-evidence"]
    elif failure == "wrong_run":
        data["run"]["case_id"] = "other-case"
    elif failure == "failed_run":
        data["run"]["status"] = "budget_exhausted"
        data["run"]["error"] = '额度不足；错误内容包含 sk-12345678901234567890'
    elif failure == "false_evidence_mapping":
        data["evidence"][0]["id"] = "e2"
    elif failure == "no_model_calls":
        data["run"]["usage"]["calls"] = 0
    api = ApiFixture(data, changed_after=failure == "changed_ledger", missing_export_locator=failure == "missing_locator")
    report = await execute(api)
    assert report["status"] == "failed", report
    expected = {"outside_snapshot": "reference_outside_snapshot", "missing_locator": "export_missing_reference_locators",
                "changed_ledger": "original_records_changed", "wrong_run": "run_binding_mismatch",
                "failed_run": "terminal_run_failed", "false_evidence_mapping": "record_evidence_identity_conflict",
                "no_model_calls": "model_mode_without_model_calls"}
    assert report["error"]["code"] == expected[failure]
    assert api.posts == 1
    assert "sk-12345678901234567890" not in json.dumps(report)
    if failure == "failed_run":
        assert report["run_status"] == "budget_exhausted"
        assert "额度不足" in report["error"]["detail"]["error"]


@pytest.mark.asyncio
async def test_timeout_keeps_actual_running_status_without_relaunching():
    api = ApiFixture(queued_forever=True)
    report = await execute(api, timeout_seconds=0.002)
    assert report["status"] == "failed"
    assert report["error"]["code"] == "poll_timeout"
    assert report["run_status"] in {"queued", "running"}
    assert report["timeout_does_not_cancel_run"] is True
    assert api.posts == 1
    assert report["verifications"]["original_records_unchanged"] is True


@pytest.mark.asyncio
async def test_empty_references_do_not_claim_full_location_rate():
    data = fixture_data()
    data["run"]["result"].update({"claims": [], "proposed_claims": [],
                                  "hypotheses": [{"status": "unresolved", "evidence_ids": []}]})
    report = await execute(ApiFixture(data))
    assert report["status"] == "passed", report
    assert report["verifications"]["exports"]["reference_location_rate"] is None
    assert report["verifications"]["exports"]["no_references_to_check"] is True


@pytest.mark.asyncio
async def test_existing_completed_run_is_verified_with_zero_launches(tmp_path):
    api = ApiFixture(complete_on_first_get=True)
    report = await execute(api, run_id="run-1", report_path=tmp_path / "existing-run.json")
    assert report["status"] == "passed", report
    assert api.posts == report["launch_posts"] == 0
    assert report["polls"] == 0
    assert report["execution"] == "inspect_existing"
    assert report["historical_ledger_state_before_run_verified"] is False
    assert "未验证已有 run 开始前" in report["records_hash_scope"]
    assert report["verifications"]["original_records_unchanged"] is True
    assert report["verifications"]["exports"]["reference_location_rate"] == 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["mode", "case", "identity", "snapshot", "terminal"])
async def test_existing_run_wrong_binding_or_failure_does_not_launch_replacement(failure):
    data = fixture_data()
    if failure == "mode":
        data["run"]["mode"] = "multi"
    elif failure == "case":
        data["run"]["case_id"] = "other-case"
    elif failure == "identity":
        data["run"]["id"] = "other-run"
    elif failure == "snapshot":
        data["snapshot"]["book_id"] = "other-book"
    elif failure == "terminal":
        data["run"]["status"] = "failed"
        data["run"]["error"] = "HTTP 402: 额度不足"
    api = ApiFixture(data, complete_on_first_get=True)
    report = await execute(api, run_id="run-1")
    assert report["status"] == "failed", report
    assert api.posts == report["launch_posts"] == 0
    expected = "snapshot_binding_mismatch" if failure == "snapshot" else (
        "terminal_run_failed" if failure == "terminal" else "run_binding_mismatch"
    )
    assert report["error"]["code"] == expected
    if failure == "terminal":
        assert report["run_status"] == "failed"
        assert "402" in report["run_error"]


@pytest.mark.asyncio
async def test_existing_stale_case_can_be_audited_as_historical_snapshot():
    data = fixture_data()
    data["case"]["stale"] = True
    api = ApiFixture(data, complete_on_first_get=True)
    report = await execute(api, run_id="run-1")
    assert report["status"] == "passed", report
    assert report["case_currently_stale"] is True
    assert report["historical_ledger_state_before_run_verified"] is False
    assert api.posts == 0


def test_cli_existing_run_does_not_need_live_opt_in_or_trigger_post(tmp_path, monkeypatch):
    api = ApiFixture(complete_on_first_get=True)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(api.handler), **kwargs,
    ))
    path = tmp_path / "existing.json"
    status = main(["--case-id", "case-1", "--mode", "single", "--run-id", "run-1", "--report", str(path)])
    assert status == 0 and api.posts == 0
    assert json.loads(path.read_text(encoding="utf-8"))["launch_posts"] == 0


def test_record_hash_preserves_money_and_ignores_order():
    rows = fixture_data()["rows"]
    assert canonical_records(rows) == canonical_records(list(reversed(rows)))
    changed = copy.deepcopy(rows)
    changed[0]["debit"] = "0.11"
    assert canonical_records(changed) != canonical_records(rows)


def test_cli_requires_live_opt_in_and_local_endpoint():
    for args in (["--case-id", "case-1", "--mode", "single"],
                 ["--case-id", "case-1", "--mode", "single", "--live", "--base-url", "https://example.com"]):
        with pytest.raises(SystemExit) as failure:
            main(args)
        assert failure.value.code == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('existing_name', ['run.v15.json', 'run.v15.workpaper.json', 'run.v15.workpaper.md'])
async def test_acceptance_existing_report_or_artifact_rejected_before_any_network(tmp_path, existing_name):
    previous = tmp_path / existing_name
    previous.write_text('PREVIOUS-PAID-RUN-EVIDENCE', encoding='utf-8')
    requests = []
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000', transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(500))) as client:
        with pytest.raises(FileExistsError):
            await run_investigation_smoke(client, case_id='case-1', mode='single',
                                          report_path=tmp_path / 'run.v15.json')
    assert requests == []
    assert previous.read_text(encoding='utf-8') == 'PREVIOUS-PAID-RUN-EVIDENCE'


@pytest.mark.asyncio
async def test_acceptance_artifact_names_preserve_version_suffix(tmp_path):
    report = await execute(ApiFixture(), report_path=tmp_path / 'run.v15.json')
    assert report['status'] == 'passed'
    assert (tmp_path / 'run.v15.workpaper.json').is_file()
    assert (tmp_path / 'run.v15.workpaper.md').is_file()
    assert not (tmp_path / 'run.workpaper.json').exists()
