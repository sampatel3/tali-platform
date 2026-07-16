"""Canonical contracts for tools shared by MCP and Taali Chat.

The public MCP server and the in-product chat have different transport and
authentication plumbing, but a shared tool must have one name, description,
input contract, risk classification, and persistence policy.  This catalogue
is that source of truth.  Transport adapters may expose a filtered subset.

Role-agent and autonomous-runtime tools are intentionally not folded in yet:
many of those tools have role-bound mutation semantics that first need durable
invocation receipts.  New shared read tools should be added here, not declared
again in ``taali_chat/tool_registry.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from ..models.api_key import (
    SCOPE_APPLICATIONS_READ,
    SCOPE_ASSESSMENTS_READ,
    SCOPE_ROLES_READ,
)


PUBLIC_MCP = "public_mcp"
TAALI_CHAT = "taali_chat"


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
CandidateReportQuery = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
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


class GetApplicationInput(ToolInput):
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


class CreateTopCandidatesReportInput(ToolInput):
    role_id: PositiveInt = Field(
        description="Role whose exact candidate shortlist will be published."
    )
    query: CandidateReportQuery
    limit: TopCandidateLimit = 10
    rank_by: ScoreType = "taali"
    confirmation_token: ConfirmationToken | None = Field(
        default=None,
        description="Opaque token from the exact server preview, when available.",
    )


class CreateScreenPoolReportInput(ToolInput):
    role_id: PositiveInt = Field(
        description="Role whose exact rediscovery result will be published."
    )
    requirement_text: CandidateReportQuery
    limit: PoolCandidateLimit = 20
    offset: NonNegativeInt = 0
    deep_verify: bool = Field(
        default=False,
        description="Re-run bounded CV evidence checks for the published snapshot.",
    )
    confirmation_token: ConfirmationToken | None = Field(
        default=None,
        description="Opaque token from the exact server preview, when available.",
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


class ListAgentDecisionsInput(ToolInput):
    role_id: PositiveInt | None = None
    status: DecisionStatus | None = None
    limit: PageLimit = 20


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
        description="The original ATS-linked role whose candidate roster will be shared."
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

    @property
    def input_schema(self) -> dict[str, Any]:
        return _compact_schema(self.input_model.model_json_schema())

    def anthropic_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
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
        "get_application",
        "Fetch one application with scores, evidence, rejection context, ATS state, and recruiter notes.",
        GetApplicationInput,
        "get_application",
        _BOTH,
        _APPLICATIONS_READ,
        persistence="sensitive",
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
        "Default for bounded qualitative candidate discovery, even without top/best wording. Returns a score-ranked shortlist (default 10) with available criterion verdicts/evidence, explicit criteria and grounding coverage. This is a pure read and never publishes a report; use create_top_candidates_report only after an explicit sharing request. Use query='candidates' for a bare top-N result; role scorecard evidence is reused when available.",
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
        "Search the scored candidate history against a new requirement. Deep verification is optional and bounded. This is a pure read and never publishes a report; use create_screen_pool_report only after an explicit sharing request.",
        ScreenPoolInput,
        "screen_pool_against_requirement",
        _CHAT,
        _APPLICATIONS_READ,
        cost="paid",
        persistence="sensitive",
        renderer="candidate_evidence",
    ),
    ToolSpec(
        "create_top_candidates_report",
        "Publish a previously reviewed role-scoped top-candidate shortlist as a PII-scrubbed, read-only 30-day bearer report. The first call only recomputes and previews the exact snapshot; creation requires explicit recruiter confirmation in a later message and revalidation at execution.",
        CreateTopCandidatesReportInput,
        "create_top_candidates_report",
        _CHAT,
        _RELATED_ROLE_ACCESS,
        effect="external_write",
        cost="paid",
        confirmation="explicit",
        persistence="sensitive",
        renderer="candidate_report",
    ),
    ToolSpec(
        "create_screen_pool_report",
        "Publish a previously reviewed, role-scoped rediscovery result as a PII-scrubbed, read-only 30-day bearer report. The first call only recomputes and previews the exact snapshot; creation requires explicit recruiter confirmation in a later message and revalidation at execution.",
        CreateScreenPoolReportInput,
        "create_screen_pool_report",
        _CHAT,
        _RELATED_ROLE_ACCESS,
        effect="external_write",
        cost="paid",
        confirmation="explicit",
        persistence="sensitive",
        renderer="candidate_report",
    ),
    ToolSpec(
        "nl_search_candidates",
        "Exhaustive, person-deduplicated retrieval for explicit all/every requests over normalized fields and indexed CV text. Reports database vs verification coverage; unchecked qualitative matches must not be described as passed or failed. Optional bounded verification and graph context.",
        NaturalLanguageSearchInput,
        "nl_search_candidates",
        _BOTH,
        _APPLICATIONS_READ,
        cost="paid",
        renderer="candidate_search",
    ),
    ToolSpec(
        "graph_search_candidates",
        "Search the organization's temporal candidate graph and return matching facts plus an inline subgraph when available.",
        GraphSearchInput,
        "graph_search_candidates",
        _BOTH,
        _APPLICATIONS_READ,
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
        "List recent autonomous-agent decisions, including status, reasoning, evidence, and recruiter resolution.",
        ListAgentDecisionsInput,
        "list_recent_agent_decisions",
        _CHAT,
        _APPLICATIONS_READ,
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
        _BOTH,
        _RECRUITING_OVERVIEW_READ,
        renderer="recruiting_overview",
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
        "Preview a separate related Taali role over an original ATS-linked role's existing applicants using a complete alternate job specification. Returns the shared-roster size, scorable count, and estimated AI usage without creating anything. Show the preview and wait for a later explicit recruiter confirmation before creating it.",
        PreviewRelatedRoleInput,
        "preview_related_role",
        _CHAT,
        _RELATED_ROLE_ACCESS,
        renderer="related_role_preview",
    ),
    ToolSpec(
        "create_related_role",
        "Create a previously previewed related role and queue fresh scores for its shared roster. Candidate stages and actions remain coupled to the original ATS role. The server requires explicit recruiter confirmation in a later message.",
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
    "PUBLIC_MCP",
    "TAALI_CHAT",
    "TOOL_SPECS",
    "TOOL_SPEC_BY_NAME",
    "ToolSpec",
    "get_tool_spec",
    "tools_for",
]
