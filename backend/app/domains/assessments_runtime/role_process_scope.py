"""Scope, preview, and progress primitives for the role Process cascade."""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ...services.logical_role_application_authority import (
    LogicalRoleApplicationAuthorizationError,
)
from ...services.logical_role_batch_operations import (
    context_fetch_transport,
    context_has_cv,
    filter_contexts_stage,
    is_related_role,
    logical_role_contexts,
    related_score_targets,
)
from .batch_runtime_state import (
    is_cancelled as _is_cancelled,
    redis_client as _redis_client,
)

_process_progress: dict[int, dict] = {}


def _logical_role_contexts_or_400(
    db: Session,
    *,
    role: Role,
    application_ids: list[int] | None = None,
):
    try:
        return logical_role_contexts(
            db,
            role=role,
            application_ids=application_ids,
        )
    except (LogicalRoleApplicationAuthorizationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


_PROCESS_CANCEL_PREFIX = "process_role:cancel:"
_PROCESS_PROGRESS_PREFIX = "process_role:progress:"
# 30 minute TTL is plenty for the longest expected cascade. Re-written every
# time the worker updates progress so it stays alive while running.
_PROCESS_PROGRESS_TTL_SECONDS = 1800


def is_process_cancelled(role_id: int) -> bool:
    return _is_cancelled(_PROCESS_CANCEL_PREFIX, role_id)


def _write_process_progress(role_id: int, progress: dict) -> None:
    """Mirror process progress to Redis so other uvicorn workers see it.

    With workers=2 the worker that runs the daemon thread holds the in-memory
    dict, but status polls round-robin to both workers. Without Redis the
    other worker returns ``idle`` and the toaster row flickers in/out.
    """
    import json as _json
    client = _redis_client()
    if client is None:
        return
    try:
        # Convert datetime to iso so json can encode it.
        safe = dict(progress)
        if isinstance(safe.get("started_at"), datetime):
            safe["started_at"] = safe["started_at"].isoformat()
        client.set(
            f"{_PROCESS_PROGRESS_PREFIX}{role_id}",
            _json.dumps(safe, default=str),
            ex=_PROCESS_PROGRESS_TTL_SECONDS,
        )
    except Exception:
        pass


def _read_process_progress(role_id: int) -> dict | None:
    import json as _json
    client = _redis_client()
    if client is None:
        return None
    try:
        raw = client.get(f"{_PROCESS_PROGRESS_PREFIX}{role_id}")
        if raw is None:
            return None
        return _json.loads(raw)
    except Exception:
        return None


def _set_process_progress(role_id: int, progress: dict) -> None:
    """Update both the in-memory dict (fast path on the worker that owns it)
    and Redis (visible to all workers)."""
    _process_progress[role_id] = progress
    _write_process_progress(role_id, progress)


def _empty_process_progress() -> dict:
    return {
        "status": "idle",
        "role_name": None,
        "current_step": None,
        "fetch": {"attempted": 0, "fetched": 0, "unavailable": 0, "errors": 0, "total": 0},
        "pre_screen": {"total": 0, "processed": 0, "errors": 0},
        "score": {"total": 0, "scored": 0, "filtered": 0, "errors": 0, "mode": "none"},
        "graph_sync": {"total": 0, "synced": 0, "errors": 0},
    }


_PIPELINE_STAGE_VALUES = {"applied", "invited", "in_assessment", "review", "advanced"}


def _matches_stage_filter(app: CandidateApplication, stage_filter: str | None) -> bool:
    """Python-side mirror of ``_apply_stage_filter`` for code paths that
    already pulled a Python list of CandidateApplications and need to
    narrow it without re-querying."""
    if not stage_filter or stage_filter == "all":
        return True
    if stage_filter == "rejected":
        return (app.application_outcome or "") == "rejected"
    if stage_filter in _PIPELINE_STAGE_VALUES:
        return (
            (app.application_outcome or "") == "open"
            and (app.pipeline_stage or "") == stage_filter
        )
    return True


def _apply_stage_filter(query, stage_filter: str | None):
    """Narrow a CandidateApplication query to a single pipeline stage or
    the rejected outcome. ``None`` / ``all`` / empty = no filter.

    Recruiters use the segmented filter above the candidate table to scope
    the Process cascade to e.g. "Advanced (35)" so they can re-score just
    the candidates the recruiter has already moved forward — without
    burning budget on the 300+ Applied rows.
    """
    if not stage_filter or stage_filter == "all":
        return query
    if stage_filter == "rejected":
        return query.filter(CandidateApplication.application_outcome == "rejected")
    if stage_filter in _PIPELINE_STAGE_VALUES:
        return query.filter(
            CandidateApplication.application_outcome == "open",
            CandidateApplication.pipeline_stage == stage_filter,
        )
    # Unknown stage — caller should have validated; fall back to no filter.
    return query


def _apply_application_ids_filter(query, application_ids: list[int] | None):
    """Narrow a CandidateApplication query to an explicit list of IDs.

    Used when the recruiter ticks checkboxes on the candidate table and
    clicks Process — the cascade only touches those rows. Overrides
    stage_filter when both are present (the explicit selection wins).
    ``None`` / empty list = no filter (callers apply stage_filter instead).
    """
    if not application_ids:
        return query
    return query.filter(CandidateApplication.id.in_(application_ids))


def _process_dry_run(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
    fetch_cvs: bool,
    refresh_cvs: bool,
    pre_screen: bool,
    refresh_pre_screen: bool,
    score_mode: str,
    sync_graph: bool = False,
    refresh_graph: bool = False,
    stage_filter: str | None = None,
    application_ids: list[int] | None = None,
    select_graph_sync_candidates,
) -> dict:
    """Compute counts for each cascade step without starting the worker.

    Cascade-aware: when ``fetch_cvs`` is on, the pre-screen and score counts
    include candidates that will end up with a CV after the fetch step.
    ``refresh_cvs`` extends the fetch step to every Workable-sourced
    candidate, even ones already cached.

    ``stage_filter`` narrows the cascade to one segment of the candidate
    table (e.g. "advanced" to re-score only the 35 candidates the
    recruiter has already moved forward). ``None`` / ``"all"`` runs the
    full role. ``application_ids`` is an explicit override — when set,
    only those specific applications are processed (ignores stage_filter).
    """
    role = (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if role is not None and is_related_role(role):
        contexts = _logical_role_contexts_or_400(
            db,
            role=role,
            application_ids=application_ids,
        )
        if not application_ids:
            contexts = filter_contexts_stage(contexts, stage=stage_filter)
        fetchable = [
            context
            for context in contexts
            if context_fetch_transport(context) is not None
            and str(context_fetch_transport(context).source or "") == "workable"
        ]
        if fetch_cvs and refresh_cvs:
            will_fetch = len(
                {int(context_fetch_transport(context).id) for context in fetchable}
            )
        elif fetch_cvs:
            will_fetch = len(
                {
                    int(context_fetch_transport(context).id)
                    for context in fetchable
                    if not context_has_cv(context)
                }
            )
        else:
            will_fetch = 0
        no_cv_no_workable = sum(
            1
            for context in contexts
            if not context_has_cv(context)
            and context_fetch_transport(context) not in [
                context_fetch_transport(item) for item in fetchable
            ]
        )
        score_targets = (
            related_score_targets(
                contexts,
                include_scored=score_mode == "all",
            )
            if score_mode in {"new", "all"}
            else ()
        )
        graph_targets = {
            context.candidate_id
            for context in contexts
            if context_has_cv(context)
            or (
                fetch_cvs
                and context_fetch_transport(context) is not None
                and str(context_fetch_transport(context).source or "")
                == "workable"
            )
        }
        return {
            "fetch_cvs": {
                "will_attempt": int(will_fetch),
                "no_cv_no_workable": int(no_cv_no_workable),
            },
            "pre_screen": {"will_run": 0, "refresh": False},
            "score": {
                "will_run": len(score_targets),
                "mode": score_mode,
            },
            "graph_sync": {
                "will_run": len(graph_targets) if sync_graph else 0,
                "refresh": bool(refresh_graph),
                "estimated_cost_cents": (
                    int(round(len(graph_targets) * 2.0)) if sync_graph else 0
                ),
            },
            "total_candidates": len(contexts),
            "scoring_scope": "related_role_evaluation",
        }

    apps_query = (
        db.query(CandidateApplication)
        .options(joinedload(CandidateApplication.candidate))
        .filter(
            CandidateApplication.role_id == role_id,
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.deleted_at.is_(None),
        )
    )
    if application_ids:
        apps_query = _apply_application_ids_filter(apps_query, application_ids)
    else:
        apps_query = _apply_stage_filter(apps_query, stage_filter)
    apps = apps_query.all()

    def has_cv(a):
        return bool((a.cv_text or "").strip())

    def will_have_cv(a):
        if has_cv(a):
            return True
        if not fetch_cvs:
            return False
        # Will be fetched only if source=workable.
        return (a.source or "") == "workable"

    # Fetch step — refresh_cvs forces every Workable-sourced application
    # back into the fetch list regardless of whether a CV is already
    # cached on the application or its parent candidate.
    if fetch_cvs and refresh_cvs:
        will_fetch = sum(1 for a in apps if (a.source or "") == "workable")
    elif fetch_cvs:
        will_fetch = sum(1 for a in apps if not has_cv(a) and (a.source or "") == "workable")
    else:
        will_fetch = 0
    no_cv_no_workable = sum(
        1 for a in apps if not has_cv(a) and (a.source or "") != "workable"
    )

    # Pre-screen step
    if refresh_pre_screen:
        # Refresh = run for everyone who'll have a CV, regardless of prior result.
        will_pre_screen = sum(1 for a in apps if will_have_cv(a))
    elif pre_screen:
        will_pre_screen = sum(
            1 for a in apps
            if will_have_cv(a)
            and (
                # never run, or about to be fetched (so the existing rec is None anyway),
                # or stale (CV uploaded after pre-screen ran).
                a.pre_screen_recommendation is None
                or a.pre_screen_run_at is None
                or (a.cv_uploaded_at is not None and a.cv_uploaded_at > a.pre_screen_run_at)
            )
        )
    else:
        will_pre_screen = 0

    # Score step
    if score_mode == "all":
        will_score = sum(1 for a in apps if will_have_cv(a))
    elif score_mode == "new":
        def needs_score(a):
            if not will_have_cv(a):
                return False
            if a.cv_match_score is not None:
                # Already scored — only stale CV would force a rescore (we
                # don't auto-rescore on stale CV in score=new mode).
                return False
            # Below-threshold candidates whose pre-screen is still valid: skip.
            if (a.pre_screen_recommendation or "") == "Below threshold":
                if a.pre_screen_run_at is not None and (
                    a.cv_uploaded_at is None or a.cv_uploaded_at <= a.pre_screen_run_at
                ):
                    return False
            return True
        will_score = sum(1 for a in apps if needs_score(a))
    else:
        will_score = 0

    # Graph sync step — count candidates of THIS role who'll need a sync.
    # The graph sync runs per-candidate, not per-application, so we
    # de-dupe by candidate_id. Cascade-aware: when the fetch step is on,
    # candidates whose Candidate.cv_text is currently empty but whose
    # app.source=workable will end up with a CV after fetch and need
    # their first graph sync — the selector won't return them today
    # (it filters on Candidate.cv_text != None), so we add them in.
    will_graph_sync = 0
    if sync_graph:
        graph_targets: set[int] = set(
            select_graph_sync_candidates(
                db,
                organization_id=organization_id,
                refresh=refresh_graph,
                role_id=role_id,
            )
        )
        if fetch_cvs:
            for a in apps:
                if (
                    not has_cv(a)
                    and (a.source or "") == "workable"
                    and a.candidate_id is not None
                ):
                    graph_targets.add(int(a.candidate_id))
        # When the recruiter scoped the cascade (via stage filter or
        # explicit application_ids), the graph sync count needs to match
        # — otherwise the dialog claims it'll index hundreds of
        # candidates that the cascade won't actually touch. ``apps`` is
        # already the scoped list, so we can derive the candidate-id
        # ceiling from it.
        if application_ids or (stage_filter and stage_filter not in (None, "", "all")):
            scoped_candidate_ids = {
                int(a.candidate_id) for a in apps if a.candidate_id is not None
            }
            graph_targets = graph_targets & scoped_candidate_ids
        will_graph_sync = len(graph_targets)

    return {
        "fetch_cvs": {
            "will_attempt": int(will_fetch),
            "no_cv_no_workable": int(no_cv_no_workable),
        },
        "pre_screen": {
            "will_run": int(will_pre_screen),
            "refresh": bool(refresh_pre_screen),
        },
        "score": {
            "will_run": int(will_score),
            "mode": score_mode,
        },
        "graph_sync": {
            "will_run": int(will_graph_sync),
            "refresh": bool(refresh_graph),
            # Rough estimate: ~5 episodes/candidate × ~$0.005 each on Haiku.
            # Surfaced in the preview so recruiters see the indexing spend
            # before it lands in their role budget.
            "estimated_cost_cents": int(round(will_graph_sync * 2.0)),
        },
        "total_candidates": len(apps),
    }
