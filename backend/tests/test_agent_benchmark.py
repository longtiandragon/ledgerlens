import asyncio
import json

import httpx
import pytest
from app.model_gateway import DeepSeekGateway
from benchmarks.run_agents import (BenchmarkOptions, BoundedGateway, PaidRequestBudget, _model_configuration,
                                   _prediction, family_bootstrap, load_observable, main, metrics, run_benchmark, select_cases)
from datasets.generate import write_dataset


@pytest.fixture
def dataset(tmp_path):
    target = tmp_path / "dataset"
    write_dataset(target)
    return target


def options(dataset, tmp_path, **kwargs):
    return BenchmarkOptions(dataset=dataset, report=tmp_path / "report.json", **kwargs)


def mock_response(proposal):
    return httpx.Response(200, json={"model": "test-provider", "usage": {"total_tokens": 12},
                                    "choices": [{"finish_reason": "stop", "message": {
                                        "role": "assistant", "content": json.dumps(proposal)}}]})


NORMAL_PROPOSAL = {"status": "normal", "claims": [], "hypotheses": [], "missing_evidence": []}


@pytest.mark.asyncio
async def test_missing_key_explicitly_refuses_model_run(dataset, tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    opts = options(dataset, tmp_path, modes=("rules", "single"), repeat=1, max_cases=1)
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        await run_benchmark(opts)
    assert not opts.report.exists()


@pytest.mark.asyncio
async def test_rules_only_executes_without_key_and_preserves_splits(dataset, tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    opts = options(dataset, tmp_path, modes=("rules",), repeat=3, max_cases=8)
    report = await run_benchmark(opts)
    assert report["status"] == "completed"
    assert len(report["cases"]) == 24
    assert report["paid_requests"] == 0
    assert report["groups"]["rules"]["families"] == 8
    assert set(report["groups"]["rules"]["by_framework"]) == {"CAS", "SCAS"}
    assert {r["split"] for r in report["cases"]} == {"development"}
    assert opts.report.with_suffix(".csv").exists()
    assert "hidden_root_cause" not in opts.report.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_all_four_methods_really_execute_with_mock_transport(dataset, tmp_path):
    sent = []
    def handler(request):
        sent.append(json.loads(request.content))
        return mock_response(NORMAL_PROPOSAL)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await run_benchmark(options(dataset, tmp_path, repeat=1, max_cases=1, max_paid_requests=20),
                                     gateway=DeepSeekGateway("test-key", client=client))
    assert len(report["cases"]) == 4
    assert report["paid_requests"] == len(sent) == 9  # 1 + 5 + 3 actual calls
    assert {r["method"]: r["paid_requests"] for r in report["cases"]} == {
        "rules": 0, "single": 1, "multi": 5, "active": 3}
    for request in sent:
        serialized = json.dumps(request)
        assert "hidden_root_cause" not in serialized
        assert "fault_mechanism" not in serialized
        assert "expected_rules" not in serialized
        assert "family_name" not in serialized
    assert report["metric_scope"] == "visible_synthetic_rule_labels_and_status_not_root_cause"


@pytest.mark.asyncio
async def test_actual_attempt_budget_counts_429_retries(dataset, tmp_path):
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(429)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await run_benchmark(options(dataset, tmp_path, modes=("single",), repeat=1,
                                             max_cases=3, max_paid_requests=2),
                                     gateway=DeepSeekGateway("test", client=client, retry_delay=0))
    assert calls == report["paid_requests"] == 2
    assert len(report["cases"]) == 3
    assert all(r["error"] for r in report["cases"])
    assert report["groups"]["single"]["failures"] == 3


@pytest.mark.asyncio
async def test_model_metric_does_not_use_fact_status_or_deterministic_claims(dataset, tmp_path):
    async def fake_runner(records, policy, finding, **kwargs):
        return {"status": "supported", "fact_status": "supported",
                "claims": [{"rule_id": "DUPLICATE_INVOICE", "evidence_ids": [records[0]["evidence_id"]]}],
                "proposals": [NORMAL_PROPOSAL], "usage": {}, "model": "test-only"}
    report = await run_benchmark(options(dataset, tmp_path, modes=("single",), repeat=1, max_cases=1),
                                 gateway=object(), runner=fake_runner)
    assert report["cases"][0]["predicted_rules"] == []
    assert report["cases"][0]["predicted_status"] == "normal"
    assert report["cases"][0]["exact_rule_match"] is True


@pytest.mark.asyncio
async def test_runtime_never_receives_truth_or_manifest_fault_metadata(dataset, tmp_path):
    entry = select_cases(json.loads((dataset / "manifest.json").read_text()), "development", 1)[0]
    observed = dataset / "observed" / f"{entry['case_id']}.json"
    content = json.loads(observed.read_text(encoding="utf-8"))
    content["hidden_root_cause"] = "DO_NOT_SEND"
    content["records"][0]["ground_truth"] = "DO_NOT_SEND"
    content["policy"]["expected_rules"] = ["DO_NOT_SEND"]
    observed.write_text(json.dumps(content), encoding="utf-8")
    manifest = json.loads((dataset / "manifest.json").read_text())
    manifest["cases"][0].pop("sha256")
    (dataset / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    seen = []
    async def fake_runner(records, policy, finding, **kwargs):
        seen.append(json.dumps([records, policy, finding]))
        assert "DO_NOT_SEND" not in seen[-1]
        # Truth is not read until after this call: make its absence observable.
        truth = dataset / "truth" / f"{entry['case_id']}.json"
        saved = truth.read_text()
        truth.unlink()
        truth.write_text(saved)
        return {"status": "unsupported", "proposals": [NORMAL_PROPOSAL], "usage": {}}
    await run_benchmark(options(dataset, tmp_path, modes=("single",), repeat=1, max_cases=1),
                        gateway=object(), runner=fake_runner)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_invalid_citations_do_not_score_as_correct(dataset, tmp_path):
    async def fake_runner(records, policy, finding, **kwargs):
        return {"status": "unsupported", "proposals": [{"status": "supported", "claims": [
            {"rule_id": "DUPLICATE_INVOICE", "evidence_ids": ["fake"]}]}], "usage": {}}
    report = await run_benchmark(options(dataset, tmp_path, modes=("single",), repeat=1, max_cases=1),
                                 gateway=object(), runner=fake_runner)
    row = report["cases"][0]
    assert row["invalid_claims"] == 1
    assert row["predicted_rules"] == []
    assert report["groups"]["single"]["citation_locatability_rate"] == 0


@pytest.mark.asyncio
async def test_failures_keep_partial_outputs_but_primary_metric_counts_failure(dataset, tmp_path):
    async def fake_runner(records, policy, finding, **kwargs):
        return {"status": "failed", "error": {"code": "model_transport", "message": "failed"},
                "proposals": [NORMAL_PROPOSAL], "usage": {"calls": 1, "tokens": 5}}
    report = await run_benchmark(options(dataset, tmp_path, modes=("single",), repeat=1, max_cases=1),
                                 gateway=object(), runner=fake_runner)
    row = report["cases"][0]
    assert row["predicted_status"] == "normal"
    assert row["exact_rule_match"] is False
    assert report["groups"]["single"]["failures"] == 1


@pytest.mark.asyncio
async def test_missing_truth_is_unscorable_not_assumed_normal(dataset, tmp_path):
    entry = select_cases(json.loads((dataset / "manifest.json").read_text()), "development", 1)[0]
    (dataset / "truth" / f"{entry['case_id']}.json").unlink()
    report = await run_benchmark(options(dataset, tmp_path, modes=("rules",), repeat=1, max_cases=1))
    assert report["cases"][0]["error"]["code"] == "truth_unavailable"
    assert report["groups"]["rules"]["samples"] == 0
    assert report["groups"]["rules"]["unscorable"] == 1


def test_small_selection_spreads_families_and_frameworks(dataset):
    manifest = json.loads((dataset / "manifest.json").read_text())
    selected = select_cases(manifest, "development", 8)
    assert len({c["family_id"] for c in selected}) == 8
    assert set(c["framework"] for c in selected) == {"CAS", "SCAS"}
    assert selected[-1]["case_id"].startswith("case-27-")
    assert all(c["split"] == "development" for c in selected)


def test_shortcut_source_names_are_opaque_but_relations_preserved(dataset):
    manifest = json.loads((dataset / "manifest.json").read_text())
    entry = next(c for c in manifest["cases"] if c["case_id"] == "case-11-00")
    entry["family_id"] = "family-11"
    content = load_observable(dataset, entry)
    serialized = json.dumps(content)
    assert "JV-COPY" not in serialized
    invoice_ids = {r["invoice_id"] for r in content["records"] if r.get("invoice_id")}
    assert len(invoice_ids) == 1


@pytest.mark.asyncio
async def test_ablation_calls_are_real_and_labelled(dataset, tmp_path):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: mock_response(NORMAL_PROPOSAL))) as client:
        report = await run_benchmark(options(dataset, tmp_path, modes=("active",), repeat=1, max_cases=1,
                                             ablations=("no_counterproof",), max_paid_requests=10),
                                     gateway=DeepSeekGateway("test", client=client))
    assert report["paid_requests"] == 6
    assert set(report["groups"]) == {"active", "active:no_counterproof"}
    assert {p["role"] for p in report["cases"][1]["model_proposals"]} == {"coordinator", "evidence", "report"}


def test_metric_name_and_failure_denominator():
    rows = [{"case_id": "a", "family_id": "f", "expected_rules": ["X"], "scored_predicted_rules": ["X"],
             "exact_rule_match": True, "error": None, "predicted_status": "insufficient"},
            {"case_id": "b", "family_id": "g", "expected_rules": ["X"], "scored_predicted_rules": [],
             "exact_rule_match": False, "error": {"code": "failed"}, "predicted_status": None}]
    result = metrics(rows)
    assert result["macro_rule_f1"] == pytest.approx(2 / 3)
    assert result["uncertainty_rate"] == 0.5
    assert result["exact_rule_match_rate"] == 0.5
    assert "root_cause_accuracy" not in result


def test_cli_missing_key_fails_without_creating_performance_report(dataset, tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    output = tmp_path / "never.json"
    assert main(["--dataset", str(dataset), "--report", str(output), "--modes", "single", "--max-cases", "1"]) == 2
    assert not output.exists()


@pytest.mark.parametrize("fields", [{"max_cases": 0}, {"repeat": 0}, {"max_paid_requests": -1},
                                    {"modes": ("single",), "ablations": ("no_counterproof",)}])
def test_options_reject_unbounded_or_invalid_comparisons(tmp_path, fields):
    with pytest.raises(ValueError):
        options(tmp_path, tmp_path, **fields).validate()


def test_env_file_is_explicit_with_environment_precedence_and_no_process_mutation(tmp_path, monkeypatch):
    for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text('DEEPSEEK_API_KEY="fixture-only-key"\nDEEPSEEK_MODEL=file-model\n'
                   'DEEPSEEK_BASE_URL=https://example.test\nUNRELATED_SECRET=do-not-read\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert _model_configuration(None)["DEEPSEEK_API_KEY"] == ""
    monkeypatch.setenv("DEEPSEEK_MODEL", "environment-model")
    settings = _model_configuration(env)
    assert settings == {"DEEPSEEK_API_KEY": "fixture-only-key", "DEEPSEEK_MODEL": "environment-model",
                        "DEEPSEEK_BASE_URL": "https://example.test"}
    assert _model_configuration(None)["DEEPSEEK_API_KEY"] == ""
    assert "UNRELATED_SECRET" not in settings


def test_env_interpolation_disabled_and_missing_file_message_does_not_expose_values(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("ANOTHER_SECRET", "must-not-expand")
    env = tmp_path / "config.env"
    env.write_text("DEEPSEEK_API_KEY=${ANOTHER_SECRET}\n", encoding="utf-8")
    assert _model_configuration(env)["DEEPSEEK_API_KEY"] == "${ANOTHER_SECRET}"
    with pytest.raises(ValueError, match="不存在或不是文件") as error:
        _model_configuration(tmp_path / "secret-filename.env")
    assert "secret-filename" not in str(error.value)


def test_fixed_macro_label_denominator_and_zero_support_policy():
    correct = {"case_id": "a", "family_id": "f", "expected_rules": ["X"], "scored_predicted_rules": ["X"],
               "exact_rule_match": True, "status_match": True, "error": None}
    extra = {**correct, "scored_predicted_rules": ["X", "Y"], "exact_rule_match": False}
    first, second = metrics([correct], labels=["X", "Y"]), metrics([extra], labels=["X", "Y"])
    assert first["rule_labels"] == second["rule_labels"] == ["X", "Y"]
    assert first["macro_rule_f1"] == second["macro_rule_f1"] == 0.5
    assert first["supported_rule_labels"] == 1
    assert first["observed_rule_macro_f1"] == second["observed_rule_macro_f1"] == 1
    assert first["unexpected_predicted_rule_count"] == 0
    assert second["unexpected_predicted_rule_count"] == 1
    assert first["per_rule"]["Y"]["f1"] == 0
    assert second["per_rule"]["Y"]["fp"] == 1
    assert second["exact_rule_match_rate"] == 0
    with pytest.raises(ValueError, match="outside"):
        metrics([extra], labels=["X"])


def test_bootstrap_clusters_keep_repeats_together_and_failure_denominator():
    rows = [{"case_id": "pass", "family_id": "passed-family", "exact_rule_match": True,
             "status_match": True, "error": None} for _ in range(10)]
    rows += [{"case_id": "fail", "family_id": "failed-family", "exact_rule_match": True,
              "status_match": True, "error": {"code": "budget_exhausted"}} for _ in range(10)]
    first = family_bootstrap(rows, iterations=500, seed=42)
    assert first == family_bootstrap(rows, iterations=500, seed=42)
    assert first["families"] == 2
    assert first["exact_rule_match_rate"] == {"lower": 0, "upper": 1}
    assert first["status_match_rate"] == {"lower": 0, "upper": 1}
    one_family = family_bootstrap(rows[:10])
    assert one_family["exact_rule_match_rate"] is None
    assert one_family["status_match_rate"] is None
    assert "至少需要两个" in one_family["limitation"]


@pytest.mark.asyncio
async def test_all_methods_use_shared_catalog_labels_and_report_scope(dataset, tmp_path):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: mock_response(NORMAL_PROPOSAL))) as client:
        report = await run_benchmark(options(dataset, tmp_path, repeat=1, max_cases=2, max_paid_requests=20),
                                     gateway=DeepSeekGateway("test-key", client=client))
    assert report["schema_version"] == "agent-benchmark-v5"
    assert report["active_mechanism"] == "known_hypothesis_pairs_per_server_cost_with_snapshot_query_execution"
    labels = report["fixed_rule_labels"]
    assert labels
    for group in report["groups"].values():
        assert group["rule_labels"] == labels
        assert group["observed_rule_labels"] == report["observed_rule_labels"]
        assert group["family_bootstrap"]["families"] == 2
        assert group["samples"] == 2
        for sub in group["by_framework"].values():
            assert sub["rule_labels"] == labels
            assert sub["observed_rule_labels"] == report["observed_rule_labels"]


def test_provider_usage_aggregation_keeps_missing_fields_and_reported_zero_distinct():
    from benchmarks.run_agents import request_usage_totals
    rows = [{"model_requests": [
        {"request_usage": {"total_tokens": 100, "prompt_cache_hit_tokens": 0}},
        {"request_usage": {"total_tokens": 40, "prompt_tokens": 30, "completion_tokens": 10,
                           "completion_tokens_details": {"reasoning_tokens": 5}}},
        {"request_usage": None},
    ]}]
    report = request_usage_totals(rows)
    assert report["reported_responses"] == 3
    assert report["fields"]["total_tokens"] == {"reported_responses": 2, "sum": 140}
    assert report["fields"]["prompt_cache_hit_tokens"] == {"reported_responses": 1, "sum": 0}
    assert report["fields"]["prompt_cache_miss_tokens"] == {"reported_responses": 0, "sum": None}
    assert report["fields"]["completion_tokens_details.reasoning_tokens"] == {"reported_responses": 1, "sum": 5}


@pytest.mark.asyncio
async def test_explicit_env_file_constructs_gateway_and_never_serializes_key(dataset, tmp_path, monkeypatch):
    import benchmarks.run_agents as module
    from types import SimpleNamespace
    for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / "test-config.env"
    env.write_text("DEEPSEEK_API_KEY=fixture-private-value\nDEEPSEEK_MODEL=fixture-model\n", encoding="utf-8")
    constructed = []
    def make_gateway(key, **kwargs):
        constructed.append({"key": key, **kwargs})
        return SimpleNamespace(model=kwargs["model"])
    monkeypatch.setattr(module, "DeepSeekGateway", make_gateway)
    async def fake_runner(records, policy, finding, **kwargs):
        assert kwargs["gateway"].model == "fixture-model"
        return {"status": "unsupported", "proposals": [NORMAL_PROPOSAL], "usage": {}, "model": "actual-fixture"}
    opts = options(dataset, tmp_path, modes=("single",), repeat=1, max_cases=1, env_file=env)
    report = await run_benchmark(opts, runner=fake_runner)
    assert constructed == [{"key": "fixture-private-value", "base_url": "https://api.deepseek.com", "model": "fixture-model"}]
    assert report["cases"][0]["configured_model"] == "fixture-model"
    assert _model_configuration(None)["DEEPSEEK_API_KEY"] == ""
    assert "fixture-private-value" not in opts.report.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_sample_records_actual_versions_and_parameters_without_configuration_secrets(dataset, tmp_path):
    async def fake_runner(records, policy, finding, **kwargs):
        return {"status": "unsupported", "proposals": [NORMAL_PROPOSAL], "usage": {}, "model": "actual-model",
                "configured_model": "configured-alias", "prompt_version": "fixture-prompt-v7",
                "tool_version": "fixture-tools-v2", "model_parameters": {"thinking": False, "max_tokens": 123}}
    opts = options(dataset, tmp_path, modes=("single",), repeat=1, max_cases=1)
    report = await run_benchmark(opts, gateway=object(), runner=fake_runner)
    row = report["cases"][0]
    assert row["model"] == "actual-model"
    assert row["configured_model"] == "configured-alias"
    assert row["prompt_version"] == "fixture-prompt-v7"
    assert row["tool_version"] == "fixture-tools-v2"
    assert row["model_parameters"] == {"thinking": False, "max_tokens": 123}
    assert "prompt_version" in opts.report.with_suffix(".csv").read_text(encoding="utf-8-sig")
    assert "api_key" not in opts.report.read_text(encoding="utf-8").lower()


@pytest.mark.asyncio
async def test_bounded_gateway_records_actual_chat_parameters_and_forwards_model():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: mock_response(NORMAL_PROPOSAL))) as client:
        gateway = DeepSeekGateway("fixture-private-key", model="configured-model", client=client)
        bounded = BoundedGateway(gateway, PaidRequestBudget(1), "fixture")
        from app.model_gateway import RunBudget
        await bounded.chat([{"role": "user", "content": "json"}], tools=None, budget=RunBudget(),
                           thinking=False, max_tokens=456, reasoning_effort="low")
    assert bounded.model == "configured-model"
    assert bounded.request_parameters == [{"logical_chat_index": 1, "role": None, "stage": None,
                                           "model": "configured-model", "thinking": False, "max_tokens": 456,
                                           "configured_reasoning_effort": "low", "reasoning_effort": None,
                                           "temperature": None, "temperature_policy": "provider_default",
                                           "tools_enabled": False, "tool_names": []}]
    assert bounded.request_attempts[0]["request_index"] == 1
    assert bounded.request_attempts[0]["retry_attempt"] == 1
    assert "fixture-private-key" not in json.dumps(bounded.request_parameters)


@pytest.mark.parametrize("assessment", ["normal", "insufficient", "unsupported"])
def test_non_finding_assessments_do_not_count_as_anomaly_labels(assessment):
    result = {"proposals": [{"status": "normal", "claims": [{"rule_id": "VOUCHER_BALANCE",
               "text": "规则检查结果", "evidence_ids": ["ev-1"], "assessment": assessment}]}]}
    prediction = _prediction(result, "single", [{"evidence_id": "ev-1"}], {"framework": "CAS"})
    assert prediction["predicted_rules"] == []
    assert prediction["invalid_claims"] == 0
    assert prediction["cited_claims"] == prediction["valid_cited_claims"] == 1


@pytest.mark.parametrize("assessment", ["finding", None])
def test_finding_or_legacy_claim_counts_without_interpreting_text(assessment):
    claim = {"rule_id": "RECEIPT_GAP", "text": "正常、未见缺口（故意矛盾的测试文本）", "evidence_ids": ["ev-1"]}
    if assessment is not None:
        claim["assessment"] = assessment
    prediction = _prediction({"proposals": [{"status": "normal", "claims": [claim]}]},
                             "single", [{"evidence_id": "ev-1"}], {"framework": "CAS"})
    assert prediction["predicted_rules"] == ["RECEIPT_GAP"]


def test_invalid_assessment_is_invalid_structured_claim():
    prediction = _prediction({"proposals": [{"status": "normal", "claims": [{"rule_id": "VOUCHER_BALANCE",
                             "evidence_ids": ["ev-1"], "assessment": "some_other_value"}]}]},
                             "single", [{"evidence_id": "ev-1"}], {"framework": "CAS"})
    assert prediction["predicted_rules"] == []
    assert prediction["invalid_claims"] == 1


@pytest.mark.asyncio
async def test_normal_case_checks_are_not_reported_as_false_positive(dataset, tmp_path):
    async def fake_runner(records, policy, finding, **kwargs):
        return {"status": "unsupported", "usage": {}, "proposals": [{"status": "normal", "claims": [
            {"rule_id": rule_id, "assessment": "normal", "text": "正常检查", "evidence_ids": [records[0]["evidence_id"]]}
            for rule_id in ("VOUCHER_BALANCE", "RECEIPT_GAP")]}]}
    report = await run_benchmark(options(dataset, tmp_path, modes=("single",), repeat=1, max_cases=1),
                                 gateway=object(), runner=fake_runner)
    assert report["prediction_policy"] == "cited_claim_assessment_finding_only_default_finding_for_legacy"
    assert report["cases"][0]["predicted_rules"] == []
    assert report["cases"][0]["exact_rule_match"] is True
    assert report["groups"]["single"]["normal_false_positive_rate"] == 0
    assert report["groups"]["single"]["citation_locatability_rate"] == 1


@pytest.mark.asyncio
async def test_live_trace_saves_current_attempt_usage_and_clears_finished_sample(dataset, tmp_path):
    opts = options(dataset, tmp_path, modes=("single",), repeat=1, max_cases=1)
    snapshots = []
    def handler(request):
        persisted = json.loads(opts.report.read_text(encoding="utf-8"))
        assert persisted["paid_requests"] == persisted["current_sample"]["paid_requests"] == 1
        assert persisted["current_sample"]["current_event"] == "model_attempt"
        assert persisted["current_sample"]["usage"]["calls"] == 1
        assert persisted["cases"] == []
        return mock_response(NORMAL_PROPOSAL)
    async def fake_runner(records, policy, finding, **kwargs):
        from app.model_gateway import RunBudget
        budget = RunBudget()
        async def record_trace(step):
            await kwargs["on_step"](step)
            snapshots.append(json.loads(opts.report.read_text(encoding="utf-8")))
        await kwargs["gateway"].chat([{"role": "user", "content": "json"}], tools=None, budget=budget,
                                     on_attempt=record_trace)
        return {"status": "unsupported", "usage": budget.usage(), "proposals": [NORMAL_PROPOSAL]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await run_benchmark(opts, gateway=DeepSeekGateway("fixture", client=client), runner=fake_runner)
    assert [r["current_sample"]["current_event"] for r in snapshots] == ["model_attempt", "model_usage"]
    assert snapshots[-1]["current_sample"]["usage"]["tokens"] == 12
    assert snapshots[-1]["current_sample"]["usage"]["reserved_tokens"] == 0
    assert "current_sample" not in report
    assert "current_sample" not in json.loads(opts.report.read_text(encoding="utf-8"))
    assert report["paid_requests"] == report["cases"][0]["paid_requests"] == 1


@pytest.mark.asyncio
async def test_interrupted_current_sample_preserves_request_and_usage_lower_bound(dataset, tmp_path):
    opts = options(dataset, tmp_path, modes=("single",), repeat=1, max_cases=1)
    async def interrupted_runner(records, policy, finding, **kwargs):
        from app.model_gateway import RunBudget
        budget = RunBudget()
        await kwargs["gateway"].chat([{"role": "user", "content": "json"}], tools=None, budget=budget,
                                     on_attempt=kwargs["on_step"])
        raise asyncio.CancelledError
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: mock_response(NORMAL_PROPOSAL))) as client:
        with pytest.raises(asyncio.CancelledError):
            await run_benchmark(opts, gateway=DeepSeekGateway("fixture", client=client), runner=interrupted_runner)
    persisted = json.loads(opts.report.read_text(encoding="utf-8"))
    assert persisted["status"] == "running"
    assert persisted["cases"] == []
    assert persisted["groups"] == {}
    assert persisted["paid_requests"] == persisted["current_sample"]["paid_requests"] == 1
    assert persisted["current_sample"]["current_event"] == "model_usage"
    assert persisted["current_sample"]["usage"]["tokens"] == 12
    assert "interrupted_current_sample_is_incomplete" in persisted["paid_request_accounting"]


@pytest.mark.asyncio
async def test_provider_access_failure_stops_future_model_dispatch_and_keeps_rows(dataset, tmp_path):
    attempts = []
    opts = options(dataset, tmp_path, modes=("rules", "single", "multi", "active"), repeat=1, max_cases=2)

    def handler(request):
        attempts.append(request)
        return httpx.Response(402, text="private provider body")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await run_benchmark(opts, gateway=DeepSeekGateway("private-key", client=client))
    assert len(attempts) == report["paid_requests"] == 1
    assert len(report["cases"]) == 8
    model_rows = [row for row in report["cases"] if row["method"] != "rules"]
    assert model_rows[0]["error"]["http_status"] == 402
    assert sum(row["error"]["code"] == "provider_unavailable_not_attempted" for row in model_rows) == 5
    assert all(not row["exact_rule_match"] and not row["status_match"] for row in model_rows)
    assert report["groups"]["rules"]["completed"] == 2
    assert "private" not in json.dumps(report)


@pytest.mark.asyncio
async def test_logical_parameters_and_two_actual_retry_attempts_have_correlatable_indexes():
    from app.model_gateway import RunBudget

    attempts, events = [], []
    tools = [{"type": "function", "function": {"name": "query_journal_lines", "parameters": {"type": "object"}}}]
    def handler(request):
        attempts.append(json.loads(request.content))
        return httpx.Response(429) if len(attempts) == 1 else mock_response(NORMAL_PROPOSAL)
    paid, budget = PaidRequestBudget(2), RunBudget()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        wrapped = BoundedGateway(DeepSeekGateway("fixture-key", client=client, retry_delay=0), paid, "fixture",
                                 context=lambda: {"role": "evidence", "stage": "role_initial"})
        await wrapped.chat([{"role": "user", "content": "fixture json"}], tools=tools, budget=budget,
                           on_attempt=events.append, thinking=True, max_tokens=512, reasoning_effort="low")
    assert len(attempts) == paid.used == 2
    assert len(wrapped.request_parameters) == 1
    assert [row["request_index"] for row in wrapped.request_attempts] == [1, 2]
    assert [row["retry_attempt"] for row in wrapped.request_attempts] == [1, 2]
    assert {row["logical_chat_index"] for row in wrapped.request_attempts} == {1}
    assert all(row["role"] == "evidence" and row["stage"] == "role_initial" for row in wrapped.request_attempts)
    assert all(row["tools_enabled"] and row["tool_names"] == ["query_journal_lines"]
               for row in wrapped.request_attempts)
    assert next(event for event in events if event["event"] == "model_usage")["request_index"] == 2
    assert wrapped.request_not_dispatched == []
    assert "fixture-key" not in json.dumps([wrapped.request_parameters, wrapped.request_attempts])


@pytest.mark.asyncio
async def test_zero_global_budget_rejection_rolls_back_known_unsent_local_reservation():
    from app.model_gateway import BeforeDispatchRejected, RunBudget

    attempts, events = [], []
    paid, budget = PaidRequestBudget(0), RunBudget()
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: attempts.append(request) or mock_response(NORMAL_PROPOSAL))) as client:
        wrapped = BoundedGateway(DeepSeekGateway("fixture-key", client=client), paid, "fixture")
        with pytest.raises(BeforeDispatchRejected):
            await wrapped.chat([], tools=None, budget=budget, on_attempt=events.append)
    assert attempts == [] and paid.used == 0
    assert budget.calls == 0 and budget.tokens == 0 and budget.reserved_tokens == 0
    assert wrapped.request_attempts == []
    assert len(wrapped.request_parameters) == 1  # A logical plan, never claimed to be a dispatch.
    assert wrapped.request_not_dispatched[0]["status"] == "not_dispatched"
    assert events[-1]["event"] == "model_not_dispatched"
    assert events[-1]["usage"]["reserved_tokens"] == 0


@pytest.mark.asyncio
async def test_retry_refusal_rolls_back_only_current_reservation_keeps_previous_unknown_fee():
    from app.model_gateway import BeforeDispatchRejected, RunBudget

    attempts, events = [], []
    paid, budget = PaidRequestBudget(1), RunBudget()
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: attempts.append(request) or httpx.Response(429))) as client:
        wrapped = BoundedGateway(DeepSeekGateway("fixture-key", client=client, retry_delay=0), paid, "fixture")
        with pytest.raises(BeforeDispatchRejected):
            await wrapped.chat([], tools=None, budget=budget, on_attempt=events.append)
    assert len(attempts) == paid.used == budget.calls == 1
    first_reservation = next(event for event in events if event["event"] == "model_attempt")["usage"]["reserved_tokens"]
    assert budget.reserved_tokens == first_reservation > 0
    assert budget.tokens == 0
    assert len(wrapped.request_attempts) == 1
    assert wrapped.request_attempts[0]["request_index"] == 1
    assert wrapped.request_not_dispatched[0]["request_index"] == 2
    assert wrapped.request_not_dispatched[0]["retry_attempt"] == 2


@pytest.mark.asyncio
async def test_interrupted_non_thinking_stage_keeps_versions_parameters_and_actual_usage(dataset, tmp_path):
    from app.investigation import MODEL_PARAMETERS, PROMPT_VERSION, TOOL_VERSION
    from app.model_gateway import RunBudget

    opts = options(dataset, tmp_path, modes=("single",), repeat=1, max_cases=1)
    seen_start = []
    async def interrupted_runner(records, policy, finding, **kwargs):
        started = json.loads(opts.report.read_text(encoding="utf-8"))["current_sample"]
        seen_start.append(started)
        await kwargs["on_step"]({"event": "agent_started", "role": "single"})
        await kwargs["on_step"]({"event": "stage_handoff", "role": "single", "thinking": False,
                                  "tools": False, "reason": "role_tool_limit"})
        await kwargs["gateway"].chat([{"role": "user", "content": "PRIVATE_MESSAGE_SENTINEL"}],
                                      tools=None, budget=RunBudget(), on_attempt=kwargs["on_step"],
                                      thinking=False, max_tokens=512)
        raise asyncio.CancelledError
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: mock_response(NORMAL_PROPOSAL))) as client:
        with pytest.raises(asyncio.CancelledError):
            await run_benchmark(opts, gateway=DeepSeekGateway("SECRET_KEY_SENTINEL", client=client),
                                runner=interrupted_runner)
    persisted = json.loads(opts.report.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == "agent-benchmark-v5"
    assert seen_start[0]["prompt_version"] == PROMPT_VERSION
    assert seen_start[0]["tool_version"] == TOOL_VERSION
    assert seen_start[0]["model_parameters"] == MODEL_PARAMETERS
    sample = persisted["current_sample"]
    assert sample["prompt_version"] == PROMPT_VERSION and sample["tool_version"] == TOOL_VERSION
    assert sample["model_parameters"]["conversation_state"] == "role_selected_queries_with_complete_manifest"
    assert sample["model_parameters"]["role_tool_batch_limit"] == 2
    assert sample["current_role"] == "single" and sample["current_stage"] == "tool_stage_finalization"
    assert sample["request_parameters"][0]["thinking"] is False
    assert sample["request_parameters"][0]["tools_enabled"] is False
    assert sample["request_attempts"][0]["role"] == "single"
    assert sample["request_attempts"][0]["stage"] == "tool_stage_finalization"
    assert sample["request_attempts"][0]["request_index"] == sample["model_requests"][0]["request_index"] == 1
    assert sample["model_requests"][0]["request_usage"]["total_tokens"] == 12
    assert sample["usage"]["reserved_tokens"] == 0
    assert persisted["cases"] == [] and persisted["groups"] == {}
    assert "PRIVATE_MESSAGE_SENTINEL" not in json.dumps(persisted)
    assert "SECRET_KEY_SENTINEL" not in json.dumps(persisted)


@pytest.mark.asyncio
async def test_interrupted_plan_validation_failure_keeps_only_safe_diagnostic_fields(dataset, tmp_path):
    opts = options(dataset, tmp_path, modes=('active',), repeat=1, max_cases=1)

    async def interrupted_runner(records, policy, finding, **kwargs):
        await kwargs['on_step']({'event': 'agent_started', 'role': 'evidence'})
        await kwargs['on_step']({'event': 'output_validation_failed', 'diagnostics': [{
            'code': 'invalid_tool_arguments', 'path': ['evidence_plan', 'queries', 0, 'arguments', 'limit'],
            'argument_error': 'greater_than_equal', 'input': 'PRIVATE_INPUT', 'msg': 'PRIVATE_MESSAGE',
        }]})
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_benchmark(opts, gateway=object(), runner=interrupted_runner)
    raw = opts.report.read_text(encoding='utf-8')
    failures = json.loads(raw)['current_sample']['validation_failures']
    assert failures == [{'role': 'evidence', 'stage': 'role_initial', 'request_count': 0, 'diagnostics': [{
        'code': 'invalid_tool_arguments', 'path': ['evidence_plan', 'queries', 0, 'arguments', 'limit'],
        'argument_error': 'greater_than_equal',
    }]}]
    assert 'PRIVATE' not in raw


@pytest.mark.asyncio
async def test_existing_report_refuses_rerun_before_any_dispatch_or_overwrite(dataset, tmp_path):
    opts = options(dataset, tmp_path, modes=("single",), repeat=1, max_cases=1)
    opts.report.write_bytes(b"HISTORIC_PAID_RUN_SENTINEL")
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: calls.append(request) or mock_response(NORMAL_PROPOSAL))) as client:
        with pytest.raises(ValueError, match="已存在"):
            await run_benchmark(opts, gateway=DeepSeekGateway("fixture", client=client))
    assert calls == []
    assert opts.report.read_bytes() == b"HISTORIC_PAID_RUN_SENTINEL"


@pytest.mark.asyncio
async def test_existing_csv_also_refuses_overwriting_historical_export(dataset, tmp_path):
    opts = options(dataset, tmp_path, modes=("rules",), repeat=1, max_cases=1)
    opts.report.with_suffix(".csv").write_bytes(b"HISTORIC_EXPORT_SENTINEL")
    with pytest.raises(ValueError, match="已存在"):
        await run_benchmark(opts)
    assert not opts.report.exists()
    assert opts.report.with_suffix(".csv").read_bytes() == b"HISTORIC_EXPORT_SENTINEL"


@pytest.mark.asyncio
async def test_unscorable_paid_sample_cost_remains_in_group_and_framework_totals(dataset, tmp_path):
    entry = select_cases(json.loads((dataset / "manifest.json").read_text()), "development", 1)[0]
    (dataset / "truth" / f"{entry['case_id']}.json").unlink()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: mock_response(NORMAL_PROPOSAL))) as client:
        report = await run_benchmark(options(dataset, tmp_path, modes=("single",), repeat=1, max_cases=1),
                                     gateway=DeepSeekGateway("fixture", client=client))
    group = report["groups"]["single"]
    assert group["samples"] == 0 and group["unscorable"] == 1
    assert group["cost_scope"] == "all_attempted_samples" and group["cost_sample_rows"] == 1
    assert report["paid_requests"] == group["paid_requests"] == 1
    assert group["tokens"] == 12
    assert group["request_usage_totals"]["fields"]["total_tokens"] == {"reported_responses": 1, "sum": 12}
    assert group["by_framework"]["CAS"]["paid_requests"] == 1
    assert group["by_family"][report["cases"][0]["family_id"]]["tokens"] == 12
    assert group["exact_rule_match_rate"] is None
    assert report["cases"][0]["error"]["code"] == "truth_unavailable"


def test_progress_report_retries_a_briefly_locked_windows_replacement(tmp_path, monkeypatch):
    """v23 validation: a concurrent reader made os.replace raise PermissionError."""
    import pathlib
    import benchmarks.run_agents as module
    original, calls = pathlib.Path.replace, []

    def flaky(self, target):
        calls.append(target)
        if len(calls) <= 2:
            raise PermissionError('reader holds the file')
        return original(self, target)

    monkeypatch.setattr(pathlib.Path, 'replace', flaky)
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    path = tmp_path / 'report.json'
    module._atomic_json(path, {'paid_requests': 3})
    assert json.loads(path.read_text(encoding='utf-8')) == {'paid_requests': 3} and len(calls) == 3
    monkeypatch.setattr(pathlib.Path, 'replace', lambda self, target: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(PermissionError):  # a persistent lock is still reported, never hidden
        module._atomic_json(path, {'paid_requests': 4})
    assert json.loads(path.read_text(encoding='utf-8')) == {'paid_requests': 3}
