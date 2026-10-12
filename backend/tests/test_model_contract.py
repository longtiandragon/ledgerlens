"""Offline contract and handoff regressions; mocks do not measure model accuracy."""
import json
from copy import deepcopy

import httpx
import pytest

from app.context_transport import deserialize_context
from app.investigation import investigate
from app.model_contract import compact_schema
from app.model_gateway import DeepSeekGateway


def test_compact_schema_preserves_business_names_and_all_constraint_values():
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:review-contract",
        "title": "Object annotation",
        "description": "Annotation, not a business field",
        "default": {},
        "examples": [{}],
        "type": "object",
        "required": ["title", "default", "description"],
        "additionalProperties": False,
        "minProperties": 3,
        "maxProperties": 5,
        "properties": {
            "title": {
                "title": "Title annotation", "description": "Field annotation", "default": "ab",
                "type": "string", "minLength": 2, "maxLength": 9, "pattern": "^[a-z]+$",
            },
            "default": {
                "type": "number", "minimum": 0, "exclusiveMaximum": 10, "multipleOf": 0.5,
            },
            "description": {
                "const": {"title": "literal", "default": {"description": "literal"}},
            },
            "literal_enum": {
                "enum": [{"title": "payload", "description": "payload", "default": 2}],
            },
            "records": {
                "type": "array", "items": {"$ref": "#/$defs/title"},
                "prefixItems": [{"title": "Item annotation", "const": "first"}],
                "minItems": 1, "maxItems": 3, "uniqueItems": True,
                "contains": {"title": "Contains annotation", "type": "integer", "minimum": 1},
                "minContains": 1, "maxContains": 2,
            },
        },
        "$defs": {
            "title": {"title": "Definition annotation", "type": "integer", "maximum": 20},
            "default": {"type": "object", "additionalProperties": {
                "description": "Value annotation", "type": "string", "maxLength": 7,
            }},
        },
        "definitions": {"description": {"description": "Legacy annotation", "const": "legacy"}},
        "patternProperties": {"^description$": {"title": "Pattern annotation", "type": "string"}},
        "dependentSchemas": {"title": {"description": "Dependency annotation", "required": ["default"]}},
        "dependentRequired": {"title": ["default", "description"]},
        "propertyNames": {"description": "Name annotation", "pattern": "^[a-z_]+$"},
        "allOf": [{"title": "All annotation", "required": ["title"]}],
        "anyOf": [{"description": "Any annotation", "required": ["default"]}, {"const": {}}],
        "oneOf": [{"default": {}, "type": "object"}, {"type": "null"}],
        "not": {"description": "Not annotation", "required": ["forbidden"]},
        "if": {"title": "If annotation", "required": ["literal_enum"]},
        "then": {"description": "Then annotation", "required": ["records"]},
        "else": {"title": "Else annotation", "maxProperties": 4},
        "unevaluatedProperties": False,
        "x-business-metadata": {"title": "literal extension", "default": {"description": "literal"}},
    }
    original = deepcopy(schema)
    expected = deepcopy(schema)
    # Exact expected contract: remove only these annotation locations. Every
    # other byte of schema data, including literal enum/const objects, remains.
    for name in ("title", "description", "default", "examples"):
        del expected[name]
    for name in ("title", "description", "default"):
        del expected["properties"]["title"][name]
    del expected["properties"]["records"]["prefixItems"][0]["title"]
    del expected["properties"]["records"]["contains"]["title"]
    del expected["$defs"]["title"]["title"]
    del expected["$defs"]["default"]["additionalProperties"]["description"]
    del expected["definitions"]["description"]["description"]
    del expected["patternProperties"]["^description$"]["title"]
    del expected["dependentSchemas"]["title"]["description"]
    del expected["propertyNames"]["description"]
    del expected["allOf"][0]["title"]
    del expected["anyOf"][0]["description"]
    del expected["oneOf"][0]["default"]
    del expected["not"]["description"]
    del expected["if"]["title"]
    del expected["then"]["description"]
    del expected["else"]["title"]

    result = compact_schema(schema)
    assert result == expected
    assert schema == original
    assert set(result["properties"]) >= {"title", "default", "description"}
    assert result["properties"]["description"]["const"] == original["properties"]["description"]["const"]
    assert result["properties"]["literal_enum"]["enum"] == original["properties"]["literal_enum"]["enum"]

    # Neither known-schema children nor uninterpreted literal values alias the
    # original. A caller can edit its prompt contract without altering validators.
    result["properties"]["title"]["minLength"] = 100
    result["required"].append("new")
    result["properties"]["description"]["const"]["default"]["description"] = "changed"
    assert schema == original


def test_compact_schema_keeps_boolean_schema_and_future_constraints():
    schema = {
        "type": "array",
        "items": False,
        "prefixItems": [True, False, {"$dynamicRef": "#node", "minLength": 1}],
        "unevaluatedItems": False,
        "x-future-constraint": {"description": "opaque constraint value", "bounds": [1, 2]},
    }
    assert compact_schema(schema) == schema
    assert compact_schema({"type": ["string", "null"], "$ref": "#/$defs/default"}) == {
        "type": ["string", "null"], "$ref": "#/$defs/default",
    }


def _review_snapshot():
    policy = {
        "framework": "CAS", "version": "fixture-v1", "period": "2026-09", "book_id": "book-a",
        "accounts": ["1403", "2202"], "snapshot_cutoff": "2026-09-30T23:59:59Z",
    }
    common = {
        "currency": "CNY", "book_id": "book-a", "business_date": "2026-09-01", "period": "2026-09",
        "observed_at": "2026-09-02T00:00:00Z", "kind": "journal", "voucher_id": "JV1",
    }
    records = [
        {**common, "id": "row-1", "evidence_id": "ev-1", "account": "1403", "debit": "100.00", "credit": "0.00"},
        {**common, "id": "row-2", "evidence_id": "ev-2", "account": "2202", "debit": "0.00", "credit": "90.00"},
    ]
    finding = {
        "task_type": "review", "record_ids": ["row-1", "row-2"], "evidence_ids": ["ev-1", "ev-2"],
        "explanation": "Review the visible fixture records without a presumed anomaly.",
    }
    return records, policy, finding


def _claim(text, assessment):
    return {
        "text": text, "evidence_ids": ["ev-1", "ev-2"], "rule_id": "VOUCHER_BALANCE",
        "assessment": assessment, "calculation": {},
    }


def _proposal(claims=None, *, status="normal", missing=None):
    return {"status": status, "claims": claims or [], "hypotheses": [], "missing_evidence": missing or []}


def _response(proposal):
    return httpx.Response(200, json={
        "model": "offline-mock", "usage": {"total_tokens": 50},
        "choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": json.dumps(proposal),
        }}],
    })


def _tool_response(calls):
    return httpx.Response(200, json={
        "model": "offline-mock", "usage": {"total_tokens": 50},
        "choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "tool_calls": [
                {"id": f"tool-{index}", "type": "function", "function": {
                    "name": name, "arguments": json.dumps(arguments),
                }} for index, (name, arguments) in enumerate(calls)
            ],
        }}],
    })


@pytest.mark.asyncio
async def test_final_synthesis_receives_ledger_finding_after_empty_specialist_opinions():
    snapshot = _review_snapshot()
    original = deepcopy(snapshot)
    briefs = []

    def handler(request):
        brief = deserialize_context(json.loads(request.content)["messages"][1]["content"])
        briefs.append(brief)
        role_index = len(brief["prior_proposal_index"])
        if role_index == 0:
            if not brief.get("current_role_tool_turns"):
                return _tool_response([("query_journal_lines", {})])
            return _response(_proposal([_claim("Only the ledger role observed this difference", "finding")]))
        if role_index < 4:
            return _response(_proposal())
        if not brief.get("current_role_tool_turns"):
            return _tool_response([("read_evidence_queries", {"query_ids": [
                q["query_id"] for q in brief["shared_evidence"]["query_manifest"]]})])
        # The mock merely echoes data made available to synthesis. This tests
        # lossless handoff, not a real model's ability to reconcile opinions.
        return _response(_proposal(deepcopy(brief["prior_proposals"][0]["claims"])))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*snapshot, mode="multi", gateway=DeepSeekGateway("offline-test", client=client))

    assert result["error"] is None
    assert result["usage"]["calls"] == 7
    assert [p["role"] for p in result["proposals"]] == ["ledger", "evidence", "policy", "critic", "report"]
    starts = [b for b in briefs if not b.get("current_role_tool_turns")]
    assert [len(b["prior_proposals"]) for b in starts] == [0, 1, 1, 1, 4]
    assert [len(b["prior_proposal_index"]) for b in starts] == [0, 1, 2, 3, 4]
    final_input = briefs[-1]["prior_proposals"]
    assert final_input[0]["claims"][0]["text"] == "Only the ledger role observed this difference"
    assert all(p["claims"] == [] for p in final_input[1:])
    assert all("role" not in p for p in final_input)
    assert all("origin" not in c and "verification" not in c for p in final_input for c in p["claims"])
    assert result["proposals"][-1]["claims"][0]["assessment"] == "finding"
    assert result["fact_status"] == "insufficient"
    assert result["claims"] == []
    assert result["proposals"][-1]["claims"][0]["verification"]["semantic_status"] == "unverified_model_proposal"
    assert snapshot == original


@pytest.mark.asyncio
async def test_synthesis_retains_conflicts_negative_checks_and_unretrieved_evidence_without_fact_upgrade():
    snapshot = _review_snapshot()
    original = deepcopy(snapshot)
    briefs = []
    pending = "The later receipt is still required"

    def handler(request):
        brief = deserialize_context(json.loads(request.content)["messages"][1]["content"])
        briefs.append(brief)
        role_index = len(brief["prior_proposal_index"])
        if role_index == 0 and not brief.get("current_role_tool_turns"):
            wrong_calculation = _claim("A deliberately wrong fixture calculation", "finding")
            wrong_calculation["calculation"] = {"difference": "999.00"}
            return _tool_response([
                ("verify_claims", {"claims": [wrong_calculation]}),
                ("request_evidence", {"description": "Later receipt", "reason": pending}),
                ("retrieve_policy", {"rule_id": "VOUCHER_BALANCE"}),
                ("query_journal_lines", {}),
            ])
        if role_index == 0:
            return _response(_proposal([_claim("Ledger proposes a difference", "finding")],
                                       status="insufficient", missing=[pending]))
        if role_index == 1:
            if not brief.get("current_role_tool_turns"):
                return _tool_response([("read_evidence_queries", {"query_ids": [
                    q["query_id"] for q in brief["shared_evidence"]["query_manifest"]
                    if q["tool"] == "query_journal_lines"]})])
            return _response(_proposal([_claim("Evidence role proposes a normal explanation", "normal")]))
        if role_index < 4:
            return _response(_proposal())
        if not brief.get("current_role_tool_turns"):
            return _tool_response([("read_evidence_queries", {"query_ids": [
                q["query_id"] for q in brief["shared_evidence"]["query_manifest"]
                if q["tool"] == "query_journal_lines"]})])
        distinct = [deepcopy(c) for p in brief["prior_proposals"] for c in p["claims"]]
        # Deliberately optimistic model status must still not become a fact.
        return _response(_proposal(distinct, status="supported", missing=[pending]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*snapshot, mode="multi", gateway=DeepSeekGateway("offline-test", client=client))

    assert result["error"] is None
    assert result["usage"]["calls"] == 8 and result["usage"]["tools"] == 6
    final_input = briefs[-1]
    opinions = final_input["prior_proposals"]
    assert len(opinions) == 4
    assert opinions[0]["claims"][0]["assessment"] == "finding"
    assert opinions[1]["claims"][0]["assessment"] == "normal"
    assert opinions[0]["missing_evidence"] == [pending]
    observations = {o["tool"]: o["result"] for o in final_input["shared_evidence"]["program_observations"]}
    assert observations["verify_claims"]["claims"][0]["calculations_verified"] is False
    assert observations["request_evidence"]["status"] == "requested"
    assert observations["request_evidence"]["retrieved"] is False
    assert observations["request_evidence"]["requires_new_snapshot"] is True
    policy_result = next(q["metadata"] for q in final_input["shared_evidence"]["query_manifest"]
                         if q["tool"] == "retrieve_policy")
    assert policy_result["references_requested"] is False
    assert policy_result["matched_reference_count"] is None
    assert final_input["shared_evidence"]["reference_passages"] == []
    assert result["program_observations"] == final_input["shared_evidence"]["program_observations"]
    assert result["proposals"][-1]["status"] == "supported"
    assert result["fact_status"] == result["status"] == "insufficient"
    assert result["claims"] == [] and pending in result["missing_evidence"]
    assert {c["assessment"] for c in result["proposals"][-1]["claims"]} == {"finding", "normal"}
    assert all(c["origin"] == "model_proposal" and c["verification"]["semantic_status"] == "unverified_model_proposal"
               for c in result["proposed_claims"])
    assert snapshot == original


@pytest.mark.asyncio
async def test_active_final_role_receives_all_previous_independent_opinions():
    briefs = []

    def handler(request):
        brief = deserialize_context(json.loads(request.content)["messages"][1]["content"])
        briefs.append(brief)
        role_index = len(brief["prior_proposal_index"])
        if role_index == 0:
            return _response(_proposal(missing=["Unresolved coordinator question"]))
        if role_index == 1:
            return _response(_proposal([_claim("Evidence role opinion", "insufficient")]))
        return _response(_proposal())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await investigate(*_review_snapshot(), mode="active", gateway=DeepSeekGateway("offline-test", client=client))

    assert result["error"] is None and result["usage"]["calls"] == 3
    assert [p["role"] for p in result["proposals"]] == ["coordinator", "evidence", "critic"]
    assert len(briefs[-1]["prior_proposals"]) == 2
    assert briefs[-1]["prior_proposals"][0]["missing_evidence"] == ["Unresolved coordinator question"]
    assert briefs[-1]["prior_proposals"][1]["claims"][0]["text"] == "Evidence role opinion"
    assert result["claims"] == [] and result["fact_status"] == "insufficient"
