"""Insert a queued ``AgentDecision`` for recruiter approval.

Called only by the agent (via MCP tool). High-stakes decisions —
``advance_to_interview``, ``reject``, ``skip_assessment_reject`` — never
auto-execute; they queue here and surface in the recruiter's pending
panel for one-click approve or override.

Idempotency key ``{run_id}:{application_id}:{decision_type}`` prevents
the agent re-queuing the same decision on retry.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domains.assessments_runtime.role_support import get_application, is_resolved
from ..models.agent_decision import AGENT_DECISION_TYPES, AgentDecision
from ..models.candidate_application_event import CandidateApplicationEvent
from .types import ACTOR_AGENT, Actor


def _capture_token_spend(
    db: Session, *, agent_run_id: int | None
) -> dict:
    """Discipline §8.5: roll up usage_events for this agent_run_id.

    Defers to ``token_spend_aggregator.aggregate`` which returns an
    empty dict on any failure or when no events match.
    """
    try:
        from ..agent_runtime import token_spend_aggregator
        return token_spend_aggregator.aggregate(db, agent_run_id=agent_run_id)
    except Exception:
        return {}


def _compute_dedup_key(
    db: Session,
    *,
    application_id: int,
    decision_type: str,
) -> str | None:
    """C4: build the cross-cycle dedup key for this would-be decision.

    Hash inputs that meaningfully change the verdict:
    ``application_id`` (which candidate),
    ``decision_type`` (advance vs reject vs send_assessment),
    ``criteria_fingerprint`` (role criteria revision),
    ``cv_fingerprint`` (which CV),
    ``pre_screen_bucket`` (5-pt bucket of pre-screen score),
    ``cv_match_bucket`` (5-pt bucket of cv-match score).

    Bucketing the scores by 5 points means trivial re-scoring noise
    (e.g. prompt-seed jitter) doesn't break dedup; a 5-pt swing in
    either direction (the same threshold the staleness service uses)
    does break it, which is what we want — material input change =>
    fresh decision allowed.

    Returns None on any error so the caller falls through to the
    existing pending/discarded guards.
    """
    import hashlib
    try:
        from ..models.candidate_application import CandidateApplication
        from ..models.role_criterion import RoleCriterion

        app = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == application_id)
            .one_or_none()
        )
        if app is None:
            return None

        criteria_rows = (
            db.query(RoleCriterion)
            .filter(
                RoleCriterion.role_id == app.role_id,
                RoleCriterion.deleted_at.is_(None),
            )
            .order_by(RoleCriterion.id)
            .all()
        )
        criteria_signature = "|".join(
            f"{c.id}:{(c.text or '').strip()}:{c.bucket or ''}:{c.weight or 0}"
            for c in criteria_rows
        )
        criteria_fp = hashlib.sha256(criteria_signature.encode("utf-8")).hexdigest()

        cv_text = (app.cv_text or "").strip()
        cv_fp = (
            hashlib.sha256(cv_text.encode("utf-8")).hexdigest()
            if cv_text else ""
        )

        def _bucket(value):
            try:
                if value is None:
                    return ""
                return str(int(float(value) // 5) * 5)
            except (TypeError, ValueError):
                return ""

        composite = "|".join([
            str(application_id),
            decision_type,
            criteria_fp,
            cv_fp,
            _bucket(getattr(app, "pre_screen_score_100", None)),
            _bucket(getattr(app, "cv_match_score", None)),
        ])
        return hashlib.sha256(composite.encode("utf-8")).hexdigest()
    except Exception:
        import logging
        logging.getLogger("taali.actions.queue_decision").warning(
            "dedup_key compute failed for app=%s type=%s",
            application_id, decision_type, exc_info=True,
        )
        return None


def _capture_input_fingerprint(
    db: Session,
    *,
    application_id: int,
    role_id: int,
) -> tuple[dict, str | None, str | None]:
    """A1: snapshot every input the decision cited.

    Returns ``(input_fingerprint_dict, criteria_fingerprint, cv_fingerprint)``.
    All three are safe-defaulted on any failure — fingerprint capture
    MUST NEVER block decision queueing. An empty dict means "pre-A1
    era, leave alone" in the staleness service.

    The criteria + cv hashes are pulled out as separate scalars (indexed
    on AgentDecision) so the drift detector can use them in WHERE
    clauses without JSON-extract.
    """
    import hashlib
    try:
        from ..models.candidate_application import CandidateApplication
        from ..models.role import Role
        from ..models.role_criterion import RoleCriterion
        from ..models.role_feedback_note import RoleFeedbackNote

        app = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == application_id)
            .one_or_none()
        )
        role = db.query(Role).filter(Role.id == role_id).one_or_none()
        if app is None or role is None:
            return ({}, None, None)

        # Criteria fingerprint: hash the stable identity of every active
        # criterion. Anything that changes the agent's evaluation
        # (text, bucket, weight) changes the hash; ordering doesn't.
        criteria_rows = (
            db.query(RoleCriterion)
            .filter(
                RoleCriterion.role_id == role_id,
                RoleCriterion.deleted_at.is_(None),
            )
            .order_by(RoleCriterion.id)
            .all()
        )
        criteria_signature = "|".join(
            f"{c.id}:{(c.text or '').strip()}:{c.bucket or ''}:{c.weight or 0}"
            for c in criteria_rows
        )
        criteria_fp = hashlib.sha256(criteria_signature.encode("utf-8")).hexdigest()

        cv_text = (app.cv_text or "").strip()
        cv_fp = (
            hashlib.sha256(cv_text.encode("utf-8")).hexdigest()
            if cv_text else None
        )

        # Latest recruiter feedback note id — if recruiter has added or
        # edited notes since the decision queued, the read-time check
        # will flag staleness.
        last_note_id = (
            db.query(RoleFeedbackNote.id)
            .filter(RoleFeedbackNote.role_id == role_id)
            .order_by(RoleFeedbackNote.id.desc())
            .first()
        )

        def _to_float(value):
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        fingerprint = {
            "criteria_fingerprint": criteria_fp,
            "cv_fingerprint": cv_fp,
            "cv_uploaded_at": (
                app.cv_uploaded_at.isoformat()
                if getattr(app, "cv_uploaded_at", None) is not None
                else None
            ),
            "pre_screen_score_at_emit": _to_float(getattr(app, "pre_screen_score_100", None)),
            "assessment_score_at_emit": _to_float(getattr(app, "assessment_score_cache_100", None)),
            "cv_match_score_at_emit": _to_float(getattr(app, "cv_match_score", None)),
            "taali_score_at_emit": _to_float(getattr(app, "taali_score_cache_100", None)),
            "pre_screen_cutoff_at_emit": _to_float(getattr(role, "pre_screen_cutoff_score_100", None)),
            "last_recruiter_note_id": int(last_note_id[0]) if last_note_id else None,
        }
        return (fingerprint, criteria_fp, cv_fp)
    except Exception:
        # Defensive — fingerprint capture failure must NEVER block the
        # queue. Empty dict = "pre-fingerprint era" in the staleness
        # service which leaves the decision alone.
        import logging
        logging.getLogger("taali.actions.queue_decision").warning(
            "input fingerprint capture failed for app=%s role=%s",
            application_id, role_id, exc_info=True,
        )
        return ({}, None, None)


def _capture_active_capabilities(
    db: Session,
    *,
    organization_id: int,
    decision_id: str,
    role_id: int | None,
) -> dict[str, bool]:
    """Snapshot every registered v10 capability for this decision.

    Captured at the moment the decision is queued — the resulting dict
    is what the audit query later relies on to reconstruct the runtime
    state. Failures here NEVER block decision queueing; an empty dict
    is the safe-degrade ("treat as v1/v2 era").
    """
    try:
        from ..capabilities import ALL_CAPABILITIES, get_shared
        return get_shared().snapshot(
            ALL_CAPABILITIES,
            db=db,
            organization_id=organization_id,
            decision_id=decision_id,
            role_id=role_id,
        )
    except Exception:
        return {}


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    role_id: int,
    application_id: int,
    decision_type: str,
    reasoning: str,
    evidence: Optional[dict[str, Any]] = None,
    confidence: Optional[float] = None,
    model_version: str,
    prompt_version: str,
    recommendation: Optional[str] = None,
    idempotency_key_suffix: Optional[str] = None,
    skip_episode: bool = False,
) -> AgentDecision:
    if actor.type != ACTOR_AGENT:
        raise HTTPException(
            status_code=403,
            detail="queue_decision is agent-only; recruiters take direct actions.",
        )
    if decision_type not in AGENT_DECISION_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"unknown decision_type={decision_type!r}",
        )
    if not (reasoning or "").strip():
        raise HTTPException(status_code=422, detail="reasoning is required")
    if actor.agent_run_id is None:
        raise HTTPException(status_code=422, detail="agent actor missing agent_run_id")

    # Validate the application belongs to the org+role.
    app = get_application(application_id, organization_id, db)
    if int(app.role_id) != int(role_id):
        raise HTTPException(
            status_code=422,
            detail=f"application {application_id} does not belong to role {role_id}",
        )

    # A6: terminal-state invariant. Resolved applications (rejected,
    # hired, advanced) are frozen forever — the agent must not queue,
    # modify, or re-evaluate decisions for them. This refuses cleanly
    # rather than silently no-opping so the orchestrator sees the
    # mistake in its error path and stops looping.
    if is_resolved(app):
        import logging
        logging.getLogger("taali.actions.queue_decision").info(
            "resolved_app_skipped action=queue_decision application_id=%s "
            "pipeline_stage=%s application_outcome=%s decision_type=%s",
            application_id, app.pipeline_stage, app.application_outcome, decision_type,
        )
        raise HTTPException(
            status_code=422,
            detail=(
                f"application {application_id} is resolved "
                f"(pipeline_stage={app.pipeline_stage!r}, "
                f"application_outcome={app.application_outcome!r}); "
                "refusing to queue decision"
            ),
        )

    # One pending decision per application at a time. The cohort planner
    # already filters apps-with-pending out of ``find_apps_in_state`` for
    # the triage states, but the agent can still call queue tools with an
    # arbitrary application_id (e.g. via ``get_application`` or a memory of
    # an id from an earlier cycle). When that happens we'd otherwise stack
    # multiple pendings on the same candidate — recruiter sees the same
    # person twice in the queue (e.g. "advance" + "send_assessment" both
    # waiting). Existing pending wins; return it so the caller treats this
    # as a dedup, not a new emit.
    existing_pending = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == application_id,
            AgentDecision.status == "pending",
        )
        .order_by(AgentDecision.created_at.desc())
        .first()
    )
    if existing_pending is not None:
        existing_pending._just_created = False  # type: ignore[attr-defined]
        return existing_pending

    # C3: recently-discarded suppression. When a recruiter discards a
    # decision and the agent — mid-cycle on the same candidate — re-emits
    # an identical decision 30s later, the recruiter perceives this as
    # "the agent didn't listen". The next ~10 minutes after a discard,
    # treat a same-type re-emit as a dedup. Beyond the window the agent
    # is welcome to try again (input may have changed, recruiter may
    # have moved on). Saves a fresh cycle's worth of Anthropic spend
    # every time a recruiter dismisses.
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    discard_window_floor = _dt.now(_tz.utc) - _td(minutes=10)
    recently_discarded = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == application_id,
            AgentDecision.decision_type == decision_type,
            AgentDecision.status == "discarded",
            AgentDecision.resolved_at >= discard_window_floor,
        )
        .order_by(AgentDecision.resolved_at.desc())
        .first()
    )
    if recently_discarded is not None:
        recently_discarded._just_created = False  # type: ignore[attr-defined]
        return recently_discarded

    # C4: cross-cycle dedup. If a decision with the same dedup_key
    # (same inputs, same decision type) was approved in the last 7 days
    # OR discarded in the last 10 min (C3 window — belt-and-braces for
    # cases where decision_type matches but the dedup_key differs by
    # bucket boundaries), dedup. We pre-fetch the application+role
    # once so the fingerprint and dedup_key compute share the same
    # baseline state.
    dedup_key = _compute_dedup_key(
        db,
        application_id=application_id,
        decision_type=decision_type,
    )
    if dedup_key:
        approved_window_floor = _dt.now(_tz.utc) - _td(days=7)
        prior_approved = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.application_id == application_id,
                AgentDecision.decision_type == decision_type,
                AgentDecision.decision_dedup_key == dedup_key,
                AgentDecision.status == "approved",
                AgentDecision.resolved_at >= approved_window_floor,
            )
            .order_by(AgentDecision.resolved_at.desc())
            .first()
        )
        if prior_approved is not None:
            prior_approved._just_created = False  # type: ignore[attr-defined]
            return prior_approved

    # Optional suffix lets the caller scope the key to a sub-identity
    # below (application_id, decision_type) — used by resend_assessment_invite
    # to keep separate approvals for separate assessments on the same
    # application from colliding (Codex #176).
    base_key = f"{actor.agent_run_id}:{application_id}:{decision_type}"
    idempotency_key = f"{base_key}:{idempotency_key_suffix}" if idempotency_key_suffix else base_key
    active_capabilities = _capture_active_capabilities(
        db,
        organization_id=organization_id,
        decision_id=idempotency_key,
        role_id=role_id,
    )
    # Discipline §8.5: roll up usage_events for this agent_run_id into
    # a single token_spend JSON blob on the decision row. Empty dict on
    # any failure — never blocks the queue.
    token_spend = _capture_token_spend(db, agent_run_id=actor.agent_run_id)
    # A1: snapshot the inputs the decision cited. Drives staleness
    # detection (A2) for pending decisions and forms the immutable
    # audit record for resolved decisions.
    input_fingerprint, criteria_fp, cv_fp = _capture_input_fingerprint(
        db, application_id=application_id, role_id=role_id,
    )

    decision = AgentDecision(
        organization_id=organization_id,
        role_id=role_id,
        application_id=application_id,
        agent_run_id=actor.agent_run_id,
        decision_type=decision_type,
        recommendation=recommendation or decision_type,
        status="pending",
        reasoning=reasoning.strip(),
        evidence=evidence,
        confidence=confidence,
        model_version=model_version,
        prompt_version=prompt_version,
        idempotency_key=idempotency_key,
        active_capabilities=active_capabilities,
        token_spend=token_spend,
        input_fingerprint=input_fingerprint,
        criteria_fingerprint=criteria_fp,
        cv_fingerprint=cv_fp,
        decision_dedup_key=dedup_key,
    )
    db.add(decision)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(AgentDecision)
            .filter(AgentDecision.idempotency_key == idempotency_key)
            .first()
        )
        if existing is not None:
            # Surface the dedup-existing branch on the returned object so
            # callers can decide whether to count it against the per-cycle
            # decision budget. Tracking this here (rather than via a
            # tuple-return API change) keeps the dozens of test callers
            # working unchanged. (Codex #179)
            existing._just_created = False  # type: ignore[attr-defined]
            return existing
        raise

    decision._just_created = True  # type: ignore[attr-defined]

    # Phase 2 §6.7: one consolidated Graphiti episode per decision.
    # Folds the four sub-agent scores into the decision body so we get
    # one LLM extraction pass per decision instead of one per score —
    # keeps Graphiti billing bounded to the decision volume. Failure
    # is logged and ignored; the Postgres row is the source of truth.
    # ``skip_episode`` is set by the deterministic bulk-decision pass:
    # it can emit hundreds of decisions in one go, and a per-decision
    # Graphiti LLM extraction would blow the cost + Celery time budget.
    # Threshold-application decisions aren't useful learning signal, and
    # Postgres remains the source of truth, so the episode is skipped.
    if not skip_episode:
        _emit_decision_episode_safe(db, decision=decision)

    # CandidateApplicationEvent so the per-role /agent/status endpoint's
    # ``last_activity`` reflects this decision the moment it's queued
    # — that's what the AgentBar tick reads. The Graphiti listener has
    # ``agent_decision_queued`` in its _NOISE_EVENT_TYPES set so this
    # doesn't double-ingest alongside _emit_decision_episode_safe above.
    db.add(
        CandidateApplicationEvent(
            application_id=application_id,
            organization_id=organization_id,
            event_type="agent_decision_queued",
            actor_type="agent",
            actor_id=actor.agent_run_id,
            reason=f"Queued {decision_type.replace('_', ' ')}",
            idempotency_key=f"agent_decision_queued:{decision.id}",
            event_metadata={"decision_id": int(decision.id), "decision_type": decision_type},
        )
    )
    return decision


def _emit_decision_episode_safe(db: Session, *, decision: AgentDecision) -> None:
    """Best-effort consolidated decision episode emit. Never raises.

    Looks up candidate + role context inline so the orchestrator caller
    doesn't have to thread them through.
    """
    try:
        from ..candidate_graph import agent_episodes
        from ..models.candidate import Candidate
        from ..models.candidate_application import CandidateApplication

        app = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id == decision.application_id)
            .one_or_none()
        )
        if app is None:
            return
        candidate = (
            db.query(Candidate).filter(Candidate.id == app.candidate_id).one_or_none()
        )
        full_name = candidate.full_name if candidate is not None else None
        candidate_id = int(candidate.id) if candidate is not None else int(app.candidate_id)
        agent_episodes.emit_decision_event(
            organization_id=int(decision.organization_id),
            candidate_full_name=full_name,
            candidate_taali_id=candidate_id,
            application_id=int(decision.application_id),
            role_id=int(decision.role_id),
            decision_id=int(decision.id),
            recommended_action=str(decision.recommendation),
            confidence=float(decision.confidence or 0.0),
            policy_revision_id=None,
            reasoning=str(decision.reasoning or ""),
            created_at=decision.created_at or _now(),
        )
    except Exception:
        import logging
        logging.getLogger("taali.actions.queue_decision").warning(
            "decision episode emit failed for decision_id=%s",
            getattr(decision, "id", None),
            exc_info=False,
        )


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
