"""Untrusted planning contracts, server authority, and hand-counted partitions."""
import json
from copy import deepcopy

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.evidence_planning import (EvidencePlan, PlanningRejected, PlanningToolBinding,
                                   plan_evidence_query, query_cache_key)


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reference: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=50, ge=1, le=100)


def no_execution(_):
    raise AssertionError("Planning must not execute a query")


def predictions(labels):
    return {hypothesis: {"status": "predicted", "label": label} if label is not None else {"status": "unknown"}
            for hypothesis, label in labels.items()}


def proposed(query_id, labels, *, reference="order-1", tool="get_business_chain", arguments=None):
    return {"query_id": query_id, "tool": tool,
            "arguments": {"reference": reference} if arguments is None else arguments,
            "predictions": predictions(labels)}


def plan(queries, hypothesis_ids=("h1", "h2", "h3", "h4")):
    return {"hypotheses": [{"hypothesis_id": identifier, "explanation": f"候选解释{identifier}"}
                           for identifier in hypothesis_ids], "queries": queries}


@pytest.fixture
def tools():
    return {"get_business_chain": PlanningToolBinding(Args, no_execution, cost_units=1,
                                                      availability=lambda args: "available" if args.reference else "unavailable")}


def test_hand_counted_balanced_partition_and_server_costs(tools):
    payload = plan([
        proposed("balanced", {"h1": "A", "h2": "A", "h3": "B", "h4": "B"}, reference="balanced-order"),
        proposed("unbalanced", {"h1": "A", "h2": "B", "h3": "B", "h4": "B"}, reference="unbalanced-order"),
        proposed("same", {"h1": "A", "h2": "A", "h3": "A", "h4": "A"}, reference="same-order"),
    ])
    original = deepcopy(payload)
    compiled = plan_evidence_query(payload, tools=tools)
    selected = compiled.decision.selected_query
    assert selected.pair_count == 4  # independently hand-counted: 2 * 2
    assert selected.distinguishable_pairs == [("h1", "h3"), ("h1", "h4"), ("h2", "h3"), ("h2", "h4")]
    assert selected.cost_units == 1
    assert compiled.query_aliases[selected.query_id] == "balanced"
    assert selected.arguments == {"reference": "balanced-order", "limit": 50}
    assert compiled.decision.execution_performed is False
    assert payload == original


def test_argument_diagnostics_locate_query_and_hide_unknown_names_and_values(tools):
    payload = plan([proposed('bad', {}, arguments={'limit': 'PRIVATE_VALUE', 'PRIVATE_FIELD': 'secret'})])
    with pytest.raises(PlanningRejected) as failure:
        plan_evidence_query(payload, tools=tools)
    assert failure.value.code == 'invalid_tool_arguments'
    assert failure.value.diagnostics == [
        {'code': 'int_type', 'path': ['queries', 0, 'arguments', 'limit']},
        {'code': 'extra_forbidden', 'path': ['queries', 0, 'arguments', 'unknown_field']},
    ]
    assert 'PRIVATE' not in json.dumps(failure.value.diagnostics)


def test_nested_argument_diagnostics_stop_at_dynamic_calculation_keys():
    from app.investigation import VerifyQuery
    tools = {'verify_claims': PlanningToolBinding(VerifyQuery, no_execution, 1, 'available')}
    payload = plan([proposed('bad', {}, tool='verify_claims', arguments={'claims': [
        {'text': 'Document text', 'evidence_ids': ['ev-1'], 'rule_id': 'VOUCHER_BALANCE',
         'calculation': {'PRIVATE_FIELD': {'PRIVATE_VALUE': 1}}},
    ]})])
    with pytest.raises(PlanningRejected) as failure:
        plan_evidence_query(payload, tools=tools)
    assert failure.value.diagnostics == [
        {'code': 'string_type', 'path': ['queries', 0, 'arguments', 'claims', 0, 'calculation']},
    ]
    assert 'PRIVATE' not in json.dumps(failure.value.diagnostics)


def test_cost_and_availability_are_server_owned_not_model_fields(tools):
    payload = plan([proposed("q", {"h1": "A", "h2": "B"})])
    for field, value in (("cost_units", 0), ("availability", "available"), ("execute", "arbitrary_code")):
        malicious = deepcopy(payload)
        malicious["queries"][0][field] = value
        with pytest.raises(PlanningRejected) as rejected:
            plan_evidence_query(malicious, tools=tools)
        assert rejected.value.code == "invalid_model_plan"
        assert str(value) not in str(rejected.value)
    altered_server = {"get_business_chain": PlanningToolBinding(Args, no_execution, 7, "available")}
    assert plan_evidence_query(payload, tools=altered_server).decision.selected_query.cost_units == 7


def test_server_availability_rejects_missing_business_identifier(tools):
    candidate = proposed("missing-id", {"h1": "A", "h2": "B"}, arguments={})
    compiled = plan_evidence_query(plan([candidate]), tools=tools)
    assert compiled.decision.status == "insufficient"
    assert compiled.decision.action == "request_evidence"
    assert compiled.decision.candidates[0].availability == "unavailable"
    assert compiled.decision.candidates[0].exclusion_reason == "not_available"


def test_server_cost_changes_selection_by_integer_ratio():
    tools = {"get_business_chain": PlanningToolBinding(Args, no_execution, 5, "available"),
             "query_journal_lines": PlanningToolBinding(Args, no_execution, 1, "available")}
    compiled = plan_evidence_query(plan([
        proposed("expensive-four", {"h1": "A", "h2": "A", "h3": "B", "h4": "B"}),
        proposed("cheap-three", {"h1": "A", "h2": "B", "h3": "B", "h4": "B"}, tool="query_journal_lines"),
    ]), tools=tools)
    assert compiled.query_aliases[compiled.decision.selected_query.query_id] == "cheap-three"
    assert compiled.decision.selected_query.pair_count == 3


def test_query_ids_are_hashes_of_normalized_parameters_not_model_aliases(tools):
    missing_default = plan([proposed("model-name-a", {"h1": "A", "h2": "B"})])
    explicit_default = plan([proposed("model-name-b", {"h1": "A", "h2": "B"},
                                      arguments={"reference": "order-1", "limit": 50})])
    first = plan_evidence_query(missing_default, tools=tools)
    second = plan_evidence_query(explicit_default, tools=tools)
    key = query_cache_key("get_business_chain", {"reference": "order-1", "limit": 50})
    assert first.decision.selected_query.query_id == second.decision.selected_query.query_id == key
    assert first.query_aliases[key] == "model-name-a"
    assert second.query_aliases[key] == "model-name-b"
    cached = plan_evidence_query(explicit_default, tools=tools, executed_query_keys=[key])
    assert cached.decision.selected_query is None
    assert cached.decision.action == "request_evidence"
    assert cached.decision.candidates[0].exclusion_reason == "already_executed"


def test_hash_matches_actual_evidence_memory_cache():
    from app.investigation import BusinessQuery, EvidenceMemory, ToolContext
    from app.model_gateway import RunBudget
    context = ToolContext([], {"framework": "CAS", "period": "2026-09"},
                          {"record_ids": [], "evidence_ids": []}, RunBudget())
    memory = EvidenceMemory()
    # This test executes an empty in-memory snapshot query, not an API call.
    memory.execute(context, "get_business_chain", {"reference": "same-source"})
    bindings = {"get_business_chain": PlanningToolBinding(BusinessQuery, no_execution, 1, "available")}
    compiled = plan_evidence_query(plan([proposed("new-model-id", {"h1": "A", "h2": "B"}, reference="same-source")]),
                                    tools=bindings, executed_query_keys=memory.queries.keys())
    assert list(compiled.query_aliases) == list(memory.queries)
    assert compiled.decision.candidates[0].exclusion_reason == "already_executed"
    assert compiled.decision.status == "insufficient"


def test_duplicate_normalized_queries_are_rejected_even_under_new_ids(tools):
    payload = plan([proposed("alias-a", {"h1": "A", "h2": "B"}),
                    proposed("alias-b", {"h1": "A", "h2": "B"}, arguments={"reference": "order-1", "limit": 50})])
    with pytest.raises(PlanningRejected) as rejected:
        plan_evidence_query(payload, tools=tools)
    assert rejected.value.code == "duplicate_normalized_query"


def test_unknown_and_omitted_predictions_cannot_invent_information_gain(tools):
    compiled = plan_evidence_query(plan([proposed("partial", {"h1": "A", "h2": "B", "h3": None})]), tools=tools)
    selected = compiled.decision.selected_query
    assert selected.pair_count == 1
    assert selected.unknown_hypothesis_ids == ["h3"]
    assert selected.unpredicted_hypothesis_ids == ["h4"]
    same = plan_evidence_query(plan([proposed("same", {"h1": "A", "h2": "A", "h3": None})]), tools=tools)
    assert same.decision.status == "insufficient"


def test_safe_public_brief_has_no_callbacks_unselected_parameters_or_cache_payload(tools):
    payload = plan([proposed("selected", {"h1": "A", "h2": "B"}, reference="visible-reference"),
                    proposed("unselected", {"h1": "A", "h2": "A"}, reference="do-not-publish-unselected")])
    compiled = plan_evidence_query(payload, tools=tools)
    brief = json.dumps(compiled.public_brief, ensure_ascii=False)
    assert "visible-reference" in brief
    assert "do-not-publish-unselected" not in brief
    assert "no_execution" not in brief
    assert "function" not in brief
    assert "cost_units" in brief
    assert "假设与预期标签为模型建议" in brief
    assert compiled.public_brief["selection"]["execution_performed"] is False


@pytest.mark.parametrize("arguments", [
    {"reference": "order-1", "sql": "SELECT hidden_truth"},
    {"reference": "order-1", "code": "execute_arbitrary_code"},
    {"reference": "order-1", "shell": "powershell arbitrary_command"},
    {"reference": "order-1", "book_id": "another-book"},
    {"reference": "order-1", "snapshot_id": "future-snapshot"},
    {"reference": "order-1", "limit": "100"},
    {"reference": "order-1", "limit": 101},
])
def test_tool_arguments_cannot_select_other_scopes_or_arbitrary_execution(tools, arguments):
    with pytest.raises(PlanningRejected) as rejected:
        plan_evidence_query(plan([proposed("bad-args", {"h1": "A", "h2": "B"}, arguments=arguments)]), tools=tools)
    assert rejected.value.code == "invalid_tool_arguments"
    assert "hidden_truth" not in str(rejected.value)
    assert "future-snapshot" not in str(rejected.value)


@pytest.mark.parametrize("cost", [0, -1, True, "1", 1.5])
def test_server_costs_are_checked_even_without_queries(cost):
    with pytest.raises(PlanningRejected) as rejected:
        plan_evidence_query(plan([]), tools={"get_business_chain": PlanningToolBinding(Args, no_execution, cost, "available")})
    assert rejected.value.code == "invalid_server_cost"


@pytest.mark.parametrize("keys", ["a" * 64, ["not-a-cache-hash"], [123], ["A" * 64], list(range(201))])
def test_cache_keys_must_be_server_hashes_not_model_query_names(tools, keys):
    with pytest.raises(PlanningRejected) as rejected:
        plan_evidence_query(plan([]), tools=tools, executed_query_keys=keys)
    assert rejected.value.code == "invalid_server_cache"


def test_arbitrary_tool_name_and_non_executable_registry_are_rejected(tools):
    with pytest.raises(PlanningRejected) as rejected:
        plan_evidence_query(plan([proposed("bad-tool", {"h1": "A", "h2": "B"}, tool="execute_sql")]), tools=tools)
    assert rejected.value.code == "tool_not_whitelisted"
    with pytest.raises(PlanningRejected) as rejected:
        plan_evidence_query(plan([]), tools={"get_business_chain": PlanningToolBinding(Args, None, 1, "available")})
    assert rejected.value.code == "invalid_executable_whitelist"


def test_size_duplicate_and_foreign_prediction_identifiers_are_rejected(tools):
    bad_plans = [plan([], [f"h{i}" for i in range(11)]),
                 plan([proposed(f"q{i}", {}, reference=f"order-{i}") for i in range(21)]),
                 plan([], ["h1", "h1"]), plan([proposed("same-id", {}), proposed("same-id", {}, reference="different")]),
                 plan([proposed("wrong-hypothesis", {"outside-plan": "A"})])]
    for payload in bad_plans:
        with pytest.raises(PlanningRejected) as rejected:
            plan_evidence_query(payload, tools=tools)
        assert rejected.value.code == "invalid_model_plan"


def test_model_cannot_supply_scope_or_executed_cache_fields(tools):
    for field in ("book_id", "snapshot_id", "hidden_truth", "executed_query_keys"):
        payload = plan([])
        payload[field] = "untrusted-model-value"
        with pytest.raises(PlanningRejected) as rejected:
            plan_evidence_query(payload, tools=tools)
        assert rejected.value.code == "invalid_model_plan"


def test_server_availability_callback_failure_is_safe_and_not_successful():
    def failure(_):
        raise RuntimeError("private-source-records")
    bindings = {"get_business_chain": PlanningToolBinding(Args, no_execution, 1, failure)}
    with pytest.raises(PlanningRejected) as rejected:
        plan_evidence_query(plan([proposed("q", {"h1": "A", "h2": "B"})]), tools=bindings)
    assert rejected.value.code == "server_availability_failed"
    assert "private-source-records" not in str(rejected.value)


def test_model_objects_are_revalidated_after_nested_mutation(tools):
    payload = EvidencePlan.model_validate(plan([proposed("q", {"h1": "A", "h2": "B"})]))
    payload.queries[0].arguments["future_snapshot"] = "not-current"
    with pytest.raises(PlanningRejected) as rejected:
        plan_evidence_query(payload, tools=tools)
    assert rejected.value.code == "invalid_tool_arguments"
