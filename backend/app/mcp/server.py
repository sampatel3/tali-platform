"""FastMCP server: read-only tools + resources for Tali.

Mounted under ``/mcp`` on the main FastAPI app. Every tool authenticates by
decoding the bearer JWT off the inbound request and loading the matching
``User``; org-scoping is enforced on every query via
``organization_id == current_user.organization_id``.
"""

# NOTE: do NOT add ``from __future__ import annotations`` — FastMCP's tool
# decorator does ``issubclass(param.annotation, Context)`` to detect the
# Context-injection convention, which only works when annotations are real
# classes rather than stringified PEP 563 forward references.

from typing import Any, Literal, Optional

from mcp.server.fastmcp import Context, FastMCP
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..platform.database import SessionLocal
from .auth import MCPAuthError, authenticate_request
from .payloads import (
    SCORE_FIELDS,
    application_detail,
    application_summary,
    candidate_detail,
    comparison_row,
    role_detail,
    role_summary,
)
from .urls import application_url, candidate_url, role_url

# Pipeline / outcome enums — mirrored from
# ``domains/assessments_runtime/pipeline_service.py`` to avoid an import
# cycle and keep the schema shown to the LLM stable.
PIPELINE_STAGES = ("applied", "invited", "in_assessment", "review")
APPLICATION_OUTCOMES = ("open", "rejected", "withdrawn", "hired")
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

Every result includes a ``frontend_url`` the user can click to open the
matching page in the Tali web app.
"""


mcp_app = FastMCP(
    "tali",
    instructions=_INSTRUCTIONS,
    stateless_http=True,
    streamable_http_path="/",
)


# ---------------------------------------------------------------------------
# Per-tool helpers
# ---------------------------------------------------------------------------


class _MCPSession:
    """Bundle a DB session with the authenticated user.

    Used as a context manager so tools always return the connection to the
    pool even on error paths::

        with _open_session(ctx) as (db, user):
            ...
    """

    __slots__ = ("db", "user")

    def __init__(self, db: Session, user) -> None:  # type: ignore[no-untyped-def]
        self.db = db
        self.user = user


def _request_from_ctx(ctx: Context) -> Any:
    rc = ctx.request_context
    request = getattr(rc, "request", None)
    if request is None:
        raise MCPAuthError("MCP context has no HTTP request bound")
    return request


class _open_session:  # noqa: N801 — context-manager-as-class is intentional
    """Open a sync DB session and authenticate the request in one step."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self._db: Session | None = None

    def __enter__(self) -> tuple[Session, Any]:
        self._db = SessionLocal()
        try:
            request = _request_from_ctx(self._ctx)
            user = authenticate_request(request, self._db)
        except Exception:
            self._db.close()
            self._db = None
            raise
        return self._db, user

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if self._db is not None:
            self._db.close()
            self._db = None


def _stage_counts_for_role(db: Session, *, organization_id: int, role_id: int) -> dict[str, int]:
    rows = (
        db.query(CandidateApplication.pipeline_stage, func.count(CandidateApplication.id))
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.role_id == role_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
        )
        .group_by(CandidateApplication.pipeline_stage)
        .all()
    )
    counts = {stage: 0 for stage in PIPELINE_STAGES}
    for stage, total in rows:
        counts[str(stage)] = int(total or 0)
    return counts


def _applications_count(db: Session, *, organization_id: int, role_id: int) -> int:
    return (
        db.query(func.count(CandidateApplication.id))
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.role_id == role_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )


def _normalize_score_input(value: float | None) -> float | None:
    """Permit either 0-10 or 0-100 thresholds; coerce to 0-100."""
    if value is None:
        return None
    f = float(value)
    if 0 <= f <= 10:
        return f * 10.0
    return f


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
    with _open_session(ctx) as (db, user):
        roles = (
            db.query(Role)
            .filter(
                Role.organization_id == user.organization_id,
                Role.deleted_at.is_(None),
            )
            .order_by(Role.created_at.desc())
            .all()
        )
        if not roles:
            return []
        role_ids = [r.id for r in roles]
        count_rows = (
            db.query(CandidateApplication.role_id, func.count(CandidateApplication.id))
            .filter(
                CandidateApplication.organization_id == user.organization_id,
                CandidateApplication.role_id.in_(role_ids),
                CandidateApplication.deleted_at.is_(None),
            )
            .group_by(CandidateApplication.role_id)
            .all()
        )
        counts = {int(rid): int(total) for rid, total in count_rows}
        out: list[dict[str, Any]] = []
        for role in roles:
            stage_counts = (
                _stage_counts_for_role(db, organization_id=user.organization_id, role_id=role.id)
                if include_stage_counts
                else None
            )
            out.append(
                role_summary(
                    role,
                    applications_count=counts.get(role.id, 0),
                    stage_counts=stage_counts,
                )
            )
        return out


@mcp_app.tool(
    name="get_role",
    description=(
        "Fetch one role with its full job spec, criteria, and per-stage "
        "open-application counts. ``role_id`` comes from ``list_roles``."
    ),
)
def get_role(ctx: Context, role_id: int) -> dict[str, Any]:
    with _open_session(ctx) as (db, user):
        role = (
            db.query(Role)
            .options(joinedload(Role.criteria))
            .filter(
                Role.id == role_id,
                Role.organization_id == user.organization_id,
                Role.deleted_at.is_(None),
            )
            .first()
        )
        if role is None:
            raise ValueError(f"role {role_id} not found")
        return role_detail(
            role,
            applications_count=_applications_count(
                db, organization_id=user.organization_id, role_id=role.id
            ),
            stage_counts=_stage_counts_for_role(
                db, organization_id=user.organization_id, role_id=role.id
            ),
        )


@mcp_app.tool(
    name="search_applications",
    description=(
        "Search applications across one role (or every role for the org) with "
        "score, stage, and outcome filters. Default scope returns only "
        "open applications sorted by ``taali_score`` descending. Use this for "
        "questions like 'candidates above 70', 'who is in review for role X', "
        "'top 10 by pre-screen for the senior backend role'."
    ),
)
def search_applications(
    ctx: Context,
    role_id: int | None = None,
    min_score: float | None = None,
    score_type: ScoreType = "taali",
    pipeline_stage: str | None = None,
    application_outcome: str | None = "open",
    q: str | None = None,
    sort_by: SortBy = "taali_score",
    sort_order: SortOrder = "desc",
    limit: int = 25,
) -> list[dict[str, Any]]:
    if score_type not in SCORE_FIELDS:
        raise ValueError(
            f"score_type must be one of {sorted(SCORE_FIELDS)}, got {score_type!r}"
        )
    if pipeline_stage and pipeline_stage not in PIPELINE_STAGES:
        raise ValueError(
            f"pipeline_stage must be one of {list(PIPELINE_STAGES)}, got {pipeline_stage!r}"
        )
    if application_outcome and application_outcome not in APPLICATION_OUTCOMES:
        raise ValueError(
            f"application_outcome must be one of {list(APPLICATION_OUTCOMES)} or null, got {application_outcome!r}"
        )
    limit = max(1, min(int(limit), 100))

    with _open_session(ctx) as (db, user):
        query = (
            db.query(CandidateApplication)
            .options(
                joinedload(CandidateApplication.candidate),
                joinedload(CandidateApplication.role),
            )
            .filter(
                CandidateApplication.organization_id == user.organization_id,
                CandidateApplication.deleted_at.is_(None),
            )
        )
        if role_id is not None:
            query = query.filter(CandidateApplication.role_id == role_id)
        if pipeline_stage:
            query = query.filter(CandidateApplication.pipeline_stage == pipeline_stage)
        if application_outcome:
            query = query.filter(CandidateApplication.application_outcome == application_outcome)
        threshold = _normalize_score_input(min_score)
        if threshold is not None:
            score_col = getattr(CandidateApplication, SCORE_FIELDS[score_type])
            query = query.filter(score_col >= threshold)
        if q:
            like = f"%{q.strip()}%"
            query = query.join(Candidate, CandidateApplication.candidate_id == Candidate.id).filter(
                or_(
                    Candidate.full_name.ilike(like),
                    Candidate.email.ilike(like),
                    Candidate.position.ilike(like),
                )
            )

        # Sort in Python so NULL scores fall to the bottom regardless of
        # dialect (Postgres NULLS LAST vs SQLite default differ).
        apps = query.all()

        sort_column_map = {
            "taali_score": "taali_score_cache_100",
            "pre_screen_score": "pre_screen_score_100",
            "rank_score": "rank_score",
            "cv_match_score": "cv_match_score",
            "created_at": "created_at",
        }
        sort_attr = sort_column_map[sort_by]
        reverse = sort_order != "asc"
        from datetime import datetime, timezone

        def _key(app: CandidateApplication) -> Any:
            value = getattr(app, sort_attr, None)
            if sort_by == "created_at":
                return value or datetime.min.replace(tzinfo=timezone.utc)
            return value if value is not None else float("-inf")

        apps.sort(key=_key, reverse=reverse)
        return [application_summary(a) for a in apps[:limit]]


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
    with _open_session(ctx) as (db, user):
        app = (
            db.query(CandidateApplication)
            .options(
                joinedload(CandidateApplication.candidate),
                joinedload(CandidateApplication.role),
            )
            .filter(
                CandidateApplication.id == application_id,
                CandidateApplication.organization_id == user.organization_id,
                CandidateApplication.deleted_at.is_(None),
            )
            .first()
        )
        if app is None:
            raise ValueError(f"application {application_id} not found")
        return application_detail(app, include_cv_text=include_cv_text)


@mcp_app.tool(
    name="get_candidate",
    description=(
        "Fetch a candidate's profile and the full list of applications they "
        "have across every role in the org. Use this for cross-role "
        "questions like 'has this person applied for anything else?'."
    ),
)
def get_candidate(ctx: Context, candidate_id: int) -> dict[str, Any]:
    with _open_session(ctx) as (db, user):
        candidate = (
            db.query(Candidate)
            .options(joinedload(Candidate.applications).joinedload(CandidateApplication.role))
            .filter(
                Candidate.id == candidate_id,
                Candidate.organization_id == user.organization_id,
                Candidate.deleted_at.is_(None),
            )
            .first()
        )
        if candidate is None:
            raise ValueError(f"candidate {candidate_id} not found")
        return candidate_detail(candidate)


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
    if not application_ids:
        raise ValueError("application_ids must contain at least one id")
    if len(application_ids) > 5:
        raise ValueError("compare_applications accepts at most 5 ids")

    with _open_session(ctx) as (db, user):
        apps = (
            db.query(CandidateApplication)
            .options(
                joinedload(CandidateApplication.candidate),
                joinedload(CandidateApplication.role),
            )
            .filter(
                CandidateApplication.id.in_(application_ids),
                CandidateApplication.organization_id == user.organization_id,
                CandidateApplication.deleted_at.is_(None),
            )
            .all()
        )
        found_ids = {a.id for a in apps}
        missing = [aid for aid in application_ids if aid not in found_ids]
        rows = [comparison_row(a) for a in apps]
        # Preserve caller's id order so the comparison is deterministic.
        order = {aid: idx for idx, aid in enumerate(application_ids)}
        rows.sort(key=lambda r: order.get(r["application_id"], len(order)))
        return {
            "applications": rows,
            "missing_ids": missing,
            "score_legend": {
                "taali": "Merged primary score (0-100) — recommended for ranking.",
                "pre_screen": "Cheap LLM gating score (0-100).",
                "rank": "Pairwise ranking against role pool (0-100).",
                "cv_match": "CV-vs-job-spec similarity (0-100).",
                "workable": "External Workable score, if synced.",
                "assessment": "Cached assessment-result score (0-100).",
                "role_fit": "Composite role-fit score (0-100).",
            },
        }


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


def _markdown_role(role: Role) -> str:
    spec = (role.job_spec_text or role.description or "").strip()
    parts = [
        f"# {role.name}",
        f"Role ID: `{role.id}`  ·  Source: `{role.source}`",
        "",
    ]
    if role.additional_requirements:
        parts.extend(["## Additional requirements", role.additional_requirements.strip(), ""])
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
        parts.extend([
            "## Pre-screen recommendation",
            app.pre_screen_recommendation,
            "",
        ])
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
    with _open_session(ctx) as (db, user):
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
    with _open_session(ctx) as (db, user):
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
    with _open_session(ctx) as (db, user):
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


__all__ = [
    "mcp_app",
    "PIPELINE_STAGES",
    "APPLICATION_OUTCOMES",
    "list_roles",
    "get_role",
    "search_applications",
    "get_application",
    "get_candidate",
    "compare_applications",
]
