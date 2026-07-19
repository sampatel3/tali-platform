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
from ..agent_policy_settings import effective_auto_skip_assessment
from ._shared import recompute_persisted_verdict
from .score_time import ensure_deterministic_decision

logger = logging.getLogger("taali.bulk_decision")

_POSITIVE_TYPES = ("send_assessment", "advance_to_interview")


class _ReplacementNotCreated(RuntimeError):
    """The stale card must remain actionable when re-queueing is refused."""


def reconcile_pending_positive_decisions(
    db: Session,
    *,
    role_id: int,
    expected_role_version: int,
) -> int:
    """Convert pending send<->advance cards whose deterministic verdict
    flipped under the role's CURRENT assessment-stage config. Covers both
    directions (skip toggled on: send -> advance; toggled off: advance ->
    send). Includes LLM-queued cards, not just bulk-deterministic ones —
    the approve path executes the stored type verbatim, so a stale send
    card would still fire an invite on a skip-toggled role.

    The caller supplies the exact committed Role revision that changed the
    effective assessment stage. We lock Organization -> Role/version first,
    then acquire application and decision rows with ``SKIP LOCKED``. Reflow
    therefore never waits on an application while holding provider authority,
    and a delayed older revision cannot overwrite a newer recruiter setting.
    Each discard + replacement is one savepoint: if the replacement producer
    declines or fails, the original pending card is restored instead of being
    silently lost.

    Best-effort: returns the number of cards converted; never raises. Does NOT
    commit — the caller commits. Call only as a clean post-commit follow-up.
    """
    converted = 0
    try:
        role_identity = (
            db.query(Role.organization_id)
            .filter(Role.id == int(role_id), Role.deleted_at.is_(None))
            .one_or_none()
        )
        if role_identity is None:
            return 0

        from ..workspace_agent_control import workspace_agent_control_snapshot

        workspace_agent_control_snapshot(
            db,
            organization_id=int(role_identity.organization_id),
            lock=True,
        )
        role = (
            db.query(Role)
            .filter(
                Role.id == int(role_id),
                Role.organization_id == int(role_identity.organization_id),
                Role.deleted_at.is_(None),
            )
            .populate_existing()
            .with_for_update(of=Role)
            .one_or_none()
        )
        if role is None:
            return 0
        if int(role.version or 1) != int(expected_role_version):
            logger.info(
                "assessment-stage toggle reconcile superseded role=%s "
                "expected_version=%s current_version=%s",
                role.id,
                expected_role_version,
                role.version,
            )
            return 0

        pending_snapshot = (
            db.query(AgentDecision.id, AgentDecision.application_id)
            .filter(
                AgentDecision.role_id == int(role.id),
                AgentDecision.status == "pending",
                AgentDecision.decision_type.in_(_POSITIVE_TYPES),
            )
            .order_by(AgentDecision.application_id, AgentDecision.id)
            .all()
        )
        if not pending_snapshot:
            return 0
        decision_ids = [int(row.id) for row in pending_snapshot]
        application_ids = sorted(
            {int(row.application_id) for row in pending_snapshot}
        )
        applications = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id.in_(application_ids),
                CandidateApplication.role_id == int(role.id),
                CandidateApplication.organization_id
                == int(role.organization_id),
            )
            .order_by(CandidateApplication.id)
            .populate_existing()
            # Provider paths may already hold an application while waiting on
            # this authority fence. Skipping avoids an inverse wait; its card
            # remains fail-closed at approve/auto-execute revalidation.
            .with_for_update(of=CandidateApplication, skip_locked=True)
            .all()
        )
        applications_by_id = {int(app.id): app for app in applications}

        pendings = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.id.in_(decision_ids),
                AgentDecision.role_id == int(role.id),
                AgentDecision.status == "pending",
                AgentDecision.decision_type.in_(_POSITIVE_TYPES),
            )
            .order_by(AgentDecision.id)
            # A recruiter may already hold one card while waiting on its
            # application row. Never wait in the inverse direction; approval
            # performs the same current-verdict check and fails stale cards
            # closed.
            .with_for_update(of=AgentDecision, skip_locked=True)
            .all()
        )
        now = datetime.now(timezone.utc)
        skip = effective_auto_skip_assessment(role)
        for d in pendings:
            app = applications_by_id.get(int(d.application_id))
            if app is None:
                continue
            new_type = recompute_persisted_verdict(db, role=role, app=app)
            # Only positive->positive flips; anything else (None, reject,
            # unchanged) leaves the existing card for its usual owners.
            if new_type not in _POSITIVE_TYPES or new_type == d.decision_type:
                continue
            nested = db.begin_nested()
            replacement = None
            try:
                old_type = str(d.decision_type)
                d.status = "discarded"
                d.resolved_at = now
                d.resolution_note = (
                    f"assessment stage {'skipped' if skip else 'restored'} by "
                    f"recruiter; re-deciding ({old_type} → {new_type})"
                )[:500]
                db.flush()  # free the one-pending-per-app slot before re-queueing
                replacement_type = ensure_deterministic_decision(
                    db,
                    app=app,
                    role=role,
                    # Keep external candidate actions outside this replacement
                    # savepoint. They run only after the new card is durable in
                    # the caller-owned transaction.
                    allow_auto_execute=False,
                )
                if replacement_type != new_type:
                    raise _ReplacementNotCreated(
                        f"replacement {new_type!r} was not created"
                    )
                replacement = (
                    db.query(AgentDecision)
                    .filter(
                        AgentDecision.application_id == int(app.id),
                        AgentDecision.id != int(d.id),
                        AgentDecision.decision_type == new_type,
                        AgentDecision.status == "pending",
                    )
                    .order_by(AgentDecision.id.desc())
                    .first()
                )
                if replacement is None:
                    raise _ReplacementNotCreated(
                        f"replacement row {new_type!r} was not persisted"
                    )
                nested.commit()
            except _ReplacementNotCreated:
                nested.rollback()
                logger.warning(
                    "assessment-stage toggle kept original pending card "
                    "role=%s decision=%s requested_type=%s",
                    role.id,
                    d.id,
                    new_type,
                )
                continue
            except Exception as exc:
                nested.rollback()
                logger.warning(
                    "assessment-stage toggle replacement failed "
                    "role=%s decision=%s requested_type=%s error_type=%s",
                    role.id,
                    d.id,
                    new_type,
                    type(exc).__name__,
                )
                continue
            converted += 1
            try:
                from ...agent_runtime.tool_registry import (
                    maybe_auto_execute_decision,
                )
                from ...domains.assessments_runtime.pipeline_service import (
                    is_post_handover_workable_stage,
                )

                maybe_auto_execute_decision(
                    db,
                    role=role,
                    decision=replacement,
                    decision_type=new_type,
                    on_policy=True,
                    force_human_review=is_post_handover_workable_stage(
                        getattr(app, "workable_stage", None)
                    ),
                )
            except Exception as exc:
                # The replacement remains pending and actionable. Automatic
                # action dispatch is best-effort and isolated from the atomic
                # discard/replacement boundary above.
                logger.warning(
                    "assessment-stage replacement auto-action failed "
                    "role=%s decision=%s error_type=%s",
                    role.id,
                    getattr(replacement, "id", None),
                    type(exc).__name__,
                )
        if converted:
            logger.info(
                "assessment-stage toggle reconcile role=%s version=%s converted=%d",
                role.id,
                expected_role_version,
                converted,
            )
    except Exception as exc:  # noqa: BLE001 — toggle save must never fail on this
        logger.warning(
            "reconcile_pending_positive_decisions failed role=%s error_type=%s",
            role_id,
            type(exc).__name__,
        )
        # This API is intentionally a clean post-commit follow-up. Clear any
        # failed lock/query transaction so its caller can continue safely.
        db.rollback()
    return converted
