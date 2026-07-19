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

import hashlib
import logging
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.candidate_application_event import CandidateApplicationEvent
from ..models.application_interview import ApplicationInterview
from . import client as graph_client
from . import episodes as episode_module
from .ingest_manifest import MAX_MANIFEST_EPISODES

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


def _finish_without_provider(
    callback: Callable[[list[episode_module.Episode]], bool] | None,
) -> int:
    if callback is not None and not bool(callback([])):
        raise RuntimeError("could not durably record empty graph operation manifest")
    return 0


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
    return billing_role_id_for_candidate(candidate, db) is not None


def billing_role_id_for_candidate(
    candidate: Candidate,
    db: Session,
) -> int | None:
    """Return the newest graph-worthy application role for billing.

    Candidate profile episodes are shared across applications, but the
    automatic sync is only triggered once a concrete application crosses the
    cost gate. Charging that newest qualifying role is deterministic and keeps
    the provider call inside an actual role budget; if no such application
    exists, the caller skips before touching a provider.
    """
    if candidate.id is None:
        return None
    rows = (
        db.query(CandidateApplication.pipeline_stage,
                 CandidateApplication.workable_stage,
                 CandidateApplication.role_id)
        .filter(CandidateApplication.candidate_id == candidate.id)
        .filter(CandidateApplication.deleted_at.is_(None))
        .order_by(
            CandidateApplication.updated_at.desc(),
            CandidateApplication.id.desc(),
        )
        .all()
    )
    for pipeline_stage, workable_stage, role_id in rows:
        ps = (pipeline_stage or "").strip().lower()
        if ps in ("in_assessment", "advanced"):
            return int(role_id)
        ws = (workable_stage or "").strip().lower()
        if ws in _POST_HANDOVER_WORKABLE_STAGES:
            return int(role_id)
    return None


def latest_application_role_id_for_candidate(
    candidate: Candidate,
    db: Session,
) -> int | None:
    """Newest live application role, used by explicit whole-org backfills."""
    if candidate.id is None:
        return None
    row = (
        db.query(CandidateApplication.role_id)
        .filter(
            CandidateApplication.candidate_id == int(candidate.id),
            CandidateApplication.deleted_at.is_(None),
        )
        .order_by(
            CandidateApplication.updated_at.desc(),
            CandidateApplication.id.desc(),
        )
        .first()
    )
    return int(row[0]) if row is not None and row[0] is not None else None


def sync_candidate(
    candidate: Candidate,
    *,
    db: Session | None = None,
    include_cv_text: bool = True,
    bill_organization_id: int | None = None,
    bill_role_id: int | None = None,
    bill_user_id: int | None = None,
    force_resync: bool = False,
    require_role_admission: bool = False,
    raise_on_error: bool = False,
    provider_attempt_callback: Callable[[], bool] | None = None,
    operation_manifest_callback: Callable[[list[episode_module.Episode]], bool]
    | None = None,
) -> int:
    """Ingest one candidate's profile (+ optional raw CV text) into Graphiti.

    Returns the number of episodes successfully sent. Returns 0 (no error)
    when Graphiti is not configured or the candidate is missing
    organization_id.

    When ``bill_organization_id`` is set (typically by the per-role Process
    cascade), each successful episode is also recorded as a graph_sync
    UsageEvent against the org/role so the cost flows into the role budget.

    **Unchanged-content skip.** The listeners fire on every Candidate AND
    CandidateApplication update, but an application stage change doesn't touch
    the profile episodes. To avoid re-running the full per-candidate Graphiti
    extraction (several Haiku calls per episode) for zero graph delta, we
    fingerprint the episode set and skip the dispatch when it matches the last
    fully-synced fingerprint. ``force_resync=True`` bypasses the skip (e.g. a
    backfill after a Graphiti schema bump that must re-extract every candidate).
    """
    if not graph_client.is_configured():
        return 0
    if candidate.id is None or candidate.organization_id is None:
        return _finish_without_provider(operation_manifest_callback)

    from ..platform.config import settings

    cv_ep = episode_module.build_cv_text_episode(candidate) if include_cv_text else None
    configured_max = max(
        1,
        min(
            int(settings.GRAPHITI_MAX_EPISODES_PER_CANDIDATE),
            MAX_MANIFEST_EPISODES,
        ),
    )
    profile_eps = episode_module.build_candidate_profile_episodes(
        candidate,
        max_episodes=max(0, configured_max - (1 if cv_ep is not None else 0)),
    )
    episodes = profile_eps + ([cv_ep] if cv_ep else [])
    if not episodes:
        return _finish_without_provider(operation_manifest_callback)

    content_hash = _episodes_content_hash(episodes)
    if (
        db is not None
        and not force_resync
        and _content_hash_unchanged(db, int(candidate.id), content_hash)
    ):
        # Graph content identical to the last full sync — skip the extraction.
        return _finish_without_provider(operation_manifest_callback)

    bill_org_id = (
        int(bill_organization_id)
        if bill_organization_id is not None
        else int(candidate.organization_id)
    )
    sent = episode_module.dispatch(
        episodes,
        db=db,
        bill_organization_id=bill_org_id,
        bill_role_id=bill_role_id,
        bill_user_id=bill_user_id,
        bill_candidate_id=int(candidate.id),
        bill_trace_id=f"graph-candidate-sync:{int(candidate.id)}",
        require_hard_admission=True,
        require_role_admission=bool(require_role_admission),
        raise_on_error=bool(raise_on_error),
        provider_attempt_callback=provider_attempt_callback,
        operation_manifest_callback=operation_manifest_callback,
    )

    if db is not None and sent > 0:
        # Only record the fingerprint when EVERY episode landed; a partial
        # failure leaves content_hash as-is so the next firing retries
        # instead of being skipped as "unchanged".
        full = sent == len(episodes)
        _record_sync_state(
            db, int(candidate.id), content_hash=content_hash if full else None
        )
    return sent


def sync_interview(
    interview: ApplicationInterview,
    *,
    db: Session | None = None,
    bill_organization_id: int | None = None,
    bill_role_id: int | None = None,
    require_role_admission: bool = False,
    raise_on_error: bool = False,
    provider_attempt_callback: Callable[[], bool] | None = None,
    operation_manifest_callback: Callable[[list[episode_module.Episode]], bool]
    | None = None,
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
        bill_role_id=bill_role_id,
        bill_trace_id=f"graph-interview-sync:{int(interview.id)}",
        require_hard_admission=bill_org_id is not None,
        require_role_admission=bool(require_role_admission),
        raise_on_error=bool(raise_on_error),
        provider_attempt_callback=provider_attempt_callback,
        operation_manifest_callback=operation_manifest_callback,
    )


def sync_event(
    event: CandidateApplicationEvent,
    *,
    db: Session | None = None,
    bill_organization_id: int | None = None,
    bill_role_id: int | None = None,
    require_role_admission: bool = False,
    raise_on_error: bool = False,
    provider_attempt_callback: Callable[[], bool] | None = None,
    operation_manifest_callback: Callable[[list[episode_module.Episode]], bool]
    | None = None,
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
        return _finish_without_provider(operation_manifest_callback)
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
        [episode],
        db=db,
        bill_organization_id=bill_org_id,
        bill_role_id=bill_role_id,
        bill_trace_id=f"graph-event-sync:{int(event.id)}",
        require_hard_admission=bill_org_id is not None,
        require_role_admission=bool(require_role_admission),
        raise_on_error=bool(raise_on_error),
        provider_attempt_callback=provider_attempt_callback,
        operation_manifest_callback=operation_manifest_callback,
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
        role_id = latest_application_role_id_for_candidate(candidate, db)
        sent = sync_candidate(
            candidate,
            db=db,
            include_cv_text=True,
            bill_organization_id=organization_id,
            bill_role_id=role_id,
            require_role_admission=role_id is not None,
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
        role_id = getattr(getattr(interview, "application", None), "role_id", None)
        out["interviews"]["episodes"] += sync_interview(
            interview,
            db=db,
            bill_organization_id=organization_id,
            bill_role_id=int(role_id) if role_id is not None else None,
            require_role_admission=role_id is not None,
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
        role_id = getattr(getattr(event, "application", None), "role_id", None)
        out["events"]["episodes"] += sync_event(
            event,
            db=db,
            bill_organization_id=organization_id,
            bill_role_id=int(role_id) if role_id is not None else None,
            require_role_admission=role_id is not None,
        )

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


def _episodes_content_hash(episodes: list) -> str:
    """sha256 over the (name, body) of every episode in dispatch order.

    Stable for identical content; changes when any episode's text changes or
    an episode is added/removed. Cheap relative to one Graphiti extraction.
    """
    h = hashlib.sha256()
    for ep in episodes:
        h.update((ep.name or "").encode("utf-8"))
        h.update(b"\x00")
        h.update((ep.body or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _content_hash_unchanged(db: Session, candidate_id: int, content_hash: str) -> bool:
    """True iff this candidate's last FULL sync recorded the same fingerprint.

    Read-only; never raises — a lookup failure returns False so we fall back
    to re-syncing (correctness over the cost optimisation).
    """
    try:
        from ..models.graph_sync_state import GraphSyncState

        row = (
            db.query(GraphSyncState.content_hash)
            .filter(GraphSyncState.candidate_id == candidate_id)
            .one_or_none()
        )
        return row is not None and row[0] is not None and row[0] == content_hash
    except Exception as exc:
        logger.debug(
            "graph_sync_state hash read skipped error_type=%s",
            type(exc).__name__,
        )
        return False


def _record_sync_state(
    db: Session, candidate_id: int, *, content_hash: str | None = None
) -> None:
    """Stamp graph_sync_state.last_synced_at = now() for this candidate.

    When ``content_hash`` is provided (a fully-successful sync) it's also
    stored so the next unchanged re-sync can be skipped. A None content_hash
    (partial send) leaves any prior fingerprint untouched so the candidate is
    re-synced next time rather than skipped.
    """
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
                    content_hash=content_hash,
                )
            )
        else:
            existing.last_synced_at = now_utc
            existing.sync_version = (existing.sync_version or 0) + 1
            if content_hash is not None:
                existing.content_hash = content_hash
        db.commit()
    except Exception as exc:
        logger.debug(
            "graph_sync_state write skipped error_type=%s",
            type(exc).__name__,
        )
        db.rollback()
