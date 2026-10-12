"""Pure, deterministic selection of a useful snapshot-scoped evidence query.

This module does not execute tools, read files, call a model, or fetch evidence.
The caller supplies an executable whitelist bound to its current book/snapshot,
strict argument schemas, integer cost units, and unverified outcome predictions.
Predictions are partitions, not calibrated probabilities or accounting facts.
Graph integration is caller-owned; selecting a query alone acquires no evidence.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from itertools import combinations
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


SUPPORTED_QUERY_TOOLS = frozenset({
    "query_journal_lines", "get_business_chain", "match_transactions", "retrieve_policy", "verify_claims", "read_policy_checks",
})
NOTICE = (
    "预期结果仅为待验证假设，不是经校准的概率或已确认事实；成本单位由调用者提供，"
    "不等于实际模型费用。选择器不执行查询；执行者必须绑定当前账套、快照与规则版本。"
)
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")]
OutcomeLabel = Annotated[str, Field(min_length=1, max_length=200)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)


class ExpectedOutcome(StrictModel):
    status: Literal["predicted", "unknown"]
    label: OutcomeLabel | None = None

    @field_validator("label")
    @classmethod
    def meaningful_label(cls, value):
        if value is None:
            return value
        value = value.strip()
        if not value or value.casefold() in {"unknown", "unpredicted", "未知", "未预测"}:
            raise ValueError("Unknown or unpredicted outcomes must use status=unknown and label=null")
        return value

    @model_validator(mode="after")
    def consistent_status(self):
        if (self.status == "predicted") != (self.label is not None):
            raise ValueError("Only predicted outcomes have a nonempty label")
        return self


class EvidenceQuery(StrictModel):
    query_id: Identifier
    tool: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any]
    # Actual scheduling units must be supplied by the caller; never default to
    # free, coerce fractional values, or infer costs from a model's probability.
    cost_units: int = Field(gt=0, le=1_000_000_000)
    availability: Literal["available", "unavailable", "unknown"] = "unknown"
    predictions: dict[Identifier, ExpectedOutcome] = Field(default_factory=dict, max_length=10)


class SelectionRequest(StrictModel):
    hypothesis_ids: list[Identifier] = Field(max_length=10)
    queries: list[EvidenceQuery] = Field(max_length=20)
    executed_query_ids: list[Identifier] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def unique_ids_and_known_predictions(self):
        hypotheses = set(self.hypothesis_ids)
        if len(hypotheses) != len(self.hypothesis_ids):
            raise ValueError("Hypothesis IDs must be unique")
        query_ids = [query.query_id for query in self.queries]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("Query IDs must be unique")
        if len(set(self.executed_query_ids)) != len(self.executed_query_ids):
            raise ValueError("Executed query IDs must be unique")
        if any(not set(query.predictions) <= hypotheses for query in self.queries):
            raise ValueError("A prediction references an unknown hypothesis")
        return self


@dataclass(frozen=True)
class ToolBinding:
    """Caller-owned, executable, snapshot-bound tool plus its strict schema.

    The selector verifies callable existence but never invokes execute. A bound
    method or closure may capture the book/snapshot; the caller enforces scope.
    """

    parameters: type[BaseModel]
    execute: Callable[[BaseModel], Any]


class QueryAnalysis(StrictModel):
    query_id: Identifier
    tool: str
    arguments: dict[str, Any]
    cost_units: int
    availability: Literal["available", "unavailable", "unknown"]
    partition: dict[str, list[str]]
    unknown_hypothesis_ids: list[str]
    unpredicted_hypothesis_ids: list[str]
    distinguishable_pairs: list[tuple[str, str]]
    pair_count: int
    eligible: bool
    exclusion_reason: str | None


class SelectionDecision(StrictModel):
    action: Literal["execute_query", "request_evidence"]
    status: Literal["selected", "insufficient"]
    selected_query: QueryAnalysis | None
    candidates: list[QueryAnalysis]
    reason_code: str
    reason: str
    evidence_request: dict[str, str] | None = None
    notice: str = NOTICE
    execution_performed: bool = False
    prediction_semantics: str = "unverified_expectations_not_calibrated_probabilities_or_facts"


def _validate_registry(registry: Mapping[str, ToolBinding]) -> None:
    if not isinstance(registry, Mapping):
        raise ValueError("An explicit executable tool whitelist is required")
    for name, binding in registry.items():
        if name not in SUPPORTED_QUERY_TOOLS:
            raise ValueError("Tool whitelist contains an unsupported query tool")
        if not isinstance(binding, ToolBinding) or not callable(binding.execute):
            raise ValueError("Whitelisted tools must have an executable binding")
        schema = binding.parameters
        if not isinstance(schema, type) or not issubclass(schema, BaseModel):
            raise ValueError("Whitelisted tools require a Pydantic argument schema")
        if schema.model_config.get("extra") != "forbid" or schema.model_config.get("strict") is not True:
            raise ValueError("Tool argument schemas must forbid extras and enforce strict types")


def _analyze(query: EvidenceQuery, hypotheses: list[str], executed: set[str], binding: ToolBinding) -> QueryAnalysis:
    try:
        # Validation happens even for unavailable/executed queries, rather than
        # accepting unsafe arguments merely because they currently lose ranking.
        arguments = binding.parameters.model_validate(query.arguments, strict=True).model_dump()
    except ValidationError:
        # Never echo arbitrary injected parameter contents in diagnostics.
        raise ValueError("Query arguments rejected by its whitelisted tool schema") from None
    partition: dict[str, list[str]] = {}
    unknown, unpredicted = [], []
    labels = {}
    for hypothesis_id in sorted(hypotheses):
        prediction = query.predictions.get(hypothesis_id)
        if prediction is None:
            unpredicted.append(hypothesis_id)
        elif prediction.status == "unknown":
            unknown.append(hypothesis_id)
        else:
            labels[hypothesis_id] = prediction.label
            partition.setdefault(prediction.label, []).append(hypothesis_id)
    # A pair is distinguishable only when BOTH outcomes were explicitly
    # predicted and their labels differ. Unknown is never a synthetic bucket.
    pairs = [(left, right) for left, right in combinations(sorted(labels), 2) if labels[left] != labels[right]]
    excluded = "already_executed" if query.query_id in executed else (
        "not_available" if query.availability != "available" else "no_distinguishable_pairs" if not pairs else None)
    return QueryAnalysis(query_id=query.query_id, tool=query.tool, arguments=arguments,
                         cost_units=query.cost_units, availability=query.availability,
                         partition={label: partition[label] for label in sorted(partition)},
                         unknown_hypothesis_ids=unknown, unpredicted_hypothesis_ids=unpredicted,
                         distinguishable_pairs=pairs, pair_count=len(pairs), eligible=excluded is None,
                         exclusion_reason=excluded)


def select_evidence_query(
    request: SelectionRequest | dict, *, tools: Mapping[str, ToolBinding],
) -> SelectionDecision:
    """Maximize known distinguishable pairs / integer cost without floats.

    Exact-score ties prefer lower cost, then lexicographically smaller query ID.
    Input order, hypothesis order, or label naming cannot change that rule.
    No hidden truth, later evidence, model response, or tool result is accessed.
    """
    _validate_registry(tools)
    # Revalidate the serialization, because nested dictionaries of even a
    # frozen Pydantic object can still have been mutated by its caller.
    payload = request.model_dump() if isinstance(request, SelectionRequest) else request
    request = SelectionRequest.model_validate(payload, strict=True)
    if any(query.tool not in tools for query in request.queries):
        raise ValueError("Query tool is absent from the executable whitelist")
    executed = set(request.executed_query_ids)
    candidates = [_analyze(query, request.hypothesis_ids, executed, tools[query.tool])
                  for query in sorted(request.queries, key=lambda query: query.query_id)]
    selected = None
    for candidate in candidates:
        if not candidate.eligible:
            continue
        if selected is None:
            selected = candidate
            continue
        left = candidate.pair_count * selected.cost_units
        right = selected.pair_count * candidate.cost_units
        if left > right or (left == right and (candidate.cost_units, candidate.query_id)
                            < (selected.cost_units, selected.query_id)):
            selected = candidate
    if selected is None:
        reason = "当前没有可执行且能区分至少一对候选解释的查询；需要补充资料或可验证的查询预期。"
        return SelectionDecision(action="request_evidence", status="insufficient", selected_query=None,
                                 candidates=candidates, reason_code="no_available_distinguishing_query", reason=reason,
                                 evidence_request={"description": "请补充能区分候选解释的资料或具体查询预期", "reason": reason})
    return SelectionDecision(action="execute_query", status="selected", selected_query=selected, candidates=candidates,
                             reason_code="maximum_known_pairs_per_cost",
                             reason=f"此查询预期区分{selected.pair_count}对解释，成本{selected.cost_units}单位；"
                                    "按整数分数比较优先级，同分依次优先较低成本与较小查询ID。")
