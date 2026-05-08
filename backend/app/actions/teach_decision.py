"""Recruiter sends a decision back to teach the agent.

This is the third action on every pending decision (alongside approve and
override). It does three things at once:

1. Inserts a ``decision_feedback`` row capturing the reviewer's correction,
   tagged failure mode, and scope (this decision / role / org).
2. Flips the source decision's ``status`` to ``reverted_for_feedback`` so it
   reappears in the queue with the prior correction attached.
3. For ``scope`` of ``role`` or ``org`` the row is also the input to the
   nightly retune job. ``scope='org'`` requires a second admin to co-sign
   before the retune fires (``cosign_required=True``).

The actual retune pipeline (consuming ``decision_feedback`` rows and
producing ``rubric_revisions``) lives outside this action — see
``docs/HOME_HUB_DESIGN.md §5.4``. This action is only the ingestion side.

Idempotency: a teach action on a decision already in ``reverted_for_feedback``
status is allowed (replaces the prior feedback row's effect on the decision
pointer; the prior row stays in history). A teach action on a decision in
any other terminal state (``approved``, ``overridden``, ``discarded``,
``expired``) returns 409.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.decision_feedback import (
    FAILURE_MODES,
    FEEDBACK_SCOPES,
    DecisionFeedback,
)
from .types import ACTOR_RECRUITER, Actor


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    decision_id: int,
    failure_mode: str,
    correction_text: str,
    scope: str,
    role_id: Optional[int] = None,
) -> Tuple[DecisionFeedback, AgentDecision]:
    if actor.type != ACTOR_RECRUITER:
        raise HTTPException(status_code=403, detail="teach is recruiter-only")
    if failure_mode not in FAILURE_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported failure_mode={failure_mode!r}",
        )
    if scope not in FEEDBACK_SCOPES:
        raise HTTPException(status_code=422, detail=f"unsupported scope={scope!r}")
    if not (correction_text or "").strip():
        raise HTTPException(status_code=422, detail="correction_text is required")

    decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == decision_id,
            AgentDecision.organization_id == organization_id,
        )
        .first()
    )
    if decision is None:
        raise HTTPException(status_code=404, detail=f"agent_decision {decision_id} not found")
    if decision.status not in ("pending", "reverted_for_feedback"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"agent_decision {decision_id} is {decision.status}; "
                "only pending decisions can be sent back & taught"
            ),
        )

    # ``role`` scope must carry a role_id — default to the decision's role
    # so the frontend doesn't have to repeat itself. ``org`` scope ignores
    # any role_id (always null in the row).
    resolved_role_id: Optional[int]
    if scope == "org":
        resolved_role_id = None
    elif scope == "role":
        resolved_role_id = int(role_id) if role_id is not None else int(decision.role_id)
    else:  # decision
        resolved_role_id = int(decision.role_id)

    cosign_required = scope == "org"

    feedback = DecisionFeedback(
        decision_id=int(decision.id),
        reviewer_id=int(actor.user_id),
        organization_id=int(organization_id),
        role_id=resolved_role_id,
        failure_mode=failure_mode,
        correction_text=correction_text.strip(),
        scope=scope,
        cosign_required=cosign_required,
    )
    db.add(feedback)
    db.flush()  # populate feedback.id so we can backref it on the decision

    now = datetime.now(timezone.utc)
    decision.status = "reverted_for_feedback"
    decision.resolved_at = now
    decision.resolved_by_user_id = int(actor.user_id)
    decision.resolution_note = correction_text.strip()
    decision.feedback_id = int(feedback.id)
    decision.human_disposition = "taught"

    return feedback, decision
