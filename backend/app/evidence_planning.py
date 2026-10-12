"""Compile untrusted model evidence plans into server-controlled selections.

Models propose explanations, queries and unverified expected outcomes. Only the
server supplies costs, availability and the current snapshot's executed cache.
The compiler normalizes arguments against executable whitelisted tool schemas
and uses the same canonical query hash as EvidenceMemory. It executes no query,
reads no source/evaluation files and does not obtain future or hidden evidence.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from .evidence_selection import (
    EvidenceQuery, ExpectedOutcome, Identifier, SelectionDecision, SelectionRequest, StrictModel, SUPPORTED_QUERY_TOOLS,
    ToolBinding, select_evidence_query,
)


Availability = Literal["available", "unavailable", "unknown"]
PLAN_VERSION = "evidence-planning-v1"
PUBLIC_NOTICE = (
    "这是当前快照的受控查询选择，不是已取得的新证据。假设与预期标签为模型建议，"
    "不是概率或会计事实。成本、可用性及重复查询判定由服务端决定；查询尚未执行。"
)


class PlannedHypothesis(StrictModel):
    hypothesis_id: Identifier
    explanation: str = Field(min_length=1, max_length=1200)


class ProposedEvidenceQuery(StrictModel):
    query_id: Identifier
    tool: str = Field(min_length=1, max_length=100, json_schema_extra={"enum": sorted(SUPPORTED_QUERY_TOOLS)})
    arguments: dict[str, Any]
    predictions: dict[Identifier, ExpectedOutcome] = Field(default_factory=dict, max_length=10)


class EvidencePlan(StrictModel):
    hypotheses: list[PlannedHypothesis] = Field(default_factory=list, max_length=10)
    queries: list[ProposedEvidenceQuery] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid_identifiers(self):
        hypotheses = [hypothesis.hypothesis_id for hypothesis in self.hypotheses]
        if len(hypotheses) != len(set(hypotheses)):
            raise ValueError("Evidence-plan hypothesis IDs must be unique")
        queries = [query.query_id for query in self.queries]
        if len(queries) != len(set(queries)):
            raise ValueError("Evidence-plan proposed query IDs must be unique")
        if any(not set(query.predictions) <= set(hypotheses) for query in self.queries):
            raise ValueError("Evidence-plan predictions reference unknown hypotheses")
        return self


@dataclass(frozen=True)
class PlanningToolBinding:
    """Trusted server binding; execute is present but never called here.

    Availability may be a constant or an IO-free predicate of the strictly
    validated Pydantic arguments and captured current-snapshot metadata. Costs
    are positive server scheduling units, not model claims or provider fees.
    """

    parameters: type[BaseModel]
    execute: Callable[[BaseModel], Any]
    cost_units: int
    availability: Availability | Callable[[BaseModel], Availability] = "unknown"


class PlanningRejected(ValueError):
    """Stable diagnostic code without echoing document/model argument values."""

    def __init__(self, code: str, diagnostics: list[dict] | None = None):
        super().__init__(f"Evidence planning rejected: {code}")
        self.code = code
        self.diagnostics = diagnostics or []


class PlannedSelection(StrictModel):
    decision: SelectionDecision
    query_aliases: dict[str, str]
    public_brief: dict[str, Any]
    plan_version: str = PLAN_VERSION


def query_cache_key(tool: str, canonical_arguments: dict) -> str:
    """Use only AFTER strict schema normalization; matches EvidenceMemory."""
    try:
        payload = json.dumps([tool, canonical_arguments], sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise PlanningRejected("non_json_tool_arguments") from None
    return hashlib.sha256(payload.encode()).hexdigest()


def _bindings(tools: Mapping[str, PlanningToolBinding]) -> dict[str, ToolBinding]:
    if not isinstance(tools, Mapping):
        raise PlanningRejected("executable_whitelist_required")
    bindings = {}
    for name, binding in tools.items():
        if not isinstance(binding, PlanningToolBinding):
            raise PlanningRejected("invalid_server_binding")
        if type(binding.cost_units) is not int or not 1 <= binding.cost_units <= 1_000_000_000:
            raise PlanningRejected("invalid_server_cost")
        if not callable(binding.availability) and (not isinstance(binding.availability, str) or
                                                  binding.availability not in {"available", "unavailable", "unknown"}):
            raise PlanningRejected("invalid_server_availability")
        bindings[name] = ToolBinding(binding.parameters, binding.execute)
    try:
        # The existing kernel is the authority for the tool-name and strict
        # schema whitelist. Even unused registrations must meet its contract.
        select_evidence_query({"hypothesis_ids": [], "queries": []}, tools=bindings)
    except ValueError:
        raise PlanningRejected("invalid_executable_whitelist") from None
    return bindings


def plan_evidence_query(
    plan: EvidencePlan | dict, *, tools: Mapping[str, PlanningToolBinding],
    executed_query_keys: Collection[str] = (),
) -> PlannedSelection:
    """Prepare one executable selection; the caller owns actual execution.

    executed_query_keys must come from this run's snapshot-local EvidenceMemory,
    never model input or another snapshot. Normalized hash IDs defeat reusing a
    cached query under a fresh model-generated query ID. Root may execute the
    selected tool/arguments, then publish its program output separately.
    """
    bindings = _bindings(tools)
    try:
        raw = plan.model_dump() if isinstance(plan, EvidencePlan) else plan
        plan = EvidencePlan.model_validate(raw, strict=True)
    except ValidationError:
        raise PlanningRejected("invalid_model_plan") from None
    if isinstance(executed_query_keys, (str, bytes)) or not isinstance(executed_query_keys, Collection):
        raise PlanningRejected("invalid_server_cache")
    executed = set()
    for key in executed_query_keys:
        if not isinstance(key, str) or len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
            raise PlanningRejected("invalid_server_cache")
        executed.add(key)
    if len(executed) > 200:
        raise PlanningRejected("server_cache_limit")
    aliases = {}
    queries = []
    for index, proposed in enumerate(plan.queries):
        binding = tools.get(proposed.tool)
        if binding is None:
            raise PlanningRejected("tool_not_whitelisted")
        try:
            arguments = binding.parameters.model_validate(proposed.arguments, strict=True)
        except ValidationError as exc:
            schema = binding.parameters.model_json_schema()
            fields = set(schema.get('properties', {}))
            for definition in schema.get('$defs', {}).values():
                fields.update(definition.get('properties', {}))
            diagnostics = []
            for error in exc.errors(include_input=False, include_context=False, include_url=False)[:20]:
                location = []
                for part in error['loc']:
                    if location and location[-1] in {'calculation', 'predictions', 'arguments'}:
                        break  # dynamic keys may contain documentary or private text
                    location.append(part if type(part) is int or part in fields else 'unknown_field')
                diagnostics.append({'code': error['type'], 'path': ['queries', index, 'arguments', *location]})
            raise PlanningRejected("invalid_tool_arguments", diagnostics) from None
        canonical = arguments.model_dump()
        key = query_cache_key(proposed.tool, canonical)
        if key in aliases:
            raise PlanningRejected("duplicate_normalized_query")
        aliases[key] = proposed.query_id
        try:
            availability = binding.availability(arguments) if callable(binding.availability) else binding.availability
        except Exception:
            # Availability predicates are trusted, but their internal errors or
            # data must not be copied into model/public diagnostic messages.
            raise PlanningRejected("server_availability_failed") from None
        if not isinstance(availability, str) or availability not in {"available", "unavailable", "unknown"}:
            raise PlanningRejected("invalid_server_availability")
        queries.append(EvidenceQuery(query_id=key, tool=proposed.tool, arguments=canonical,
                                     predictions=proposed.predictions, cost_units=binding.cost_units,
                                     availability=availability))
    request = SelectionRequest(hypothesis_ids=[item.hypothesis_id for item in plan.hypotheses],
                               queries=queries, executed_query_ids=sorted(executed))
    decision = select_evidence_query(request, tools=bindings)
    selection = decision.model_dump(mode="json")
    # No server callbacks, tool reprs, unselected argument values, private cache
    # contents, evaluator labels, or tool results are included in this brief.
    selected = selection.pop("selected_query")
    candidates = selection.pop("candidates")
    brief = {"schema_version": PLAN_VERSION, "hypotheses": [item.model_dump() for item in plan.hypotheses],
             "selection": selection, "selected_query": selected,
             "candidates": [{key: item[key] for key in (
                 "query_id", "tool", "cost_units", "availability", "pair_count", "eligible", "exclusion_reason",
                 "partition", "unknown_hypothesis_ids", "unpredicted_hypothesis_ids")}
                 | {"proposal_query_id": aliases[item["query_id"]]} for item in candidates],
             "notice": PUBLIC_NOTICE}
    return PlannedSelection(decision=decision, query_aliases=aliases, public_brief=brief)
