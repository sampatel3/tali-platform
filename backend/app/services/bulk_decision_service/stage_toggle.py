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
from ..role_execution_guard import lock_live_role
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
        # Every production caller starts this reconciliation after committing
        # the role/task toggle. Re-acquire current authority in the platform's
        # canonical Organization -> Role order before touching a Decision row.
        # RoleIntent invalidation owns the same Role lock before discarding
        # decisions, so taking Decision first here would invert that order.
        with db.no_autoflush:
            role_id = int(role.id)
            organization_id = int(role.organization_id)
        role = lock_live_role(
            db,
            role_id=role_id,
            organization_id=organization_id,
        )
        if role is None:
            return 0

        # Read only the identities while Role is locked, then establish
        # Role -> Application -> Decision ownership in deterministic order.
        # Queueing a replacement later re-enters the same locks, never the
        # former Decision -> Role path. Rows are revalidated by the locking
        # query so a decision resolved before Role ownership is not rewritten.
        pending_refs = (
            db.query(AgentDecision.id, AgentDecision.application_id)
            .filter(
                AgentDecision.role_id == role_id,
                AgentDecision.status == "pending",
                AgentDecision.decision_type.in_(_POSITIVE_TYPES),
            )
            .order_by(AgentDecision.application_id, AgentDecision.id)
            .all()
        )
        if not pending_refs:
            return 0
        application_ids = sorted(
            {int(application_id) for _decision_id, application_id in pending_refs}
        )
        applications = (
            db.query(CandidateApplication)
            .filter(CandidateApplication.id.in_(application_ids))
            .order_by(CandidateApplication.id)
            .populate_existing()
            .with_for_update(of=CandidateApplication)
            .all()
        )
        applications_by_id = {int(app.id): app for app in applications}
        decision_ids = [int(decision_id) for decision_id, _app_id in pending_refs]
        pendings = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.id.in_(decision_ids),
                AgentDecision.role_id == role_id,
                AgentDecision.status == "pending",
                AgentDecision.decision_type.in_(_POSITIVE_TYPES),
            )
            .order_by(AgentDecision.application_id, AgentDecision.id)
            .populate_existing()
            .with_for_update(of=AgentDecision)
            .all()
        )
        now = datetime.now(timezone.utc)
        skip = bool(getattr(role, "auto_skip_assessment", False))
        for d in pendings:
            app = applications_by_id.get(int(d.application_id))
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
