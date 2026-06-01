"""High-level sync entry points used by listeners + the backfill CLI.

Each function turns one or more domain rows into Graphiti episodes via
``episodes.py`` and dispatches them to Graphiti. All functions are
idempotent at the Graphiti level — duplicate ``add_episode`` calls with
the same body are deduped by Graphiti's content fingerprint, so retries
and re-runs are safe.

These functions are sync-callable; they hide Graphiti's async surface
behind ``client.run_async``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.candidate_application_event import CandidateApplicationEvent
from ..models.application_interview import ApplicationInterview
from . import client as graph_client
from . import episodes as episode_module

logger = logging.getLogger("taali.candidate_graph.sync")


# Workable stages that mean the candidate is past handover — the recruiter
# has already advanced them. Mirrors POST_HANDOVER_WORKABLE_STAGES in
# pipeline_service; redeclared here so candidate_graph has no dependency
# on the assessments_runtime domain.
_POST_HANDOVER_WORKABLE_STAGES = frozenset({
    "phone_screen", "phone_interview",
    "interview", "technical_interview", "final_interview", "onsite",
    "assessment",
    "offer", "offer_extended", "offer_accepted",
    "hired",
})


def should_sync_candidate_to_graph(candidate: Candidate, db: Session) -> bool:
    """Cost gate for the listener-driven Graphiti sync.

    Returns True only when the candidate has at least one application
    worth indexing into the graph: either Tali has promoted them past
    initial screening (``pipeline_stage`` in ``in_assessment`` /
    ``advanced``) OR the recruiter has moved them past handover in
    Workable (``workable_stage`` in the post-handover set).

    Rejected and not-yet-advanced candidates are skipped to keep
    Graphiti extraction costs bounded. The backfill CLI bypasses this
    gate by calling ``sync_candidate`` directly.
    """
    if candidate.id is None:
        return False
    rows = (
        db.query(CandidateApplication.pipeline_stage,
                 CandidateApplication.workable_stage)
        .filter(CandidateApplication.candidate_id == candidate.id)
        .filter(CandidateApplication.deleted_at.is_(None))
        .all()
    )
    for pipeline_stage, workable_stage in rows:
        ps = (pipeline_stage or "").strip().lower()
        if ps in ("in_assessment", "advanced"):
            return True
        ws = (workable_stage or "").strip().lower()
        if ws in _POST_HANDOVER_WORKABLE_STAGES:
            return True
    return False


def sync_candidate(
    candidate: Candidate,
    *,
    db: Session | None = None,
    include_cv_text: bool = True,
    bill_organization_id: int | None = None,
    bill_role_id: int | None = None,
    bill_user_id: int | None = None,
) -> int:
    """Ingest one candidate's profile (+ optional raw CV text) into Graphiti.

    Returns the number of episodes successfully sent. Returns 0 (no error)
    when Graphiti is not configured or the candidate is missing
    organization_id.

    When ``bill_organization_id`` is set (typically by the per-role Process
    cascade), each successful episode is also recorded as a graph_sync
    UsageEvent against the org/role so the cost flows into the role budget.
    """
    if not graph_client.is_configured():
        return 0
    if candidate.id is None or candidate.organization_id is None:
        return 0

    from ..platform.config import settings

    profile_eps = episode_module.build_candidate_profile_episodes(
        candidate,
        max_episodes=int(settings.GRAPHITI_MAX_EPISODES_PER_CANDIDATE),
    )
    cv_ep = episode_module.build_cv_text_episode(candidate) if include_cv_text else None
    episodes = profile_eps + ([cv_ep] if cv_ep else [])
    sent = episode_module.dispatch(
        episodes,
        db=db,
        bill_organization_id=bill_organization_id,
        bill_role_id=bill_role_id,
        bill_user_id=bill_user_id,
        bill_candidate_id=int(candidate.id),
    )

    if db is not None and sent > 0:
        _record_sync_state(db, int(candidate.id))
    return sent


def sync_interview(
    interview: ApplicationInterview,
    *,
    db: Session | None = None,
    bill_organization_id: int | None = None,
) -> int:
    """Ingest one interview transcript + structured summary.

    ``bill_organization_id`` lets a caller that already knows the org (e.g.
    the per-org backfill) attribute the spend directly; when omitted we fall
    back to resolving it via the interview's application → candidate chain.
    """
    if not graph_client.is_configured():
        return 0
    episodes = episode_module.build_interview_episodes(interview)
    # Resolve org via the interview's application → candidate chain so
    # the metered async wrapper can tag the claude_call_log rows with
    # the right org. Best-effort; fall through unattributed when the
    # relationships aren't loaded.
    bill_org_id: int | None = bill_organization_id
    if bill_org_id is None:
        try:
            application = getattr(interview, "application", None)
            if application is not None and application.organization_id is not None:
                bill_org_id = int(application.organization_id)
        except Exception:
            bill_org_id = None
    return episode_module.dispatch(
        episodes,
        db=db,
        bill_organization_id=bill_org_id,
    )


def sync_event(
    event: CandidateApplicationEvent,
    *,
    db: Session | None = None,
    bill_organization_id: int | None = None,
) -> int:
    """Ingest a pipeline event (best-effort; some are no-op).

    ``db`` + ``bill_organization_id`` let the spend be attributed to the org
    (a graph_sync usage_event per call). When ``bill_organization_id`` is
    omitted we fall back to the event's own organization_id, then its
    application chain. Without ``db`` no usage_event can be written — the prior
    version never passed it, so every event-sync call landed org=NULL.
    """
    if not graph_client.is_configured():
        return 0
    episode = episode_module.build_event_episode(event)
    if episode is None:
        return 0
    bill_org_id: int | None = bill_organization_id
    if bill_org_id is None:
        try:
            if getattr(event, "organization_id", None) is not None:
                bill_org_id = int(event.organization_id)
            else:
                application = getattr(event, "application", None)
                if application is not None and application.organization_id is not None:
                    bill_org_id = int(application.organization_id)
        except Exception:
            bill_org_id = None
    return episode_module.dispatch(
        [episode], db=db, bill_organization_id=bill_org_id
    )


def sync_organization(
    db: Session,
    organization_id: int,
    *,
    since_year: int | None = None,
    cv_only: bool = False,
) -> dict:
    """Backfill: ingest every candidate + every linked interview for one org.

    Returns ``{candidates: {total, succeeded, episodes}, interviews:
    {total, episodes}, events: {total, episodes}}``. Idempotent — safe to
    re-run after schema bumps.
    """
    if not graph_client.is_configured():
        return {"status": "unconfigured"}

    out = {
        "candidates": {"total": 0, "succeeded": 0, "episodes": 0},
        "interviews": {"total": 0, "episodes": 0},
        "events": {"total": 0, "episodes": 0},
    }

    from datetime import datetime, timezone

    if since_year is not None:
        # Filter to candidates who submitted an application in or after since_year.
        # Use a subquery on candidate_id (integer) to avoid SELECT DISTINCT on json
        # columns in the candidates table (postgres has no equality op for json).
        cutoff = datetime(since_year, 1, 1, tzinfo=timezone.utc)
        applied_ids = db.query(CandidateApplication.candidate_id).filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.created_at >= cutoff,
            CandidateApplication.deleted_at.is_(None),
        ).distinct()
        cand_q = (
            db.query(Candidate)
            .filter(Candidate.id.in_(applied_ids))
            .filter(Candidate.deleted_at.is_(None))
        )
    else:
        cand_q = (
            db.query(Candidate)
            .filter(Candidate.organization_id == organization_id)
            .filter(Candidate.deleted_at.is_(None))
        )
    if cv_only:
        cand_q = cand_q.filter(
            Candidate.cv_text.isnot(None),
            Candidate.cv_text != "",
        )
    candidates = cand_q.all()
    out["candidates"]["total"] = len(candidates)
    for candidate in candidates:
        # Backfill is per-org — attribute the indexing spend to this org so
        # the metered async wrapper writes a graph_sync usage_event per call
        # (otherwise re-index Anthropic spend lands as org=NULL call_log rows
        # that reconcile against Anthropic but never reach the org's budget).
        sent = sync_candidate(
            candidate,
            db=db,
            include_cv_text=True,
            bill_organization_id=organization_id,
        )
        if sent > 0:
            out["candidates"]["succeeded"] += 1
            out["candidates"]["episodes"] += sent

    interviews = (
        db.query(ApplicationInterview)
        .join(
            CandidateApplication,
            CandidateApplication.id == ApplicationInterview.application_id,
        )
        .filter(ApplicationInterview.organization_id == organization_id)
        .filter(CandidateApplication.deleted_at.is_(None))
        .all()
    )
    out["interviews"]["total"] = len(interviews)
    for interview in interviews:
        out["interviews"]["episodes"] += sync_interview(
            interview, db=db, bill_organization_id=organization_id
        )

    events = (
        db.query(CandidateApplicationEvent)
        .join(
            CandidateApplication,
            CandidateApplication.id == CandidateApplicationEvent.application_id,
        )
        .filter(CandidateApplication.organization_id == organization_id)
        .filter(CandidateApplication.deleted_at.is_(None))
        .all()
    )
    out["events"]["total"] = len(events)
    for event in events:
        out["events"]["episodes"] += sync_event(event)

    return out


def sync_all_organizations(
    db: Session,
    *,
    since_year: int | None = None,
    cv_only: bool = False,
) -> dict:
    """Backfill every organisation. Used by ``backfill --all-orgs``."""
    if not graph_client.is_configured():
        return {"status": "unconfigured"}
    org_ids = [
        int(row[0])
        for row in db.query(Candidate.organization_id)
        .filter(Candidate.organization_id.is_not(None))
        .distinct()
        .all()
    ]
    aggregate = {
        "orgs": len(org_ids),
        "since_year": since_year,
        "cv_only": cv_only,
        "candidates": {"total": 0, "succeeded": 0, "episodes": 0},
        "interviews": {"total": 0, "episodes": 0},
        "events": {"total": 0, "episodes": 0},
    }
    for org_id in org_ids:
        result = sync_organization(db, org_id, since_year=since_year, cv_only=cv_only)
        if not isinstance(result, dict) or "candidates" not in result:
            continue
        for key in ("candidates", "interviews", "events"):
            for sub in result[key]:
                aggregate[key][sub] += result[key][sub]
    return aggregate


def _record_sync_state(db: Session, candidate_id: int) -> None:
    """Stamp graph_sync_state.last_synced_at = now() for this candidate."""
    try:
        from ..models.graph_sync_state import GraphSyncState

        existing = (
            db.query(GraphSyncState)
            .filter(GraphSyncState.candidate_id == candidate_id)
            .one_or_none()
        )
        now_utc = datetime.now(timezone.utc)
        if existing is None:
            db.add(
                GraphSyncState(
                    candidate_id=candidate_id,
                    last_synced_at=now_utc,
                    sync_version=1,
                )
            )
        else:
            existing.last_synced_at = now_utc
            existing.sync_version = (existing.sync_version or 0) + 1
        db.commit()
    except Exception as exc:
        logger.debug("graph_sync_state write skipped: %s", exc)
        db.rollback()
