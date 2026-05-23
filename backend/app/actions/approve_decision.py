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
from ._decision_side_effects import apply_decision_side_effects
from .types import ACTOR_RECRUITER, Actor


_REJECT_DECISION_TYPES = ("reject", "skip_assessment_reject")


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    decision_id: int,
    note: Optional[str] = None,
    workable_target_stage: Optional[str] = None,
    collect_side_effects: Optional[dict] = None,
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
    if decision.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"agent_decision {decision_id} is {decision.status}, not pending",
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

    # "Did this approval freshly reject the candidate?" — gates the
    # background Workable disqualify / rejection email so an already-rejected
    # candidate isn't notified twice. Set in the reject branch below.
    reject_notify = False

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
    elif decision.decision_type in _REJECT_DECISION_TYPES:
        prev_outcome = (
            getattr(app, "application_outcome", None) if app is not None else None
        )
        reject_application.run(
            db,
            actor,
            organization_id=organization_id,
            application_id=int(decision.application_id),
            reason=reason,
            idempotency_key=f"approve_decision:{decision.id}",
            metadata={**metadata, "decision_type": decision.decision_type},
            defer_notify=True,
        )
        reject_notify = bool(
            app is not None
            and prev_outcome != "rejected"
            and getattr(app, "application_outcome", None) == "rejected"
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

    # Best-effort side effects (Workable writeback + recruiter-action graph
    # episode). By default they run inline (agent runs, tests). When the
    # caller passes ``collect_side_effects`` — the approve / bulk-approve
    # routes do — we skip the inline work and hand the route what it needs to
    # enqueue the deferred Celery task post-commit, so the recruiter's click
    # returns immediately instead of waiting on slow Workable / LLM calls.
    if collect_side_effects is None:
        apply_decision_side_effects(
            db,
            actor,
            decision=decision,
            app=app,
            org=org,
            role=role,
            disposition="approved",
            note=note,
            workable_target_stage=workable_target_stage,
            reject_notify=reject_notify,
        )
    else:
        collect_side_effects["reject_notify"] = reject_notify

    return decision
