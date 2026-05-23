"""Recruiter overrides a queued ``AgentDecision`` and picks an alternative.

Closes the loop. The recruiter doesn't just disagree — they say what
should happen instead, and the underlying action runs server-side with
``actor=recruiter`` so the audit trail records the recruiter as the
one who took the action. ``override_action`` is one of:

  - ``reject``                  → reject_application.run
  - ``advance``                 → advance_stage.run (to next stage)
  - ``skip_assessment_advance`` → advance_stage.run + metadata flag
  - ``send_assessment``         → send_assessment.run
  - ``hold`` / None             → no side effect (legacy "just disagree")

The free-text ``note`` is the recruiter's "why" — fed to calibration
so the agent learns from the disagreement on top of the concrete
action.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from . import advance_stage, reject_application, send_assessment
from ._workable_decision_summary import (
    post_decision_summary_to_workable,
    try_workable_advance,
)
from .types import ACTOR_RECRUITER, Actor


_OVERRIDE_DISPATCH_ACTIONS = {
    "reject",
    "advance",
    "skip_assessment_advance",
    "send_assessment",
}
_VERDICT_BY_OVERRIDE_ACTION = {
    "reject": "rejected",
    "advance": "advanced",
    "skip_assessment_advance": "skip_advanced",
    "send_assessment": "assessment_sent",
}
# Legacy override_action values that older clients pass — we still
# accept them as no-op overrides (status=overridden, no side effect on
# candidate) so the v0 "manual_review" UI path doesn't 422. Eventually
# the migrated clients drop these and we can tighten the allowlist.
_OVERRIDE_LEGACY_NOOP = {
    "manual_review",
    "hold",
}


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    decision_id: int,
    override_action: Optional[str] = None,
    note: Optional[str] = None,
    workable_target_stage: Optional[str] = None,
) -> AgentDecision:
    if actor.type != ACTOR_RECRUITER:
        raise HTTPException(status_code=403, detail="override is recruiter-only")

    # C2: row-level lock so two concurrent overrides don't both dispatch.
    # See approve_decision.run for the full reasoning.
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
    # ``reverted_for_feedback`` (taught) decisions stay actionable so a
    # recruiter can override the corrected row, not just freshly-pending ones.
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
        "override_reason": note,
    }
    idempotency = f"override_decision:{decision.id}"

    # Dispatch the alternative action the recruiter picked. None / "hold"
    # are no-ops (legacy "just disagree" branch — kept for back-compat
    # while the UI rolls out).
    if override_action == "reject":
        reject_application.run(
            db,
            actor,
            organization_id=organization_id,
            application_id=int(decision.application_id),
            reason=(note or "").strip() or f"Override reject on agent decision #{decision.id}",
            idempotency_key=idempotency,
            metadata={**metadata, "override_action": override_action},
        )
    elif override_action in ("advance", "skip_assessment_advance"):
        advance_stage.run(
            db,
            actor,
            organization_id=organization_id,
            application_id=int(decision.application_id),
            to_stage="advanced",
            reason=(note or "").strip() or f"Override advance on agent decision #{decision.id}",
            idempotency_key=idempotency,
            metadata={**metadata, "override_action": override_action},
        )
        _adv_app = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == int(decision.application_id),
                CandidateApplication.organization_id == organization_id,
            )
            .first()
        )
        if _adv_app is not None:
            _adv_org = (
                db.query(Organization)
                .filter(Organization.id == organization_id)
                .first()
            )
            try_workable_advance(
                db,
                actor,
                app=_adv_app,
                org=_adv_org,
                role=getattr(_adv_app, "role", None),
                target_stage=workable_target_stage,
                reason=(note or "").strip() or "Recruiter advanced via override",
            )
    elif override_action == "send_assessment":
        send_result = send_assessment.run(
            db,
            actor,
            organization_id=organization_id,
            application_id=int(decision.application_id),
            task_id=None,
            duration_minutes=90,
        )
        # send_assessment can no-op (misconfigured / insufficient_credits /
        # already_exists / no_candidate / voided). Don't mark the decision
        # overridden in those cases — the candidate didn't get an email
        # and closing the queue row would lose the pending state without
        # actually doing the override (Codex #192).
        send_status = getattr(send_result, "status", None)
        if send_status not in ("sent", "already_exists"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"send_assessment override failed (status={send_status!r}). "
                    "Decision remains pending."
                ),
            )
    elif (
        override_action
        and override_action not in _OVERRIDE_DISPATCH_ACTIONS
        and override_action not in _OVERRIDE_LEGACY_NOOP
    ):
        raise HTTPException(
            status_code=422,
            detail=f"unknown override_action={override_action!r}",
        )

    decision.status = "overridden"
    decision.resolved_at = datetime.now(timezone.utc)
    decision.resolved_by_user_id = actor.user_id
    decision.override_action = override_action
    decision.resolution_note = note
    decision.human_disposition = "overridden"

    # Best-effort Workable activity note for any candidate-affecting
    # override. No-op for the legacy hold / manual_review / None branch
    # (those don't change candidate state, so they don't need a Workable
    # audit entry).
    verdict = _VERDICT_BY_OVERRIDE_ACTION.get(override_action or "")
    if verdict:
        _app = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.id == int(decision.application_id),
                CandidateApplication.organization_id == organization_id,
            )
            .first()
        )
        if _app is not None:
            _org = (
                db.query(Organization)
                .filter(Organization.id == organization_id)
                .first()
            )
            try:
                post_decision_summary_to_workable(
                    db,
                    actor,
                    app=_app,
                    org=_org,
                    decision=decision,
                    verdict=verdict,
                    override_action=override_action,
                    reason=note,
                )
            except Exception:
                import logging
                logging.getLogger("taali.actions.override_decision").warning(
                    "decision-summary post raised for decision_id=%s",
                    getattr(decision, "id", None),
                )

    # Phase 2 §6.7: emit a recruiter-action episode. The override
    # action (what the recruiter manually did instead) rides in the
    # reason so the graph extractor can pick it up.
    try:
        from ..candidate_graph import agent_episodes
        reason_parts = []
        if override_action:
            reason_parts.append(f"override_action={override_action}")
        if note:
            reason_parts.append(note)
        agent_episodes.emit_recruiter_action_event(
            organization_id=int(organization_id),
            decision_id=int(decision.id),
            recruiter_id=int(actor.user_id) if actor.user_id else 0,
            action="override",
            reason=" | ".join(reason_parts) if reason_parts else None,
            happened_at=decision.resolved_at,
        )
    except Exception:
        import logging
        logging.getLogger("taali.actions.override_decision").warning(
            "recruiter-action episode emit failed for decision_id=%s",
            getattr(decision, "id", None),
        )
    return decision
