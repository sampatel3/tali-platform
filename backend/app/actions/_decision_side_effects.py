"""Best-effort side effects after a recruiter resolves an ``AgentDecision``.

Centralizes the three slow effects that fire *after* the decision's actual
state change (advance / reject / send) has committed:

1. Workable stage move (advance) or disqualify (reject) — the GATED /
   *critical* writeback. Under ``strict_workable_writes`` (the decision-batch
   path) a failure here raises ``WorkableWritebackError`` so the batch can
   re-queue the decision rather than commit a Tali-only change.
2. The Workable activity-feed summary note + 30-day report share link.
3. The recruiter-action graph episode (Graphiti — an LLM indexing call,
   the slowest of the three).

Steps 2 and 3 are *best-effort*: a failure in either only logs / records an
event, never raises and never re-queues.

These ran inline on the approve / override request and added 20-30s to
every click. The synchronous path (agent runs, tests) and the deferred
Celery task (``app.tasks.decision_tasks.apply_decision_side_effects``)
both call ``apply_decision_side_effects`` so the logic stays identical no
matter where it runs.

``steps`` lets a caller run only part of the work: the bulk-approve batch
keeps the GATED step 1 INLINE (under ``strict_workable_writes`` so a failed
critical write still re-queues) while deferring the best-effort steps 2+3 to
the Celery task — so a 100-row batch drains fast and releases the per-org
Workable mutex instead of doing a Graphiti/Voyage LLM call per decision while
holding it. ``"all"`` (the default) preserves the original behavior.
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


# Which subset of the three side effects to run. The batch path splits them:
# the GATED Workable writeback (step 1) runs inline + strict so a failure
# re-queues, while the best-effort note + graph episode (steps 2+3) defer to
# the Celery task off the serialized per-org mutex.
SIDE_EFFECTS_ALL = "all"
SIDE_EFFECTS_CRITICAL = "critical"  # step 1 only (gated Workable writeback)
SIDE_EFFECTS_BEST_EFFORT = "best_effort"  # steps 2 + 3 only


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
    steps: str = SIDE_EFFECTS_ALL,
) -> None:
    """Run the side effects for a resolved decision.

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

    ``steps`` selects which effects run: ``"all"`` (default) runs everything;
    ``"critical"`` runs only the gated Workable writeback (step 1); and
    ``"best_effort"`` runs only the summary note + graph episode (steps 2+3).
    The bulk-approve batch uses the split — critical inline (re-queues on
    failure), best-effort deferred to Celery — so it isn't blocked on a
    per-decision Graphiti/Voyage call while holding the per-org mutex.
    """
    verdict = verdict_for(
        disposition=disposition,
        decision_type=decision.decision_type,
        override_action=override_action,
    )

    if steps in (SIDE_EFFECTS_ALL, SIDE_EFFECTS_CRITICAL):
        _apply_critical_workable_writeback(
            db,
            actor,
            decision=decision,
            app=app,
            org=org,
            role=role,
            verdict=verdict,
            note=note,
            workable_target_stage=workable_target_stage,
            reject_notify=reject_notify,
        )

    if steps in (SIDE_EFFECTS_ALL, SIDE_EFFECTS_BEST_EFFORT):
        _apply_best_effort_side_effects(
            db,
            actor,
            decision=decision,
            app=app,
            org=org,
            verdict=verdict,
            disposition=disposition,
            override_action=override_action,
            note=note,
        )


def _apply_critical_workable_writeback(
    db: Session,
    actor: Actor,
    *,
    decision: AgentDecision,
    app: Optional[CandidateApplication],
    org: Optional[Organization],
    role: Optional[Role],
    verdict: Optional[str],
    note: Optional[str],
    workable_target_stage: Optional[str],
    reject_notify: bool,
) -> None:
    """Step 1: the gated Workable stage move (advance) or disqualify (reject).

    The only side effect that may raise: under ``strict_workable_writes`` a
    failed move / disqualify propagates ``WorkableWritebackError`` so the
    decision-batch path re-queues instead of committing a Tali-only change.
    """
    if app is None:
        return
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


def _apply_best_effort_side_effects(
    db: Session,
    actor: Actor,
    *,
    decision: AgentDecision,
    app: Optional[CandidateApplication],
    org: Optional[Organization],
    verdict: Optional[str],
    disposition: str,
    override_action: Optional[str],
    note: Optional[str],
) -> None:
    """Steps 2 + 3: the Workable summary note and the recruiter-action graph
    episode. Both best-effort — a failure only logs / records an event, never
    raises and never re-queues. Deferred to Celery for the bulk-approve batch.
    """
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
