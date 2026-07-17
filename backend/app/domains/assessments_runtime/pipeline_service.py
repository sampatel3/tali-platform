from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from ...models.agent_decision import AgentDecision
from ...models.assessment import Assessment
from ...models.candidate_application import CandidateApplication
from ...models.candidate_application_event import CandidateApplicationEvent
from ...models.role import Role
from ...services.related_role_application_runtime import sync_shared_advance
from ...services.sister_role_service import reconcile_related_roles_after_outcome

# An application is described by TWO independent axes:
#
#   pipeline_stage  — WHERE the candidate is in Tali's own funnel.
#   application_outcome — the RESULT, orthogonal to the stage.
#
# pipeline_stage:
#   sourced        — a PROSPECT: added to the role BEFORE they applied (recruiter
#                    or agent sourced them). No CV, never scored, never in the
#                    decision queue. Taali-NATIVE only — a synced ATS candidate is
#                    already applied+, so sync/legacy mapping never yields this.
#                    Moves to `applied` (and only then gets scored) when the person
#                    engages / applies. This is the sole pre-`applied` stage.
#   applied        — entered Tali; not yet actioned.
#   invited        — invited to a Tali assessment.
#   in_assessment  — assessment in progress.
#   review         — assessment done / awaiting a recruiter decision in Tali.
#   advanced       — handed OUT of Tali into the recruiter's Workable flow.
#                    Set ONLY by an explicit Tali hand-back decision, never
#                    derived from the Workable stage. This is terminal for Tali:
#                    the candidate is frozen (no scoring, no profile updates, no
#                    agent activity — see role_support.is_resolved). Workable
#                    sync keeps running purely to capture the realized outcome
#                    for model refinement.
#
# application_outcome:
#   open      — still live (no terminal result yet).
#   rejected  — not proceeding (Tali reject, Workable rejection, or disqualified).
#   withdrawn — candidate withdrew.
#   hired     — hired.
#
# pipeline_stage_source — who set the current stage: system | recruiter | sync | agent.
PIPELINE_STAGES = ("sourced", "applied", "invited", "in_assessment", "review", "advanced")
APPLICATION_OUTCOMES = ("open", "rejected", "withdrawn", "hired")
PIPELINE_STAGE_SOURCES = ("system", "recruiter", "sync", "agent")

# Stages a synced ATS (Workable / Bullhorn) may map a remote status onto.
# `sourced` is Taali-NATIVE only (a pre-applied lead added inside Taali); a synced
# candidate is already applied+, so it must never be a sync mapping target. Used
# by the Bullhorn stage-map config to keep `sourced` off the selectable list.
SYNC_MAPPABLE_STAGES = tuple(stage for stage in PIPELINE_STAGES if stage != "sourced")

# Workable stages that mean "the candidate is past Tali's handover point" —
# they're now in the recruiter's interview/offer flow. We collapse all of
# these into Tali's single `advanced` bucket; the precise stage stays
# visible via the workable_stage column on the row.
POST_HANDOVER_WORKABLE_STAGES = frozenset({
    "phone_screen", "phone_interview",
    "first_stage",
    "interview", "technical", "technical_interview", "final_interview", "onsite",
    "presentation",
    "assessment",
    "offer", "offer_extended", "offer_accepted",
    "hired",
})

# The subset of post-handover stages that are POSITIVE TERMINAL hand-offs — the
# recruiter has effectively decided (an offer is out, or they're hired). Only
# these freeze the candidate on Taali via the positive-advance path (transition
# to the `advanced` stage, which trips the A6 freeze in role_support.is_resolved).
# A mid-interview stage (phone / technical / final) is NOT terminal: the
# candidate could still wash out, so Taali keeps them in-funnel and decidable
# rather than freezing them. The broader POST_HANDOVER set still governs the
# "don't auto-reject someone a human is interviewing" guard + the funnel display
# bucketing.
#
# NB: distinct from ``workable/sync_service.py``'s ``TERMINAL_STAGES``, which is
# the all-terminal set (NEGATIVE rejected/disqualified/withdrawn + positive
# offer/hired) used to detect "candidate is done, skip enrichment / capture
# outcome". Negatives never reach this advance gate — they land via
# transition_outcome (outcome != 'open'), which the reconcile excludes upfront.
TERMINAL_WORKABLE_STAGES = frozenset({
    "offer", "offer_extended", "offer_accepted",
    "hired",
})

_RECRUITER_STAGE_TRANSITIONS = {
    # Engagement: a sourced prospect becomes a real applicant. The ONLY forward
    # edge out of `sourced` — a sourced lead may never skip ahead to
    # invited/review/advanced without first passing through `applied` (where
    # scoring runs). A recruiter may promote a sourced lead by hand; the native
    # apply / CV-arrival path does the same via the `system` set below.
    ("sourced", "applied"),
    ("applied", "invited"),
    ("review", "invited"),
    # Any earlier Tali stage may jump to "advanced" — used by the
    # Workable hand-back flow when the recruiter moves the candidate
    # directly to an interview/offer stage in the ATS.
    ("applied", "advanced"),
    ("invited", "advanced"),
    ("in_assessment", "advanced"),
    ("review", "advanced"),
    ("advanced", "review"),
}
_SYSTEM_STAGE_TRANSITIONS = {
    # Engagement (native apply / a CV arrives for a sourced lead). Scoring runs
    # only AFTER this transition — a sourced prospect is never scored.
    ("sourced", "applied"),
    ("invited", "in_assessment"),
    ("in_assessment", "review"),
    # Re-assessment: a candidate sitting in `review` (a prior attempt was
    # submitted, or auto-finalized on timeout — see PR #698) who starts a
    # freshly issued assessment is genuinely back `in_assessment`.
    # `start_or_resume_assessment` is the only system caller that targets
    # `in_assessment`, and it only fires this transition when a
    # PENDING/never-started assessment is actually started — so landing them
    # `in_assessment` is always correct. Without this edge the guard 409s and
    # the candidate start endpoint surfaces a generic 500 ("Failed to start
    # assessment session").
    ("review", "in_assessment"),
}
_LEGACY_COMPAT_EDGES: dict[str, list[tuple[str, str]]] = {
    # Direct edges to "advanced" mirror the recruiter hand-back flow
    # (see _RECRUITER_STAGE_TRANSITIONS): a legacy status that maps to
    # "advanced" (hired / offer_accepted / any post-handover Workable
    # stage) must be able to actually reach it, otherwise
    # apply_legacy_status_update raises 409 "cannot reach stage 'advanced'".
    "applied": [("invited", "recruiter"), ("advanced", "recruiter")],
    "invited": [("in_assessment", "system"), ("advanced", "recruiter")],
    "in_assessment": [("review", "system"), ("advanced", "recruiter")],
    "review": [("invited", "recruiter"), ("advanced", "recruiter")],
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_pipeline_key(value: str | None) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def is_post_handover_workable_stage(value: str | None) -> bool:
    """True when the Workable stage means a human recruiter has advanced this
    candidate past Tali's handover point (interview/offer/hired).

    Such a stage is a STRONG POSITIVE signal: a human has already validated the
    candidate. Per the agent's EXTERNAL PIPELINE STAGE rule, Tali must not
    reject these on score alone — and the cheap pre-screen reject path must
    honour the same rule, even though it never runs the agent prompt.
    """
    return normalize_pipeline_key(value) in POST_HANDOVER_WORKABLE_STAGES


def is_terminal_workable_stage(value: str | None) -> bool:
    """True when the Workable stage is a TERMINAL hand-off (offer / hired) —
    the recruiter has effectively decided. Only these freeze the candidate on
    Taali (advance to `advanced`). Mid-interview post-handover stages are NOT
    terminal: they stay decidable. Always a subset of post-handover stages.
    """
    return normalize_pipeline_key(value) in TERMINAL_WORKABLE_STAGES


def _not_post_handover_sql():
    """SQL form of ``not is_post_handover_workable_stage(workable_stage)``: a
    candidate is NOT past hand-off when workable_stage is null or its normalised
    value isn't a post-handover stage. Used to keep candidates the recruiter is
    already interviewing/offering in Workable OUT of 'not yet decided' — they're
    past the decision, not awaiting one. Mirrors normalize_pipeline_key
    (lower → '-'→'_' → ' '→'_')."""
    norm = func.replace(
        func.replace(func.lower(CandidateApplication.workable_stage), "-", "_"),
        " ", "_",
    )
    return or_(
        CandidateApplication.workable_stage.is_(None),
        norm.notin_(tuple(POST_HANDOVER_WORKABLE_STAGES)),
    )


def _post_handover_sql():
    """Boolean SQL form of ``is_post_handover_workable_stage(workable_stage)`` —
    true when the recruiter has advanced the candidate into an interview/offer/
    hired stage in Workable. Used to DISPLAY such candidates as 'advanced' in the
    funnel (alignment with Workable) without touching pipeline_stage, so every
    backend decision/calibration service keeps Tali's own decision-based
    'advanced'. Mirrors normalize_pipeline_key (lower → '-'→'_' → ' '→'_')."""
    norm = func.replace(
        func.replace(func.lower(CandidateApplication.workable_stage), "-", "_"),
        " ", "_",
    )
    return and_(
        CandidateApplication.workable_stage.isnot(None),
        norm.in_(tuple(POST_HANDOVER_WORKABLE_STAGES)),
    )


def normalize_pipeline_stage(value: str | None) -> str:
    normalized = normalize_pipeline_key(value)
    if normalized not in PIPELINE_STAGES:
        raise HTTPException(status_code=422, detail=f"Unsupported pipeline_stage={value!r}")
    return normalized


def normalize_application_outcome(value: str | None) -> str:
    normalized = normalize_pipeline_key(value)
    if normalized not in APPLICATION_OUTCOMES:
        raise HTTPException(status_code=422, detail=f"Unsupported application_outcome={value!r}")
    return normalized


def normalize_stage_source(value: str | None) -> str:
    normalized = normalize_pipeline_key(value)
    if normalized not in PIPELINE_STAGE_SOURCES:
        raise HTTPException(status_code=422, detail=f"Unsupported pipeline_stage_source={value!r}")
    return normalized


def _normalize_stage_against(
    value: str | None, allowed_slugs: tuple[str, ...] | None
) -> str:
    """Normalize + validate a stage slug. With ``allowed_slugs`` (configurable
    stages, flag ON) validate against the org's own stages; with ``None`` (flag
    OFF) fall back to the legacy hard-coded ``PIPELINE_STAGES`` — unchanged."""
    if allowed_slugs is None:
        return normalize_pipeline_stage(value)
    normalized = normalize_pipeline_key(value)
    if normalized not in allowed_slugs:
        raise HTTPException(
            status_code=422, detail=f"Unsupported pipeline_stage={value!r}"
        )
    return normalized


def _configurable_stage_slugs(
    db: Session, app: CandidateApplication
) -> tuple[str, ...] | None:
    """Always ``None`` — callers use the legacy ``PIPELINE_STAGES``. The ATS owns
    the pipeline; Tali no longer keeps an org-configurable stage set."""
    return None


def _resolve_allowed_slugs_for_app(
    app: CandidateApplication,
) -> tuple[str, ...] | None:
    """Always ``None`` — callers use the legacy ``PIPELINE_STAGES``. The ATS owns
    the pipeline; Tali no longer keeps an org-configurable stage set."""
    return None


def map_legacy_status_to_pipeline(status: str | None) -> tuple[str, str]:
    key = normalize_pipeline_key(status)
    if key in {"invited", "pending", "assessment_sent"}:
        return "invited", "open"
    if key in {"in_progress", "started"}:
        return "in_assessment", "open"
    if key in {"review", "completed", "completed_due_to_timeout", "scored"}:
        return "review", "open"
    if key in {"rejected", "declined", "disqualified"}:
        return "review", "rejected"
    if key in {"withdrawn"}:
        return "review", "withdrawn"
    if key in {"hired", "offer_accepted"}:
        return "advanced", "hired"
    if key in POST_HANDOVER_WORKABLE_STAGES:
        return "advanced", "open"
    return "applied", "open"


# Tali pipeline stages, in order. Used to decide whether an incoming
# Workable stage represents *forward* movement past the recruiter's
# current Tali stage — auto-advance is forward-only.
_STAGE_ORDER: dict[str, int] = {stage: idx for idx, stage in enumerate(PIPELINE_STAGES)}


def should_auto_advance_to_advanced(current_stage: str | None) -> bool:
    """Return True when an incoming `advanced` mapping should overwrite the
    current Tali stage. Forward-only: we move `applied`/`invited`/
    `in_assessment`/`review` → `advanced`, but never demote from
    `advanced` back if Workable wobbles.
    """
    current = normalize_pipeline_key(current_stage)
    if current not in _STAGE_ORDER:
        return True
    return _STAGE_ORDER[current] < _STAGE_ORDER["advanced"]


def status_from_pipeline(stage: str, outcome: str) -> str:
    # Tolerate per-org configurable stages (flag ON): normalize the KEY without
    # validating against the legacy tuple. Callers pass already-validated stages,
    # so for legacy stages this is identical to normalize_pipeline_stage.
    normalized_stage = normalize_pipeline_key(stage)
    normalized_outcome = normalize_application_outcome(outcome)
    if normalized_outcome in {"rejected", "withdrawn", "hired"}:
        return normalized_outcome
    if normalized_stage == "in_assessment":
        return "in_progress"
    return normalized_stage


def _event_to_payload(event: CandidateApplicationEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "application_id": event.application_id,
        "organization_id": event.organization_id,
        "event_type": event.event_type,
        "from_stage": event.from_stage,
        "to_stage": event.to_stage,
        "from_outcome": event.from_outcome,
        "to_outcome": event.to_outcome,
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "reason": event.reason,
        "metadata": event.event_metadata or {},
        "idempotency_key": event.idempotency_key,
        "created_at": event.created_at,
    }


def list_application_events(
    db: Session,
    *,
    organization_id: int,
    application_id: int,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    rows = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.organization_id == organization_id,
            CandidateApplicationEvent.application_id == application_id,
        )
        .order_by(CandidateApplicationEvent.created_at.desc(), CandidateApplicationEvent.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_event_to_payload(item) for item in rows]


def stage_external_drift(app: CandidateApplication) -> bool:
    external = normalize_pipeline_key(app.external_stage_normalized or app.external_stage_raw or app.workable_stage)
    if not external:
        return False
    local = normalize_pipeline_key(app.pipeline_stage)
    return bool(local and local != external)


def ensure_pipeline_fields(
    app: CandidateApplication,
    *,
    source: str = "system",
    allowed_slugs: tuple[str, ...] | None = None,
) -> None:
    now = _utcnow()
    normalized_source = normalize_stage_source(source)
    stage = normalize_pipeline_key(app.pipeline_stage)
    outcome = normalize_pipeline_key(app.application_outcome)
    if allowed_slugs is None:
        # Callers that don't thread the org's slugs (transition_outcome, the
        # event helpers, external ensure_pipeline_fields callers) still get
        # org-aware validation under the flag — a custom stage must survive
        # these paths. No-op when the flag is off.
        allowed_slugs = _resolve_allowed_slugs_for_app(app)
    valid_stages = allowed_slugs if allowed_slugs is not None else PIPELINE_STAGES
    if stage not in valid_stages or outcome not in APPLICATION_OUTCOMES:
        mapped_stage, mapped_outcome = map_legacy_status_to_pipeline(app.status)
        stage = mapped_stage
        outcome = mapped_outcome
    app.pipeline_stage = stage
    app.application_outcome = outcome
    if not app.pipeline_stage_updated_at:
        app.pipeline_stage_updated_at = now
    if not app.application_outcome_updated_at:
        app.application_outcome_updated_at = now
    app.pipeline_stage_source = normalize_stage_source(app.pipeline_stage_source or normalized_source)
    app.status = status_from_pipeline(app.pipeline_stage, app.application_outcome)
    if app.version is None or app.version < 1:
        app.version = 1


def _guard_stage_transition(
    *,
    source: str,
    from_stage: str,
    to_stage: str,
    app: CandidateApplication,
    allowed_targets: tuple[str, ...] | None = None,
) -> None:
    if from_stage == to_stage:
        return
    if allowed_targets is not None and source == "recruiter":
        # Configurable stages (flag ON): recruiters may move a candidate to any
        # active stage — the ATS-standard model. Target validity is already
        # enforced by _normalize_stage_against against the org's stages.
        return
    if source == "recruiter":
        if (from_stage, to_stage) not in _RECRUITER_STAGE_TRANSITIONS:
            raise HTTPException(
                status_code=409,
                detail=f"Recruiter transition {from_stage}->{to_stage} is not allowed",
            )
        return
    if source == "system":
        if (from_stage, to_stage) not in _SYSTEM_STAGE_TRANSITIONS:
            raise HTTPException(
                status_code=409,
                detail=f"System transition {from_stage}->{to_stage} is not allowed",
            )
        return
    if source == "sync":
        # Sync may always forward-advance a candidate to "advanced" once
        # Workable confirms they're past the handover point — Workable is
        # the source of truth for that part of the lifecycle. All other
        # sync transitions still require the row to be unedited locally
        # (version <= 1).
        if to_stage == "advanced":
            return
        if app.version > 1:
            raise HTTPException(
                status_code=409,
                detail="Sync cannot override local pipeline_stage after recruiter/system updates",
            )


def _legacy_compatibility_path(from_stage: str, to_stage: str) -> list[tuple[str, str]] | None:
    start = normalize_pipeline_stage(from_stage)
    target = normalize_pipeline_stage(to_stage)
    if start == target:
        return []

    queue: deque[tuple[str, list[tuple[str, str]]]] = deque([(start, [])])
    visited: set[str] = {start}

    while queue:
        current, path = queue.popleft()
        for next_stage, source in _LEGACY_COMPAT_EDGES.get(current, []):
            step_path = [*path, (next_stage, source)]
            if next_stage == target:
                return step_path
            if next_stage in visited:
                continue
            visited.add(next_stage)
            queue.append((next_stage, step_path))
    return None


def _existing_idempotent_event(
    db: Session,
    *,
    application_id: int,
    idempotency_key: str | None,
) -> CandidateApplicationEvent | None:
    token = str(idempotency_key or "").strip()
    if not token:
        return None
    return (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == application_id,
            CandidateApplicationEvent.idempotency_key == token,
        )
        .first()
    )


def _append_event(
    db: Session,
    *,
    app: CandidateApplication,
    event_type: str,
    actor_type: str,
    actor_id: int | None = None,
    from_stage: str | None = None,
    to_stage: str | None = None,
    from_outcome: str | None = None,
    to_outcome: str | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> CandidateApplicationEvent:
    event = CandidateApplicationEvent(
        application_id=app.id,
        organization_id=app.organization_id,
        event_type=event_type,
        from_stage=from_stage,
        to_stage=to_stage,
        from_outcome=from_outcome,
        to_outcome=to_outcome,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=(reason or "").strip() or None,
        event_metadata=metadata or None,
        idempotency_key=(str(idempotency_key or "").strip() or None),
    )
    db.add(event)
    return event


def initialize_pipeline_event_if_missing(
    db: Session,
    *,
    app: CandidateApplication,
    actor_type: str = "system",
    actor_id: int | None = None,
    reason: str | None = None,
) -> None:
    ensure_pipeline_fields(app)
    existing = (
        db.query(CandidateApplicationEvent.id)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "pipeline_initialized",
        )
        .first()
    )
    if existing:
        return
    _append_event(
        db,
        app=app,
        event_type="pipeline_initialized",
        actor_type=actor_type,
        actor_id=actor_id,
        to_stage=app.pipeline_stage,
        to_outcome=app.application_outcome,
        reason=reason or "Pipeline initialized",
        metadata={"legacy_status": app.status},
    )


def append_application_event(
    db: Session,
    *,
    app: CandidateApplication,
    event_type: str,
    actor_type: str,
    actor_id: int | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    from_stage: str | None = None,
    to_stage: str | None = None,
    from_outcome: str | None = None,
    to_outcome: str | None = None,
) -> CandidateApplicationEvent:
    ensure_pipeline_fields(app)
    existing_idempotent = _existing_idempotent_event(
        db,
        application_id=app.id,
        idempotency_key=idempotency_key,
    )
    if existing_idempotent:
        return existing_idempotent
    return _append_event(
        db,
        app=app,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        from_stage=from_stage,
        to_stage=to_stage,
        from_outcome=from_outcome,
        to_outcome=to_outcome,
        reason=reason,
        metadata=metadata,
        idempotency_key=idempotency_key,
    )


def transition_stage(
    db: Session,
    *,
    app: CandidateApplication,
    to_stage: str,
    source: str,
    actor_type: str,
    actor_id: int | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    expected_version: int | None = None,
) -> CandidateApplication:
    allowed_slugs = _configurable_stage_slugs(db, app)
    ensure_pipeline_fields(app, source=source, allowed_slugs=allowed_slugs)
    source_key = normalize_stage_source(source)
    target = _normalize_stage_against(to_stage, allowed_slugs)
    from_stage = _normalize_stage_against(app.pipeline_stage, allowed_slugs)

    if expected_version is not None and int(expected_version) != int(app.version or 0):
        raise HTTPException(
            status_code=409,
            detail=f"Version mismatch: expected={expected_version}, current={app.version}",
        )

    existing_idempotent = _existing_idempotent_event(
        db,
        application_id=app.id,
        idempotency_key=idempotency_key,
    )
    if existing_idempotent:
        if from_stage == "advanced":
            sync_shared_advance(db, app, target, source_key)
        return app

    _guard_stage_transition(
        source=source_key,
        from_stage=from_stage,
        to_stage=target,
        app=app,
        allowed_targets=allowed_slugs,
    )
    if from_stage == target:
        sync_shared_advance(db, app, target, source_key)
        return app

    now = _utcnow()
    previous_status = app.status
    app.pipeline_stage = target
    app.pipeline_stage_updated_at = now
    app.pipeline_stage_source = source_key
    app.status = status_from_pipeline(app.pipeline_stage, app.application_outcome)
    app.version = int(app.version or 1) + 1

    _append_event(
        db,
        app=app,
        event_type="pipeline_stage_changed",
        actor_type=actor_type,
        actor_id=actor_id,
        from_stage=from_stage,
        to_stage=target,
        from_outcome=app.application_outcome,
        to_outcome=app.application_outcome,
        reason=reason,
        metadata={
            "source": source_key,
            "legacy_status_before": previous_status,
            **(metadata or {}),
        },
        idempotency_key=idempotency_key,
    )
    sync_shared_advance(db, app, target, source_key)

    try:
        from ...agent_runtime import outcome_learning
        outcome_learning.record_advance_outcome_on_stage(
            db, application=app, new_stage=target,
            role_id=(metadata or {}).get("acting_role_id") or app.role_id,
        )
    except Exception:  # pragma: no cover — never block a stage transition
        import logging
        logging.getLogger("taali.pipeline_service").exception(
            "outcome_learning hook on stage transition failed (application_id=%s)",
            app.id,
        )

    return app


def reconcile_post_handover_advanced(
    db: Session, *, app: CandidateApplication, role: "Role | None" = None
) -> bool:
    """Reconcile a Workable-side advance onto Taali, with Taali's verdict.

    When a recruiter moves a candidate forward in Workable directly (Phone
    Screen / Technical / Final Interview / Offer … — a post-handover stage),
    Taali gives its deterministic SECOND OPINION and reconciles, but it only
    FREEZES the candidate (transition to ``advanced``, which trips the A6
    freeze) for a TERMINAL hand-off:

      * Taali would REJECT → surface it in the reject queue (don't advance) —
        "you're interviewing someone I'd have passed on."
      * Workable stage is TERMINAL (offer / hired) → reflect the hand-off as
        ``advanced`` (Taali's job is done) and discard every now-moot pending
        decision.
      * Workable stage is MID-INTERVIEW (phone / technical / final) → do NOT
        freeze. The candidate could still wash out, so Taali keeps them in
        their current stage — decidable, agent still live — but discards any
        stale pending REJECT card (a "reject" on someone in a live interview is
        the dangerous case), while leaving legitimate advance/send cards alone.

    LOCAL only — Workable already has them in that stage, so it writes NOTHING
    back. Idempotent. Does NOT commit — the caller owns the transaction. Returns
    True iff it advanced (froze) the candidate.
    """
    if app is None:
        return False
    if getattr(app, "application_outcome", None) != "open":
        return False
    if not is_post_handover_workable_stage(getattr(app, "workable_stage", None)):
        return False

    # Taali's deterministic second opinion. A reject is surfaced in the reject
    # queue by decide_post_handover (which also un-advances) — so we must NOT
    # advance. Lazy import: bulk_decision_service imports this module.
    role = role if role is not None else getattr(app, "role", None)
    if role is not None:
        try:
            from ...services.bulk_decision_service import decide_post_handover

            action = decide_post_handover(db, app=app, role=role)
        except Exception:  # pragma: no cover — never block the sync
            action = None
        if action in ("reject", "skip_assessment_reject"):
            return False  # surfaced in the reject queue; do NOT advance

    terminal = is_terminal_workable_stage(getattr(app, "workable_stage", None))

    # Lazy import: pre_screen_decision_emitter imports this module.
    from ...services.pre_screen_decision_emitter import (
        discard_pending_decisions_for_app,
    )

    if not terminal:
        # Mid-interview: stay decidable (no freeze). A pending reject card on a
        # candidate the recruiter is interviewing is KEPT — it is Taali's honest
        # second opinion, surfaced as a HITL card whose approve surfaces warn
        # the recruiter (advice, never auto-executed). Verdict-flip staleness is
        # owned by the cohort tick's ``_reconcile_stale_pending``, not by the
        # sync's stage reflection.
        #
        # Heal a candidate STRANDED in 'review' by an earlier agent reject
        # second-opinion (the advanced→review pull-back, source='agent') whose
        # card has since been resolved/discarded — they'd otherwise sit in
        # 'review' looking like they await a Taali decision when in truth
        # they're being interviewed in Workable. Reflect that honestly as
        # 'advanced' (handed off). Never fires while a reject card is still
        # pending (advancing under a live reject card would contradict it).
        # Genuine assessment-completion review is source='system', untouched.
        from ...models.agent_decision import AgentDecision

        has_pending_reject = (
            db.query(AgentDecision.id)
            .filter(
                AgentDecision.application_id == int(app.id),
                AgentDecision.status.in_(("pending", "processing")),
                AgentDecision.decision_type.in_(
                    ("reject", "skip_assessment_reject")
                ),
            )
            .first()
            is not None
        )
        if (
            not has_pending_reject
            and normalize_pipeline_stage(app.pipeline_stage) == "review"
            and normalize_pipeline_key(app.pipeline_stage_source) == "agent"
        ):
            transition_stage(
                db,
                app=app,
                to_stage="advanced",
                source="sync",
                actor_type="sync",
                reason=(
                    f"Reflecting Workable interview hand-off ({app.workable_stage}); "
                    "no live Taali reject second-opinion remains"
                ),
                idempotency_key=f"posthandover_heal_advanced:{app.id}",
            )
            return True
        return False

    if normalize_pipeline_stage(app.pipeline_stage) == "advanced":
        return False

    transition_stage(
        db,
        app=app,
        to_stage="advanced",
        source="sync",
        actor_type="sync",
        reason=f"Advanced in Workable ({app.workable_stage}) — reflecting the hand-off on Taali",
        idempotency_key=f"workable_handover_advance:{app.id}",
    )
    # A terminal hand-off freezes the candidate, so every queued decision is moot
    # — discard quietly so no stale reject/advance card lingers.
    try:
        discard_pending_decisions_for_app(
            db,
            application_id=int(app.id),
            reason=f"superseded: advanced in Workable ({app.workable_stage})",
        )
    except Exception:  # pragma: no cover — never block the reconcile
        import logging

        logging.getLogger("taali.pipeline_service").exception(
            "post-handover decision discard failed (application_id=%s)", app.id,
        )
    return True


def transition_outcome(
    db: Session,
    *,
    app: CandidateApplication,
    to_outcome: str,
    actor_type: str,
    actor_id: int | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    expected_version: int | None = None,
) -> CandidateApplication:
    ensure_pipeline_fields(app)
    target = normalize_application_outcome(to_outcome)
    from_outcome = normalize_application_outcome(app.application_outcome)
    if expected_version is not None and int(expected_version) != int(app.version or 0):
        raise HTTPException(
            status_code=409,
            detail=f"Version mismatch: expected={expected_version}, current={app.version}",
        )

    existing_idempotent = _existing_idempotent_event(
        db,
        application_id=app.id,
        idempotency_key=idempotency_key,
    )
    if existing_idempotent:
        return app

    if from_outcome == target:
        return app

    now = _utcnow()
    previous_status = app.status
    app.application_outcome = target
    app.application_outcome_updated_at = now
    app.status = status_from_pipeline(app.pipeline_stage, app.application_outcome)
    app.version = int(app.version or 1) + 1

    _append_event(
        db,
        app=app,
        event_type="application_outcome_changed",
        actor_type=actor_type,
        actor_id=actor_id,
        from_stage=app.pipeline_stage,
        to_stage=app.pipeline_stage,
        from_outcome=from_outcome,
        to_outcome=target,
        reason=reason,
        metadata={
            "legacy_status_before": previous_status,
            **(metadata or {}),
        },
        idempotency_key=idempotency_key,
    )

    # Best-effort hook imported here to avoid a hard agent-runtime dependency;
    # failures never block the canonical outcome change.
    try:
        from ...agent_runtime import outcome_learning
        outcome_learning.record_outcome_on_outcome_change(
            db, application=app, new_outcome=target,
            role_id=(metadata or {}).get("acting_role_id") or app.role_id,
        )
    except Exception:  # pragma: no cover — never block an outcome change
        import logging
        logging.getLogger("taali.pipeline_service").exception(
            "outcome_learning hook on outcome change failed (application_id=%s)",
            app.id,
        )

    # When an application closes, its queued agent decisions are moot —
    # discard them so the Review queue doesn't show live cards for candidates
    # already out of the funnel. In an approve/override flow the decision
    # being acted on is re-stamped to approved/overridden by the caller AFTER
    # this returns, so it still resolves correctly. Best-effort: never block
    # the outcome change.
    if target != "open":
        try:
            from ...services.pre_screen_decision_emitter import (
                discard_pending_decisions_for_app,
            )

            discard_pending_decisions_for_app(
                db,
                application_id=int(app.id),
                reason=f"superseded: application closed ({target})",
            )
        except Exception:  # pragma: no cover — never block an outcome change
            import logging
            logging.getLogger("taali.pipeline_service").exception(
                "discard_pending_decisions_for_app failed (application_id=%s)",
                app.id,
            )

    reconcile_related_roles_after_outcome(db, app)
    return app


def apply_legacy_status_update(
    db: Session,
    *,
    app: CandidateApplication,
    status: str,
    actor_type: str,
    actor_id: int | None = None,
    reason: str | None = None,
    expected_version: int | None = None,
) -> CandidateApplication:
    target_stage, target_outcome = map_legacy_status_to_pipeline(status)
    current_stage = normalize_pipeline_stage(app.pipeline_stage)
    current_outcome = normalize_application_outcome(app.application_outcome)
    legacy_metadata = {"legacy_status_input": status, "compatibility_mode": True}
    stage_changed = False
    next_expected_version = expected_version

    if target_stage != current_stage:
        path = _legacy_compatibility_path(current_stage, target_stage)
        if path is None:
            raise HTTPException(
                status_code=409,
                detail=f"Legacy status cannot reach stage {target_stage!r} from {current_stage!r}",
            )
        for stage_name, source_name in path:
            transition_stage(
                db,
                app=app,
                to_stage=stage_name,
                source=source_name,
                actor_type=actor_type,
                actor_id=actor_id,
                reason=reason or f"Legacy status update: {status}",
                metadata=legacy_metadata,
                expected_version=next_expected_version,
            )
            stage_changed = True
            next_expected_version = None
    if target_outcome != current_outcome:
        transition_outcome(
            db,
            app=app,
            to_outcome=target_outcome,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason or f"Legacy status update: {status}",
            metadata=legacy_metadata,
            expected_version=next_expected_version if not stage_changed else None,
        )
    app.status = status_from_pipeline(app.pipeline_stage, app.application_outcome)
    return app


# Recruiter-facing funnel buckets (DISPLAY), derived from the stored
# pipeline_stage + whether the CV has been scored. This is the single funnel
# vocabulary surfaced on the home hub, jobs list and role page:
#   Sourced   — stage `sourced` (a pre-applied prospect; un-scored, no decision).
#               Its OWN bucket — never folded into Applied, so it can't inflate
#               the applied count.
#   Applied   — stage `applied`, CV not yet scored (= "new CVs / ready to score")
#   Scored    — stage `applied`, CV scored, awaiting the send-assessment call
#   Invited   — stage `invited` + `in_assessment` (assessment out / in progress)
#   Completed — stage `review` (assessment done, awaiting advance/reject)
#   Advanced  — stage `advanced` (handed to recruiter)            } outcomes,
#   Rejected  — application_outcome `rejected` (across all stages) } outside the flow
# No DB enum change — the stored pipeline_stage is unchanged; this only buckets
# it for display.
FUNNEL_BUCKETS = ("sourced", "applied", "scored", "invited", "completed", "advanced", "rejected")


def funnel_bucket_for(stage_key: str, is_scored: bool) -> str | None:
    """Map a stored ``pipeline_stage`` (+ scored flag) to a display bucket.
    Returns None for stages with no open-bucket (none today). ``rejected`` is an
    outcome, counted separately by the callers below."""
    if stage_key == "sourced":
        # Its own bucket — a sourced prospect is pre-applied and un-scored, so it
        # must never count as `applied` (or `scored`).
        return "sourced"
    if stage_key == "applied":
        return "scored" if is_scored else "applied"
    if stage_key in ("invited", "in_assessment"):
        return "invited"
    if stage_key == "review":
        return "completed"
    if stage_key == "advanced":
        return "advanced"
    return None


# Configurable stages (flag ON): bucket by the stage's KIND so custom per-org
# stages map onto the same 6 display buckets the FE expects (FUNNEL_BUCKETS).
# Flag-off keeps the slug-based funnel_bucket_for above, byte-for-byte unchanged.
_KIND_TO_BUCKET = {
    "sourced": "sourced",
    "applied": "applied",
    "screening": "invited",
    "assessment": "invited",
    "review": "completed",
    "interview": "advanced",
    "offer": "advanced",
    "hired": "advanced",
}


def funnel_bucket_for_kind(kind: str | None, is_scored: bool) -> str | None:
    """Kind-based analogue of ``funnel_bucket_for`` (flag ON). Applies the #867
    scored composition ON TOP of the kind->bucket base: an EVALUATED candidate in
    an applied-kind stage buckets as ``scored``, never ``applied``. Unknown kinds
    return None (the caller leaves them out of the fixed buckets)."""
    base = _KIND_TO_BUCKET.get(kind or "")
    if base == "applied" and is_scored:
        return "scored"
    return base


def _org_stage_kind_map(db: Session, organization_id: int) -> dict[str, str] | None:
    """Always ``None`` — callers bucket by the legacy slug mapping. The ATS owns
    the pipeline; Tali no longer keeps an org-configurable stage set."""
    return None


# pipeline_stage values that normalise to the "invited" funnel stage (sent, not
# yet started) — mirrors normalize_pipeline_key()'s invited mapping.
_INVITED_STAGE_VALUES = ("invited", "pending", "assessment_sent")


def _invite_delivery_extra(
    db: Session,
    *,
    organization_id: int,
    role_ids: list[int],
    opened_only: bool,
) -> dict[int, int]:
    """Per-role count of INVITED-stage (sent, not yet started) candidates whose
    assessment invite the Resend webhook recorded as delivered / opened.

    A sub-count used to break the Invited funnel stage down on the hub. Callers
    ADD the ``in_assessment`` (started) count — starting implies delivered+opened
    even when the webhook missed the event — so this only covers the not-yet-
    started remainder. ``opened_only`` restricts to opened (else delivered-or-
    opened). Post-handover candidates are excluded to match the Invited bucket.
    """
    cond = (
        Assessment.invite_opened_at.isnot(None)
        if opened_only
        else or_(
            Assessment.invite_delivered_at.isnot(None),
            Assessment.invite_opened_at.isnot(None),
        )
    )
    rows = (
        db.query(
            CandidateApplication.role_id,
            func.count(func.distinct(CandidateApplication.id)),
        )
        .join(
            Assessment,
            and_(
                Assessment.candidate_id == CandidateApplication.candidate_id,
                Assessment.role_id == CandidateApplication.role_id,
                Assessment.organization_id == CandidateApplication.organization_id,
                Assessment.is_voided.is_(False),
            ),
        )
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.role_id.in_(role_ids),
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
            CandidateApplication.pipeline_stage.in_(_INVITED_STAGE_VALUES),
            _not_post_handover_sql(),
            cond,
        )
        .group_by(CandidateApplication.role_id)
        .all()
    )
    return {int(rid): int(n or 0) for rid, n in rows}


def role_pipeline_counts(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
) -> dict[str, int]:
    # "Scored" means a candidate carries a REAL role-fit score, not merely a
    # cv_match_scored_at timestamp — "Scored" means the platform has EVALUATED
    # the candidate: a real cv_match score OR a pre-screen score (including
    # pre-screen-filtered candidates, whose cv_match_score stays NULL — they
    # were evaluated at the cheap gate and screened out, and the pre-screen
    # reject chip sits under Scored). Counting only cv_match_score dropped the
    # filtered ones back into "Applied", making a fully pre-screened cohort
    # read as untouched. "Applied" = no evaluation of any kind yet.
    # `not_yet_decided` below uses the same basis so the chips reconcile.
    # The pre-screen side requires a GENUINE run (pre_screen_run_at):
    # pre_screen_score_100 is also written as a display value from the full
    # cv_match snapshot, and score invalidation nulls only cv_match_score —
    # without the guard an invalidated (awaiting re-score) candidate would
    # keep counting as Scored off the stale display value.
    scored_expr = or_(
        CandidateApplication.cv_match_score.isnot(None),
        and_(
            CandidateApplication.pre_screen_score_100.isnot(None),
            CandidateApplication.pre_screen_run_at.isnot(None),
        ),
    )
    # A candidate the recruiter has advanced in Workable (interview/offer/hired)
    # shows in the funnel as 'advanced' for alignment — the furthest stage wins —
    # regardless of Tali's pipeline_stage (which stays 'applied' for the backend).
    ph_expr = _post_handover_sql()
    rows = (
        db.query(
            CandidateApplication.pipeline_stage,
            scored_expr,
            ph_expr,
            func.count(CandidateApplication.id),
        )
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.role_id == role_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
        )
        .group_by(CandidateApplication.pipeline_stage, scored_expr, ph_expr)
        .all()
    )
    # `in_assessment` is a SUB-count of the `invited` bucket — assessments that
    # are in progress (issued and started, not yet completed). The hub surfaces
    # it as an "N in progress" chip under the Invited stage; it is not itself a
    # funnel bucket, so it never changes the headline stage totals.
    counts = {bucket: 0 for bucket in FUNNEL_BUCKETS}
    counts["in_assessment"] = 0
    kind_map = _org_stage_kind_map(db, organization_id)
    for stage, is_scored, is_post_handover, total in rows:
        if is_post_handover:
            counts["advanced"] += int(total or 0)
            continue
        normalized = normalize_pipeline_key(stage)
        if normalized == "in_assessment":
            counts["in_assessment"] += int(total or 0)
        if kind_map is not None:
            bucket = funnel_bucket_for_kind(kind_map.get(normalized), bool(is_scored))
        else:
            bucket = funnel_bucket_for(normalized, bool(is_scored))
        if bucket:
            counts[bucket] += int(total or 0)
    # `rejected` is an application_outcome, orthogonal to pipeline_stage, so it is
    # counted across all stages rather than via the open-stage query above.
    rejected_total = (
        db.query(func.count(CandidateApplication.id))
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.role_id == role_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "rejected",
        )
        .scalar()
    )
    counts["rejected"] = int(rejected_total or 0)
    # 'not_yet_decided' = scored, open candidates that carry NO agent decision
    # (pending OR resolved). The TRUE count for the funnel's "not yet decided"
    # chip — the frontend used to derive it as scored − pending, which
    # over-counted resolved candidates + the cv_match_scored_at basis (which
    # includes pre-screen-filtered candidates with no real score).
    not_yet_decided = (
        db.query(func.count(CandidateApplication.id))
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.role_id == role_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
            # Same evaluated basis as `scored_expr` above — pre-screen-filtered
            # candidates count as Scored, and the reconcile emits their reject
            # decision each agent tick, so any without one are genuinely in limbo.
            or_(
                CandidateApplication.cv_match_score.isnot(None),
                and_(
                    CandidateApplication.pre_screen_score_100.isnot(None),
                    CandidateApplication.pre_screen_run_at.isnot(None),
                ),
            ),
            # "Not yet decided BY THE AGENT" only means anything where the agent
            # is ON for the role (it may be paused — that's the usual case). On a
            # role with the agent OFF the recruiter decides manually, so there's
            # no agent verdict to await — don't count it as limbo.
            Role.agentic_mode_enabled.is_(True),
            # Exclude candidates already advanced in Workable (interview/offer/
            # hired) — they're being interviewed, not awaiting a Tali decision.
            _not_post_handover_sql(),
            ~(
                db.query(AgentDecision.id)
                .filter(
                    AgentDecision.application_id == CandidateApplication.id,
                    AgentDecision.status.in_(
                        ("pending", "processing", "approved", "overridden")
                    ),
                )
                .exists()
            ),
        )
        .scalar()
    )
    counts["not_yet_decided"] = int(not_yet_decided or 0)
    # Delivery sub-counts of the Invited stage. delivered/opened are CUMULATIVE
    # and include in_assessment (started → delivered+opened), so they nest:
    # sent (the bucket value) ≥ delivered ≥ opened ≥ in_progress.
    delivered_extra = _invite_delivery_extra(
        db, organization_id=organization_id, role_ids=[role_id], opened_only=False
    ).get(role_id, 0)
    opened_extra = _invite_delivery_extra(
        db, organization_id=organization_id, role_ids=[role_id], opened_only=True
    ).get(role_id, 0)
    counts["invited_delivered"] = counts["in_assessment"] + delivered_extra
    counts["invited_opened"] = counts["in_assessment"] + opened_extra
    return counts


def role_pipeline_counts_bulk(
    db: Session,
    *,
    organization_id: int,
    role_ids: list[int],
) -> dict[int, dict[str, int]]:
    """Batched ``role_pipeline_counts`` for many roles in two queries.

    Returns ``{role_id: {stage: count, ..., "rejected": count}}`` for every
    requested role (roles with no applications still get a zero-filled dict).
    The per-role helper does 2 queries each; callers iterating a role list —
    e.g. the Hub's /agent/roles/breakdown — would N+1 without this.
    """
    counts: dict[int, dict[str, int]] = {
        int(rid): {
            **{bucket: 0 for bucket in FUNNEL_BUCKETS},
            "not_yet_decided": 0,
            "in_assessment": 0,
            "invited_delivered": 0,
            "invited_opened": 0,
        }
        for rid in role_ids
    }
    if not role_ids:
        return counts

    # "Scored" = evaluated (real cv_match score OR a genuinely-run pre-screen,
    # which includes pre-screen-filtered candidates) — see role_pipeline_counts()
    # for why the pre-screen side requires pre_screen_run_at.
    scored_expr = or_(
        CandidateApplication.cv_match_score.isnot(None),
        and_(
            CandidateApplication.pre_screen_score_100.isnot(None),
            CandidateApplication.pre_screen_run_at.isnot(None),
        ),
    )
    # Workable-advanced candidates display as 'advanced' (alignment) regardless of
    # Tali's pipeline_stage — see role_pipeline_counts().
    ph_expr = _post_handover_sql()
    open_rows = (
        db.query(
            CandidateApplication.role_id,
            CandidateApplication.pipeline_stage,
            scored_expr,
            ph_expr,
            func.count(CandidateApplication.id),
        )
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.role_id.in_(role_ids),
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
        )
        .group_by(
            CandidateApplication.role_id,
            CandidateApplication.pipeline_stage,
            scored_expr,
            ph_expr,
        )
        .all()
    )
    kind_map = _org_stage_kind_map(db, organization_id)
    for role_id, stage, is_scored, is_post_handover, total in open_rows:
        bucket = counts.get(int(role_id))
        if bucket is None:
            continue
        if is_post_handover:
            bucket["advanced"] += int(total or 0)
            continue
        normalized = normalize_pipeline_key(stage)
        if normalized == "in_assessment":
            bucket["in_assessment"] += int(total or 0)
        if kind_map is not None:
            key = funnel_bucket_for_kind(kind_map.get(normalized), bool(is_scored))
        else:
            key = funnel_bucket_for(normalized, bool(is_scored))
        if key:
            bucket[key] += int(total or 0)

    # `rejected` is an application_outcome, orthogonal to pipeline_stage —
    # counted across all stages, mirroring role_pipeline_counts().
    rejected_rows = (
        db.query(
            CandidateApplication.role_id,
            func.count(CandidateApplication.id),
        )
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.role_id.in_(role_ids),
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "rejected",
        )
        .group_by(CandidateApplication.role_id)
        .all()
    )
    for role_id, total in rejected_rows:
        bucket = counts.get(int(role_id))
        if bucket is not None:
            bucket["rejected"] = int(total or 0)

    # 'not_yet_decided' per role — scored, open candidates with NO agent decision
    # (pending OR resolved). The TRUE count for the funnel's chip (see the
    # single-role helper). One batched query, NOT EXISTS against AgentDecision.
    nyd_rows = (
        db.query(CandidateApplication.role_id, func.count(CandidateApplication.id))
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.role_id.in_(role_ids),
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
            # Same evaluated basis as `scored_expr` — see role_pipeline_counts().
            or_(
                CandidateApplication.cv_match_score.isnot(None),
                and_(
                    CandidateApplication.pre_screen_score_100.isnot(None),
                    CandidateApplication.pre_screen_run_at.isnot(None),
                ),
            ),
            # Only roles with the agent ON — see role_pipeline_counts(). An
            # agent-off role's candidates aren't awaiting an agent verdict.
            Role.agentic_mode_enabled.is_(True),
            # Exclude candidates already advanced in Workable (interview/offer/
            # hired) — they're being interviewed, not awaiting a Tali decision.
            _not_post_handover_sql(),
            ~(
                db.query(AgentDecision.id)
                .filter(
                    AgentDecision.application_id == CandidateApplication.id,
                    AgentDecision.status.in_(
                        ("pending", "processing", "approved", "overridden")
                    ),
                )
                .exists()
            ),
        )
        .group_by(CandidateApplication.role_id)
        .all()
    )
    for role_id, total in nyd_rows:
        bucket = counts.get(int(role_id))
        if bucket is not None:
            bucket["not_yet_decided"] = int(total or 0)

    # Delivery sub-counts of the Invited stage (delivered/opened), cumulative
    # over in_assessment — see role_pipeline_counts(). Two more batched queries.
    delivered_map = _invite_delivery_extra(
        db, organization_id=organization_id, role_ids=role_ids, opened_only=False
    )
    opened_map = _invite_delivery_extra(
        db, organization_id=organization_id, role_ids=role_ids, opened_only=True
    )
    for rid in role_ids:
        bucket = counts[int(rid)]
        bucket["invited_delivered"] = bucket["in_assessment"] + delivered_map.get(int(rid), 0)
        bucket["invited_opened"] = bucket["in_assessment"] + opened_map.get(int(rid), 0)

    return counts
