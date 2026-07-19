"""HTTP routes for the Hub's teach loop ("Send back & teach").

Split from the read-side ``hub_routes`` so each module stays under the
500-LOC architecture gate.

  POST /agent-decisions/{id}/snooze   hide a pending row for 1h
  POST /agent/feedback                "Send back & teach" — creates a decision_feedback
  POST /agent/feedback/{id}/cosign    second-admin co-sign for org-scope teach
  POST /agent/feedback/{id}/revert    1h grace-window undo
  GET  /agent/feedback                list teach events (SIGNAL section)
  GET  /agent/rubric-revisions        list rubric revisions (SIGNAL audit)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ._hub_shared import (
    CosignResult,
    FEEDBACK_REVERT_GRACE,
    FeedbackBody,
    FeedbackCreateResult,
    FeedbackPayload,
    RevertResult,
    SNOOZE_DURATION,
    SnoozeResult,
    feedback_payload,
    now_utc,
)
from ...actions import teach_decision as teach_decision_action
from ...actions.types import Actor
from ...deps import get_current_user
from ...models.agent_decision import AgentDecision
from ...models.decision_feedback import (
    ATTRIBUTED_TO_VALUES,
    FAILURE_MODES,
    FEEDBACK_DIRECTIONS,
    FEEDBACK_SCOPES,
    DecisionFeedback,
)
from ...models.user import User
from ...platform.database import get_db


router = APIRouter(tags=["agentic-hub"])
logger = logging.getLogger("taali.agentic.hub_feedback")


# ---------------------------------------------------------------------------
# POST /agent-decisions/{id}/snooze
# ---------------------------------------------------------------------------


@router.post("/agent-decisions/{decision_id}/snooze", response_model=SnoozeResult)
def snooze_decision(
    decision_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == decision_id,
            AgentDecision.organization_id == current_user.organization_id,
        )
        .first()
    )
    if decision is None:
        raise HTTPException(status_code=404, detail=f"agent_decision {decision_id} not found")
    if decision.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"only pending decisions can be snoozed (got {decision.status})",
        )

    decision.snoozed_until = now_utc() + SNOOZE_DURATION
    try:
        db.commit()
        db.refresh(decision)
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to snooze agent decision %s", decision_id)
        raise HTTPException(status_code=500, detail="Failed to snooze decision") from exc
    return SnoozeResult(
        decision_id=int(decision.id),
        snoozed_until=decision.snoozed_until,
    )


# ---------------------------------------------------------------------------
# POST /agent/feedback
# ---------------------------------------------------------------------------


@router.post("/agent/feedback", response_model=FeedbackCreateResult)
def create_feedback(
    body: FeedbackBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if body.failure_mode not in FAILURE_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported failure_mode={body.failure_mode!r}",
        )
    if body.scope not in FEEDBACK_SCOPES:
        raise HTTPException(status_code=422, detail=f"unsupported scope={body.scope!r}")
    if body.attributed_to is not None and body.attributed_to not in ATTRIBUTED_TO_VALUES:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported attributed_to={body.attributed_to!r}",
        )
    if body.direction is not None and body.direction not in FEEDBACK_DIRECTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported direction={body.direction!r}",
        )

    try:
        feedback, decision = teach_decision_action.run(
            db,
            Actor.recruiter(current_user),
            organization_id=current_user.organization_id,
            decision_id=body.decision_id,
            failure_mode=body.failure_mode,
            correction_text=body.correction_text,
            scope=body.scope,
            role_id=body.role_id,
            attributed_to=body.attributed_to,
            direction=body.direction,
            graph_write_hints=body.graph_write_hints,
        )
        db.commit()
        db.refresh(feedback)
        db.refresh(decision)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to create feedback for decision %s", body.decision_id)
        raise HTTPException(status_code=500, detail="Failed to create feedback") from exc

    # We deliberately do NOT promise the user any automated scoring/agent
    # retune. Scoring and decision-making improvements are a separate
    # workstream — see docs/HOME_HUB_DESIGN.md §8. The feedback row +
    # cosign tray + audit trail are enough surface on their own.
    payload = feedback_payload(
        db,
        feedback,
        organization_id=current_user.organization_id,
    )
    return FeedbackCreateResult(
        feedback=payload,
        decision_status=str(decision.status),
    )


# ---------------------------------------------------------------------------
# POST /agent/feedback/{id}/cosign
# ---------------------------------------------------------------------------


@router.post("/agent/feedback/{feedback_id}/cosign", response_model=CosignResult)
def cosign_feedback(
    feedback_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    feedback = (
        db.query(DecisionFeedback)
        .filter(
            DecisionFeedback.id == feedback_id,
            DecisionFeedback.organization_id == current_user.organization_id,
        )
        .first()
    )
    if feedback is None:
        raise HTTPException(status_code=404, detail=f"feedback {feedback_id} not found")
    if not feedback.cosign_required:
        raise HTTPException(
            status_code=409,
            detail="feedback does not require co-sign",
        )
    if feedback.cosigned_at is not None:
        raise HTTPException(status_code=409, detail="feedback is already co-signed")
    if feedback.reviewer_id == current_user.id:
        raise HTTPException(
            status_code=403,
            detail="the reviewer who submitted org-scope feedback cannot co-sign their own submission",
        )
    if feedback.reverted_at is not None:
        raise HTTPException(status_code=409, detail="feedback has been reverted")

    feedback.cosigned_by_user_id = int(current_user.id)
    feedback.cosigned_at = now_utc()
    try:
        db.commit()
        db.refresh(feedback)
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to co-sign feedback %s", feedback_id)
        raise HTTPException(status_code=500, detail="Failed to co-sign feedback") from exc
    payload = feedback_payload(
        db,
        feedback,
        organization_id=current_user.organization_id,
    )
    return CosignResult(feedback=payload)


# ---------------------------------------------------------------------------
# POST /agent/feedback/{id}/revert
# ---------------------------------------------------------------------------


@router.post("/agent/feedback/{feedback_id}/revert", response_model=RevertResult)
def revert_feedback(
    feedback_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    feedback = (
        db.query(DecisionFeedback)
        .filter(
            DecisionFeedback.id == feedback_id,
            DecisionFeedback.organization_id == current_user.organization_id,
        )
        .first()
    )
    if feedback is None:
        raise HTTPException(status_code=404, detail=f"feedback {feedback_id} not found")
    if feedback.reverted_at is not None:
        raise HTTPException(status_code=409, detail="feedback already reverted")
    if feedback.applied_at is not None:
        raise HTTPException(
            status_code=409,
            detail="feedback already applied to a rubric revision; cannot revert",
        )

    # ``created_at`` may come back tz-naive on SQLite (server-default
    # ``now()`` returns naive). Promote it to UTC for the comparison.
    created = feedback.created_at
    if created.tzinfo is None:
        from datetime import timezone

        created = created.replace(tzinfo=timezone.utc)
    grace_deadline = created + FEEDBACK_REVERT_GRACE
    if now_utc() > grace_deadline:
        raise HTTPException(
            status_code=409,
            detail="grace window has closed (1h after creation)",
        )

    decision = (
        db.query(AgentDecision)
        .filter(AgentDecision.id == feedback.decision_id)
        .first()
    )
    if decision is None:
        # Defensive: should never happen because of FK, but keep it safe.
        feedback.reverted_at = now_utc()
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.exception("Failed to revert orphaned feedback %s", feedback_id)
            raise HTTPException(status_code=500, detail="Failed to revert feedback") from exc
        return RevertResult(
            feedback_id=int(feedback.id),
            decision_id=int(feedback.decision_id),
            decision_status="orphaned",
        )

    # Restore the decision to ``pending`` so the queue picks it up again.
    # The decision retains its prior reasoning/evidence; the human_disposition
    # is cleared because the teach action no longer counts.
    decision.status = "pending"
    decision.resolved_at = None
    decision.resolved_by_user_id = None
    decision.resolution_note = None
    decision.feedback_id = None
    decision.human_disposition = None

    feedback.reverted_at = now_utc()
    try:
        db.commit()
        db.refresh(decision)
        db.refresh(feedback)
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to revert feedback %s", feedback_id)
        raise HTTPException(status_code=500, detail="Failed to revert feedback") from exc
    return RevertResult(
        feedback_id=int(feedback.id),
        decision_id=int(decision.id),
        decision_status=str(decision.status),
    )


# ---------------------------------------------------------------------------
# GET /agent/feedback
# ---------------------------------------------------------------------------


@router.get("/agent/feedback", response_model=list[FeedbackPayload])
def list_feedback(
    role_id: Optional[int] = Query(default=None),
    since: Optional[datetime] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(DecisionFeedback).filter(
        DecisionFeedback.organization_id == current_user.organization_id
    )
    if role_id is not None:
        q = q.filter(DecisionFeedback.role_id == int(role_id))
    if since is not None:
        q = q.filter(DecisionFeedback.created_at >= since)
    rows = q.order_by(desc(DecisionFeedback.created_at)).limit(limit).all()
    return [
        feedback_payload(db, r, organization_id=current_user.organization_id)
        for r in rows
    ]


# NOTE: ``GET /agent/rubric-revisions`` was removed deliberately. The
# ``rubric_revisions`` table + model still exist as quiet infrastructure
# for the future scoring/decision-making rework, but we don't surface an
# endpoint or any UI affordance until that rework is real. We don't want
# to imply automated retunes are happening when they aren't.
