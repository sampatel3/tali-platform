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

from typing import Any, Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy.orm import Session, joinedload

from ..models.api_key import SCOPE_APPLICATIONS_READ, SCOPE_ROLES_READ
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..platform.database import SessionLocal
from ..services.role_criteria_service import render_role_intent_block
from . import handlers, operations
from .auth import MCPAuthError, authenticate_request, enforce_scope
from .catalog import (
    ApplicationOutcome,
    AssessmentAttention,
    AssessmentStatus,
    CandidateGraphSearchQuery,
    ComparisonApplicationIds,
    NaturalLanguageCandidateQuery,
    NonNegativeInt,
    PageLimit,
    PipelineStage,
    PositiveInt,
    ScoreThreshold,
    ScoreType,
    SimpleApplicationSearchText,
    SortBy,
    SortOrder,
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

For semantic queries ("AWS Glue engineer with 5+ years", "people who worked
at YC companies"), use ``nl_search_candidates`` rather than
``search_applications`` — it parses the query, runs JSONB/CV-text filters,
and can optionally run bounded deep verification. Common deterministic or
cached searches can be free; ambiguous Sonnet parsing and optional verification
may consume organization credits. ``graph_search_candidates`` queries the
temporal knowledge graph (Graphiti) for shape-based questions ("colleagues of
X", "worked at startups").

Every result includes a ``frontend_url`` the user can click to open the
matching page in the Tali web app.
"""


mcp_app = FastMCP(
    "tali",
    instructions=_INSTRUCTIONS,
    stateless_http=True,
    json_response=True,
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
    q: Optional[SimpleApplicationSearchText] = None,
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
    with _open_session(
        ctx, get_tool_spec("search_applications").required_scopes
    ) as (db, user):
        return handlers.search_applications(db, user, **args)


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
    with _open_session(
        ctx, get_tool_spec("compare_applications").required_scopes
    ) as (db, user):
        return handlers.compare_applications(db, user, **args)


@_catalog_tool("nl_search_candidates")
def nl_search_candidates(
    ctx: Context,
    query: NaturalLanguageCandidateQuery,
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
    with _open_session(
        ctx, get_tool_spec("nl_search_candidates").required_scopes
    ) as (db, user):
        return handlers.nl_search_candidates(db, user, **args)


@_catalog_tool("graph_search_candidates")
def graph_search_candidates(
    ctx: Context,
    query: CandidateGraphSearchQuery,
    limit: PageLimit = 25,
) -> dict[str, Any]:
    args = get_tool_spec("graph_search_candidates").validate(
        {"query": query, "limit": limit}
    )
    with _open_session(
        ctx, get_tool_spec("graph_search_candidates").required_scopes
    ) as (db, user):
        return handlers.graph_search_candidates(db, user, **args)


@_catalog_tool("get_candidate_cv")
def get_candidate_cv(ctx: Context, candidate_id: PositiveInt) -> dict[str, Any]:
    args = get_tool_spec("get_candidate_cv").validate(
        {"candidate_id": candidate_id}
    )
    with _open_session(ctx, get_tool_spec("get_candidate_cv").required_scopes) as (
        db,
        user,
    ):
        return handlers.get_candidate_cv(db, user, **args)


@_catalog_tool("get_recruiting_overview")
def get_recruiting_overview(
    ctx: Context,
    role_id: Optional[PositiveInt] = None,
) -> dict[str, Any]:
    args = get_tool_spec("get_recruiting_overview").validate(
        {"role_id": role_id}
    )
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


def _markdown_application(app: CandidateApplication) -> str:
    candidate = app.candidate
    role = app.role
    name = candidate.full_name if candidate else "(unknown candidate)"
    role_name = role.name if role else "(unknown role)"
    cv = (app.cv_text or "")
    if not cv and candidate:
        cv = candidate.cv_text or ""
    cv = cv.strip()
    parts = [
        f"# {name} — {role_name}",
        (
            f"Application `{app.id}`  ·  Stage `{app.pipeline_stage}`  ·  "
            f"Outcome `{app.application_outcome}`"
        ),
        "",
        "## Scores",
        f"- taali: {app.taali_score_cache_100}",
        f"- pre_screen: {app.pre_screen_score_100}",
        f"- rank: {app.rank_score}",
        f"- cv_match: {app.cv_match_score}",
        f"- assessment: {app.assessment_score_cache_100}",
        "",
    ]
    if app.pre_screen_recommendation:
        parts.extend(["## Pre-screen recommendation", app.pre_screen_recommendation, ""])
    if app.notes:
        parts.extend(["## Notes", app.notes, ""])
    if cv:
        parts.extend(["## CV", cv, ""])
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
    description="Application snapshot as markdown — scores, stage, CV.",
    mime_type="text/markdown",
)
def application_resource(application_id: str) -> str:
    ctx = mcp_app.get_context()
    with _open_session(ctx, SCOPE_APPLICATIONS_READ) as (db, user):
        app = (
            db.query(CandidateApplication)
            .options(
                joinedload(CandidateApplication.candidate),
                joinedload(CandidateApplication.role),
            )
            .filter(
                CandidateApplication.id == int(application_id),
                CandidateApplication.organization_id == user.organization_id,
                CandidateApplication.deleted_at.is_(None),
            )
            .first()
        )
        if app is None:
            raise ValueError(f"application {application_id} not found")
        return _markdown_application(app)


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
