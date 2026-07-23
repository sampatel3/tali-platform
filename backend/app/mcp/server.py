"""FastMCP server: read-only tools + resources for Tali.

Mounted under ``/mcp`` on the main FastAPI app. Each tool authenticates
the bearer JWT off the inbound request, opens a sync DB session, and
delegates to a pure-function handler in ``handlers.py``. Org-scoping is
enforced inside the handlers via ``user.organization_id``.

Adding a tool: define its contract/exposures in ``catalog.py``, add the
implementation to ``handlers.py`` or ``operations.py``, then register a thin
authenticated wrapper here. Taali Chat and public MCP share the catalog and
handler; this module only adapts the public transport.
"""

# NOTE: do NOT add ``from __future__ import annotations`` — FastMCP's tool
# decorator does ``issubclass(param.annotation, Context)`` to detect the
# Context-injection convention, which only works when annotations are real
# classes rather than stringified PEP 563 forward references.

from datetime import datetime
from typing import Any, Literal, Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy.orm import Session

from ..models.api_key import SCOPE_APPLICATIONS_READ, SCOPE_ROLES_READ
from ..models.candidate import Candidate
from ..models.role import Role
from ..platform.database import SessionLocal
from ..services.role_criteria_service import render_role_intent_block
from . import handlers, operations
from .auth import MCPAuthError, authenticate_request, enforce_scope
from .catalog import (
    ApplicationOutcome,
    AssessmentAttention,
    AssessmentStatus,
    AgentDecisionType,
    CandidateAction,
    CandidateActionActor,
    CandidateActionStatus,
    ComparisonApplicationIds,
    DecisionStatus,
    NonEmptyString,
    NonNegativeInt,
    PageLimit,
    PipelineStage,
    PositiveInt,
    ScoreThreshold,
    ScoreType,
    SortBy,
    SortOrder,
    TopCandidateLimit,
    get_tool_spec,
)


_INSTRUCTIONS = """Read-only access to Tali's recruiting data for the
authenticated user's organization.

Pipeline stages: sourced -> applied -> invited -> in_assessment -> review -> advanced.
Application outcomes: open, rejected, withdrawn, hired.

The default score (``taali``) is the merged primary score on a 0-100 scale.
``pre_screen`` is a cheap LLM gating score, ``rank`` is the pairwise rank
score, ``cv_match`` is the CV/job-spec similarity score. Use ``taali`` for
"score above X" questions unless the user specifies otherwise. ``workable``
filters accept 0-10 or 0-100 input; results include both the stored 0-10
``workable_score`` and normalized ``workable_score_100``. ``assessment`` and
``role_fit`` expose their corresponding cached 0-100 scores when present.

For bounded qualitative queries ("who has hands-on Agentforce experience?"),
use the paid ``find_top_candidates`` tool. It searches the complete active
logical-role pool, verifies cited evidence, and distinguishes an exhaustive
negative from incomplete evidence. For explicit all/every retrieval, use
``nl_search_candidates`` rather than ``search_applications``. The latter only
matches identity/state fields. ``graph_search_candidates`` is the graph-oriented,
topology-returning view of the same authorized retrieval framework.

``get_application``, ``compare_applications``, and
``tali://application/{application_id}`` are legacy physical-record evidence
reads and intentionally omit logical-role state. Use ``get_role_candidate``,
``compare_role_applications``, or
``tali://role/{role_id}/application/{application_id}`` whenever score, pipeline,
outcome, membership, or a recommendation matters.

Every result includes a ``frontend_url`` the user can click to open the
matching page in the Tali web app.
"""


mcp_app = FastMCP(
    "tali",
    instructions=_INSTRUCTIONS,
    stateless_http=True,
    streamable_http_path="/",
    # This server is mounted under ``/mcp`` on the public FastAPI app and reached
    # through our own reverse proxy, so real clients (claude.ai) arrive with the
    # deployment's Host, not ``127.0.0.1``. FastMCP would otherwise auto-enable
    # DNS-rebinding protection (default host ``127.0.0.1``) and 421 every request
    # whose Host isn't localhost. Host validation belongs at our edge; disable the
    # library's rebinding check here so the mounted endpoint is reachable.
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)


# ---------------------------------------------------------------------------
# Per-tool plumbing: open a sync DB session and authenticate the request.
# ---------------------------------------------------------------------------


class _open_session:  # noqa: N801 — context-manager-as-class is intentional
    """Sync DB session + authenticated principal scoped to one MCP tool call.

    ``require_scopes`` gate API-key principals; JWT (session) principals are
    exempt. Handlers read only ``.organization_id`` off the returned principal.
    """

    def __init__(
        self,
        ctx: Context,
        require_scopes: str | frozenset[str],
    ) -> None:
        self._ctx = ctx
        self._require_scopes = (
            frozenset({require_scopes})
            if isinstance(require_scopes, str)
            else require_scopes
        )
        self._db: Session | None = None

    def __enter__(self) -> tuple[Session, Any]:
        self._db = SessionLocal()
        try:
            request = getattr(self._ctx.request_context, "request", None)
            if request is None:
                raise MCPAuthError("MCP context has no HTTP request bound")
            principal = authenticate_request(request, self._db)
            for scope in sorted(self._require_scopes):
                enforce_scope(principal, scope)
        except Exception:
            self._db.close()
            self._db = None
            raise
        return self._db, principal

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if self._db is not None:
            self._db.close()
            self._db = None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def _strip_application_counts(role_payload: dict[str, Any]) -> dict[str, Any]:
    """Remove funnel-volume fields from a role payload.

    ``applications_count`` / ``stage_counts`` are application metrics — the
    public REST API gates the analogous role-metrics endpoint behind
    ``applications:read``, so a roles-only key must not read them here either.
    """
    role_payload.pop("applications_count", None)
    role_payload.pop("stage_counts", None)
    return role_payload


def _catalog_tool(name: str):
    """Register a flat FastMCP adapter using the catalog's description."""

    return mcp_app.tool(name=name, description=get_tool_spec(name).description)


@_catalog_tool("list_roles")
def list_roles(
    ctx: Context,
    include_stage_counts: bool = False,
) -> list[dict[str, Any]]:
    args = get_tool_spec("list_roles").validate(
        {"include_stage_counts": include_stage_counts}
    )
    with _open_session(ctx, get_tool_spec("list_roles").required_scopes) as (db, user):
        can_read_applications = user.has_scope(SCOPE_APPLICATIONS_READ)
        roles = handlers.list_roles(
            db,
            user,
            include_stage_counts=args["include_stage_counts"] and can_read_applications,
        )
        if not can_read_applications:
            roles = [_strip_application_counts(r) for r in roles]
        return roles


@_catalog_tool("get_role")
def get_role(ctx: Context, role_id: PositiveInt) -> dict[str, Any]:
    args = get_tool_spec("get_role").validate({"role_id": role_id})
    with _open_session(ctx, get_tool_spec("get_role").required_scopes) as (db, user):
        role = handlers.get_role(db, user, **args)
        if not user.has_scope(SCOPE_APPLICATIONS_READ):
            role = _strip_application_counts(role)
        return role


@_catalog_tool("search_applications")
def search_applications(
    ctx: Context,
    role_id: Optional[PositiveInt] = None,
    min_score: Optional[ScoreThreshold] = None,
    score_type: ScoreType = "taali",
    pipeline_stage: Optional[PipelineStage] = None,
    application_outcome: Optional[ApplicationOutcome] = "open",
    q: Optional[str] = None,
    sort_by: SortBy = "taali_score",
    sort_order: SortOrder = "desc",
    limit: PageLimit = 25,
    offset: NonNegativeInt = 0,
) -> list[dict[str, Any]]:
    args = get_tool_spec("search_applications").validate(
        {
            "role_id": role_id,
            "min_score": min_score,
            "score_type": score_type,
            "pipeline_stage": pipeline_stage,
            "application_outcome": application_outcome,
            "q": q,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "limit": limit,
            "offset": offset,
        }
    )
    with _open_session(ctx, get_tool_spec("search_applications").required_scopes) as (
        db,
        user,
    ):
        return handlers.search_applications(db, user, **args)


@_catalog_tool("search_role_candidates")
def search_role_candidates(
    ctx: Context,
    role_id: PositiveInt,
    min_score: Optional[ScoreThreshold] = None,
    score_type: ScoreType = "taali",
    pipeline_stage: Optional[PipelineStage] = None,
    application_outcome: Optional[ApplicationOutcome] = "open",
    q: Optional[str] = None,
    sort_by: SortBy = "taali_score",
    sort_order: SortOrder = "desc",
    limit: PageLimit = 25,
    offset: NonNegativeInt = 0,
    ats_stage: Optional[str] = None,
    workable_stage: Optional[str] = None,
    has_pending_decision: Optional[bool] = None,
) -> dict[str, Any]:
    args = get_tool_spec("search_role_candidates").validate(
        {
            "role_id": role_id,
            "min_score": min_score,
            "score_type": score_type,
            "pipeline_stage": pipeline_stage,
            "application_outcome": application_outcome,
            "q": q,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "limit": limit,
            "offset": offset,
            "ats_stage": ats_stage,
            "workable_stage": workable_stage,
            "has_pending_decision": has_pending_decision,
        }
    )
    with _open_session(
        ctx, get_tool_spec("search_role_candidates").required_scopes
    ) as (db, user):
        return handlers.search_role_candidates(db, user, **args)


@_catalog_tool("get_application")
def get_application(
    ctx: Context,
    application_id: PositiveInt,
    include_cv_text: bool = False,
) -> dict[str, Any]:
    args = get_tool_spec("get_application").validate(
        {"application_id": application_id, "include_cv_text": include_cv_text}
    )
    with _open_session(ctx, get_tool_spec("get_application").required_scopes) as (
        db,
        user,
    ):
        return handlers.get_application(db, user, **args)


@_catalog_tool("get_role_candidate")
def get_role_candidate(
    ctx: Context,
    role_id: PositiveInt,
    application_id: PositiveInt,
    include_cv_text: bool = False,
) -> dict[str, Any]:
    args = get_tool_spec("get_role_candidate").validate(
        {
            "role_id": role_id,
            "application_id": application_id,
            "include_cv_text": include_cv_text,
        }
    )
    with _open_session(ctx, get_tool_spec("get_role_candidate").required_scopes) as (
        db,
        user,
    ):
        return handlers.get_role_candidate(db, user, **args)


@_catalog_tool("get_candidate")
def get_candidate(ctx: Context, candidate_id: PositiveInt) -> dict[str, Any]:
    args = get_tool_spec("get_candidate").validate({"candidate_id": candidate_id})
    with _open_session(ctx, get_tool_spec("get_candidate").required_scopes) as (
        db,
        user,
    ):
        return handlers.get_candidate(db, user, **args)


@_catalog_tool("compare_applications")
def compare_applications(
    ctx: Context,
    application_ids: ComparisonApplicationIds,
) -> dict[str, Any]:
    args = get_tool_spec("compare_applications").validate(
        {"application_ids": application_ids}
    )
    with _open_session(ctx, get_tool_spec("compare_applications").required_scopes) as (
        db,
        user,
    ):
        return handlers.compare_applications(db, user, **args)


@_catalog_tool("compare_role_applications")
def compare_role_applications(
    ctx: Context,
    application_ids: ComparisonApplicationIds,
    role_id: PositiveInt,
) -> dict[str, Any]:
    args = get_tool_spec("compare_role_applications").validate(
        {"role_id": role_id, "application_ids": application_ids}
    )
    with _open_session(
        ctx, get_tool_spec("compare_role_applications").required_scopes
    ) as (db, user):
        return handlers.compare_role_applications(db, user, **args)


@_catalog_tool("find_top_candidates")
def find_top_candidates(
    ctx: Context,
    query: NonEmptyString,
    limit: TopCandidateLimit = 10,
    rank_by: ScoreType = "taali",
    role_id: Optional[PositiveInt] = None,
) -> dict[str, Any]:
    args = get_tool_spec("find_top_candidates").validate(
        {
            "query": query,
            "limit": limit,
            "rank_by": rank_by,
            "role_id": role_id,
        }
    )
    with _open_session(ctx, get_tool_spec("find_top_candidates").required_scopes) as (
        db,
        user,
    ):
        return handlers.find_top_candidates(db, user, **args)


@_catalog_tool("nl_search_candidates")
def nl_search_candidates(
    ctx: Context,
    query: NonEmptyString,
    role_id: Optional[PositiveInt] = None,
    deep_verify: bool = False,
    include_graph: bool = False,
    limit: PageLimit = 25,
    offset: NonNegativeInt = 0,
) -> dict[str, Any]:
    args = get_tool_spec("nl_search_candidates").validate(
        {
            "query": query,
            "role_id": role_id,
            "deep_verify": deep_verify,
            "include_graph": include_graph,
            "limit": limit,
            "offset": offset,
        }
    )
    with _open_session(ctx, get_tool_spec("nl_search_candidates").required_scopes) as (
        db,
        user,
    ):
        return handlers.nl_search_candidates(db, user, **args)


@_catalog_tool("graph_search_candidates")
def graph_search_candidates(
    ctx: Context,
    query: NonEmptyString,
    limit: PageLimit = 25,
    role_id: Optional[PositiveInt] = None,
) -> dict[str, Any]:
    args = get_tool_spec("graph_search_candidates").validate(
        {"query": query, "limit": limit, "role_id": role_id}
    )
    with _open_session(
        ctx, get_tool_spec("graph_search_candidates").required_scopes
    ) as (db, user):
        return handlers.graph_search_candidates(db, user, **args)


@_catalog_tool("get_candidate_cv")
def get_candidate_cv(ctx: Context, candidate_id: PositiveInt) -> dict[str, Any]:
    args = get_tool_spec("get_candidate_cv").validate({"candidate_id": candidate_id})
    with _open_session(ctx, get_tool_spec("get_candidate_cv").required_scopes) as (
        db,
        user,
    ):
        return handlers.get_candidate_cv(db, user, **args)


@_catalog_tool("list_recent_agent_decisions")
def list_recent_agent_decisions(
    ctx: Context,
    role_id: Optional[PositiveInt] = None,
    status: Optional[DecisionStatus] = None,
    application_id: Optional[PositiveInt] = None,
    candidate_id: Optional[PositiveInt] = None,
    decision_type: Optional[AgentDecisionType] = None,
    created_after: Optional[datetime] = None,
    created_before: Optional[datetime] = None,
    resolved_after: Optional[datetime] = None,
    resolved_before: Optional[datetime] = None,
    limit: PageLimit = 20,
    offset: NonNegativeInt = 0,
) -> dict[str, Any]:
    args = get_tool_spec("list_recent_agent_decisions").validate(
        {
            "role_id": role_id,
            "status": status,
            "application_id": application_id,
            "candidate_id": candidate_id,
            "decision_type": decision_type,
            "created_after": created_after,
            "created_before": created_before,
            "resolved_after": resolved_after,
            "resolved_before": resolved_before,
            "limit": limit,
            "offset": offset,
        }
    )
    with _open_session(
        ctx, get_tool_spec("list_recent_agent_decisions").required_scopes
    ) as (db, user):
        return handlers.list_recent_agent_decisions(db, user, **args)


@_catalog_tool("list_candidate_actions")
def list_candidate_actions(
    ctx: Context,
    role_id: PositiveInt,
    application_id: Optional[PositiveInt] = None,
    candidate_id: Optional[PositiveInt] = None,
    action: Optional[CandidateAction] = None,
    target_stage: Optional[str] = None,
    status: CandidateActionStatus = "confirmed",
    actor_type: Optional[CandidateActionActor] = None,
    actor_id: Optional[PositiveInt] = None,
    occurred_after: Optional[datetime] = None,
    occurred_before: Optional[datetime] = None,
    result_view: Literal["events", "candidates"] = "events",
    limit: PageLimit = 50,
    offset: NonNegativeInt = 0,
) -> dict[str, Any]:
    args = get_tool_spec("list_candidate_actions").validate(
        {
            "role_id": role_id,
            "application_id": application_id,
            "candidate_id": candidate_id,
            "action": action,
            "target_stage": target_stage,
            "status": status,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "occurred_after": occurred_after,
            "occurred_before": occurred_before,
            "result_view": result_view,
            "limit": limit,
            "offset": offset,
        }
    )
    with _open_session(
        ctx, get_tool_spec("list_candidate_actions").required_scopes
    ) as (db, user):
        return handlers.list_candidate_actions(db, user, **args)


@_catalog_tool("get_recruiting_overview")
def get_recruiting_overview(
    ctx: Context,
    role_id: Optional[PositiveInt] = None,
) -> dict[str, Any]:
    args = get_tool_spec("get_recruiting_overview").validate({"role_id": role_id})
    with _open_session(
        ctx, get_tool_spec("get_recruiting_overview").required_scopes
    ) as (db, user):
        return operations.get_recruiting_overview(db, user, **args)


@_catalog_tool("list_assessments")
def list_assessments(
    ctx: Context,
    status: Optional[AssessmentStatus] = None,
    role_id: Optional[PositiveInt] = None,
    attention: AssessmentAttention = "any",
    limit: PageLimit = 25,
    offset: NonNegativeInt = 0,
) -> dict[str, Any]:
    args = get_tool_spec("list_assessments").validate(
        {
            "status": status,
            "role_id": role_id,
            "attention": attention,
            "limit": limit,
            "offset": offset,
        }
    )
    with _open_session(ctx, get_tool_spec("list_assessments").required_scopes) as (
        db,
        user,
    ):
        return operations.list_assessments(db, user, **args)


# ---------------------------------------------------------------------------
# Resources (read-only, addressable URIs for @-mention context).
# ---------------------------------------------------------------------------


def _markdown_role(role: Role) -> str:
    spec = (role.job_spec_text or role.description or "").strip()
    parts = [
        f"# {role.name}",
        f"Role ID: `{role.id}`  ·  Source: `{role.source}`",
        "",
    ]
    intent_block = render_role_intent_block(role)
    if intent_block:
        parts.extend(["## Recruiter criteria", intent_block, ""])
    if spec:
        parts.extend(["## Job spec", spec, ""])
    return "\n".join(parts).strip() + "\n"


def _markdown_physical_application(payload: dict[str, Any]) -> str:
    name = str(payload.get("candidate_name") or "(unknown candidate)")
    ats = payload.get("ats_evidence")
    ats = ats if isinstance(ats, dict) else {}
    context = ats.get("context")
    context = context if isinstance(context, dict) else {}
    cv = str(payload.get("cv_text") or "").strip()
    parts = [
        f"# {name} — physical application evidence",
        f"Application `{payload.get('application_id')}`",
        "",
        f"> {payload.get('notice')}",
        "",
        "## Explicit ATS transport evidence",
        f"- provider: {context.get('provider')}",
        f"- raw stage: {context.get('raw_stage')}",
        f"- normalized stage: {context.get('normalized_stage')}",
        f"- Workable stage: {ats.get('workable_stage')}",
        f"- Bullhorn status: {ats.get('bullhorn_status')}",
        "",
    ]
    if cv:
        parts.extend(["## CV", cv, ""])
    return "\n".join(parts).strip() + "\n"


def _markdown_role_application(payload: dict[str, Any]) -> str:
    name = str(payload.get("candidate_name") or "(unknown candidate)")
    role_name = str(payload.get("role_name") or "(unknown role)")
    state = payload.get("current_state")
    state = state if isinstance(state, dict) else {}
    parts = [
        f"# {name} — {role_name}",
        (
            f"Logical role `{payload.get('role_id')}`  ·  Application "
            f"`{payload.get('application_id')}`"
        ),
        (
            f"Current stage `{state.get('pipeline_stage')}`  ·  Outcome "
            f"`{state.get('application_outcome')}`"
        ),
        "",
        "## Role-local scores",
        f"- taali: {payload.get('taali_score')}",
        f"- pre_screen: {payload.get('pre_screen_score')}",
        f"- rank: {payload.get('rank_score')}",
        f"- cv_match: {payload.get('cv_match_score')}",
        f"- assessment: {payload.get('assessment_score')}",
        f"- role_fit: {payload.get('role_fit_score')}",
        "",
    ]
    recommendation = payload.get("pre_screen_recommendation")
    if recommendation:
        parts.extend(["## Role-local recommendation", str(recommendation), ""])
    cv = str(payload.get("cv_text") or "").strip()
    if cv:
        parts.extend(["## CV/source evidence", cv, ""])
    return "\n".join(parts).strip() + "\n"


@mcp_app.resource(
    "tali://role/{role_id}",
    name="role",
    description="Role spec as markdown — use as @-mention context.",
    mime_type="text/markdown",
)
def role_resource(role_id: str) -> str:
    ctx = mcp_app.get_context()
    with _open_session(ctx, SCOPE_ROLES_READ) as (db, user):
        role = (
            db.query(Role)
            .filter(
                Role.id == int(role_id),
                Role.organization_id == user.organization_id,
                Role.deleted_at.is_(None),
            )
            .first()
        )
        if role is None:
            raise ValueError(f"role {role_id} not found")
        return _markdown_role(role)


@mcp_app.resource(
    "tali://application/{application_id}",
    name="application",
    description=(
        "Legacy physical application evidence as markdown. Logical-role scores, "
        "pipeline, outcome, and judgments are intentionally omitted."
    ),
    mime_type="text/markdown",
)
def application_resource(application_id: str) -> str:
    ctx = mcp_app.get_context()
    with _open_session(ctx, SCOPE_APPLICATIONS_READ) as (db, user):
        payload = handlers.get_application(
            db,
            user,
            application_id=int(application_id),
            include_cv_text=True,
        )
        return _markdown_physical_application(payload)


@mcp_app.resource(
    "tali://role/{role_id}/application/{application_id}",
    name="role-application",
    description=(
        "Authoritative candidate snapshot for one logical role, including "
        "role-local score, current state, restrictions, and CV/source evidence."
    ),
    mime_type="text/markdown",
)
def role_application_resource(role_id: str, application_id: str) -> str:
    ctx = mcp_app.get_context()
    with _open_session(ctx, SCOPE_APPLICATIONS_READ) as (db, user):
        payload = handlers.get_role_candidate(
            db,
            user,
            role_id=int(role_id),
            application_id=int(application_id),
            include_cv_text=True,
        )
        return _markdown_role_application(payload)


@mcp_app.resource(
    "tali://candidate/{candidate_id}/cv",
    name="candidate-cv",
    description="Raw CV text for a candidate.",
    mime_type="text/plain",
)
def candidate_cv_resource(candidate_id: str) -> str:
    ctx = mcp_app.get_context()
    with _open_session(ctx, SCOPE_APPLICATIONS_READ) as (db, user):
        candidate = (
            db.query(Candidate)
            .filter(
                Candidate.id == int(candidate_id),
                Candidate.organization_id == user.organization_id,
                Candidate.deleted_at.is_(None),
            )
            .first()
        )
        if candidate is None:
            raise ValueError(f"candidate {candidate_id} not found")
        return (candidate.cv_text or "").strip() or "(no CV on file)"


__all__ = ["mcp_app"]
