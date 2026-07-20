"""Best-effort side effects after a recruiter resolves an ``AgentDecision``.

Centralizes the three slow, best-effort effects that fire *after* the
decision's actual state change (advance / reject / send) has committed:

1. Workable stage move (advance) or disqualify (reject).
2. The Workable activity-feed summary note + 30-day report share link.
3. A durable recruiter-action graph intent for later indexing.

These ran inline on the approve / override request and added 20-30s to
every click. The synchronous path (agent runs, tests) and the deferred
Celery task (``app.tasks.decision_tasks.apply_decision_side_effects``)
both call ``apply_decision_side_effects`` so the logic stays identical no
matter where it runs. Every step is wrapped: a failure in one never
aborts the others or raises to the caller.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import ROLE_KIND_SISTER, Role
from ..services.workable_actions_service import WorkableWritebackError
from ._workable_decision_summary import (
    post_decision_summary_to_workable,
    try_workable_advance,
)
from .types import Actor

logger = logging.getLogger("taali.actions.decision_side_effects")


def _organization_resolution_guard_statement(
    organization_id: int,
    *,
    exclusive: bool = False,
):
    """Lock the workspace key before any decision-resolution row locks.

    Graph provider admission takes ``Organization`` then ``Role``. Holding a
    key-share lock first gives approval/override the same order while still
    allowing ordinary foreign-key inserts, including the graph outbox row.

    Assessment creation later locks the same organization ``FOR UPDATE`` while
    reserving capacity. Those paths must take the exclusive lock here instead
    of upgrading a held ``KEY SHARE`` lock: two concurrent send resolutions
    could otherwise each hold the weak lock while waiting to upgrade past the
    other.
    """
    statement = select(Organization.id).where(
        Organization.id == int(organization_id)
    )
    if exclusive:
        return statement.with_for_update()
    return statement.with_for_update(read=True, key_share=True)


def lock_organization_for_decision_resolution(
    db: Session,
    *,
    organization_id: int,
    exclusive: bool = False,
) -> None:
    """Acquire the organization-first lock-order guard for a resolution."""
    organization = db.execute(
        _organization_resolution_guard_statement(
            int(organization_id),
            exclusive=exclusive,
        )
    ).scalar_one_or_none()
    if organization is None:
        raise RuntimeError(f"organization {int(organization_id)} not found")


# Plain-English verdict for the resolved decision — drives which Workable
# writeback runs and the headline on the activity note.
VERDICT_BY_DECISION_TYPE = {
    "advance_to_interview": "advanced",
    "reject": "rejected",
    "skip_assessment_reject": "rejected",
    # Invite decisions commit a durable *delivery intent*, not a confirmed
    # candidate contact. Their Workable stage/note is owned exclusively by the
    # provider-success handoff outbox; posting a decision summary here would
    # falsely claim a send when Resend later rejects it.
}
VERDICT_BY_OVERRIDE_ACTION = {
    "reject": "rejected",
    "advance": "advanced",
    "skip_assessment_advance": "skip_advanced",
}


def verdict_for(
    *,
    disposition: str,
    decision_type: Optional[str],
    override_action: Optional[str],
) -> Optional[str]:
    """Resolve the verdict for an approved / overridden decision."""
    if disposition == "overridden":
        return VERDICT_BY_OVERRIDE_ACTION.get(override_action or "")
    return VERDICT_BY_DECISION_TYPE.get(decision_type or "")


def _operational_role(
    db: Session,
    *,
    app: CandidateApplication,
    decision_role: Optional[Role],
) -> Optional[Role]:
    if (
        decision_role is None
        or str(decision_role.role_kind or "") != ROLE_KIND_SISTER
    ):
        return decision_role
    owner_id = int(decision_role.ats_owner_role_id or app.role_id or 0)
    owner = db.get(Role, owner_id) if owner_id else None
    if owner is None or int(owner.organization_id) != int(app.organization_id):
        return decision_role
    return owner


def apply_decision_side_effects(
    db: Session,
    actor: Actor,
    *,
    decision: AgentDecision,
    app: Optional[CandidateApplication],
    org: Optional[Organization],
    role: Optional[Role],
    disposition: str,
    override_action: Optional[str] = None,
    note: Optional[str] = None,
    workable_target_stage: Optional[str] = None,
    reject_notify: bool = True,
) -> None:
    """Run all best-effort side effects for a resolved decision.

    Best-effort and never raises EXCEPT under ``strict_workable_writes`` (the
    decision-batch path), where a failed critical Workable writeback (stage
    move / disqualify) raises ``WorkableWritebackError`` so the batch task can
    abort + re-queue the decision instead of committing a Tali-only change.
    The activity note (step 2) and graph episode (step 3) stay best-effort
    regardless.

    ``disposition`` is ``"approved"`` or ``"overridden"``. ``reject_notify``
    is the caller's "this resolution is what freshly rejected the candidate"
    signal — guards against re-disqualifying / re-emailing a candidate who
    was already rejected by another path (mirrors the inline freshness check
    that used to live in ``reject_application.run``).
    """
    verdict = verdict_for(
        disposition=disposition,
        decision_type=decision.decision_type,
        override_action=override_action,
    )

    # 1. Workable stage move (advance) or disqualify (reject).
    if app is not None:
        if verdict in ("advanced", "skip_advanced"):
            writeback_role = _operational_role(
                db,
                app=app,
                decision_role=role,
            )
            try:
                try_workable_advance(
                    db,
                    actor,
                    app=app,
                    org=org,
                    role=writeback_role,
                    target_stage=workable_target_stage,
                    reason=(note or "").strip()
                    or "Advanced by recruiter (decision resolution)",
                )
            except WorkableWritebackError:
                # strict (batch) path — propagate so the batch can re-queue.
                raise
            except Exception:  # pragma: no cover — defensive
                logger.warning(
                    "workable advance raised for decision_id=%s",
                    getattr(decision, "id", None),
                )
        elif verdict == "rejected" and reject_notify:
            try:
                from .reject_application import notify_rejection

                notify_rejection(db, app=app, actor=actor, reason=note)
            except WorkableWritebackError:
                # strict (batch) path — propagate so the batch can re-queue.
                raise
            except Exception:  # pragma: no cover — defensive
                logger.warning(
                    "rejection notify raised for decision_id=%s",
                    getattr(decision, "id", None),
                )

    # 2. Workable activity-feed summary note (+ 30-day report share link).
    if app is not None and verdict:
        try:
            post_decision_summary_to_workable(
                db,
                actor,
                app=app,
                org=org,
                decision=decision,
                verdict=verdict,
                override_action=override_action,
                reason=note,
            )
        except Exception:  # pragma: no cover — defensive
            logger.warning(
                "decision-summary post raised for decision_id=%s",
                getattr(decision, "id", None),
            )

    # Flush the decision/Workable state before opening the optional savepoint.
    # SQLAlchemy otherwise performs this flush while entering begin_nested();
    # a core-state failure there could be mistaken for a disposable graph
    # failure and leave the outer transaction unusable.
    db.flush()

    # 3. Recruiter-action graph episode (Phase 2 §6.7) — persist a durable
    # outbox row in this transaction. Graphiti and its provider metering run
    # later in the graph-outbox worker, after the approval/Workable transaction
    # commits, so optional graph work can never hold up a recruiter action.
    try:
        from ..candidate_graph import episode_outbox

        recruiter_id = int(actor.user_id) if actor.user_id else 0
        if disposition == "overridden":
            reason_parts: list[str] = []
            if override_action:
                reason_parts.append(f"override_action={override_action}")
            if note:
                reason_parts.append(note)
            action = "override"
            reason = " | ".join(reason_parts) if reason_parts else None
        else:
            action = "approve"
            reason = note
        # The savepoint makes this optional write truly best-effort: a rollout
        # mismatch or dedup race can roll back only the outbox insert, leaving
        # the already-confirmed Workable/decision transaction committable.
        with db.begin_nested():
            episode_outbox.enqueue_recruiter_action(
                db,
                organization_id=int(decision.organization_id),
                role_id=int(decision.role_id),
                decision_id=int(decision.id),
                recruiter_id=recruiter_id,
                action=action,
                reason=reason,
                happened_at=decision.resolved_at,
            )
    except Exception:  # pragma: no cover — defensive
        logger.warning(
            "recruiter-action episode enqueue failed for decision_id=%s",
            getattr(decision, "id", None),
            exc_info=True,
        )
