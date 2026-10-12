"""Independent hand-counted partitions; no production calculator or LLM oracle."""
from copy import deepcopy

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.evidence_selection import SelectionRequest, ToolBinding, select_evidence_query


class QueryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reference: str = Field(min_length=1, max_length=100)
    limit: int = Field(default=20, ge=1, le=100)


def must_not_execute(_):
    raise AssertionError("The deterministic selector must never execute a tool")


@pytest.fixture
def registry():
    return {"get_business_chain": ToolBinding(QueryArguments, must_not_execute)}


def query(query_id, labels, *, cost=1, availability="available", arguments=None):
    return {"query_id": query_id, "tool": "get_business_chain", "arguments": arguments or {"reference": "order-1"},
            "cost_units": cost, "availability": availability,
            "predictions": {hypothesis: {"status": "predicted", "label": label} if label is not None
                            else {"status": "unknown"} for hypothesis, label in labels.items()}}


def request(queries, hypotheses=("h1", "h2", "h3", "h4"), executed=()):
    return {"hypothesis_ids": list(hypotheses), "queries": queries, "executed_query_ids": list(executed)}


def test_balanced_partition_beats_unbalanced_same_unknown_and_missing(registry):
    # With four hypotheses, 2x2 gives four cross-group pairs. 1x3 gives
    # three. Same labels, unknown-only, and missing-only each give zero.
    queries = [query("balanced", {"h1": "A", "h2": "A", "h3": "B", "h4": "B"}),
               query("unbalanced", {"h1": "A", "h2": "B", "h3": "B", "h4": "B"}),
               query("same", {hypothesis: "A" for hypothesis in ("h1", "h2", "h3", "h4")}),
               query("unknown", {hypothesis: None for hypothesis in ("h1", "h2", "h3", "h4")}),
               query("missing", {})]
    result = select_evidence_query(request(queries), tools=registry)
    assert result.status == "selected"
    assert result.action == "execute_query"
    assert result.selected_query.query_id == "balanced"
    assert result.selected_query.partition == {"A": ["h1", "h2"], "B": ["h3", "h4"]}
    assert result.selected_query.distinguishable_pairs == [("h1", "h3"), ("h1", "h4"), ("h2", "h3"), ("h2", "h4")]
    counts = {item.query_id: item.pair_count for item in result.candidates}
    assert counts == {"balanced": 4, "missing": 0, "same": 0, "unbalanced": 3, "unknown": 0}
    assert result.selected_query.cost_units == 1
    assert "4对" in result.reason
    assert result.execution_performed is False


def test_unknown_and_unpredicted_do_not_create_distinguishing_buckets(registry):
    result = select_evidence_query(request([query("partial", {"h1": "A", "h2": "B", "h3": None})]), tools=registry)
    selected = result.selected_query
    # Only h1-h2 counts. h3 is unknown; h4 had no prediction at all.
    assert selected.pair_count == 1
    assert selected.distinguishable_pairs == [("h1", "h2")]
    assert selected.unknown_hypothesis_ids == ["h3"]
    assert selected.unpredicted_hypothesis_ids == ["h4"]
    assert selected.partition == {"A": ["h1"], "B": ["h2"]}
    no_pair = select_evidence_query(request([query("partial", {"h1": "A", "h2": "A", "h3": None})]), tools=registry)
    assert no_pair.action == "request_evidence"
    assert no_pair.status == "insufficient"


def test_cost_ratio_uses_hand_calculated_integer_scores(registry):
    # 4/5 versus 3/1: the three-pair query is preferable despite fewer pairs.
    result = select_evidence_query(request([
        query("balanced-expensive", {"h1": "A", "h2": "A", "h3": "B", "h4": "B"}, cost=5),
        query("unbalanced-cheap", {"h1": "A", "h2": "B", "h3": "B", "h4": "B"}, cost=1),
    ]), tools=registry)
    assert result.selected_query.query_id == "unbalanced-cheap"
    assert result.selected_query.pair_count == 3
    assert result.selected_query.cost_units == 1


def test_exact_ratio_ties_prefer_lower_cost_then_stable_query_id(registry):
    # 3/3 equals 1/1 exactly; lower scheduling cost wins. The two 1/1
    # queries tie again and must be selected by ID, irrespective of order.
    queries = [query("full", {"h1": "A", "h2": "B", "h3": "B", "h4": "B"}, cost=3),
               query("z-partial", {"h1": "A", "h2": "B"}, cost=1),
               query("a-partial", {"h3": "C", "h4": "D"}, cost=1)]
    forward = select_evidence_query(request(queries), tools=registry)
    reversed_result = select_evidence_query(request(list(reversed(queries)), tuple(reversed(("h1", "h2", "h3", "h4")))), tools=registry)
    assert forward.selected_query.query_id == "a-partial"
    assert forward.model_dump() == reversed_result.model_dump()


def test_executed_and_unavailable_queries_are_filtered_even_when_they_score_more(registry):
    full = {"h1": "A", "h2": "B", "h3": "C", "h4": "D"}  # six pairs
    queries = [query("executed", full), query("unavailable", full, availability="unavailable"),
               query("unknown-availability", full, availability="unknown"), query("available", {"h1": "A", "h2": "B"})]
    result = select_evidence_query(request(queries, executed=("executed",)), tools=registry)
    assert result.selected_query.query_id == "available"
    exclusions = {item.query_id: item.exclusion_reason for item in result.candidates}
    assert exclusions["executed"] == "already_executed"
    assert exclusions["unavailable"] == exclusions["unknown-availability"] == "not_available"


@pytest.mark.parametrize("queries,hypotheses,executed", [
    ([], [], []), ([], ["h1"], []), ([query("one", {"h1": "A"})], ["h1"], []),
    ([query("unknown", {"h1": None, "h2": None})], ["h1", "h2"], []),
    ([query("done", {"h1": "A", "h2": "B"})], ["h1", "h2"], ["done"]),
])
def test_no_solution_requests_evidence_and_retains_insufficient(registry, queries, hypotheses, executed):
    result = select_evidence_query(request(queries, hypotheses, executed), tools=registry)
    assert result.status == "insufficient"
    assert result.action == "request_evidence"
    assert result.selected_query is None
    assert result.evidence_request["description"]
    assert result.reason_code == "no_available_distinguishing_query"
    assert "不是经校准的概率或已确认事实" in result.notice


@pytest.mark.parametrize("cost", [0, -1, True, 1.5, "1", 1_000_000_001])
def test_costs_must_be_positive_bounded_integers(registry, cost):
    with pytest.raises(ValidationError):
        select_evidence_query(request([query("invalid", {"h1": "A", "h2": "B"}, cost=cost)]), tools=registry)


@pytest.mark.parametrize("arguments", [
    {"reference": "order-1", "sql": "SELECT secret FROM hidden_truth"},
    {"reference": "order-1", "shell": "powershell arbitrary-command"},
    {"reference": "order-1", "code": "import os"},
    {"reference": "order-1", "limit": "100"},
    {"reference": "order-1", "limit": True},
    {"reference": "order-1", "limit": 101},
])
def test_malicious_or_unbounded_tool_arguments_are_rejected_without_execution(registry, arguments):
    with pytest.raises(ValueError, match="arguments rejected") as error:
        select_evidence_query(request([query("bad", {"h1": "A", "h2": "B"}, arguments=arguments,
                                                 availability="unavailable")]), tools=registry)
    assert "hidden_truth" not in str(error.value)
    assert "powershell" not in str(error.value)


def test_selector_requires_external_executable_whitelist(registry):
    payload = request([query("candidate", {"h1": "A", "h2": "B"})])
    with pytest.raises(TypeError):
        select_evidence_query(payload)
    with pytest.raises(ValueError, match="absent"):
        select_evidence_query(payload, tools={})
    with pytest.raises(ValueError, match="executable"):
        select_evidence_query(payload, tools={"get_business_chain": ToolBinding(QueryArguments, None)})
    dangerous = deepcopy(payload)
    dangerous["queries"][0]["tool"] = "execute_sql"
    with pytest.raises(ValueError, match="unsupported"):
        select_evidence_query(dangerous, tools={"execute_sql": ToolBinding(QueryArguments, must_not_execute)})


def test_non_strict_registry_schema_is_rejected():
    class UnsafeArguments(BaseModel):
        reference: str
    with pytest.raises(ValueError, match="forbid extras"):
        select_evidence_query(request([]), tools={"get_business_chain": ToolBinding(UnsafeArguments, must_not_execute)})


@pytest.mark.parametrize("prediction", [
    {"status": "predicted"}, {"status": "predicted", "label": "unknown"},
    {"status": "predicted", "label": "未预测"}, {"status": "predicted", "label": " "},
    {"status": "unknown", "label": "A"}, {"status": "predicted", "label": "A", "probability": 0.9},
])
def test_unknown_labels_and_probability_fields_cannot_create_partition_buckets(registry, prediction):
    candidate = query("invalid", {"h1": "A", "h2": "B"})
    candidate["predictions"]["h1"] = prediction
    with pytest.raises(ValidationError):
        select_evidence_query(request([candidate]), tools=registry)


def test_size_limits_duplicate_ids_and_unknown_hypotheses(registry):
    invalid = [request([], [f"h{i}" for i in range(11)]),
               request([query(f"q{i}", {}) for i in range(21)]),
               request([], ["h1", "h1"]),
               request([query("same-id", {}), query("same-id", {})]),
               request([query("wrong-hypothesis", {"not-known": "A"})]),
               request([], executed=["q1", "q1"])]
    for payload in invalid:
        with pytest.raises(ValidationError):
            select_evidence_query(payload, tools=registry)


def test_untrusted_extra_data_and_mutated_nested_arguments_are_not_accepted(registry):
    payload = request([query("valid", {"h1": "A", "h2": "B"})])
    payload["hidden_truth"] = {"h1": "real-answer"}
    with pytest.raises(ValidationError):
        select_evidence_query(payload, tools=registry)
    payload.pop("hidden_truth")
    parsed = SelectionRequest.model_validate(payload)
    parsed.queries[0].arguments["sql"] = "SELECT future_evidence"
    with pytest.raises(ValueError, match="arguments rejected"):
        select_evidence_query(parsed, tools=registry)


def test_safe_arguments_are_normalized_without_changing_input(registry):
    payload = request([query("valid", {"h1": "A", "h2": "B"})])
    original = deepcopy(payload)
    selected = select_evidence_query(payload, tools=registry).selected_query
    assert payload == original
    assert selected.arguments == {"reference": "order-1", "limit": 20}
