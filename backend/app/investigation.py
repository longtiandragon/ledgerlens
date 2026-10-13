"""Snapshot-scoped LangGraph investigation with deterministic fact boundaries.

Integration: supply an async PostgreSQL checkpointer in checkpoint_config as
{'checkpointer': saver, 'config': {'configurable': {'thread_id': case_version}},
 'resume': True, 'usage': persisted_usage}. Without a saver this is an explicit
non-resumable local/test run. Persist on_step usage before allowing a paid call.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Annotated, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .business_chain import business_chain
from .context_transport import FORMAT as CONTEXT_FORMAT, serialize_context
from .domain import check_records, rule_catalog, rule_identity, ensure_rule_compatibility
from .evidence_planning import EvidencePlan, PlanningRejected, PlanningToolBinding, plan_evidence_query, query_cache_key
from .evidence_selection import SUPPORTED_QUERY_TOOLS
from .model_gateway import BudgetExceeded, DeepSeekGateway, GatewayError, RunBudget, notify, strict_json
from .model_contract import PROTOCOL, ROOT_CAUSE_PROTOCOL, compact_schema

STATUSES = {"supported", "normal", "insufficient", "unsupported"}
MODES = {"rules", "single", "multi", "active"}
ABLATIONS = {"no_counterproof", "no_time_view", "no_program_verification", "no_active_selection"}
PROMPT_VERSION = "investigation-v26"
TOOL_VERSION = "snapshot-tools-v11"
MAX_PROGRAM_OBSERVATIONS = 60  # Same ceiling as the run-wide executed-tool budget.
MAX_ROLE_TOOL_BATCHES = 2
MODEL_PARAMETERS = {"thinking": True, "max_tokens": 8192, "reasoning_effort": "low", "temperature": None,
                    "truncation_recovery": {"maximum": 1, "thinking": False, "tools": False},
                    "active_coordinator": "planning_only", "prior_context": "specialist_latest_synthesis_all_readable_history",
                    "conversation_state": "role_selected_queries_with_complete_manifest",
                    "program_observation_context": "cumulative_only",
                    "role_tool_batch_limit": MAX_ROLE_TOOL_BATCHES,
                    "tool_stage_finalization": {"thinking": False, "tools": False},
                    "policy_reference_memory": "source_id_deduplicated",
                    "policy_reference_retrieval": "explicit_query",
                    "policy_reference_provenance": "frozen_source_binding_v2_runtime_gated",
                    "policy_check_context": "run_frozen_saved_checks_v1",
                    "rule_context": "lossless_definition_pool",
                    "context_encoding": CONTEXT_FORMAT,
                    "review_task_initialization": "neutral_without_presumed_anomaly",
                    "proposal_rule_ids": "current_framework_enum",
                    "schema_repair_conversation": "fresh_scoped_program_context",
                    "schema_encoding": "constraints_without_annotations",
                    "root_cause_output": "narrow_assessment_v1",
                    "role_outputs": "specialist_opinions_final_synthesis",
                    "evidence_retrieval": "snapshot_bound_cached_queries_max_6_per_read",
                    "plan_repair_context": "validated_tool_contracts_and_field_diagnostics",
                    "program_observations": {"maximum": MAX_PROGRAM_OBSERVATIONS, "replayed": True}}
PUBLIC_FIELDS = {
    "id", "evidence_id", "source_id", "kind", "business_date", "period", "observed_at",
    "currency", "counterparty", "reference", "order_id", "invoice_id", "voucher_id",
    "account", "debit", "credit", "amount", "description",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


EvidenceId = Annotated[str, Field(min_length=1, max_length=200)]
EvidenceNeed = Annotated[str, Field(min_length=1, max_length=1000)]


class Claim(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=50)
    rule_id: str = Field(max_length=100)
    calculation: dict[str, str] = Field(default_factory=dict)
    policy_source_ids: list[EvidenceId] = Field(default_factory=list, max_length=20)
    assessment: Literal["finding", "normal", "insufficient", "unsupported"] = Field(
        default="finding", description="finding: observed rule condition/deviation; normal: checked without deviation; "
        "insufficient: cannot check condition; unsupported: outside scope. Must distinguish normal checks from findings.")


class Hypothesis(StrictModel):
    explanation: str = Field(min_length=1, max_length=1200)
    status: Literal["supported", "refuted", "unresolved"] = "unresolved"
    evidence_ids: list[EvidenceId] = Field(default_factory=list, max_length=50)
    missing_evidence: list[EvidenceNeed] = Field(default_factory=list, max_length=10)


class PolicyInterpretation(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    policy_check_ids: list[EvidenceId] = Field(min_length=1, max_length=6)

    @field_validator('policy_check_ids')
    @classmethod
    def distinct_checks(cls, value):
        if len(value) != len(set(value)):
            raise ValueError('Policy check citations must be unique')
        return value


class EvidenceAsk(StrictModel):
    kind: Literal["order", "receipt", "invoice", "bank", "journal"]
    reference: str | None = Field(default=None, min_length=1, max_length=100)


class CauseAssessment(StrictModel):
    """Root-cause task only: categories from the task taxonomy and one optional request."""
    categories: list[str] = Field(default_factory=list, max_length=4)
    evidence_request: EvidenceAsk | None = None
    explanation: str = Field(default="", max_length=1200)
    evidence_ids: list[EvidenceId] = Field(default_factory=list, max_length=6)


class RootCauseAssessment(CauseAssessment):
    categories: list[str] = Field(max_length=4)
    explanation: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[EvidenceId] = Field(max_length=6)


class RootCauseProposal(StrictModel):
    """Model wire contract; normalized to the existing stored proposal envelope."""
    status: Literal["supported", "normal", "insufficient", "unsupported"]
    cause_assessment: RootCauseAssessment


class RootCausePlanProposal(RootCauseProposal):
    evidence_plan: EvidencePlan | None = None


class BaseProposal(StrictModel):
    status: Literal["supported", "normal", "insufficient", "unsupported"]
    claims: list[Claim] = Field(default_factory=list, max_length=20)
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=10)
    missing_evidence: list[EvidenceNeed] = Field(default_factory=list, max_length=20)
    policy_interpretations: list[PolicyInterpretation] = Field(default_factory=list, max_length=20)
    cause_assessment: CauseAssessment | None = None


class Proposal(BaseProposal):
    evidence_plan: EvidencePlan | None = None


class ProposalRejected(ValueError):
    def __init__(self, diagnostics: list[dict]):
        super().__init__("Model proposal rejected")
        self.diagnostics = diagnostics


def validate_proposal(content: str, context, *, allow_plan: bool = False,
                      read_evidence_ids: set[str] | None = None,
                      read_reference_ids: set[str] | None = None,
                      read_policy_check_ids: set[str] | None = None) -> BaseProposal:
    """Return safe field diagnostics without publishing rejected values."""
    try:
        raw = strict_json(content)
    except (ValueError, TypeError):
        raise ProposalRejected([{"code": "invalid_json", "path": []}]) from None
    try:
        model = ((RootCausePlanProposal if allow_plan else RootCauseProposal) if context.cause_taxonomy
                 else (Proposal if allow_plan else BaseProposal))
        proposal = model.model_validate(raw)
        if context.cause_taxonomy:
            proposal = (Proposal if allow_plan else BaseProposal).model_validate(proposal.model_dump())
    except ValidationError as exc:
        allowed_fields = {"status", "claims", "text", "evidence_ids", "rule_id", "calculation", "assessment",
                          "policy_source_ids", "hypotheses", "explanation", "missing_evidence", "evidence_plan",
                          "hypothesis_id", "queries", "query_id", "tool", "arguments", "predictions", "label",
                          "policy_interpretations", "policy_check_ids", "cause_assessment", "categories",
                          "evidence_request", "kind", "reference"}
        errors = []
        for error in exc.errors()[:20]:
            path = []
            for part in error["loc"]:
                if path and path[-1] in {"calculation", "arguments", "predictions"}:
                    break  # dynamic keys may contain untrusted document text
                path.append(part if type(part) is int or part in allowed_fields else "unknown_field")
            errors.append({"code": error["type"], "path": path})
        raise ProposalRejected(errors) from None
    errors = []
    for index, interpretation in enumerate(proposal.policy_interpretations):
        refs = set(interpretation.policy_check_ids)
        if not refs <= context.policy_checks.keys():
            errors.append({'code': 'policy_check_outside_run',
                           'path': ['policy_interpretations', index, 'policy_check_ids']})
        elif not refs <= (read_policy_check_ids or set()):
            # Unlike the pure core proposal helper, an interpretation always
            # requires a supplied record of this role's full program read.
            errors.append({'code': 'policy_check_content_not_read',
                           'path': ['policy_interpretations', index, 'policy_check_ids']})
    if proposal.cause_assessment is not None:
        assessment = proposal.cause_assessment
        if not context.cause_taxonomy:
            errors.append({'code': 'cause_assessment_outside_task', 'path': ['cause_assessment']})
        else:
            if (len(set(assessment.categories)) != len(assessment.categories)
                    or not set(assessment.categories) <= context.cause_taxonomy.keys()):
                errors.append({'code': 'unknown_cause_category', 'path': ['cause_assessment', 'categories']})
            ask = assessment.evidence_request
            if ask is not None and not context.evidence_request_allowed:
                errors.append({'code': 'evidence_request_not_allowed', 'path': ['cause_assessment', 'evidence_request']})
            elif ask is not None and ask.reference is not None and ask.reference not in context.visible_references:
                errors.append({'code': 'reference_not_visible',
                               'path': ['cause_assessment', 'evidence_request', 'reference']})
            citations = set(assessment.evidence_ids)
            path = ['cause_assessment', 'evidence_ids']
            if len(citations) != len(assessment.evidence_ids):
                errors.append({'code': 'duplicate_citation', 'path': path})
            if not citations <= context.evidence_ids:
                errors.append({'code': 'citation_outside_snapshot', 'path': path})
            elif ((proposal.status in {'supported', 'normal'} and not citations)
                  or not citations <= (read_evidence_ids or set())):
                errors.append({'code': 'evidence_content_not_read', 'path': path})
            if bool(assessment.categories) != (proposal.status in {'supported', 'normal'}):
                errors.append({'code': 'inconsistent_cause_conclusion', 'path': ['cause_assessment', 'categories']})
    for index, claim in enumerate(proposal.claims):
        if not set(claim.evidence_ids) <= context.evidence_ids:
            errors.append({"code": "citation_outside_snapshot", "path": ["claims", index, "evidence_ids"]})
        if claim.rule_id not in context.rule_ids:
            errors.append({"code": "unknown_rule", "path": ["claims", index, "rule_id"]})
        if not set(claim.policy_source_ids) <= context.reference_ids:
            errors.append({"code": "invalid_policy_citation", "path": ["claims", index, "policy_source_ids"]})
        if (read_evidence_ids is not None and claim.assessment in {"finding", "normal"}
                and set(claim.evidence_ids) <= context.evidence_ids
                and not set(claim.evidence_ids) <= read_evidence_ids):
            errors.append({"code": "evidence_content_not_read", "path": ["claims", index, "evidence_ids"]})
        if (read_reference_ids is not None and set(claim.policy_source_ids) <= context.reference_ids
                and not set(claim.policy_source_ids) <= read_reference_ids):
            errors.append({"code": "policy_content_not_read", "path": ["claims", index, "policy_source_ids"]})
    for index, hypothesis in enumerate(proposal.hypotheses):
        if not set(hypothesis.evidence_ids) <= context.evidence_ids:
            errors.append({"code": "citation_outside_snapshot", "path": ["hypotheses", index, "evidence_ids"]})
        if (read_evidence_ids is not None and hypothesis.status in {"supported", "refuted"}
                and set(hypothesis.evidence_ids) <= context.evidence_ids
                and (not hypothesis.evidence_ids or not set(hypothesis.evidence_ids) <= read_evidence_ids)):
            errors.append({"code": "evidence_content_not_read", "path": ["hypotheses", index, "evidence_ids"]})
    if allow_plan and proposal.evidence_plan is not None:
        try:
            plan_evidence_query(proposal.evidence_plan, tools=planning_tools(context, EvidenceMemory()))
        except PlanningRejected as exc:
            if exc.diagnostics:
                errors.extend({'code': exc.code, 'path': ['evidence_plan', *item['path']],
                               'argument_error': item['code']} for item in exc.diagnostics)
            else:
                errors.append({"code": exc.code, "path": ["evidence_plan"]})
    if errors:
        raise ProposalRejected(errors[:20])
    return proposal


def proposal_brief(value: dict) -> dict:
    """Strip server-added metadata so models do not copy it into strict output."""
    brief = {"status": value["status"], "claims": [
        {k: v for k, v in claim.items() if k in Claim.model_fields} for claim in value["claims"]],
        "hypotheses": value["hypotheses"], "missing_evidence": value["missing_evidence"]}
    brief['policy_interpretations'] = [
        {key: value for key, value in item.items() if key in PolicyInterpretation.model_fields}
        for item in value.get('policy_interpretations', [])]
    if value.get("evidence_plan") is not None:
        brief["evidence_plan"] = value["evidence_plan"]
    if value.get("cause_assessment") is not None:
        brief["cause_assessment"] = value["cause_assessment"]
    return brief


class JournalQuery(StrictModel):
    account: str | None = Field(default=None, max_length=100)
    voucher_id: str | None = Field(default=None, max_length=200)
    reference: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=50, ge=1, le=100)


class BusinessQuery(StrictModel):
    order_id: str | None = Field(default=None, max_length=200)
    invoice_id: str | None = Field(default=None, max_length=200)
    reference: str | None = Field(default=None, max_length=200)
    anchor_evidence_id: EvidenceId | None = None
    limit: int = Field(default=50, ge=1, le=100)
    max_hops: int = Field(default=2, ge=0, le=2)

    @field_validator("order_id", "invoice_id", "reference", "anchor_evidence_id")
    @classmethod
    def nonempty_identifier(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Business chain identifiers cannot be empty")
        return value


class MatchQuery(StrictModel):
    left_kind: Literal["invoice", "bank", "journal", "receipt", "order"] = "invoice"
    right_kind: Literal["invoice", "bank", "journal", "receipt", "order"] = "bank"
    flow: Literal["payable", "receivable"] = "payable"
    reference: str | None = Field(default=None, max_length=200)


class PolicyQuery(StrictModel):
    rule_id: str | None = Field(default=None, max_length=100)
    query: str = Field(default="", max_length=200,
                       description="显式填写业务关键词才检索政策全文；空值只读取执行规则，不表示没有政策文献。")

    @field_validator("query")
    @classmethod
    def trim_query(cls, value):
        return value.strip()


class ReferencePassage(StrictModel):
    source_id: EvidenceId
    text: str = Field(min_length=1, max_length=2000)
    url: str = Field(max_length=2000)
    framework: Literal["CAS", "SCAS", "BOTH"]
    title: str = Field(default="", max_length=300)
    effective_from: str | None = None
    effective_to: str | None = None
    status: str = Field(default="reference_only", max_length=100)
    source_binding: dict | None = None

    @field_validator("effective_from", "effective_to")
    @classmethod
    def valid_date(cls, value):
        if value is not None:
            date.fromisoformat(value)
        return value


class EvidenceRequest(StrictModel):
    description: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=1000)


class VerifyQuery(StrictModel):
    claims: list[Claim] = Field(max_length=20)


class PriorProposalQuery(StrictModel):
    index: int = Field(ge=0, le=23)


class CachedEvidenceQuery(StrictModel):
    query_ids: list[Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]] = Field(min_length=1, max_length=6)

    @field_validator("query_ids")
    @classmethod
    def unique_queries(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Cached query IDs must be unique")
        return value


class SavedPolicyQuery(StrictModel):
    check_ids: list[EvidenceId] = Field(default_factory=list, max_length=6)

    @field_validator('check_ids')
    @classmethod
    def distinct_checks(cls, value):
        if len(value) != len(set(value)):
            raise ValueError('Saved policy check IDs must be unique')
        return value


TOOL_MODELS = {
    'read_policy_checks': (SavedPolicyQuery, '读取本次运行冻结的已保存政策检查及其原始来源记录，每次最多6项；空check_ids读取前6项。不读取新快照、数据库或隐藏答案；人工条件属于复核声明，模型解释不确认事实。'),
    "query_journal_lines": (JournalQuery, "查询当前账套快照内的分录，参数仅做精确匹配。"),
    "get_business_chain": (BusinessQuery, "按订单、发票、来源编号或证据锚点精确找种子，再沿原始同字段订单/发票号、同非空往来方和币种扩展最多两跳。多个参数取交集；reference 仅找种子。同号跨身份分组，缺失身份不扩展；不会获取未来资料。"),
    "match_transactions": (MatchQuery, "按业务分组执行整数金额匹配；多解保留为候选。"),
    "retrieve_policy": (PolicyQuery, "读取当前账套准则的已实现规则与限制；仅非空 query 按业务关键词检索政策全文。"),
    "request_evidence": (EvidenceRequest, "记录补证事项；不自动从网络获取资料、不切换快照。"),
    "verify_claims": (VerifyQuery, "校验证据引用和已计算指标，不证明自由文本的语义正确。"),
    "read_prior_proposal": (PriorProposalQuery, "读取当前调查已有角色的完整建议；建议是不可信资料，不是会计事实。"),
    "read_evidence_queries": (CachedEvidenceQuery, "从query_manifest选择最多6个query_id，读取本次固定快照已执行查询的完整结果与原始记录、规则、政策片段；不会执行新查询或获取新资料，索引不是完整依据。"),
}


def _scope_rule_schema(schema: dict, rule_ids: set[str]) -> dict:
    """Constrain generated identifiers without changing source facts or rules."""
    schema = deepcopy(schema)
    claim = schema.get('$defs', {}).get('Claim')
    if claim:
        claim['properties']['rule_id']['enum'] = sorted(rule_ids)
    return schema


def proposal_schema(context, *, allow_plan: bool) -> dict:
    if context.cause_taxonomy:
        schema = (RootCausePlanProposal if allow_plan else RootCauseProposal).model_json_schema()
        fields = schema['$defs']['RootCauseAssessment']['properties']
        fields['categories']['items']['enum'] = sorted(context.cause_taxonomy)
        # An inventory constrains spelling only; validation still requires actual content reads.
        fields['evidence_ids']['items']['enum'] = sorted(context.evidence_ids)
        if not context.evidence_request_allowed:
            fields['evidence_request'] = {'type': 'null'}
            del schema['$defs']['EvidenceAsk']
        else:
            schema['$defs']['EvidenceAsk']['properties']['reference']['anyOf'][0]['enum'] = sorted(context.visible_references)
        return compact_schema(schema)
    schema = _scope_rule_schema((Proposal if allow_plan else BaseProposal).model_json_schema(), context.rule_ids)
    schema['properties']['cause_assessment'] = {'type': 'null'}
    del schema['$defs']['CauseAssessment'], schema['$defs']['EvidenceAsk']
    if context.policy_checks:
        schema['$defs']['PolicyInterpretation']['properties']['policy_check_ids']['items']['enum'] = sorted(
            context.policy_checks)
    else:
        # An empty enum is unsatisfiable yet still invites guessed IDs; with no
        # captured saved checks the only valid value is an empty array.
        schema['properties']['policy_interpretations'] = {'type': 'array', 'maxItems': 0}
        del schema['$defs']['PolicyInterpretation']
    return compact_schema(schema)


def tool_schemas(context=None) -> list[dict]:
    result = []
    for name, (cls, desc) in TOOL_MODELS.items():
        if name == 'read_policy_checks' and context is not None and not context.policy_checks:
            continue  # Nothing was captured; offering the tool only invites a false "missing" item.
        parameters = cls.model_json_schema()
        if context is not None:
            parameters = _scope_rule_schema(parameters, context.rule_ids)
            if name == 'retrieve_policy':
                parameters['properties']['rule_id']['anyOf'][0]['enum'] = sorted(context.rule_ids)
            if name == 'read_policy_checks':
                parameters['properties']['check_ids']['items']['enum'] = sorted(context.policy_checks)
        result.append({'type': 'function', 'function': {'name': name, 'description': desc,
                                                       'parameters': compact_schema(parameters)}})
    return result


def _stamp(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)


class ToolContext:
    def __init__(self, records: list[dict], policy: dict, finding: dict, budget: RunBudget,
                 *, ablation: str | None = None):
        self.records = deepcopy(records)
        self.policy = deepcopy(policy)
        self.finding = deepcopy(finding)
        self.budget = budget
        self.ablation = ablation
        if finding.get('task_type', 'anomaly') not in {'anomaly', 'review', 'root_cause'}:
            raise ValueError('Unknown investigation task type')
        task_metrics = finding.get('metrics') if isinstance(finding.get('metrics'), dict) else {}
        taxonomy = task_metrics.get('cause_taxonomy') if finding.get('task_type') == 'root_cause' else None
        if finding.get('task_type') == 'root_cause' and (
                not isinstance(taxonomy, dict) or not taxonomy
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in taxonomy.items())):
            raise ValueError('A root-cause task requires a nonempty visible cause taxonomy')
        self.cause_taxonomy: dict[str, str] = dict(taxonomy or {})
        self.evidence_request_allowed = bool(self.cause_taxonomy) and task_metrics.get('evidence_request_allowed') is True
        self.visible_references = {str(r[key]) for r in records for key in ('order_id', 'invoice_id', 'reference')
                                   if r.get(key)}
        self.requests: list[str] = []
        self.prior_proposals: list[dict] = []
        if policy.get("framework") not in {"CAS", "SCAS"}:
            raise ValueError("Unknown accounting framework")
        ids = [str(r["id"]) for r in records]
        if len(ids) != len(set(ids)):
            raise ValueError("Snapshot record IDs must be unique")
        books = {r["book_id"] for r in records if r.get("book_id")}
        if len(books) > 1 or (policy.get("book_id") and books and books != {policy["book_id"]}):
            raise ValueError("Cross-book context rejected")
        cutoff = policy.get("snapshot_cutoff")
        for row in records:
            if cutoff and (not row.get("observed_at") or _stamp(row["observed_at"]) > _stamp(cutoff)):
                raise ValueError("Future or undated evidence is outside the snapshot")
        self.evidence_ids = {str(r["evidence_id"]) for r in records if r.get("evidence_id")}
        # Round two may supply obtained documents directly in the initial brief.
        # Accept only complete, byte-for-value snapshot records, never bare IDs or altered copies.
        supplied = task_metrics.get('released_records', []) if self.cause_taxonomy else []
        if not isinstance(supplied, list) or any(row not in records for row in supplied):
            raise ValueError('Supplied root-cause documents must match complete snapshot records')
        self.supplied_cause_evidence_ids = {row['evidence_id'] for row in supplied}
        self.policy_check_context = None
        supplied_policy_context = policy.get('investigation_policy_context')
        if supplied_policy_context is not None:
            from .policy_context_contract import validate_policy_context
            if not all(policy.get(key) for key in ('book_id', 'snapshot_id', 'snapshot_digest')):
                raise ValueError('Saved policy checks require bound book, snapshot and snapshot digest identities')
            self.policy_check_context = validate_policy_context(
                supplied_policy_context, book_id=policy['book_id'],
                snapshot_id=policy.get('snapshot_id'), snapshot_digest=policy.get('snapshot_digest'),
                evidence_ids=self.evidence_ids)
        self.policy_checks = {item['id']: deepcopy(item) for item in
                              (self.policy_check_context or {}).get('checks', [])}
        for check in self.policy_checks.values():
            binding = check['binding']
            if (binding['framework'] != policy['framework'] or binding['period'] != policy.get('period')
                    or not cutoff or _stamp(binding['source_cutoff']) != _stamp(cutoff)):
                raise ValueError('Saved policy check framework, period or source cutoff differs from the ledger snapshot')
        self.policy_check_manifest = {
            'provided': self.policy_check_context is not None,
            'run_id': (self.policy_check_context or {}).get('run_id'),
            'snapshot_id': (self.policy_check_context or {}).get('snapshot_id'),
            'snapshot_digest': (self.policy_check_context or {}).get('snapshot_digest'),
            'captured_at': (self.policy_check_context or {}).get('captured_at'),
            'policy_rule_identity': (self.policy_check_context or {}).get('policy_rule_identity'),
            'check_count': len(self.policy_checks),
            'observation_count': len((self.policy_check_context or {}).get('observation_manifest', [])),
            'policy_check_index': [{'id': item['id'], 'created_at': item['created_at'],
                'observation_id': item['observation_id'], 'artifact_digest': item['artifact_digest']}
                for item in self.policy_checks.values()],
            'notice': ('Index only; local review annotations and source cutoff are separate. No check body has been supplied yet.'
                       if self.policy_checks else
                       'No saved policy checks were captured for this run. This is not missing evidence; do not request it.')}
        if not set(finding.get("record_ids", [])) <= set(ids):
            raise ValueError("Finding references records outside the snapshot")
        if not set(finding.get("evidence_ids", [])) <= self.evidence_ids:
            raise ValueError("Finding references evidence outside the snapshot")
        self.rules = [r for r in rule_catalog() if r["framework"] == policy["framework"]]
        self.rule_ids = {r["rule_id"] for r in self.rules}
        kinds = Counter(str(r.get("kind")) for r in records)
        # Program inventory of the fixed snapshot: which kinds exist and which
        # rules can run. Content still has to be read before it is cited.
        self.snapshot_scope = {
            "record_kind_counts": dict(sorted(kinds.items())),
            "rule_applicability": [{"rule_id": rule["rule_id"],
                                    "missing_kinds": sorted(k for k in rule["required_kinds"] if not kinds.get(k))}
                                   for rule in sorted(self.rules, key=lambda item: item["rule_id"])],
        }
        self.verified_finding: dict | None = None
        self.references = []
        if policy.get("reference_choice") is not None:
            from .reference_selection import verify_snapshot_references
            verify_snapshot_references(policy)
        for raw in policy.get("reference_passages", []):
            passage = ReferencePassage.model_validate(raw)
            if passage.source_binding is not None and policy.get("reference_choice") is None:
                from .knowledge import verify_frozen_references
                verify_frozen_references([raw])
            if passage.framework not in {policy["framework"], 'BOTH'}:
                continue
            # Corpus applicability is separate from executable rules. Dates are
            # evaluated at month level because a book currently has one period.
            period = policy.get("period", "")
            if passage.effective_from and period and period < passage.effective_from[:7]:
                continue
            if passage.effective_to and period and period > passage.effective_to[:7]:
                continue
            frozen = passage.model_dump()
            if passage.source_binding is None:
                frozen.pop('source_binding')  # Internal caller fixtures are not a runtime permission boundary.
            self.references.append(frozen)
        self.reference_ids = {r["source_id"] for r in self.references}
        self.memory_scope = hashlib.sha256(json.dumps(
            [self.records, self.policy, self.finding, self.ablation, rule_identity()],
            ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()

    def public_record(self, row: dict) -> dict:
        result = {k: row[k] for k in PUBLIC_FIELDS if k in row}
        # Keep deterministic equality without transmitting a counterparty name.
        if result.get("counterparty"):
            result["counterparty"] = "party-" + hashlib.sha256(str(result["counterparty"]).encode()).hexdigest()[:12]
        # Descriptions are untrusted documentary data, never tool instructions.
        if "description" in result:
            result["description"] = str(result["description"])[:1500]
        return result

    def verify(self, claims: list[dict]) -> dict:
        results = []
        for raw in claims:
            claim = Claim.model_validate(raw)
            unknown = sorted(set(claim.evidence_ids) - self.evidence_ids)
            rule_known = claim.rule_id in self.rule_ids
            metrics = self.verified_finding.get("metrics", {}) if self.verified_finding else {}
            source_evidence = set(self.verified_finding.get("evidence_ids", [])) if self.verified_finding else set()
            source_matches = (self.verified_finding is not None
                              and claim.rule_id == self.verified_finding.get("rule_id")
                              and bool(claim.evidence_ids) and set(claim.evidence_ids) <= source_evidence)
            calculations_verified = source_matches and all(
                key in metrics and str(metrics[key]) == value for key, value in claim.calculation.items()
            ) if claim.calculation else None
            if self.ablation == "no_program_verification":
                calculations_verified = None
            results.append({"text": claim.text, "citations_valid": not unknown,
                            "unknown_evidence_ids": unknown, "rule_known": rule_known,
                            "policy_citations_valid": set(claim.policy_source_ids) <= self.reference_ids,
                            "calculations_verified": calculations_verified,
                            "calculation_status": "not_requested" if not claim.calculation else
                            "disabled" if self.ablation == "no_program_verification" else
                            "verified" if calculations_verified else "not_verified",
                            "program_verification": "disabled_synthetic_ablation" if
                            self.ablation == "no_program_verification" else "enabled",
                            **({"metric_source": {"rule_id": self.verified_finding["rule_id"],
                                                   "evidence_ids": sorted(source_evidence)}
                                if self.verified_finding else None,
                                "calculation_source_matches": source_matches} if claim.calculation else {}),
                            "semantic_status": "unverified_model_proposal"})
        return {"claims": results, "notice": "引用存在和数值吻合不等于文本结论已获业务证明"}

    def execute(self, name: str, args: dict, *, count: bool = True) -> dict:
        if count:
            self.budget.use_tool()
        if name not in TOOL_MODELS:
            raise ValueError("Tool not allowed")
        values = TOOL_MODELS[name][0].model_validate(args).model_dump()
        if name == 'read_policy_checks':
            check_ids = values['check_ids']
            if any(identity not in self.policy_checks for identity in check_ids):
                raise ValueError('Saved policy check is outside this run capture')
            selected = check_ids or list(self.policy_checks)[:6]
            refs = {locator['evidence_id'] for identity in selected
                    for locator in self.policy_checks[identity]['binding']['evidence_locators']}
            return {'status': 'available' if self.policy_checks else 'not_provided',
                    'policy_checks': [deepcopy(self.policy_checks[identity]) for identity in selected],
                    'policy_check_ids': list(selected), 'available_check_ids': list(self.policy_checks),
                    'total': len(self.policy_checks), 'truncated': len(selected) < len(self.policy_checks),
                    'records': [self.public_record(row) for row in self.records if row.get('evidence_id') in refs],
                    'policy_check_context': deepcopy(self.policy_check_manifest),
                    'notice': 'Frozen saved checks and source records only. Human annotations are review assertions; model interpretations are unverified.'}
        if name == "get_business_chain":
            output = business_chain(self.records, **values)
            output["records"] = [self.public_record(row) for row in output["records"]]
            return output
        if name == "query_journal_lines":
            filters = {k: v for k, v in values.items() if k != "limit" and v is not None}
            rows = [r for r in self.records if r.get("kind") == "journal"
                    and all(r.get(k) == v for k, v in filters.items())]
            return {"records": [self.public_record(r) for r in rows[:values["limit"]]],
                    "total": len(rows), "truncated": len(rows) > values["limit"]}
        if name == "retrieve_policy":
            references_requested = bool(values["query"])
            references = [r for r in self.references if
                          values["query"].lower() in (r["text"] + r["title"]).lower()] if references_requested else []
            rules = [r for r in self.rules if not values["rule_id"] or r["rule_id"] == values["rule_id"]]
            # The directory need not repeat the same source hash/URL and file
            # path for every rule on every turn. A targeted lookup retains all
            # applicability/source/validation fields. This does not broaden
            # either framework's executable coverage.
            if not values["rule_id"]:
                fields = {"rule_id", "title", "framework", "required_kinds", "status", "limitations"}
                rules = [{key: value for key, value in r.items() if key in fields} for r in rules]
            return {"framework": self.policy["framework"], "version": self.policy.get("version"),
                    "rules": rules, "rules_are_summaries": not bool(values["rule_id"]),
                    "detail_instruction": "按 rule_id 查询完整适用范围、来源和验证状态",
                    "references_requested": references_requested,
                    "available_reference_count": len(self.references),
                    "matched_reference_count": len(references) if references_requested else None,
                    "reference_passages": references[:10], "reference_count": len(references),
                    "references_truncated": len(references) > 10,
                    "notice": "未请求政策全文不等于没有文献；需要时显式用 query 按业务关键词检索。实验性规则覆盖不代表完整准则实现；文献 source_id 仅用于政策引用，不能充当业务证据或执行规则"}
        if name == "request_evidence":
            if values["description"] not in self.requests:
                self.requests.append(values["description"])
            return {"status": "requested", "description": values["description"], "reason": values["reason"],
                    "requires_new_snapshot": True, "retrieved": False}
        if name == "verify_claims":
            return self.verify(values["claims"])
        if name == "read_prior_proposal":
            if values["index"] >= len(self.prior_proposals):
                return {"status": "unavailable", "reason": "当前运行尚无此角色建议"}
            return {"status": "available", "proposal": proposal_brief(self.prior_proposals[values["index"]]),
                    "notice": "仅为当前调查模型建议；证据引用存在不代表其文本结论已获证明"}
        if name == "match_transactions":
            from .matching import match_transactions

            if values["left_kind"] == values["right_kind"]:
                raise ValueError("Matching sides must use distinct record kinds")
            selected = [r for r in self.records if not values["reference"] or r.get("reference") == values["reference"]]
            left = [r for r in selected if r.get("kind") == values["left_kind"]]
            right = [r for r in selected if r.get("kind") == values["right_kind"]]
            if len(left) + len(right) > 100:
                return {"status": "unsupported", "reason": "请用来源编号缩小匹配范围（最多 100 条）"}
            return match_transactions(left, right, max_group=6, flow=values["flow"])
        raise ValueError("Tool not implemented")


class InvestigationState(TypedDict, total=False):
    fingerprint: str
    base_status: str
    claims: list[dict]
    hypotheses: list[dict]
    missing_evidence: list[str]
    proposals: list[dict]
    steps: list[dict]
    usage: dict
    role_index: int
    rounds: int
    error: dict | None
    actual_models: list[str]
    result: dict
    evidence_memory: dict
    evidence_selections: list[dict]


class EvidenceMemory:
    """Snapshot-local program outputs, shared across roles without model edits.

    Record payloads are interned durably by their evidence identity. Each role
    explicitly selects the query payloads it needs; repeats reuse stored data.
    This is a query cache, not evidence acquisition or business confirmation.
    """

    def __init__(self, stored: dict | None = None):
        stored = stored or {}
        self.scope = stored.get("scope")
        self.records = deepcopy(stored.get("records", {}))
        self.queries = deepcopy(stored.get("queries", {}))
        self.references = deepcopy(stored.get("references", {}))
        self.policy_checks = deepcopy(stored.get('policy_checks', {}))
        self.policy_check_manifest = deepcopy(stored.get('policy_check_manifest', {
            'provided': False, 'check_count': 0, 'policy_check_index': []}))
        self.program_observations = deepcopy(stored.get("program_observations", []))
        if not isinstance(self.program_observations, list) or len(self.program_observations) > MAX_PROGRAM_OBSERVATIONS:
            raise ValueError("Program observation history exceeds the executed-tool bound")

    def stored(self) -> dict:
        return {"scope": self.scope, "records": deepcopy(self.records), "queries": deepcopy(self.queries),
                "references": deepcopy(self.references),
                'policy_checks': deepcopy(self.policy_checks),
                'policy_check_manifest': deepcopy(self.policy_check_manifest),
                "program_observations": deepcopy(self.program_observations)}

    def bind(self, context: ToolContext, *, attach: bool = True) -> None:
        if self.scope is None:
            if self.records or self.queries or self.references or self.policy_checks or self.program_observations:
                raise ValueError("Unbound evidence memory cannot be attached to a snapshot")
            if attach:
                self.scope = context.memory_scope
        elif self.scope != context.memory_scope:
            raise ValueError("Evidence memory belongs to a different snapshot or policy")
        for identity, item in self.policy_checks.items():
            if context.policy_checks.get(identity) != item:
                raise ValueError('Cached saved policy check differs from the frozen run capture')
        for query in self.queries.values():
            if query.get('tool') == 'read_policy_checks':
                args = SavedPolicyQuery.model_validate(query['parameters'])
                expected = args.check_ids or list(context.policy_checks)[:6]
                if query['result'].get('policy_check_ids') != expected:
                    raise ValueError('Cached saved policy query no longer matches its captured parameters')
                if not set(expected) <= self.policy_checks.keys():
                    raise ValueError('Cached saved policy query is missing its full captured check content')
        if attach:
            self.policy_check_manifest = deepcopy(context.policy_check_manifest)

    def brief(self, query_ids: set[str] | None = None) -> dict:
        """Complete internal view, or a role's explicit read view plus manifest.

        Selection never deletes durable data. Every observation remains visible;
        unselected query payloads remain available via a bounded scoped read.
        """
        if query_ids is not None and not query_ids <= self.queries.keys():
            raise ValueError("Unknown cached query identity")
        queries = []
        # Keyword searches often return the same executable-rule directory.
        # Intern exact definitions in the transport view only: the durable
        # query results retain their original content, ordering and scope.
        # Summary and detailed definitions stay distinct; never upgrade a
        # summary by silently replacing it with another query's full rule.
        definitions, identities = {}, {}
        selected_records, selected_references, selected_policy_checks = set(), set(), set()
        manifest = []
        for query in self.queries.values():
            query = deepcopy(query)
            if query_ids is not None:
                result = query["result"]
                fields = {"status", "total", "truncated", "framework", "version", "rules_are_summaries",
                          "references_requested", "available_reference_count", "matched_reference_count",
                          "reference_count", "references_truncated", "record_evidence_ids", "reference_source_ids",
                          'policy_check_ids', 'available_check_ids'}
                manifest.append({"query_id": query["query_id"], "tool": query["tool"],
                    "payload_kind": "model_opinion" if query["tool"] == "read_prior_proposal" else "program_output",
                    "parameters": query["parameters"],
                    "metadata": {k: deepcopy(v) for k, v in result.items() if k in fields},
                    "rule_ids": [r["rule_id"] for r in result.get("rules", [])],
                    "result_fields": list(result),
                    "result_sha256": self.query_hash(query["query_id"]),
                    "hash_scope": "query_and_referenced_record_policy_payloads",
                    "payload_in_view": query["query_id"] in query_ids})
                if query["query_id"] not in query_ids:
                    continue
            selected_records.update(query["result"].get("record_evidence_ids", []))
            selected_references.update(query["result"].get("reference_source_ids", []))
            selected_policy_checks.update(query['result'].get('policy_check_ids', []))
            if query["tool"] == "read_prior_proposal" and query_ids is None:
                # Full reports remain available through the same scoped read
                # tool; putting them all back in the brief defeats lazy reads.
                query = {**query, "result": {"status": "readable_in_run_history"}}
            if query["tool"] == "retrieve_policy" and "rules" in query["result"]:
                rule_ids = []
                for rule in query["result"].pop("rules"):
                    canonical = json.dumps(rule, ensure_ascii=False, sort_keys=True, allow_nan=False)
                    if canonical not in identities:
                        identity = f"rule-definition-{len(definitions)}"
                        identities[canonical] = identity
                        definitions[identity] = rule
                    rule_ids.append(identities[canonical])
                query["result"]["rule_definition_ids"] = rule_ids
            queries.append(query)
        output = {"records": [deepcopy(v) for k, v in self.records.items()
                               if query_ids is None or k in selected_records], "query_results": queries,
                "rule_definitions": definitions,
                "reference_passages": [deepcopy(v) for k, v in self.references.items()
                                       if query_ids is None or k in selected_references],
                'policy_checks': [deepcopy(item) for identity, item in self.policy_checks.items()
                                  if query_ids is None or identity in selected_policy_checks],
                'policy_check_context': deepcopy(self.policy_check_manifest),
                'policy_check_index': deepcopy(self.policy_check_manifest.get('policy_check_index', [])),
                "program_observations": deepcopy(self.program_observations),
                "notice": "当前快照具名工具的已返回记录。记录引用取 evidence_id；缓存不代表业务关系已确认。"}
        if query_ids is not None:
            output.update({"view": "explicit_role_reads", "query_manifest": manifest,
                           "unread_query_count": len(self.queries) - len(query_ids),
                           "notice": "query_manifest覆盖全部已执行查询，但索引不含完整依据。payload_in_view=false的原文尚未给当前角色；需read_evidence_queries读取，不能把未读取当成无证据。program_observations全量保留。"})
        return output

    def content_ids(self, query_ids: set[str]) -> tuple[set[str], set[str]]:
        if not query_ids <= self.queries.keys():
            raise ValueError("Unknown cached query identity")
        records, references = set(), set()
        for key in query_ids:
            result = self.queries[key]["result"]
            records.update(result.get("record_evidence_ids", []))
            references.update(result.get("reference_source_ids", []))
        return records, references

    def policy_content_ids(self, query_ids: set[str]) -> set[str]:
        if not query_ids <= self.queries.keys():
            raise ValueError('Unknown cached query identity')
        checks = {identity for key in query_ids if self.queries[key]['tool'] == 'read_policy_checks'
                  for identity in self.queries[key]['result'].get('policy_check_ids', [])}
        if not checks <= self.policy_checks.keys():
            raise ValueError('Cached query references missing saved policy checks')
        return checks

    def query_hash(self, key: str) -> str:
        records, references = self.content_ids({key})
        envelope = {"query": self.queries[key],
                    "records": {k: self.records[k] for k in sorted(records)},
                    "reference_passages": {k: self.references[k] for k in sorted(references)}}
        checks = self.policy_content_ids({key})
        if checks:
            envelope['policy_checks'] = {identity: self.policy_checks[identity] for identity in sorted(checks)}
        return hashlib.sha256(json.dumps(envelope, ensure_ascii=False, sort_keys=True,
                                         allow_nan=False).encode()).hexdigest()

    def read_queries(self, context: ToolContext, query_ids: list[str]) -> dict:
        self.bind(context, attach=False)
        if any(key not in self.queries for key in query_ids):
            # Reject the whole batch; do not reveal a partial result for a
            # guessed or foreign query identity, and never make an empty cache.
            raise ValueError("Unknown cached query identity")
        self.bind(context)
        return {"status": "available", "cache_hit": True, "query_ids": list(query_ids),
                "evidence": self.brief(set(query_ids)),
                "notice": "只读取本运行既有快照结果，未执行新查询、取得新资料或确认业务事实。"}

    def execute(self, context: ToolContext, name: str, args: dict) -> dict:
        if name not in TOOL_MODELS:
            raise ValueError("Tool not allowed")
        canonical = TOOL_MODELS[name][0].model_validate(args).model_dump()
        self.bind(context, attach=False)
        if name == "read_evidence_queries":
            return self.read_queries(context, canonical["query_ids"])
        key = query_cache_key(name, canonical)
        if name in {"request_evidence", "verify_claims"}:
            # Requests must update the current run; claim checks depend on the
            # active finding, so neither uses result caching. Their program
            # results still survive fresh conversations and role boundaries.
            # Never silently drop a negative check or a still-unmet request to
            # fit a model context: the gateway's size guard fails explicitly.
            if len(self.program_observations) >= MAX_PROGRAM_OBSERVATIONS:
                raise BudgetExceeded("程序观察记录达到工具上限，保留已有结果并停止调查")
            output = context.execute(name, canonical, count=False)
            self.bind(context)
            self.program_observations.append({"tool": name, "result": deepcopy(output)})
            return output
        if key in self.queries:
            return {"cache_hit": True, "query_id": key, **deepcopy(self.queries[key]["result"]),
                    "notice": "缓存命中非空结果；本次读取后完整记录和政策片段进入当前角色的shared_evidence，分别用evidence_id和source_id定位。"}
        output = context.execute(name, canonical, count=False)
        if name == "read_prior_proposal" and output.get("status") != "available":
            # Prior proposals grow as roles finish. An unavailable index is
            # not an immutable snapshot read and must not poison later reads.
            return {"cache_hit": False, "query_id": key, **output}
        new_records = []
        new_references = []
        compact = deepcopy(output)
        pending_records = {}
        for row in compact.get("records", []):
            identity = row.get("evidence_id")
            if not identity:
                raise ValueError("Cached records require an evidence identity")
            previous = pending_records.get(identity, self.records.get(identity))
            if previous is not None and previous != row:
                raise ValueError("Conflicting record content for one evidence identity")
            pending_records[identity] = row
        if 'policy_checks' in compact:
            check_ids, pending_checks = [], {}
            for item in compact.pop('policy_checks'):
                identity = item['id']
                if context.policy_checks.get(identity) != item:
                    raise ValueError('Tool returned a policy check outside its frozen run capture')
                previous = pending_checks.get(identity, self.policy_checks.get(identity))
                if previous is not None and previous != item:
                    raise ValueError('Conflicting saved policy check content')
                pending_checks[identity] = deepcopy(item)
                check_ids.append(identity)
            self.bind(context)
            self.policy_checks.update(pending_checks)
            compact['policy_check_ids'] = check_ids
        if "reference_passages" in compact:
            reference_ids, pending = [], {}
            for passage in compact.pop("reference_passages"):
                identity = passage["source_id"]
                previous = pending.get(identity, self.references.get(identity))
                if previous is not None and previous != passage:
                    raise ValueError("Conflicting policy passage content for one source identity")
                reference_ids.append(identity)
                if previous is None:
                    pending[identity] = deepcopy(passage)
            # Validate the whole batch before mutating the program memory.
            self.bind(context)
            self.references.update(pending)
            new_references = list(pending.values())
            compact["reference_source_ids"] = reference_ids
            compact["references_are_incremental"] = True
        if "records" in compact:
            self.bind(context)
            record_ids = []
            for row in compact.pop("records"):
                identity = row.get("evidence_id")
                if not identity:
                    raise ValueError("Cached records require an evidence identity")
                record_ids.append(identity)
                if identity not in self.records:
                    self.records[identity] = deepcopy(row)
                    new_records.append(row)
            compact["record_evidence_ids"] = record_ids
            compact["records_are_incremental"] = True
        self.bind(context)
        self.queries[key] = {"query_id": key, "tool": name, "parameters": canonical, "result": compact}
        response = {"cache_hit": False, "query_id": key, **deepcopy(compact)}
        if "record_evidence_ids" in compact:
            response["new_records"] = deepcopy(new_records)
            response["notice"] = "查询命中记录列在 record_evidence_ids；仅新见记录附在 new_records。其余记录已返回。"
        if "reference_source_ids" in compact:
            response["new_reference_passages"] = deepcopy(new_references)
            response["notice"] = "政策片段引用列在 reference_source_ids；完整片段在 shared_evidence.reference_passages。首次返回的全文附在 new_reference_passages，复用不会删除适用条件。"
        return response


def planning_tools(context: ToolContext, memory: EvidenceMemory) -> dict[str, PlanningToolBinding]:
    """Server-owned read bindings; every executor captures this fixed snapshot.

    Scheduling costs are relative work units, not probabilities or API fees.
    Available means a scoped query can run, not that it will find evidence.
    """
    def business_available(args):
        return "available" if any((args.order_id, args.invoice_id, args.reference, args.anchor_evidence_id)) else "unavailable"

    def matching_available(args):
        return "available" if args.left_kind != args.right_kind else "unavailable"

    def saved_policy_available(args):
        return ('available' if context.policy_checks and set(args.check_ids) <= context.policy_checks.keys()
                else 'unavailable')

    result = {}
    for name in sorted(SUPPORTED_QUERY_TOOLS):
        schema = TOOL_MODELS[name][0]
        availability = "available"
        if name == "get_business_chain":
            availability = business_available
        elif name == "match_transactions":
            availability = matching_available
        elif name == 'read_policy_checks':
            availability = saved_policy_available
        result[name] = PlanningToolBinding(
            parameters=schema, execute=lambda args, tool=name: memory.execute(context, tool, args.model_dump()),
            cost_units=5 if name == "match_transactions" else 1, availability=availability)
    return result


def _fingerprint(records: list[dict], policy: dict, finding: dict, mode: str, ablation: str | None = None,
                 configured_model: str | None = None) -> str:
    return hashlib.sha256(json.dumps([records, policy, finding, mode, ablation, rule_identity(),
                                    PROMPT_VERSION, TOOL_VERSION, configured_model, MODEL_PARAMETERS], ensure_ascii=False,
                                   sort_keys=True, allow_nan=False).encode()).hexdigest()


def _initial_hypotheses(finding: dict) -> list[dict]:
    if finding.get('task_type') == 'review':
        explanations = ['可见资料范围内可能没有规则差异', '可能存在需要查证的数据差异',
                        '部分适用检查可能因资料不足而无法判定']
    elif finding.get("rule_id") in {"DUPLICATE_INVOICE", "INVOICE_OVERBOOKED", "INVOICE_EVIDENCE"}:
        explanations = ["可能存在重复确认", "可能是正常分拆确认或结算", "可能缺少冲销或原始发票资料"]
    elif finding.get("rule_id") in {"RECEIPT_GAP", "RECEIPT_EXCESS"}:
        explanations = ["可能是正常分批收货", "可能存在迟到、变更或取消资料", "可能是错误关联或重复收货记录"]
    else:
        explanations = ["当前规则发现数据差异", "可能存在正常业务解释或缺失资料"]
    return [{"explanation": e, "status": "unresolved", "evidence_ids": [],
             "missing_evidence": list(finding.get("missing_evidence", []))} for e in explanations]


def _active_role(hypotheses: list[dict], completed: int, finding: dict) -> tuple[str, str]:
    # A transparent heuristic, not a calibrated information-gain estimate.
    if completed == 0:
        return "coordinator", "先列出可由不同资料区分的候选解释"
    if completed == 2:
        return "critic", "最后检查反证、正常业务解释和证据不足"
    needs = " ".join(item for h in hypotheses for item in h.get("missing_evidence", []))
    needs += " " + " ".join(finding.get("missing_evidence", []))
    if any(term in needs for term in ("准则", "期间", "确认", "政策", "科目")):
        return "policy", "缺失事项涉及适用政策或确认条件，优先查规则"
    return "evidence", "优先查询能区分业务解释的关联单据和资料缺口"


ROLE_INSTRUCTIONS = {
    "single": "你独立完成候选解释、取证、反证和复核建议。",
    "ledger": "你负责账务核对。调用程序检查金额，不心算和猜测。",
    "evidence": "你负责业务取证。核对订单、收货、发票、付款关系；资料不足时提出具体补证。",
    "policy": "你负责政策适用。先查看policy_check_index，按check_ids用read_policy_checks读取已保存的政策检查及其人工条件和原始记录；这些检查与核心规则不同，只在policy_interpretations中解释。人工条件录入时间不等于历史来源时点。按rule_id查看核心受控规则；政策全文显式query业务关键词。未读取不代表没有依据，明确未覆盖业务。",
    "critic": "你负责反证。主动寻找正常分拆、未达、冲销及缺失资料解释，反驳未经证明的推断。",
    "report": "你负责整理底稿。区分程序验证事实与模型待复核建议，保留冲突和缺失。",
    "coordinator": "你负责提出多个解释及能区分解释的证据，不重复描述问题。",
}


async def investigate(
    records: list[dict], policy: dict, finding: dict, mode: str = "rules",
    gateway: DeepSeekGateway | None = None, on_step=None, checkpoint_config: dict | None = None,
) -> dict:
    """Run one case; no source mutation, network retrieval, or automatic posting.

    Failed model work returns failed/budget_exhausted and keeps deterministic
    claims. Proposed claims remain explicitly unverified even with valid IDs.
    """
    if mode not in MODES:
        raise ValueError("Unknown investigation mode")
    records, policy, finding = deepcopy((records, policy, finding))
    if 'execution_rules' in policy:
        ensure_rule_compatibility(policy)
    checkpoint_config = checkpoint_config or {}
    ablation = checkpoint_config.get("ablation")
    if ablation is not None:
        if ablation not in ABLATIONS:
            raise ValueError("Unknown ablation")
        if checkpoint_config.get("synthetic_evaluation") is not True:
            raise ValueError("Ablations require an explicitly synthetic evaluation context")
        if mode == "rules" and ablation != "no_time_view":
            raise ValueError("This ablation requires a model mode")
        if ablation == "no_active_selection" and mode != "active":
            raise ValueError("no_active_selection applies only to active mode")
        if ablation == "no_time_view":
            comparison = checkpoint_config.get("comparison_records")
            if not isinstance(comparison, list) or not comparison:
                raise ValueError("no_time_view requires comparison_records with simulated later evidence")
            if not {r["id"] for r in records} <= {r["id"] for r in comparison}:
                raise ValueError("Comparison view must retain the original snapshot records")
            by_id = {r["id"]: r for r in comparison}
            if any(r != by_id[r["id"]] for r in records):
                raise ValueError("Comparison view must not rewrite original records")
            cutoff = policy.get("snapshot_cutoff")
            if not cutoff or not any(r.get("observed_at") and _stamp(r["observed_at"]) > _stamp(cutoff)
                                     for r in comparison):
                raise ValueError("no_time_view requires genuinely later simulated evidence and a cutoff")
            records = deepcopy(comparison)
            policy = {**policy, "snapshot_cutoff": None}
    budget = RunBudget.restore(checkpoint_config.get("usage"))
    context = ToolContext(records, policy, finding, budget, ablation=ablation)
    configured_model = getattr(gateway, "model", None)
    fingerprint = _fingerprint(records, policy, finding, mode, ablation, configured_model)
    fixed_roles = ["ledger", "evidence", "policy", "critic", "report"]
    if ablation == "no_counterproof":
        fixed_roles.remove("critic")
    steps: list[dict] = []

    async def emit(event: dict):
        event = {**event, "usage": budget.usage()}
        steps.append(event)
        await notify(on_step, event)

    async def detect(state: InvestigationState):
        candidates = check_records(context.records, context.policy)
        review_task = finding.get('task_type') == 'review'
        if finding.get('task_type') == 'root_cause':
            # Root-cause tasks start from ALL deterministic results, as in the
            # product flow (rules first); the model never replaces detection.
            flagged = [c for c in candidates if c["status"] in {"supported", "insufficient"}]
            base = ("insufficient" if any(c["status"] == "insufficient" for c in flagged)
                    else "supported" if flagged else "normal")
            claims = [{"text": c["explanation"], "evidence_ids": c["evidence_ids"], "rule_id": c["rule_id"],
                       "calculation": c.get("metrics", {}), "origin": "deterministic_rule"} for c in flagged]
            await emit({"event": "deterministic_check", "title": "复核固定快照中的规则结果", "status": base,
                        "rule_id": None, 'task_type': 'root_cause'})
            return {"base_status": base, "claims": claims,
                    "missing_evidence": sorted({m for c in flagged for m in c.get("missing_evidence", [])}),
                    "hypotheses": _initial_hypotheses(finding), "steps": list(steps), "usage": budget.usage()}
        match = (None if review_task else next((c for c in candidates if c["rule_id"] == finding.get("rule_id")
                      and set(c["record_ids"]) == set(finding.get("record_ids", []))), None))
        context.verified_finding = match
        if match:
            base = match["status"] if match["status"] in STATUSES else "insufficient"
            claims = [{"text": match["explanation"], "evidence_ids": match["evidence_ids"],
                       "rule_id": match["rule_id"], "calculation": match.get("metrics", {}),
                       "origin": "deterministic_rule"}]
            missing = list(match.get("missing_evidence", []))
        elif review_task:
            # A request to review a book is not an already detected anomaly.
            # Do not manufacture a missing-evidence fact or feed detector
            # labels into an answer-blind model baseline.
            base, claims, missing = 'insufficient', [], []
        else:
            base, claims = "unsupported", []
            missing = ["当前快照无法独立复现该异常，请重新运行检查或检查规则覆盖范围"]
        await emit({"event": "deterministic_check", "title": "复核固定快照中的规则结果", "status": base,
                    "rule_id": finding.get("rule_id"), 'task_type': finding.get('task_type', 'anomaly')})
        return {"base_status": base, "claims": claims, "missing_evidence": missing,
                "hypotheses": _initial_hypotheses(finding), "steps": list(steps), "usage": budget.usage()}

    async def model_node(state: InvestigationState):
        nonlocal steps
        steps = list(state.get("steps", []))
        if not context.verified_finding and finding.get('task_type') != 'review':
            context.verified_finding = next((c for c in check_records(context.records, context.policy)
                if c["rule_id"] == finding.get("rule_id") and set(c["record_ids"]) == set(finding.get("record_ids", []))), None)
        # Restore durable usage and never reduce counters persisted mid-node.
        for key, value in state.get("usage", {}).items():
            if key in budget.usage():
                setattr(budget, key, max(getattr(budget, key), value))
        index = state.get("role_index", 0)
        if mode == "single":
            role, reason = "single", "单智能体基线"
        elif mode == "multi":
            role, reason = fixed_roles[index], "固定分工基线"
        elif ablation == "no_active_selection":
            role, reason = ["coordinator", "evidence", "critic"][index], "合成消融：固定角色选择"
        else:
            role, reason = _active_role(state.get("hypotheses", []), index, finding)
            if index == 1 and state.get("evidence_selections"):
                selected = state["evidence_selections"][-1].get("selected_query") or {}
                role = "policy" if selected.get("tool") in {'retrieve_policy', 'read_policy_checks'} else "evidence"
                reason = "复核程序按可区分解释对数与服务端成本选择的查询结果"
            if ablation == "no_counterproof" and role == "critic":
                role, reason = "report", "合成消融：省略独立反证阶段"
        await emit({"event": "agent_started", "role": role, "title": reason})
        proposals = list(state.get("proposals", []))
        context.prior_proposals = deepcopy(proposals)
        models = list(state.get("actual_models", []))
        error = None
        missing = list(state.get("missing_evidence", []))
        hypotheses = list(state.get("hypotheses", []))
        memory = EvidenceMemory(state.get("evidence_memory"))
        memory.bind(context)
        selected_query_ids: set[str] = set()
        # The active selector has explicitly supplied one result for this
        # role. Preserve that handoff; other cached queries stay readable.
        if mode == "active" and state.get("evidence_selections"):
            execution = state["evidence_selections"][-1].get("execution", {})
            if execution.get("performed") is True and execution.get("query_id") in memory.queries:
                selected_query_ids.add(execution["query_id"])
        try:
            if gateway is None:
                raise GatewayError("model_configuration", "未配置 DeepSeek API Key；模型模式不会回退为伪造演示")
            allow_plan = mode == "active" and ablation != "no_active_selection"
            planning_only = allow_plan and role == "coordinator"
            schema = proposal_schema(context, allow_plan=allow_plan)
            role_instruction = (f'你负责原因调查的{role}阶段，仅提交窄化的原因结论。' if context.cause_taxonomy
                                else ROLE_INSTRUCTIONS[role])
            if ablation == "no_counterproof" and role == "single":
                role_instruction = "你独立完成候选解释、取证和复核建议。此次合成消融省略专门反证阶段。"
            if allow_plan:
                role_instruction += (
                    "本轮还需提交 evidence_plan：为待区分的解释设置稳定 hypothesis_id，提出最多4个具体候选查询。"
                    "每个查询用 predictions 写明各解释下预计观察到的结果标签；不知道填 status=unknown,label=null。"
                    "标签不同才表示预计能区分，不能编造概率。仅使用已开放的只读工具；"
                    "查询参数必须是真实可见的编号，不能使用尚未获知的资料。不要提前执行拟让选择器比较的查询。"
                    "服务端会校验参数、判定可用性、排除已执行查询并选择一个执行；不要填写成本或availability。"
                    "解释已经明确或没有区分性查询时，evidence_plan可为null并说明未决资料。")
            if planning_only:
                role_instruction += (
                    "协调阶段只制定解释和查询计划，不执行工具。候选工具的参数契约在 tool_contracts 中；"
                    "需要查看资料的查询放入 evidence_plan，交给程序选择执行。"
                    "不把预期标签当成已取得的查询结果。" +
                    ("尚未确定原因时categories留空，status为insufficient。" if context.cause_taxonomy else
                     "尚未取得证据时claims留空，hypotheses保持unresolved。"))
            if finding.get('task_type') == 'root_cause':
                role_instruction += ('本任务是原因调查：verified_claims是程序规则结果，不要重复检测。'
                    '综合资料与文字备注判断原因，在cause_assessment.categories中只用cause_taxonomy的类别，'
                    'status表示原因结论：已确定原因且属于需要更正的问题用supported；已确定原因且属于正常业务安排用normal；只有原因无法确定时才用insufficient并将categories留空，已选出原因时不要用insufficient。'
                    + ('本案最多可提出一次结构化补证evidence_request（kind为资料种类，reference只能是可见编号），'
                       '只有它能取得快照外资料；请选择最能区分原因的资料。'
                       if context.evidence_request_allowed else '本案补证机会已用完或不提供，evidence_request必须为null。'))
            if finding.get('task_type') == 'review':
                role_instruction += ('本任务是普通资料复核，没有预先确定的异常。候选解释均为未决假设。'
                    '先核实可见资料和适用条件；没有观察到适用规则差异可以返回normal，'
                    '只在确有差异时标finding，不把尚未提供且不适用的资料当成缺口。')
            synthesis = role in {'single', 'report'} or (mode == 'active' and index == 2)
            output_scope = ('Synthesize the full task, including all prior role opinions and unresolved conflicts.'
                            if synthesis else 'Give only this specialist role opinion; final synthesis belongs to a later role.')
            protocol = ROOT_CAUSE_PROTOCOL if context.cause_taxonomy else PROTOCOL
            system = (protocol + '\nRole: ' + role_instruction + '\n' + output_scope + '\noutput_schema=' +
                      json.dumps(schema, ensure_ascii=False, separators=(',', ':')))
            brief = {"framework": policy["framework"], "policy_version": policy.get("version"),
                     "period": policy.get("period"), "finding": finding,
                     "verified_claims": [{**c, "calculation": {k: v for k, v in c.get("calculation", {}).items()
                                          if isinstance(v, str)}} for c in state.get("claims", [])],
                     "prior_proposals": [proposal_brief(p) for p in (proposals if synthesis else proposals[-1:])],
                     "prior_proposal_index": [{"index": i, "role": p["role"], "status": p["status"],
                         "claim_rule_ids": sorted({c["rule_id"] for c in p["claims"]}),
                         "claim_count": len(p["claims"]), "hypothesis_count": len(p["hypotheses"]),
                         "missing_evidence": p["missing_evidence"]} for i, p in enumerate(proposals)],
                     "unresolved_hypotheses": list({json.dumps(
                         {k: v for k, v in h.items() if k in Hypothesis.model_fields}, sort_keys=True, ensure_ascii=False):
                         {k: v for k, v in h.items() if k in Hypothesis.model_fields}
                         for h in state.get("hypotheses", []) if h.get("status") == "unresolved"}.values()),
                     "missing_evidence": list(state.get("missing_evidence", [])),
                     "shared_evidence": memory.brief(selected_query_ids),
                     "evidence_selections": state.get("evidence_selections", []),
                     'policy_check_context': deepcopy(context.policy_check_manifest),
                     "snapshot_scope": deepcopy(context.snapshot_scope),
                     "available_evidence_count": len(context.evidence_ids)}
            if planning_only:
                brief["tool_contracts"] = tool_schemas(context)
            request_brief = brief
            messages = [{"role": "system", "content": system},
                        {"role": "user", "content": serialize_context(request_brief)}]
            repairs = 0
            finish_only = planning_only
            recovered_truncation = False
            tool_turns = 0
            # All methods use the same per-role retrieval-batch limit and the
            # same run-wide paid-attempt, token and tool limits. After the last
            # complete tool batch, require a no-tools formatting pass so later
            # roles can inspect the evidence and unresolved matters. This is
            # a scheduling stop, never a declaration that the case is correct.
            for _ in range(budget.max_calls):
                try:
                    thinking_enabled = not recovered_truncation and repairs == 0 and (planning_only or not finish_only)
                    response = await gateway.chat(messages, tools=None if finish_only else tool_schemas(context),
                                                  budget=budget, on_attempt=emit,
                                                  thinking=thinking_enabled,
                                                  max_tokens=MODEL_PARAMETERS["max_tokens"],
                                                  reasoning_effort=MODEL_PARAMETERS["reasoning_effort"] if thinking_enabled else None)
                except GatewayError as exc:
                    if exc.code != "model_truncated" or recovered_truncation:
                        raise
                    # The truncated body is NEVER a proposal or evidence. One
                    # fresh bounded formatting pass uses only the same scoped
                    # program results. Keep the billed attempt in usage/steps;
                    # all comparison methods follow this identical policy.
                    recovered_truncation = True
                    finish_only = True
                    request_brief = {**request_brief, 'shared_evidence': memory.brief(selected_query_ids)}
                    messages = [{"role": "system", "content": system}, {"role": "user", "content":
                        serialize_context(request_brief)},
                        {"role": "user", "content": "上次输出超过限制，截断内容已丢弃。仅基于现有资料生成简洁、完整的 JSON。"
                         "本次不再调用工具，不添加事实；无法证明的解释保留未决，仍须满足全部引用和结构校验。"}]
                    await emit({"event": "model_truncation_recovery", "role": role,
                                "title": "丢弃截断输出，以同一证据进行一次受限结构化重试", "thinking": False,
                                "truncated_output_accepted": False})
                    continue
                models.append(response["model"])
                message = response["message"]
                messages.append(message)  # retained only if this response needs a schema-repair continuation
                calls = message.get("tool_calls") or []
                if calls:
                    if finish_only:
                        if planning_only:
                            raise GatewayError("model_tool_policy", "协调阶段只能提出查询计划，不能直接执行工具")
                        raise GatewayError("model_no_progress", "取证已停止后模型仍请求工具；保存已有证据而不继续空转")
                    progress = False
                    recent_outputs = []
                    for call in calls:
                        name = call["function"]["name"]
                        first_role_read = False
                        budget.use_tool()  # rejected/invalid tool requests consume the same budget
                        await emit({"event": "tool_attempt", "role": role,
                                    "tool": name if name in TOOL_MODELS else "rejected"})
                        try:
                            args = strict_json(call["function"]["arguments"])
                            if not isinstance(args, dict):
                                raise TypeError("Arguments must be an object")
                            output = memory.execute(context, name, args)
                        except (ValueError, ValidationError, TypeError):
                            # Give a bounded error to the model, never echo SQL,
                            # secrets or arbitrary injected argument contents.
                            output = {"error": "invalid_tool_or_arguments", "allowed_tools": list(TOOL_MODELS)}
                        if "error" not in output:
                            visible_keys = (set(output["query_ids"]) if name == "read_evidence_queries"
                                            else {output["query_id"]} if output.get("query_id") in memory.queries
                                            else set())
                            first_role_read = bool(visible_keys - selected_query_ids)
                            selected_query_ids.update(visible_keys)
                            if first_role_read or (output.get("cache_hit") is not True and
                                    not (name == "read_prior_proposal" and output.get("status") != "available")):
                                progress = True
                        await emit({"event": "tool_completed", "role": role,
                                    "tool": name if name in TOOL_MODELS else "rejected", "ok": "error" not in output,
                                    "cache_hit": output.get("cache_hit", False),
                                    "content_first_read": first_role_read})
                        if name == "read_prior_proposal" or "error" in output:
                            recent_outputs.append({"tool": name if name in TOOL_MODELS else "rejected", "output": output})
                    tool_turns += 1
                    # Start a NEW API conversation from authoritative program
                    # outputs after each tool batch. Do not trim reasoning from
                    # a retained assistant/tool transcript: there is no retained
                    # transcript here. The native continuation protocol remains
                    # implemented and tested in the gateway/live protocol probe.
                    request_brief = {**brief, 'shared_evidence': memory.brief(selected_query_ids), 'current_role_tool_turns': tool_turns,
                                     'recent_tool_results': recent_outputs}
                    messages = [{"role": "system", "content": system}, {"role": "user", "content":
                        serialize_context(request_brief)}]
                    await emit({"event": "evidence_context_refreshed", "role": role,
                                "title": "用当前快照程序查询结果开始新对话，不重复传输私有思考链",
                                "tool_turns": tool_turns, "loaded_query_count": len(selected_query_ids),
                                "unread_query_count": len(memory.queries) - len(selected_query_ids)})
                    if tool_turns >= MAX_ROLE_TOOL_BATCHES:
                        finish_only = True
                        messages.append({"role": "user", "content":
                                         "本角色已完成两批受控查询。停止工具调用，输出截至当前证据的完整 JSON 以交接下一阶段。"
                                         "查询上限不表示查证成功；不能补造事实或把未决解释改为已证实。"
                                         "在output_schema允许的字段中明确保留证据不足及其原因，本次采用非思考结构化输出。"})
                        await emit({"event": "role_tool_limit", "role": role, "tool_batches": tool_turns,
                                    "maximum": MAX_ROLE_TOOL_BATCHES, "fact_confirmed": False,
                                    "title": "本角色取证批次达到上限，保留未决并准备交接"})
                        await emit({"event": "stage_handoff", "role": role, "tools": False, "thinking": False,
                                    "reason": "role_tool_limit", "title": "以已有证据形成受限结论供后续角色复核"})
                    if not progress:
                        finish_only = True
                        messages.append({"role": "user", "content":
                                         "本轮查询未向当前角色提供新内容或参数无效。停止工具调用；根据已读取证据输出完整 JSON。"
                                         "在output_schema允许的字段中保留未决原因和资料缺口。"})
                        await emit({"event": "evidence_stagnation", "role": role,
                                    "title": "重复查询未带来新资料，转入已有证据结论"})
                    continue
                try:
                    read_ids, read_reference_ids = memory.content_ids(selected_query_ids)
                    # Initial deterministic claims are themselves supplied
                    # program support. Prior MODEL opinions and manifest IDs
                    # alone never grant access to unread documentary content.
                    supplied_ids = {e for c in state.get("claims", []) for e in c.get("evidence_ids", [])}
                    supplied_ids.update(context.supplied_cause_evidence_ids)
                    proposal = validate_proposal(message.get("content", ""), context, allow_plan=allow_plan,
                                                 read_evidence_ids=read_ids | supplied_ids,
                                                 read_reference_ids=read_reference_ids,
                                                 read_policy_check_ids=memory.policy_content_ids(selected_query_ids))
                except ProposalRejected as exc:
                    await emit({"event": "output_validation_failed", "role": role,
                                "diagnostics": exc.diagnostics})
                    if repairs >= 1:
                        raise GatewayError("model_output", "模型结论未通过结构或证据引用校验") from None
                    repairs += 1
                    # A fresh request contains only authoritative scoped input
                    # and safe diagnostics. No assistant/tool transcript is
                    # continued, so rejected output/private reasoning need not
                    # be replayed. Native gateway continuations retain their
                    # separate protocol contract.
                    repair_contracts = ({name: compact_schema(TOOL_MODELS[name][0].model_json_schema())
                                         for name in sorted(SUPPORTED_QUERY_TOOLS)} if allow_plan else {})
                    finish_only = True
                    messages = [{"role": 'system', 'content': system},
                                {'role': 'user', 'content': serialize_context(request_brief)},
                                {"role": "user", "content":
                                     "输出未通过校验。修复这些字段并重新输出完整 JSON；不添加 origin、verification 或角色元数据。"
                                     "evidence_ids 必须取工具返回记录的 evidence_id，不能用记录 id 或业务编号。错误字段：" +
                                     json.dumps(exc.diagnostics, ensure_ascii=False) +
                                     ("。本次不能取得新资料；依据尚未读取时保留insufficient/unresolved，"
                                      "删除未读取的政策引用，不得假称已读。" if any(
                                          d["code"] in {"evidence_content_not_read", "policy_content_not_read", 'policy_check_content_not_read'}
                                          for d in exc.diagnostics) else "") +
                                     ("。evidence_plan查询参数严格使用以下具名工具契约；不使用其他字段，不自动执行计划。"
                                      "若没有合法且必要的后续查询，计划可为null并明确保留未决资料。tool_contracts=" +
                                      json.dumps(repair_contracts, ensure_ascii=False, separators=(',', ':'))
                                      if repair_contracts else '')}]
                    await emit({"event": "schema_repair", "role": role, "title": "输出校验失败，进行一次受限修复"})
                    continue
                output = proposal.model_dump()
                verification = context.verify(output["claims"])
                for claim, check in zip(output["claims"], verification["claims"]):
                    check["evidence_content_read"] = set(claim["evidence_ids"]) <= read_ids | supplied_ids
                    check["policy_content_read"] = set(claim["policy_source_ids"]) <= read_reference_ids
                    claim["verification"] = check
                    claim["origin"] = "model_proposal"
                output["role"] = role
                for interpretation in output['policy_interpretations']:
                    interpretation.update({'origin': 'model_proposal',
                        'semantic_status': 'unverified_model_interpretation'})
                output["status_is_model_proposal"] = True
                if context.cause_taxonomy:
                    output['cause_assessment_origin'] = 'model_proposal'
                    output['cause_assessment_semantic_status'] = 'unverified_model_interpretation'
                output["truncation_recovery_used"] = recovered_truncation
                proposals.append(output)
                missing.extend(output["missing_evidence"])
                hypotheses.extend({**h, "origin": "model_proposal"} for h in output["hypotheses"])
                await emit({"event": "agent_completed", "role": role, "title": "候选解释已保存，事实结论仍由程序核验",
                            "read_query_ids": sorted(selected_query_ids),
                            'read_policy_check_ids': sorted(memory.policy_content_ids(selected_query_ids)),
                            "unread_query_count": len(memory.queries) - len(selected_query_ids)})
                break
            else:
                raise BudgetExceeded("单角色工具对话轮次达到上限，已有证据予以保留")
        except GatewayError as exc:
            error = {"code": exc.code, "message": str(exc)}
            if exc.http_status is not None:
                error["http_status"] = exc.http_status
            await emit({"event": "investigation_failed", "role": role, "error": error})
        missing.extend(context.requests)
        return {"proposals": proposals, "actual_models": sorted(set(models)), "error": error,
                "missing_evidence": list(dict.fromkeys(missing)), "hypotheses": hypotheses,
                "evidence_memory": memory.stored(),
                "role_index": index + 1, "rounds": index + 1 if mode == "active" else 1,
                "steps": list(steps), "usage": budget.usage()}

    async def select_node(state: InvestigationState):
        nonlocal steps
        steps = list(state.get("steps", []))
        for key, value in state.get("usage", {}).items():
            if key in budget.usage():
                setattr(budget, key, max(getattr(budget, key), value))
        memory = EvidenceMemory(state.get("evidence_memory"))
        memory.bind(context)
        selections = list(state.get("evidence_selections", []))
        missing = list(state.get("missing_evidence", []))
        latest = state.get("proposals", [{}])[-1]
        plan = latest.get("evidence_plan")
        if plan is None and not latest.get("missing_evidence") and not any(
                h["status"] == "unresolved" for h in latest.get("hypotheses", [])):
            await emit({"event": "evidence_selection_skipped", "role": "selector",
                        "title": "模型未提出待区分解释，保留其结论供后续反证复核"})
            return {"steps": list(steps), "usage": budget.usage()}
        plan = plan or {"hypotheses": [], "queries": []}
        error = None
        try:
            selection = plan_evidence_query(plan, tools=planning_tools(context, memory),
                                            executed_query_keys=memory.queries.keys())
            brief = selection.public_brief
            await emit({"event": "evidence_selected", "role": "selector", "selection": brief,
                        "title": selection.decision.reason})
            selected = selection.decision.selected_query
            if selected is not None:
                budget.use_tool()
                await emit({"event": "tool_attempt", "role": "selector", "tool": selected.tool})
                output = memory.execute(context, selected.tool, selected.arguments)
                await emit({"event": "tool_completed", "role": "selector", "tool": selected.tool,
                            "ok": True, "cache_hit": output.get("cache_hit", False)})
                brief = {**brief, "execution": {"performed": True, "status": "executed",
                         "query_id": selected.query_id, "record_evidence_ids": output.get("record_evidence_ids", []),
                         'policy_check_ids': output.get('policy_check_ids', [])},
                         "notice": "已执行当前快照受控查询；结果在共享证据中。预期分区仍属模型建议，不是业务事实。"}
            else:
                request = selection.decision.evidence_request
                budget.use_tool()
                await emit({"event": "tool_attempt", "role": "selector", "tool": "request_evidence"})
                memory.execute(context, "request_evidence", request)
                missing.append(request["description"])
                await emit({"event": "tool_completed", "role": "selector", "tool": "request_evidence", "ok": True})
                brief = {**brief, "execution": {"performed": False, "status": "evidence_requested",
                         "requires_new_snapshot": True, "retrieved": False}}
            selections.append(brief)
        except (PlanningRejected, ValueError) as exc:
            error = {"code": "evidence_plan_invalid", "message": "主动查询计划未通过服务端校验"}
            if isinstance(exc, PlanningRejected):
                error["diagnostic"] = exc.code
            await emit({"event": "investigation_failed", "role": "selector", "error": error})
        except GatewayError as exc:
            error = {"code": exc.code, "message": str(exc)}
            await emit({"event": "investigation_failed", "role": "selector", "error": error})
        return {"evidence_memory": memory.stored(), "evidence_selections": selections,
                "missing_evidence": list(dict.fromkeys(missing)), "error": error,
                "steps": list(steps), "usage": budget.usage()}

    def choose_next(state: InvestigationState):
        if state.get("error"):
            return "finalize"
        target = {"single": 1, "multi": len(fixed_roles), "active": 3}.get(mode, 0)
        if state.get("role_index", 0) >= target:
            return "finalize"
        return "select" if mode == "active" and ablation != "no_active_selection" else "model"

    async def finalize(state: InvestigationState):
        nonlocal steps
        steps = list(state.get("steps", []))
        error = state.get("error")
        status = ("budget_exhausted" if error["code"] == "budget_exhausted" else "failed") if error else state["base_status"]
        await emit({"event": "investigation_completed", "title": "保存复核底稿", "status": status})
        result = {"status": status, "fact_status": state["base_status"], "mode": mode,
                  "claims": state.get("claims", []), "proposed_claims": [c for p in state.get("proposals", []) for c in p["claims"]],
                  'policy_interpretations': [dict(item, role=p['role']) for p in state.get('proposals', [])
                                             for item in p.get('policy_interpretations', [])],
                  'policy_check_context': deepcopy(context.policy_check_manifest),
                  'policy_check_cache_count': len(state.get('evidence_memory', {}).get('policy_checks', {})),
                  "proposals": state.get("proposals", []), "hypotheses": state.get("hypotheses", []),
                  "missing_evidence": state.get("missing_evidence", []), "steps": list(steps),
                  "usage": budget.usage(), "model": ",".join(state.get("actual_models", [])) or None,
                  "error": error, "framework": policy["framework"], "policy_version": policy.get("version"),
                  "execution_rules": rule_identity(),
                  "evidence_selections": state.get("evidence_selections", []),
                  "program_observations": state.get("evidence_memory", {}).get("program_observations", []),
                  "model_requests": [{k: s.get(k) for k in (
                      "request_index", "request_usage", "provider_model", "provider_system_fingerprint",
                      "context_bytes", "response_token_limit")}
                      for s in steps if s["event"] == "model_usage"],
                  "evidence_cache": {"records": len(state.get("evidence_memory", {}).get("records", {})),
                                     "queries": len(state.get("evidence_memory", {}).get("queries", {})),
                                     "hits": sum(s.get("cache_hit") is True for s in steps)},
                  "fingerprint": fingerprint, "prompt_version": PROMPT_VERSION,
                  "configured_model": configured_model, "model_parameters": dict(MODEL_PARAMETERS),
                  "ablation": ablation, "evaluation_only": ablation is not None,
                  "tool_version": TOOL_VERSION, "resumable": checkpoint_config.get("checkpointer") is not None,
                  "notice": "本结果属于研究与复核辅助；模型建议不自动转为会计事实或正式记账"}
        return {"result": result, "steps": list(steps), "usage": budget.usage()}

    builder = StateGraph(InvestigationState)
    builder.add_node("detect", detect)
    builder.add_node("model", model_node)
    builder.add_node("select", select_node)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "detect")
    builder.add_edge("detect", "finalize" if mode == "rules" else "model")
    builder.add_conditional_edges("model", choose_next, {"model": "model", "select": "select", "finalize": "finalize"})
    builder.add_conditional_edges("select", lambda state: "finalize" if state.get("error") else "model",
                                 {"model": "model", "finalize": "finalize"})
    builder.add_edge("finalize", END)
    graph = builder.compile(checkpointer=checkpoint_config.get("checkpointer"))
    config = checkpoint_config.get("config", {})
    initial: dict | None = {"fingerprint": fingerprint, "role_index": 0, "rounds": 0,
                            "proposals": [], "steps": [], "usage": budget.usage(), "actual_models": [], "error": None,
                            "evidence_memory": {}, "evidence_selections": [], "claims": [],
                            "hypotheses": [], "missing_evidence": []}
    if checkpoint_config.get("resume") and checkpoint_config.get("checkpointer") is None:
        raise ValueError("Resume requires a persistent checkpointer")
    if checkpoint_config.get("checkpointer") is not None:
        saved = await graph.aget_state(config)
        if saved.values:
            if not checkpoint_config.get("resume"):
                raise ValueError("Checkpoint thread already exists; use resume or a new run thread")
            if saved.values.get("fingerprint") != fingerprint:
                raise ValueError("Checkpoint belongs to a different snapshot, policy, finding, or mode")
            for key, value in saved.values.get("usage", {}).items():
                if key in budget.usage():
                    setattr(budget, key, max(getattr(budget, key), value))
            if not saved.next and saved.values.get("result"):
                return saved.values["result"]
            initial = None
    final = await graph.ainvoke(initial, config={**config, "recursion_limit": 30})
    return final["result"]
