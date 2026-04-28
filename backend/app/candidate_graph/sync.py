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


def sync_candidate(
    candidate: Candidate,
    *,
    db: Session | None = None,
    include_cv_text: bool = True,
) -> int:
    """Ingest one candidate's profile (+ optional raw CV text) into Graphiti.

    Returns the number of episodes successfully sent. Returns 0 (no error)
    when Graphiti is not configured or the candidate is missing
    organization_id.
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
    sent = episode_module.dispatch(episodes)

    if db is not None and sent > 0:
        _record_sync_state(db, int(candidate.id))
    return sent


def sync_interview(interview: ApplicationInterview, *, db: Session | None = None) -> int:
    """Ingest one interview transcript + structured summary."""
    if not graph_client.is_configured():
        return 0
    episodes = episode_module.build_interview_episodes(interview)
    return episode_module.dispatch(episodes)


def sync_event(event: CandidateApplicationEvent) -> int:
    """Ingest a pipeline event (best-effort; some are no-op)."""
    if not graph_client.is_configured():
        return 0
    episode = episode_module.build_event_episode(event)
    if episode is None:
        return 0
    return episode_module.dispatch([episode])


def sync_organization(
    db: Session,
    organization_id: int,
    *,
    since_year: int | None = None,
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

    cand_q = (
        db.query(Candidate)
        .filter(Candidate.organization_id == organization_id)
        .filter(Candidate.deleted_at.is_(None))
    )
    if since_year is not None:
        from datetime import datetime, timezone
        cand_q = cand_q.filter(
            Candidate.created_at >= datetime(since_year, 1, 1, tzinfo=timezone.utc)
        )
    candidates = cand_q.all()
    out["candidates"]["total"] = len(candidates)
    for candidate in candidates:
        sent = sync_candidate(candidate, db=db, include_cv_text=True)
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
        out["interviews"]["episodes"] += sync_interview(interview, db=db)

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


def sync_all_organizations(db: Session, *, since_year: int | None = None) -> dict:
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
        "candidates": {"total": 0, "succeeded": 0, "episodes": 0},
        "interviews": {"total": 0, "episodes": 0},
        "events": {"total": 0, "episodes": 0},
    }
    for org_id in org_ids:
        result = sync_organization(db, org_id, since_year=since_year)
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
