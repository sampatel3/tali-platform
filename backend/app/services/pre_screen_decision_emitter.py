"""Emit a Decision Hub card when pre-screen scores a candidate below threshold.

Pre-screen is deterministic — if the score is < threshold, the recruiter
needs to see this candidate in the Review queue and either approve the
reject or override and keep them in pipeline. The original design (see
``application_automation_service.run_auto_reject_if_needed`` comment) was
for the agent's next cycle to queue this via ``queue_skip_assessment_reject_decision``,
but the agent's cohort planner never surveyed "below-threshold" candidates,
so they sat invisibly. 270 candidates went missing this way before this
emitter was wired in.

We don't run this through the agent because:
- The decision is deterministic (threshold check, no AI reasoning needed).
- Surfacing them via the agent means waiting for the next cron tick.
- Pre-screen rejects can volume up to thousands; the agent's per-cycle
  decision budget would just throttle them anyway (and we just removed
  that gate, so the only knob left would be cron cadence).

Decision type ``skip_assessment_reject`` is reused (existing type), so the
recruiter UI and downstream handlers don't change — these decisions look
and behave like any agent-queued reject. ``agent_run_id`` is NULL since
no agent run produced them (the column is already nullable).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.candidate_application_event import CandidateApplicationEvent
from ..models.role import Role

logger = logging.getLogger("taali.pre_screen_decision_emitter")


_DECISION_TYPE = "skip_assessment_reject"
_PROMPT_VERSION = "pre_screen_threshold.v1"
_MODEL_VERSION = "pre_screen_v1"  # deterministic; not an LLM


def _idempotency_key(application_id: int) -> str:
    return f"pre_screen_reject:{int(application_id)}"


def _below_threshold(
    score: float | None, recommendation: str | None, threshold: float | None
) -> bool:
    """Whether this candidate is a pre-screen reject under ``threshold`` —
    mirrors the deterministic gate in ``evaluate_auto_reject_decision``.

    A numeric score is authoritative AND only meaningful against a configured
    cutoff: with a numeric score we reject iff ``score < threshold`` and
    ``threshold is not None``. With no threshold there is no score-based
    reject — the ``'Below threshold'`` recommendation is a hard-coded ``< 50``
    label, not a role verdict, so it must NOT keep a numeric-score card alive
    after the cutoff is cleared.

    The recommendation only justifies a reject when there is *no* numeric
    score (must-have miss / invalidated score), with or without a threshold.
    """
    if score is not None:
        return threshold is not None and float(score) < float(threshold)
    return (recommendation or "").strip().lower() == "below threshold"


def _format_reasoning(score: float | None, threshold: float | None) -> str:
    score_str = f"{score:.1f}" if isinstance(score, (int, float)) else "unknown"
    thr_str = f"{threshold:.1f}" if isinstance(threshold, (int, float)) else "unknown"
    return (
        f"Below pre-screen threshold (score: {score_str}, threshold: {thr_str}). "
        f"Surfaced for recruiter review — approve to reject, override to keep in pipeline."
    )


def queue_pre_screen_reject(
    db: Session,
    *,
    organization_id: int,
    role: Role,
    application: CandidateApplication,
    pre_screen_score: float | None,
    threshold: float | None,
    evidence: Optional[dict[str, Any]] = None,
) -> AgentDecision | None:
    """Create a pending ``skip_assessment_reject`` ``AgentDecision`` for
    a candidate that failed pre-screen, *or* return the existing one if
    already queued. Returns None on unexpected error (never raises).

    Idempotent on ``application_id`` — re-running pre-screen against the
    same application produces at most one row.

    Gated on ``role.agentic_mode_enabled``: agent-OFF roles aren't under
    agent management, so we shouldn't auto-create Decision Hub cards on
    their behalf. The recruiter would see decisions appearing for roles
    they didn't enable the agent on — surprising and unwelcome.
    """
    if not bool(getattr(role, "agentic_mode_enabled", False)):
        return None
    try:
        key = _idempotency_key(int(application.id))
        # Skip if THIS app already has any pending decision — not just one
        # with our exact idempotency key. The original backfill (#201 + the
        # post-deploy run) only deduped on its own ``pre_screen_reject:*``
        # key, so apps that already had an agent-emitted ``reject`` row
        # got a *second* row from the backfill — recruiter saw the same
        # candidate twice. One pending per app, always.
        existing_pending = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.application_id == int(application.id),
                AgentDecision.status == "pending",
            )
            .order_by(AgentDecision.created_at.desc())
            .first()
        )
        if existing_pending is not None:
            return existing_pending

        body = {"pre_screen_score_100": pre_screen_score, "threshold_100": threshold}
        if evidence:
            body.update(evidence)

        decision = AgentDecision(
            organization_id=int(organization_id),
            role_id=int(role.id),
            application_id=int(application.id),
            agent_run_id=None,  # system-emitted; no agent cycle ran
            decision_type=_DECISION_TYPE,
            recommendation=_DECISION_TYPE,
            status="pending",
            reasoning=_format_reasoning(pre_screen_score, threshold),
            evidence=body,
            confidence=None,
            model_version=_MODEL_VERSION,
            prompt_version=_PROMPT_VERSION,
            idempotency_key=key,
            active_capabilities={},
            token_spend={},
        )
        db.add(decision)
        try:
            db.flush()
        except IntegrityError:
            # A row with this app's idempotency key already exists. Two cases:
            #   1. Race — a concurrent path inserted a *pending* row.
            #   2. Replay — a prior card for this app was discarded (e.g. a
            #      threshold 50→30 reconcile), and now we want one again
            #      (30→50). The key is per-application, so we can't insert a
            #      second row; the existing row is non-pending.
            # In case 2 we must REVIVE the existing row to pending, otherwise
            # the candidate is left with no pending card while ``created`` is
            # incremented — the silent-miss the reconcile replay path hit.
            db.rollback()
            existing = (
                db.query(AgentDecision)
                .filter(AgentDecision.idempotency_key == key)
                .first()
            )
            # Only revive a *system-discarded* card (what the reconcile /
            # supersede paths set: status='discarded' with NO human resolver).
            # Never reopen a recruiter resolution — that includes ``overridden``
            # / ``approved`` / ``reverted_for_feedback`` AND a recruiter
            # *discard* (the toggle-off bulk discard also sets
            # status='discarded' but stamps ``resolved_by_user_id``). The
            # cohort tick re-runs reconcile every cycle, so reviving any of
            # these would undo the human decision on every tick. Leave them
            # untouched and return as-is.
            if (
                existing is not None
                and existing.status == "discarded"
                and existing.resolved_by_user_id is None
            ):
                existing.status = "pending"
                existing.resolved_at = None
                existing.resolution_note = None
                existing.agent_run_id = None
                existing.reasoning = _format_reasoning(pre_screen_score, threshold)
                existing.evidence = body
                db.flush()
            return existing

        db.add(
            CandidateApplicationEvent(
                application_id=int(application.id),
                organization_id=int(organization_id),
                event_type="agent_decision_queued",
                actor_type="system",
                actor_id=None,
                reason="Queued pre-screen reject",
                idempotency_key=f"agent_decision_queued:pre_screen:{decision.id}",
                event_metadata={
                    "decision_id": int(decision.id),
                    "decision_type": _DECISION_TYPE,
                    "source": "pre_screen_threshold",
                    "pre_screen_score_100": pre_screen_score,
                    "threshold_100": threshold,
                },
            )
        )
        return decision
    except Exception:
        logger.exception(
            "queue_pre_screen_reject failed for application_id=%s",
            getattr(application, "id", None),
        )
        return None


def backfill_existing_below_threshold(
    db: Session, *, organization_id: int | None = None
) -> dict:
    """One-shot: queue a pre-screen-reject decision for every application
    that's currently below threshold and doesn't already have one.

    Two cohorts qualify:
      * ``pre_screen_score_100 < 50`` with a numeric score — the original
        case from the 270-stranded-candidates incident.
      * ``pre_screen_recommendation = 'Below threshold'`` with NULL score
        — covers candidates whose numeric score got invalidated (#209) or
        who short-circuited on a must-have miss without an LLM call. The
        recommendation is still authoritative; we shouldn't refuse to
        surface them just because the cache-invalidation path nulled the
        number.

    Intended to be invoked once after deploy so historical stranded
    candidates surface in the Review queue. Idempotent — safe to re-run;
    each application gets at most one row via the idempotency key.
    """
    from sqlalchemy import and_, or_

    q = (
        db.query(CandidateApplication, Role)
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            or_(
                and_(
                    CandidateApplication.pre_screen_score_100.isnot(None),
                    CandidateApplication.pre_screen_score_100 < 50,
                ),
                CandidateApplication.pre_screen_recommendation == "Below threshold",
            ),
            CandidateApplication.application_outcome == "open",
            Role.deleted_at.is_(None),
            # Only agent-on roles. Agent-off roles aren't under agent
            # management; surfacing decisions for them would surprise the
            # recruiter ("why are these candidates in my queue when I
            # never enabled the agent here?").
            Role.agentic_mode_enabled.is_(True),
        )
    )
    if organization_id is not None:
        q = q.filter(CandidateApplication.organization_id == int(organization_id))

    created = 0
    skipped_existing = 0
    failed = 0
    for app, role in q.all():
        # Skip apps that already have ANY pending decision, not just our
        # own pre_screen_reject key. The first version of this loop only
        # checked the latter and produced 21 duplicate rows in prod when
        # an agent-emitted reject already existed on the app.
        existing_pending = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.application_id == int(app.id),
                AgentDecision.status == "pending",
            )
            .first()
        )
        if existing_pending is not None:
            skipped_existing += 1
            continue
        result = queue_pre_screen_reject(
            db,
            organization_id=int(app.organization_id),
            role=role,
            application=app,
            pre_screen_score=float(app.pre_screen_score_100)
            if app.pre_screen_score_100 is not None
            else None,
            threshold=50.0,
        )
        if result is None:
            failed += 1
        else:
            created += 1
            db.commit()  # commit each row so a single failure doesn't roll back the batch

    return {"created": created, "skipped_existing": skipped_existing, "failed": failed}


def reconcile_pre_screen_reject_decisions(
    db: Session,
    *,
    role: Role,
    organization_id: int,
    threshold: float | None,
) -> dict:
    """Re-align the ``skip_assessment_reject`` queue with a role's new
    pre-screen threshold. Call this when the role's *effective* threshold
    changes (the ``score_threshold`` override or ``auto_reject_threshold_mode``).

    Moving the threshold changes the reject *verdict* for every candidate
    without changing any *score*. So — unlike ``mark_role_scores_stale`` —
    this does NOT invalidate or re-score anything. It only reconciles the
    deterministic reject cards:

    - **Discard** pending cards for candidates now at/above the new
      threshold. Asking the recruiter to approve a reject the current
      cutoff wouldn't produce is exactly the stale-card problem this
      fixes. Cards whose ``pre_screen_recommendation == 'Below threshold'``
      are kept — that verdict is independent of the numeric cutoff
      (must-have miss / invalidated score), same carve-out the emitter and
      backfill already honour.
    - **Emit** a card for every open candidate now below the new threshold
      that doesn't already have a pending decision (reuses
      ``queue_pre_screen_reject`` and its one-pending-per-app invariant).

    No-op for agent-off roles (we don't manage cards there) and for roles
    with ``auto_reject`` on (those disqualify in Workable directly rather
    than carding — that path is the auto-reject task's job, not the Hub).

    Returns ``{"discarded": int, "created": int, "skipped_existing": int}``.
    """
    from sqlalchemy import and_, func, or_

    if not bool(getattr(role, "agentic_mode_enabled", False)):
        return {"discarded": 0, "created": 0, "skipped_existing": 0}
    if bool(getattr(role, "auto_reject", False)):
        return {"discarded": 0, "created": 0, "skipped_existing": 0}

    now = datetime.now(timezone.utc)

    # --- Discard cards the new threshold no longer justifies -------------
    if threshold is not None:
        discard_note = f"superseded: pre-screen threshold changed to {threshold:.1f}"
    else:
        discard_note = "superseded: pre-screen threshold cleared"
    pending_cards = (
        db.query(AgentDecision, CandidateApplication)
        .join(
            CandidateApplication,
            CandidateApplication.id == AgentDecision.application_id,
        )
        .filter(
            AgentDecision.role_id == int(role.id),
            AgentDecision.status == "pending",
            AgentDecision.decision_type == _DECISION_TYPE,
        )
        .all()
    )
    discarded = 0
    for decision, app in pending_cards:
        if _below_threshold(
            app.pre_screen_score_100, app.pre_screen_recommendation, threshold
        ):
            continue  # still a valid reject under the new cutoff — keep
        decision.status = "discarded"
        decision.resolved_at = now
        decision.resolution_note = discard_note[:500]
        discarded += 1
    if discarded:
        # Commit the discards before the emit loop. ``queue_pre_screen_reject``
        # issues a full ``db.rollback()`` if it loses an insert race, which
        # would otherwise drop these discards along with the racing row.
        db.commit()

    # --- Emit cards for candidates now below the new threshold -----------
    created = 0
    skipped_existing = 0
    # Case/space-insensitive match — the decider and the discard path both
    # normalize ``pre_screen_recommendation``, so non-canonical stored values
    # ("below threshold", trailing space) must count here too or they'd be
    # treated as below-threshold by policy yet never get a reconciled card.
    rec_below = (
        func.lower(func.trim(func.coalesce(CandidateApplication.pre_screen_recommendation, "")))
        == "below threshold"
    )
    # No numeric score → the 'Below threshold' recommendation (must-have miss
    # / invalidated score) is the reject signal. This branch holds even when
    # ``threshold`` is None (a cleared/auto-fallback threshold), so rec-only
    # rejects aren't stranded.
    below_conditions = [
        and_(CandidateApplication.pre_screen_score_100.is_(None), rec_below)
    ]
    if threshold is not None:
        # Numeric score is authoritative against the cutoff.
        below_conditions.append(
            and_(
                CandidateApplication.pre_screen_score_100.isnot(None),
                CandidateApplication.pre_screen_score_100 < float(threshold),
            )
        )
    below = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.role_id == int(role.id),
            CandidateApplication.organization_id == int(organization_id),
            CandidateApplication.application_outcome == "open",
            or_(*below_conditions),
        )
        .all()
    )
    for app in below:
            existing_pending = (
                db.query(AgentDecision)
                .filter(
                    AgentDecision.application_id == int(app.id),
                    AgentDecision.status == "pending",
                )
                .first()
            )
            if existing_pending is not None:
                skipped_existing += 1
                continue
            result = queue_pre_screen_reject(
                db,
                organization_id=int(app.organization_id),
                role=role,
                application=app,
                pre_screen_score=float(app.pre_screen_score_100)
                if app.pre_screen_score_100 is not None
                else None,
                threshold=float(threshold) if threshold is not None else None,
            )
            if result is not None:
                created += 1
                db.commit()  # per-row so one race doesn't roll back the batch

    return {
        "discarded": discarded,
        "created": created,
        "skipped_existing": skipped_existing,
    }


def rederive_pre_screen_recommendations(
    db: Session,
    *,
    role_id: int | None = None,
    organization_id: int | None = None,
) -> dict:
    """Correct stored ``pre_screen_recommendation`` labels that the old
    hard-coded ``< 50`` rule over-flagged as "Below threshold".

    Rows scored before the label became threshold-aware keep a stale
    "Below threshold" even when the candidate is *above* the role's actual
    cutoff (e.g. a 40-scorer on a role that rejects at 30). This relabels
    them in place — no LLM re-run — so the displayed verdict matches the
    threshold the agent actually rejects on.

    Deliberately **relax-only**: it only touches rows currently labelled
    "Below threshold" whose score is at/above the role's cutoff, and only
    ever moves them *off* the reject label. It never introduces a new
    "Below threshold" (which could trip the label-keyed auto-reject hooks
    in applications_routes). The reject *decisions* are reconciled
    separately and score-authoritatively by
    ``reconcile_pre_screen_reject_decisions``.

    Skips:
    - NULL-score rows — their "Below threshold" is a must-have-miss /
      invalidated verdict, not score-derivable.
    - fraud-capped rows — "Below threshold" there is a fraud verdict
      forced independently of the score band.
    - roles with no configured threshold — nothing to compare against.

    Returns ``{"updated": int, "scanned": int}``.
    """
    # Lazy imports: the emitter is imported by application_automation_service
    # alongside pre_screening_service, so importing the latter at module load
    # would risk a cycle. Inside the function it's safe.
    from sqlalchemy import func

    from .pre_screening_service import resolved_auto_reject_config
    from .pre_screening_snapshot import pre_screen_recommendation_label

    q = (
        db.query(CandidateApplication, Role)
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            CandidateApplication.pre_screen_score_100.isnot(None),
            # Case/space-insensitive — the decider normalizes too, so
            # non-canonical stored labels ("below threshold", trailing space)
            # must be corrected here as well or the self-heal never converges.
            func.lower(func.trim(func.coalesce(CandidateApplication.pre_screen_recommendation, "")))
            == "below threshold",
            CandidateApplication.deleted_at.is_(None),
            Role.deleted_at.is_(None),
        )
    )
    if role_id is not None:
        q = q.filter(CandidateApplication.role_id == int(role_id))
    if organization_id is not None:
        q = q.filter(CandidateApplication.organization_id == int(organization_id))

    threshold_cache: dict[int, float | None] = {}
    updated = 0
    scanned = 0
    for app, role in q.all():
        scanned += 1
        evidence = app.pre_screen_evidence if isinstance(app.pre_screen_evidence, dict) else {}
        if evidence.get("fraud_capped"):
            continue
        if role.id not in threshold_cache:
            threshold_cache[role.id] = resolved_auto_reject_config(None, role, db=db)["threshold_100"]
        threshold = threshold_cache[role.id]
        if threshold is None:
            continue
        if float(app.pre_screen_score_100) < float(threshold):
            continue  # genuinely below the cutoff — keep the reject label
        new_label = pre_screen_recommendation_label(app.pre_screen_score_100, threshold)
        if new_label and new_label != "Below threshold":
            app.pre_screen_recommendation = new_label
            updated += 1
    if updated:
        db.commit()
    return {"updated": updated, "scanned": scanned}
