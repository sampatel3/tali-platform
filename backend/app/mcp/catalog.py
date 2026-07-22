"""Canonical contracts for tools shared by MCP and Taali Chat.

The public MCP server and the in-product chat have different transport and
authentication plumbing, but a shared tool must have one name, description,
input contract, risk classification, and persistence policy.  This catalogue
is that source of truth.  Transport adapters may expose a filtered subset.

Role-agent and autonomous-runtime mutations retain their own confirmation and
governance plumbing. Their authoritative candidate reads are generated from
this catalogue, so every model-facing surface shares one typed contract.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from ..models.api_key import (
    SCOPE_APPLICATIONS_READ,
    SCOPE_ASSESSMENTS_READ,
    SCOPE_ROLES_READ,
)


PUBLIC_MCP = "public_mcp"
TAALI_CHAT = "taali_chat"
AGENT_CHAT = "agent_chat"
AUTONOMOUS_AGENT = "autonomous_agent"

CANDIDATE_POOL_STATE = "candidate.pool_state"
CANDIDATE_DETAIL = "candidate.detail"
CANDIDATE_ACTION_HISTORY = "candidate.action_history"
CANDIDATE_DECISION_HISTORY = "candidate.decision_history"


class ToolInput(BaseModel):
    """Strict base contract for model-generated tool arguments."""

    model_config = ConfigDict(extra="forbid", strict=True)


# Reused by the Pydantic models and the flat FastMCP adapter signatures. This
# keeps client-visible bounds/enums and runtime validation semantically aligned
# without relying on FastMCP private internals.
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PageLimit = Annotated[int, Field(ge=1, le=100)]
TopCandidateLimit = Annotated[int, Field(ge=1, le=25)]
PoolCandidateLimit = Annotated[int, Field(ge=1, le=50)]
NonEmptyString = Annotated[str, Field(min_length=1)]
ScoreThreshold = Annotated[float, Field(ge=0, le=100)]
ComparisonApplicationIds = Annotated[
    list[PositiveInt], Field(min_length=2, max_length=5)
]
RelatedRoleName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
RelatedRoleJobSpec = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=80, max_length=100_000)
]
ConfirmationToken = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]


class ListRolesInput(ToolInput):
    include_stage_counts: bool = Field(
        default=False, description="Include open-application counts per pipeline stage."
    )


class GetRoleInput(ToolInput):
    role_id: PositiveInt


PipelineStage = Literal[
    "sourced", "applied", "invited", "in_assessment", "review", "advanced"
]
ApplicationOutcome = Literal["open", "rejected", "withdrawn", "hired"]
ScoreType = Literal[
    "taali", "pre_screen", "rank", "cv_match", "workable", "assessment", "role_fit"
]
SortBy = Literal[
    "taali_score",
    "pre_screen_score",
    "rank_score",
    "cv_match_score",
    "workable_score",
    "assessment_score",
    "role_fit_score",
    "created_at",
]
SortOrder = Literal["desc", "asc"]


class SearchApplicationsInput(ToolInput):
    role_id: PositiveInt | None = None
    min_score: ScoreThreshold | None = Field(
        default=None, description="Threshold on a 0-100 scale; 0-10 is auto-scaled."
    )
    score_type: ScoreType = "taali"
    pipeline_stage: PipelineStage | None = None
    application_outcome: ApplicationOutcome | None = "open"
    q: str | None = Field(
        default=None, description="Simple name, email, or position text match only."
    )
    sort_by: SortBy = "taali_score"
    sort_order: SortOrder = "desc"
    limit: PageLimit = 25
    offset: NonNegativeInt = 0


class SearchRoleCandidatesInput(SearchApplicationsInput):
    """Exact, role-bound candidate-pool query used by every agent surface."""

    role_id: PositiveInt
    ats_stage: str | None = Field(
        default=None,
        description=(
            "Case-insensitive match against the current provider stage/status "
            "or its normalized value."
        ),
    )


class GetApplicationInput(ToolInput):
    application_id: PositiveInt
    include_cv_text: bool = False


class GetRoleCandidateInput(ToolInput):
    role_id: PositiveInt
    application_id: PositiveInt
    include_cv_text: bool = False


class GetCandidateInput(ToolInput):
    candidate_id: PositiveInt


class CompareApplicationsInput(ToolInput):
    application_ids: ComparisonApplicationIds


class FindTopCandidatesInput(ToolInput):
    query: NonEmptyString
    limit: TopCandidateLimit = 10
    rank_by: ScoreType = "taali"
    role_id: PositiveInt | None = None


class ScreenPoolInput(ToolInput):
    requirement_text: NonEmptyString = Field(
        description="The new requirement or mini job specification."
    )
    limit: PoolCandidateLimit = 20
    offset: NonNegativeInt = 0
    role_id: PositiveInt | None = None
    deep_verify: bool = Field(
        default=False, description="Opt in to bounded per-candidate CV evidence checks."
    )


class NaturalLanguageSearchInput(ToolInput):
    query: NonEmptyString
    role_id: PositiveInt | None = None
    deep_verify: bool = False
    include_graph: bool = False
    limit: PageLimit = 25
    offset: NonNegativeInt = 0


class GraphSearchInput(ToolInput):
    query: NonEmptyString
    limit: PageLimit = 25
    role_id: PositiveInt | None = None


class GetCandidateCVInput(ToolInput):
    candidate_id: PositiveInt


DecisionStatus = Literal[
    "pending",
    "processing",
    "approved",
    "overridden",
    "reverted_for_feedback",
    "discarded",
    "expired",
]


AgentDecisionType = Literal[
    "advance_to_interview",
    "reject",
    "skip_assessment_reject",
    "send_assessment",
    "resend_assessment_invite",
    "escalate_low_confidence",
]


class ListAgentDecisionsInput(ToolInput):
    role_id: PositiveInt | None = None
    status: DecisionStatus | None = None
    application_id: PositiveInt | None = None
    candidate_id: PositiveInt | None = None
    decision_type: AgentDecisionType | None = None
    created_after: datetime | None = Field(default=None, strict=False)
    created_before: datetime | None = Field(default=None, strict=False)
    resolved_after: datetime | None = Field(default=None, strict=False)
    resolved_before: datetime | None = Field(default=None, strict=False)
    limit: PageLimit = 20
    offset: NonNegativeInt = 0


CandidateAction = Literal[
    "advanced",
    "rejected",
    "hired",
    "withdrawn",
    "assessment_sent",
    "assessment_resent",
    "ats_moved",
]
CandidateActionStatus = Literal["confirmed", "failed", "skipped"]
CandidateActionActor = Literal["recruiter", "agent", "system", "sync"]


class ListCandidateActionsInput(ToolInput):
    role_id: PositiveInt
    application_id: PositiveInt | None = None
    candidate_id: PositiveInt | None = None
    action: CandidateAction | None = None
    target_stage: str | None = None
    status: CandidateActionStatus = "confirmed"
    actor_type: CandidateActionActor | None = None
    occurred_after: datetime | None = Field(default=None, strict=False)
    occurred_before: datetime | None = Field(default=None, strict=False)
    limit: PageLimit = 50
    offset: NonNegativeInt = 0


class ListAgentRunsInput(ToolInput):
    role_id: PositiveInt | None = None
    trigger: Literal["event", "cron", "manual"] | None = None
    limit: PageLimit = 20


class ExplainAgentDecisionInput(ToolInput):
    decision_id: PositiveInt


class RecruitingOverviewInput(ToolInput):
    role_id: PositiveInt | None = Field(
        default=None,
        description="Restrict the overview to one role.",
    )


AssessmentStatus = Literal[
    "pending", "in_progress", "completed", "completed_due_to_timeout", "expired"
]
AssessmentAttention = Literal[
    "any",
    "needs_attention",
    "none",
    "expiring_soon",
    "delivery_failed",
    "scoring_pending",
    "scoring_failed",
]


class ListAssessmentsInput(ToolInput):
    status: AssessmentStatus | None = None
    role_id: PositiveInt | None = None
    attention: AssessmentAttention = "any"
    limit: PageLimit = 25
    offset: NonNegativeInt = 0


class PreviewRelatedRoleInput(ToolInput):
    role_id: PositiveInt = Field(
        description=(
            "The logical source role whose current explicit candidate pool is "
            "copied once. It may be a standard ATS-linked role or an existing "
            "related role; this does not create future fan-out."
        )
    )
    name: RelatedRoleName = Field(description="Name for the new related role.")
    job_spec_text: RelatedRoleJobSpec = Field(
        description="The complete related job specification, not only its differences."
    )


class CreateRelatedRoleInput(PreviewRelatedRoleInput):
    confirmation_token: ConfirmationToken | None = Field(
        default=None,
        description="Opaque token from the server preview, when available.",
    )


def _compact_schema(value: Any) -> Any:
    """Drop display-only JSON-schema titles to reduce prompt tokens."""

    if isinstance(value, dict):
        return {
            key: _compact_schema(item)
            for key, item in value.items()
            if key != "title"
        }
    if isinstance(value, list):
        return [_compact_schema(item) for item in value]
    return value


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[ToolInput]
    handler_name: str
    exposures: frozenset[str]
    required_scopes: frozenset[str]
    effect: Literal["read", "internal_write", "external_write", "destructive"] = "read"
    cost: Literal["free", "paid"] = "free"
    confirmation: Literal["none", "explicit"] = "none"
    execution: Literal["synchronous", "queued"] = "synchronous"
    persistence: Literal["standard", "sensitive", "ephemeral"] = "standard"
    renderer: str = "generic"
    capabilities: frozenset[str] = frozenset()
    role_scoped: bool = False

    @property
    def input_schema(self) -> dict[str, Any]:
        return _compact_schema(self.input_model.model_json_schema())

    def anthropic_definition(self, *, bound_role: bool = False) -> dict[str, Any]:
        schema = self.input_schema
        if bound_role and self.role_scoped:
            schema = deepcopy(schema)
            properties = schema.get("properties")
            if isinstance(properties, dict):
                properties.pop("role_id", None)
            required = schema.get("required")
            if isinstance(required, list):
                remaining = [item for item in required if item != "role_id"]
                if remaining:
                    schema["required"] = remaining
                else:
                    schema.pop("required", None)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }

    def validate(self, arguments: dict[str, Any] | None) -> dict[str, Any]:
        if arguments is None:
            arguments = {}
        elif not isinstance(arguments, dict):
            raise ValueError(
                f"invalid arguments for {self.name}: expected an object"
            )
        try:
            parsed = self.input_model.model_validate(arguments)
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}"
                for error in exc.errors(include_url=False)
            )
            raise ValueError(f"invalid arguments for {self.name}: {details}") from exc
        return parsed.model_dump(exclude_unset=True)


_BOTH = frozenset({PUBLIC_MCP, TAALI_CHAT})
_CHAT = frozenset({TAALI_CHAT})
_ALL_AGENT_READS = frozenset(
    {PUBLIC_MCP, TAALI_CHAT, AGENT_CHAT, AUTONOMOUS_AGENT}
)
_ROLES_READ = frozenset({SCOPE_ROLES_READ})
_APPLICATIONS_READ = frozenset({SCOPE_APPLICATIONS_READ})
_ASSESSMENTS_READ = frozenset({SCOPE_ASSESSMENTS_READ})
_RECRUITING_OVERVIEW_READ = frozenset(
    {SCOPE_ROLES_READ, SCOPE_APPLICATIONS_READ, SCOPE_ASSESSMENTS_READ}
)
_RELATED_ROLE_ACCESS = frozenset({SCOPE_ROLES_READ, SCOPE_APPLICATIONS_READ})


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "list_roles",
        "List roles and their lifecycle state in the organization. Use first to discover role IDs; optionally include per-stage application counts.",
        ListRolesInput,
        "list_roles",
        _BOTH,
        _ROLES_READ,
    ),
    ToolSpec(
        "get_role",
        "Fetch one role with its job specification, recruiter criteria, and open pipeline counts.",
        GetRoleInput,
        "get_role",
        _BOTH,
        _ROLES_READ,
    ),
    ToolSpec(
        "search_applications",
        "Filter applications by score, pipeline stage, outcome, or simple name/email/position text. Use natural-language search for skills or experience.",
        SearchApplicationsInput,
        "search_applications",
        _BOTH,
        _APPLICATIONS_READ,
        renderer="candidate_grid",
    ),
    ToolSpec(
        "search_role_candidates",
        "Search one role's exact logical candidate pool by current role-local state, ATS stage, score, outcome, or simple identity text. Returns exact totals and current state; use qualitative search for skills or experience.",
        SearchRoleCandidatesInput,
        "search_role_candidates",
        _ALL_AGENT_READS,
        _APPLICATIONS_READ,
        renderer="candidate_grid",
        capabilities=frozenset({CANDIDATE_POOL_STATE}),
        role_scoped=True,
    ),
    ToolSpec(
        "get_application",
        "Fetch one application with scores, evidence, rejection context, ATS state, and recruiter notes.",
        GetApplicationInput,
        "get_application",
        _BOTH,
        _APPLICATIONS_READ,
        persistence="sensitive",
    ),
    ToolSpec(
        "get_role_candidate",
        "Fetch one candidate application as it exists in one logical role, including role-local score/evidence, current pipeline and ATS state, restrictions, and recruiter notes.",
        GetRoleCandidateInput,
        "get_role_candidate",
        _ALL_AGENT_READS,
        _APPLICATIONS_READ,
        persistence="sensitive",
        capabilities=frozenset({CANDIDATE_DETAIL, CANDIDATE_POOL_STATE}),
        role_scoped=True,
    ),
    ToolSpec(
        "get_candidate",
        "Fetch a candidate profile and their applications across every role in the organization.",
        GetCandidateInput,
        "get_candidate",
        _BOTH,
        _APPLICATIONS_READ,
    ),
    ToolSpec(
        "compare_applications",
        "Compare two to five applications on a common scorecard before recommending who should advance.",
        CompareApplicationsInput,
        "compare_applications",
        _BOTH,
        _APPLICATIONS_READ,
        renderer="comparison",
    ),
    ToolSpec(
        "find_top_candidates",
        "Default for bounded qualitative candidate discovery, even without top/best wording. Unhedged qualities are required; only explicit ideally/prefer/nice-to-have wording is optional. Returns verified required matches ranked by grounded constraints/preferences and query relevance; any existing role score is shown separately as context, not evidence for the search requirement. Includes explicit criterion verdicts/coverage and an unguessable 30-day read-only bearer report link. The query must be self-contained, including any title/population retained from a follow-up. Use query='candidates' for a bare top-N report; role scorecard evidence is reused when available.",
        FindTopCandidatesInput,
        "find_top_candidates",
        _CHAT,
        _APPLICATIONS_READ,
        cost="paid",
        persistence="sensitive",
        renderer="candidate_evidence",
    ),
    ToolSpec(
        "screen_pool_against_requirement",
        "Search the scored candidate history against a new requirement. Deep verification is optional and bounded.",
        ScreenPoolInput,
        "screen_pool_against_requirement",
        _CHAT,
        _APPLICATIONS_READ,
        cost="paid",
        persistence="sensitive",
        renderer="candidate_evidence",
    ),
    ToolSpec(
        "nl_search_candidates",
        "Person-deduplicated hybrid retrieval for explicit all/every requests over normalized fields, indexed CV text, and source-backed graph recall. Reports separate PostgreSQL and fused retrieval counts plus capped/exhaustive/is_exact_empty. Say that no candidates exist only when is_exact_empty=true; otherwise say no candidates were retrieved and disclose the partial/unavailable coverage warning. Unchecked qualitative matches must not be described as passed or failed. Optional bounded verification and graph context.",
        NaturalLanguageSearchInput,
        "nl_search_candidates",
        _BOTH,
        _APPLICATIONS_READ,
        cost="paid",
        renderer="candidate_search",
    ),
    ToolSpec(
        "graph_search_candidates",
        "Graph-oriented view over the same person-deduplicated hybrid candidate search used elsewhere. Uses PostgreSQL as the organization/role authorization boundary, admits graph hits only when backed by original source evidence, and returns coverage, exact-empty state, evidence references, and an inline topology when available. graph_facts are generated visual context and are never citations; use only evidence references to ground a claim. Exact colleague and multi-hop paths fail closed until a parameterized path retriever is available.",
        GraphSearchInput,
        "graph_search_candidates",
        _BOTH,
        _APPLICATIONS_READ,
        cost="paid",
        renderer="candidate_graph",
    ),
    ToolSpec(
        "get_candidate_cv",
        "Fetch parsed CV sections and raw extracted CV text for one candidate when exact evidence is necessary.",
        GetCandidateCVInput,
        "get_candidate_cv",
        _BOTH,
        _APPLICATIONS_READ,
        persistence="sensitive",
    ),
    ToolSpec(
        "list_recent_agent_decisions",
        "Audit autonomous-agent recommendations and recruiter resolutions. Use created dates for when the agent recommended something and resolved dates for when a recruiter approved or overrode it. This is not proof that a candidate movement completed; use list_candidate_actions for confirmed actions.",
        ListAgentDecisionsInput,
        "list_recent_agent_decisions",
        _ALL_AGENT_READS,
        _APPLICATIONS_READ,
        capabilities=frozenset({CANDIDATE_DECISION_HISTORY}),
        role_scoped=True,
    ),
    ToolSpec(
        "list_candidate_actions",
        "List confirmed or failed candidate workflow actions for one logical role, with exact totals, occurrence time, target stage/outcome, actor, linked decision when available, and each candidate's current state. Use for 'who was advanced/rejected/moved/sent an assessment and when'; pending recommendations are not completed actions.",
        ListCandidateActionsInput,
        "list_candidate_actions",
        _ALL_AGENT_READS,
        _APPLICATIONS_READ,
        capabilities=frozenset({CANDIDATE_ACTION_HISTORY}),
        role_scoped=True,
    ),
    ToolSpec(
        "list_recent_agent_runs",
        "List recent autonomous cycles with trigger, status, tools, decisions, spend, and errors.",
        ListAgentRunsInput,
        "list_recent_agent_runs",
        _CHAT,
        _APPLICATIONS_READ,
    ),
    ToolSpec(
        "explain_agent_decision",
        "Explain one agent decision and the cycle that produced it, including evidence and model/prompt versions.",
        ExplainAgentDecisionInput,
        "explain_agent_decision",
        _CHAT,
        _APPLICATIONS_READ,
    ),
    ToolSpec(
        "get_recruiting_overview",
        "Summarize recruiting operations for the organization or one role: roles, candidates, application funnel, assessment statuses, and attention counts.",
        RecruitingOverviewInput,
        "get_recruiting_overview",
        _ALL_AGENT_READS,
        _RECRUITING_OVERVIEW_READ,
        renderer="recruiting_overview",
        capabilities=frozenset({CANDIDATE_POOL_STATE}),
        role_scoped=True,
    ),
    ToolSpec(
        "list_assessments",
        "List a safe assessment work queue by status, role, or attention condition such as expiring invitations, delivery failures, or scoring failures.",
        ListAssessmentsInput,
        "list_assessments",
        _BOTH,
        _ASSESSMENTS_READ,
        renderer="assessment_queue",
    ),
    ToolSpec(
        "preview_related_role",
        "Preview a separate related Taali role seeded once from the selected logical role's explicit current pool (including another related role). Returns the initial-roster size, scorable count, and estimated AI usage without creating anything. The optional ATS owner is transport only. Show the preview and wait for a later explicit recruiter confirmation before creating it.",
        PreviewRelatedRoleInput,
        "preview_related_role",
        _CHAT,
        _RELATED_ROLE_ACCESS,
        renderer="related_role_preview",
    ),
    ToolSpec(
        "create_related_role",
        "Create a previously previewed related role and queue fresh scores for its explicit roster. It is an independent logical role with role-local candidate state and action history; any shared ATS linkage appears only as operational context and action restrictions. The server requires explicit recruiter confirmation in a later message.",
        CreateRelatedRoleInput,
        "create_related_role",
        _CHAT,
        _RELATED_ROLE_ACCESS,
        effect="internal_write",
        cost="paid",
        confirmation="explicit",
        execution="queued",
        renderer="related_role_created",
    ),
)


TOOL_SPEC_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_SPECS}


def tools_for(exposure: str) -> list[ToolSpec]:
    return [spec for spec in TOOL_SPECS if exposure in spec.exposures]


def get_tool_spec(name: str) -> ToolSpec:
    try:
        return TOOL_SPEC_BY_NAME[name]
    except KeyError as exc:
        raise KeyError(f"unknown tool: {name}") from exc


__all__ = [
    "AGENT_CHAT",
    "AUTONOMOUS_AGENT",
    "CANDIDATE_ACTION_HISTORY",
    "CANDIDATE_DECISION_HISTORY",
    "CANDIDATE_DETAIL",
    "CANDIDATE_POOL_STATE",
    "PUBLIC_MCP",
    "TAALI_CHAT",
    "TOOL_SPECS",
    "TOOL_SPEC_BY_NAME",
    "ToolSpec",
    "get_tool_spec",
    "tools_for",
]
