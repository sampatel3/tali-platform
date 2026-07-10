"""Re-flow pending positive decisions after an assessment-stage toggle.

Flipping ``role.auto_skip_assessment`` changes what the deterministic
verdict translates to (send_assessment <-> advance_to_interview), but
already-queued PENDING cards keep their old type — so a role marked as
skipping assessments would still have recruiters approving assessment
invites (Codex review on #866). This converts them in place: discard the
stale card and immediately re-queue through ``ensure_deterministic_decision``
so the Decision Hub reflects the new routing without waiting for the next
agent tick.

Deliberately scoped to the two POSITIVE types only — a toggle flip must
never mint new reject cards.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ...models.agent_decision import AgentDecision
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ._shared import recompute_persisted_verdict
from .score_time import ensure_deterministic_decision

logger = logging.getLogger("taali.bulk_decision")

_POSITIVE_TYPES = ("send_assessment", "advance_to_interview")


def reconcile_pending_positive_decisions(db: Session, *, role: Role) -> int:
    """Convert pending send<->advance cards whose deterministic verdict
    flipped under the role's CURRENT assessment-stage config. Covers both
    directions (skip toggled on: send -> advance; toggled off: advance ->
    send). Includes LLM-queued cards, not just bulk-deterministic ones —
    the approve path executes the stored type verbatim, so a stale send
    card would still fire an invite on a skip-toggled role.

    Best-effort: returns the number of cards converted; never raises.
    Does NOT commit — the caller commits.
    """
    converted = 0
    try:
        pendings = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.role_id == int(role.id),
                AgentDecision.status == "pending",
                AgentDecision.decision_type.in_(_POSITIVE_TYPES),
            )
            .all()
        )
        now = datetime.now(timezone.utc)
        skip = bool(getattr(role, "auto_skip_assessment", False))
        for d in pendings:
            app = (
                db.query(CandidateApplication)
                .filter(CandidateApplication.id == d.application_id)
                .one_or_none()
            )
            if app is None:
                continue
            new_type = recompute_persisted_verdict(db, role=role, app=app)
            # Only positive->positive flips; anything else (None, reject,
            # unchanged) leaves the existing card for its usual owners.
            if new_type not in _POSITIVE_TYPES or new_type == d.decision_type:
                continue
            d.status = "discarded"
            d.resolved_at = now
            d.resolution_note = (
                f"assessment stage {'skipped' if skip else 'restored'} by recruiter; "
                f"re-deciding ({d.decision_type} → {new_type})"
            )[:500]
            db.flush()  # free the one-pending-per-app slot before re-queueing
            ensure_deterministic_decision(db, app=app, role=role)
            converted += 1
        if converted:
            logger.info(
                "assessment-stage toggle reconcile role=%s converted=%d",
                role.id, converted,
            )
    except Exception:  # noqa: BLE001 — toggle save must never fail on this
        logger.exception(
            "reconcile_pending_positive_decisions failed role=%s",
            getattr(role, "id", "?"),
        )
    return converted
