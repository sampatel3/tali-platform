"""FastMCP server: read-only tools + resources for Tali.

Mounted under ``/mcp`` on the main FastAPI app. Each tool authenticates
the bearer JWT off the inbound request, opens a sync DB session, and
delegates to a pure-function handler in ``handlers.py``. Org-scoping is
enforced inside the handlers via ``user.organization_id``.

Adding a tool: add the implementation to ``handlers.py``, then register
a thin wrapper here that calls it. Both the MCP HTTP surface and the
in-process copilot orchestrator (``app/copilot/...``) reuse the same
handlers so behaviour stays consistent.
"""

# NOTE: do NOT add ``from __future__ import annotations`` — FastMCP's tool
# decorator does ``issubclass(param.annotation, Context)`` to detect the
# Context-injection convention, which only works when annotations are real
# classes rather than stringified PEP 563 forward references.

from typing import Any, Literal, Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy.orm import Session, joinedload

from ..models.api_key import SCOPE_APPLICATIONS_READ, SCOPE_ROLES_READ
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..platform.database import SessionLocal
from ..services.role_criteria_service import render_role_intent_block
from . import handlers
from .auth import MCPAuthError, authenticate_request, enforce_scope

ScoreType = Literal["taali", "pre_screen", "rank", "cv_match"]
SortBy = Literal["taali_score", "pre_screen_score", "rank_score", "cv_match_score", "created_at"]
SortOrder = Literal["desc", "asc"]


_INSTRUCTIONS = """Read-only access to Tali's recruiting data for the
authenticated user's organization.

Pipeline stages: applied -> invited -> in_assessment -> review.
Application outcomes: open, rejected, withdrawn, hired.

The default score (``taali``) is the merged primary score on a 0-100 scale.
``pre_screen`` is a cheap LLM gating score, ``rank`` is the pairwise rank
score, ``cv_match`` is the CV/job-spec similarity score. Use ``taali`` for
"score above X" questions unless the user specifies otherwise.

For semantic queries ("AWS Glue engineer with 5+ years", "people who worked
at YC companies"), use ``nl_search_candidates`` rather than
``search_applications`` — it parses the query, runs JSONB/CV-text filters,
and re-ranks with an LLM. ``graph_search_candidates`` queries the temporal
knowledge graph (Graphiti) for shape-based questions ("colleagues of X",
"worked at startups").

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

    ``require_scope`` gates API-key principals; JWT (session) principals are
    exempt. Handlers read only ``.organization_id`` off the returned principal.
    """

    def __init__(self, ctx: Context, require_scope: str) -> None:
        self._ctx = ctx
        self._require_scope = require_scope
        self._db: Session | None = None

    def __enter__(self) -> tuple[Session, Any]:
        self._db = SessionLocal()
        try:
            request = getattr(self._ctx.request_context, "request", None)
            if request is None:
                raise MCPAuthError("MCP context has no HTTP request bound")
            principal = authenticate_request(request, self._db)
            enforce_scope(principal, self._require_scope)
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


@mcp_app.tool(
    name="list_roles",
    description=(
        "List every active role for the authenticated user's organization. "
        "Use this first to discover ``role_id`` values for other tools. "
        "Set ``include_stage_counts=True`` to also return per-stage open "
        "application counts (one extra query per role)."
    ),
)
def list_roles(
    ctx: Context,
    include_stage_counts: bool = False,
) -> list[dict[str, Any]]:
    with _open_session(ctx, SCOPE_ROLES_READ) as (db, user):
        return handlers.list_roles(db, user, include_stage_counts=include_stage_counts)


@mcp_app.tool(
    name="get_role",
    description=(
        "Fetch one role with its full job spec, criteria, and per-stage "
        "open-application counts. ``role_id`` comes from ``list_roles``."
    ),
)
def get_role(ctx: Context, role_id: int) -> dict[str, Any]:
    with _open_session(ctx, SCOPE_ROLES_READ) as (db, user):
        return handlers.get_role(db, user, role_id=role_id)


@mcp_app.tool(
    name="search_applications",
    description=(
        "Filter applications by score / stage / outcome / simple text. Default "
        "scope returns only open applications sorted by ``taali_score`` desc. "
        "For semantic queries (skills, years of experience, narrative fit), use "
        "``nl_search_candidates`` instead — this tool's ``q`` only matches the "
        "candidate's name/email/position."
    ),
)
def search_applications(
    ctx: Context,
    role_id: Optional[int] = None,
    min_score: Optional[float] = None,
    score_type: ScoreType = "taali",
    pipeline_stage: Optional[str] = None,
    application_outcome: Optional[str] = "open",
    q: Optional[str] = None,
    sort_by: SortBy = "taali_score",
    sort_order: SortOrder = "desc",
    limit: int = 25,
) -> list[dict[str, Any]]:
    with _open_session(ctx, SCOPE_APPLICATIONS_READ) as (db, user):
        return handlers.search_applications(
            db,
            user,
            role_id=role_id,
            min_score=min_score,
            score_type=score_type,
            pipeline_stage=pipeline_stage,
            application_outcome=application_outcome,
            q=q,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
        )


@mcp_app.tool(
    name="get_application",
    description=(
        "Fetch one application by id with all four scores, evidence, "
        "auto-reject reason, and notes. Set ``include_cv_text=True`` to "
        "embed the full CV in the response (otherwise a 500-char preview "
        "is returned)."
    ),
)
def get_application(
    ctx: Context,
    application_id: int,
    include_cv_text: bool = False,
) -> dict[str, Any]:
    with _open_session(ctx, SCOPE_APPLICATIONS_READ) as (db, user):
        return handlers.get_application(
            db, user, application_id=application_id, include_cv_text=include_cv_text
        )


@mcp_app.tool(
    name="get_candidate",
    description=(
        "Fetch a candidate's profile and the full list of applications they "
        "have across every role in the org. Use this for cross-role "
        "questions like 'has this person applied for anything else?'."
    ),
)
def get_candidate(ctx: Context, candidate_id: int) -> dict[str, Any]:
    with _open_session(ctx, SCOPE_APPLICATIONS_READ) as (db, user):
        return handlers.get_candidate(db, user, candidate_id=candidate_id)


@mcp_app.tool(
    name="compare_applications",
    description=(
        "Side-by-side scorecard for 2-5 applications. Use this when the "
        "user asks 'which candidate should advance' — this surfaces every "
        "score on a common scale so the model can reason over them."
    ),
)
def compare_applications(
    ctx: Context,
    application_ids: list[int],
) -> dict[str, Any]:
    with _open_session(ctx, SCOPE_APPLICATIONS_READ) as (db, user):
        return handlers.compare_applications(db, user, application_ids=application_ids)


@mcp_app.tool(
    name="nl_search_candidates",
    description=(
        "Semantic / natural-language candidate search. Parses the query "
        "(skills, locations, years of experience, soft criteria, graph "
        "predicates), runs JSONB + CV-text filters, optionally re-ranks "
        "the top results with an LLM, and returns application summaries. "
        "This is the tool to use for questions like 'AWS Glue engineer "
        "with 5+ years' or 'senior backend devs in EMEA who've worked at "
        "fintechs'. Set ``role_id`` to scope to a specific role's pool. "
        "``rerank=False`` skips the rerank pass for speed (still returns "
        "good results, just not LLM-judged for soft criteria)."
    ),
)
def nl_search_candidates(
    ctx: Context,
    query: str,
    role_id: Optional[int] = None,
    rerank: bool = True,
    limit: int = 25,
) -> dict[str, Any]:
    with _open_session(ctx, SCOPE_APPLICATIONS_READ) as (db, user):
        return handlers.nl_search_candidates(
            db, user, query=query, role_id=role_id, rerank=rerank, limit=limit
        )


@mcp_app.tool(
    name="graph_search_candidates",
    description=(
        "Knowledge-graph search across the org's temporal subgraph "
        "(Graphiti / Neo4j). Returns candidates whose stored facts "
        "mention the query plus the matching fact strings so you can "
        "cite specifics. Use this for graph-shaped questions: "
        "'colleagues of X', 'people who worked at startups before "
        "joining Big Tech', 'engineers whose CVs mention tool Y'. "
        "Returns ``warnings: [{code: 'neo4j_unavailable'}]`` when the "
        "graph is not configured for this deployment — fall back to "
        "``nl_search_candidates`` in that case."
    ),
)
def graph_search_candidates(
    ctx: Context,
    query: str,
    limit: int = 25,
) -> dict[str, Any]:
    with _open_session(ctx, SCOPE_APPLICATIONS_READ) as (db, user):
        return handlers.graph_search_candidates(db, user, query=query, limit=limit)


@mcp_app.tool(
    name="get_candidate_cv",
    description=(
        "Parsed CV sections (work history, education, skills) plus the raw "
        "extracted CV text for one candidate. Use this when you need to "
        "quote a candidate's CV verbatim or check specific experience "
        "details — much cheaper than embedding the full CV in every "
        "search response."
    ),
)
def get_candidate_cv(ctx: Context, candidate_id: int) -> dict[str, Any]:
    with _open_session(ctx, SCOPE_APPLICATIONS_READ) as (db, user):
        return handlers.get_candidate_cv(db, user, candidate_id=candidate_id)


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
