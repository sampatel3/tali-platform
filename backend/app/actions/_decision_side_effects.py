"""Best-effort side effects after a recruiter resolves an ``AgentDecision``.

Centralizes the three slow, best-effort effects that fire *after* the
decision's actual state change (advance / reject / send) has committed:

1. Workable stage move (advance) or disqualify (reject).
2. The Workable activity-feed summary note + 30-day report share link.
3. The recruiter-action graph episode (Graphiti — an LLM indexing call).

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

from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from ..services.workable_actions_service import WorkableWritebackError
from ._workable_decision_summary import (
    post_decision_summary_to_workable,
    try_workable_advance,
)
from .types import Actor

logger = logging.getLogger("taali.actions.decision_side_effects")


# Plain-English verdict for the resolved decision — drives which Workable
# writeback runs and the headline on the activity note.
VERDICT_BY_DECISION_TYPE = {
    "advance_to_interview": "advanced",
    "reject": "rejected",
    "skip_assessment_reject": "rejected",
    "send_assessment": "assessment_sent",
    "resend_assessment_invite": "invite_resent",
}
VERDICT_BY_OVERRIDE_ACTION = {
    "reject": "rejected",
    "advance": "advanced",
    "skip_assessment_advance": "skip_advanced",
    "send_assessment": "assessment_sent",
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
            try:
                try_workable_advance(
                    db,
                    actor,
                    app=app,
                    org=org,
                    role=role,
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

    # 3. Recruiter-action graph episode (Phase 2 §6.7) — one per resolved
    # decision. The override action / note rides in the reason so the graph
    # extractor can learn from the disagreement.
    try:
        from ..candidate_graph import agent_episodes

        recruiter_id = int(actor.user_id) if actor.user_id else 0
        if disposition == "overridden":
            reason_parts: list[str] = []
            if override_action:
                reason_parts.append(f"override_action={override_action}")
            if note:
                reason_parts.append(note)
            agent_episodes.emit_recruiter_action_event(
                organization_id=int(decision.organization_id),
                decision_id=int(decision.id),
                recruiter_id=recruiter_id,
                action="override",
                reason=" | ".join(reason_parts) if reason_parts else None,
                happened_at=decision.resolved_at,
            )
        else:
            agent_episodes.emit_recruiter_action_event(
                organization_id=int(decision.organization_id),
                decision_id=int(decision.id),
                recruiter_id=recruiter_id,
                action="approve",
                reason=note,
                happened_at=decision.resolved_at,
            )
    except Exception:  # pragma: no cover — defensive
        logger.warning(
            "recruiter-action episode emit failed for decision_id=%s",
            getattr(decision, "id", None),
        )
