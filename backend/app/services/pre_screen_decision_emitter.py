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
            # Race: another path inserted it between our SELECT and our INSERT.
            db.rollback()
            return (
                db.query(AgentDecision)
                .filter(AgentDecision.idempotency_key == key)
                .first()
            )

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

    Intended to be invoked once after deploy so historical stranded
    candidates (the 270 we found in prod) surface in the Review queue.
    Idempotent — safe to re-run; each application gets at most one row
    via the idempotency key.
    """
    q = (
        db.query(CandidateApplication, Role)
        .join(Role, Role.id == CandidateApplication.role_id)
        .filter(
            CandidateApplication.pre_screen_score_100.isnot(None),
            CandidateApplication.pre_screen_score_100 < 50,
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
