"""Recruiter approves a queued ``AgentDecision``.

Resolves the queue row to ``approved`` and dispatches the underlying
action with ``actor=recruiter`` so the audit row records *the recruiter*
as the one who made the change — with metadata pointing back to the
agent's reasoning and run id.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from . import advance_stage, reject_application, resend_assessment_invite, send_assessment
from ._workable_decision_summary import (
    post_decision_summary_to_workable,
    try_workable_advance,
)
from .types import ACTOR_RECRUITER, Actor


_REJECT_DECISION_TYPES = ("reject", "skip_assessment_reject")
_VERDICT_BY_DECISION_TYPE = {
    "advance_to_interview": "advanced",
    "reject": "rejected",
    "skip_assessment_reject": "rejected",
    "send_assessment": "assessment_sent",
    "resend_assessment_invite": "invite_resent",
}


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    decision_id: int,
    note: Optional[str] = None,
    workable_target_stage: Optional[str] = None,
) -> AgentDecision:
    if actor.type != ACTOR_RECRUITER:
        raise HTTPException(status_code=403, detail="approve is recruiter-only")

    # C2: row-level lock on the decision. Two recruiters approving the
    # same pending decision in the same second would otherwise both pass
    # the ``status != "pending"`` check and both dispatch the underlying
    # action — double Workable POST, double pipeline-stage event, double
    # candidate email. ``with_for_update`` blocks the second request
    # until the first commits; it then sees ``status='approved'`` and
    # 409s. SQLite tests ignore the row lock (no NOWAIT support) which
    # is fine since the race only matters in real production traffic.
    decision_query = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == decision_id,
            AgentDecision.organization_id == organization_id,
        )
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        decision_query = decision_query.with_for_update()
    decision = decision_query.first()
    if decision is None:
        raise HTTPException(status_code=404, detail=f"agent_decision {decision_id} not found")
    # ``reverted_for_feedback`` is a taught-but-not-yet-resolved decision —
    # the whole point of "teach" is that the corrected row can then be
    # approved/overridden, so it must remain actionable alongside ``pending``.
    if decision.status not in ("pending", "reverted_for_feedback"):
        raise HTTPException(
            status_code=409,
            detail=f"agent_decision {decision_id} is {decision.status}, not actionable",
        )

    metadata = {
        "agent_decision_id": int(decision.id),
        "agent_run_id": int(decision.agent_run_id) if decision.agent_run_id else None,
        "agent_reasoning": decision.reasoning,
        "model_version": decision.model_version,
        "prompt_version": decision.prompt_version,
    }
    reason = (note or "").strip() or f"Approved agent recommendation #{decision.id}"
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(decision.application_id),
            CandidateApplication.organization_id == organization_id,
        )
        .first()
    )
    org = (
        db.query(Organization).filter(Organization.id == organization_id).first()
        if app is not None
        else None
    )
    role = getattr(app, "role", None) if app is not None else None

    if decision.decision_type == "advance_to_interview":
        advance_stage.run(
            db,
            actor,
            organization_id=organization_id,
            application_id=int(decision.application_id),
            to_stage="advanced",
            reason=reason,
            idempotency_key=f"approve_decision:{decision.id}",
            metadata=metadata,
        )
        if app is not None:
            try_workable_advance(
                db,
                actor,
                app=app,
                org=org,
                role=role,
                target_stage=workable_target_stage,
                reason=reason,
            )
    elif decision.decision_type in _REJECT_DECISION_TYPES:
        reject_application.run(
            db,
            actor,
            organization_id=organization_id,
            application_id=int(decision.application_id),
            reason=reason,
            idempotency_key=f"approve_decision:{decision.id}",
            metadata={**metadata, "decision_type": decision.decision_type},
        )
    elif decision.decision_type == "send_assessment":
        # Evidence (set when the agent queued the decision) may carry the
        # task_id / duration_minutes the agent picked. Fall back to the
        # send_assessment defaults when absent.
        ev = decision.evidence or {}
        send_assessment.run(
            db,
            actor,
            organization_id=organization_id,
            application_id=int(decision.application_id),
            task_id=int(ev["task_id"]) if ev.get("task_id") is not None else None,
            duration_minutes=int(ev.get("duration_minutes") or 90),
        )
    elif decision.decision_type == "resend_assessment_invite":
        ev = decision.evidence or {}
        assessment_id = ev.get("assessment_id")
        if assessment_id is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"resend_assessment_invite decision {decision.id} is missing "
                    "evidence.assessment_id — cannot dispatch."
                ),
            )
        resend_assessment_invite.run(
            db,
            actor,
            organization_id=organization_id,
            assessment_id=int(assessment_id),
        )
    else:
        raise HTTPException(
            status_code=422,
            detail=f"unknown decision_type={decision.decision_type!r}",
        )

    decision.status = "approved"
    decision.resolved_at = datetime.now(timezone.utc)
    decision.resolved_by_user_id = actor.user_id
    decision.resolution_note = note
    decision.human_disposition = "approved"

    # Best-effort Workable activity note so the recruiter's Workable view
    # records who/why/score + a 30-day share link to the full Tali report.
    # No-op when Workable isn't connected or the application isn't linked.
    verdict = _VERDICT_BY_DECISION_TYPE.get(decision.decision_type)
    if app is not None and verdict:
        try:
            post_decision_summary_to_workable(
                db,
                actor,
                app=app,
                org=org,
                decision=decision,
                verdict=verdict,
                reason=note,
            )
        except Exception:
            import logging
            logging.getLogger("taali.actions.approve_decision").warning(
                "decision-summary post raised for decision_id=%s",
                getattr(decision, "id", None),
            )

    # Phase 2 §6.7: emit a recruiter-action episode (low volume — one
    # per resolved decision). Never blocks the response.
    try:
        from ..candidate_graph import agent_episodes
        agent_episodes.emit_recruiter_action_event(
            organization_id=int(organization_id),
            decision_id=int(decision.id),
            recruiter_id=int(actor.user_id) if actor.user_id else 0,
            action="approve",
            reason=note,
            happened_at=decision.resolved_at,
        )
    except Exception:
        import logging
        logging.getLogger("taali.actions.approve_decision").warning(
            "recruiter-action episode emit failed for decision_id=%s",
            getattr(decision, "id", None),
        )
    return decision
