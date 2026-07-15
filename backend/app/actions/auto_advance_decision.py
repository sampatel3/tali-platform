"""Provider-confirmed completion for an agent auto-advance decision.

External-ATS advances are two-phase:

1. the agent transaction leaves the decision in ``processing`` and records a
   replay-safe ATS move operation;
2. the serialized ATS worker confirms the provider write, then calls
   :func:`complete` to advance the local pipeline and resolve the decision.

Keeping the completion here (rather than in the agent tool registry) gives the
Workable and Bullhorn handlers one provider-neutral state transition while
preserving their provider-specific write receipts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.assessment import Assessment
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from ..services.agent_policy_settings import automation_enabled_for_decision
from ..services.workable_actions_service import WorkableWritebackError
from . import advance_stage
from ._decision_side_effects import apply_decision_side_effects
from .types import Actor


@dataclass(frozen=True)
class AutoAdvanceContext:
    decision: AgentDecision
    application: CandidateApplication
    organization: Organization
    role: Role


def _dispatch_error(*, code: str, message: str, retriable: bool) -> WorkableWritebackError:
    # The durable ATS shell intentionally uses one transport-neutral error type
    # for Workable and Bullhorn operations.
    return WorkableWritebackError(
        action="auto_advance",
        code=code,
        message=message,
        retriable=retriable,
    )


def preflight(
    db: Session,
    *,
    organization_id: int,
    decision_id: int,
    application: CandidateApplication,
) -> AutoAdvanceContext | None:
    """Lock and re-authorize a queued auto-advance before any ATS write.

    ``None`` means a replay arrived after the decision was already resolved or
    otherwise made non-actionable, so the provider write must be skipped.
    ``pending``/missing is retriable because an eager worker can race the agent
    transaction that is about to commit ``processing``.
    """

    query = db.query(AgentDecision).filter(
        AgentDecision.id == int(decision_id),
        AgentDecision.organization_id == int(organization_id),
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    decision = query.one_or_none()
    if decision is None or decision.status == "pending":
        raise _dispatch_error(
            code="decision_not_ready",
            message="Agent auto-advance decision is not committed for processing yet",
            retriable=True,
        )
    if decision.status != "processing":
        return None
    if decision.decision_type != "advance_to_interview":
        raise _dispatch_error(
            code="invalid_decision",
            message="Durable auto-advance payload references a non-advance decision",
            retriable=False,
        )
    if int(decision.application_id) != int(application.id):
        raise _dispatch_error(
            code="invalid_decision",
            message="Durable auto-advance payload does not match the decision application",
            retriable=False,
        )

    from ..services.role_execution_guard import (
        assessment_task_is_current,
        automatic_role_action_block_reason,
        lock_live_role,
    )

    role = lock_live_role(
        db,
        role_id=int(decision.role_id),
        organization_id=int(organization_id),
    )
    block = automatic_role_action_block_reason(role)
    if (
        block is None
        and role is not None
        and not automation_enabled_for_decision(role, "advance_to_interview")
    ):
        block = "role.auto_advance is disabled"
    if block or role is None:
        raise _dispatch_error(
            code="authorization_revoked",
            message=block or "Role is no longer available for autonomous advance",
            retriable=False,
        )
    if int(application.role_id or 0) != int(role.id):
        raise _dispatch_error(
            code="invalid_decision",
            message="Application no longer belongs to the decision role",
            retriable=False,
        )
    if not (
        str(getattr(application, "workable_candidate_id", None) or "").strip()
        or str(
            getattr(application, "bullhorn_job_submission_id", None) or ""
        ).strip()
    ):
        raise _dispatch_error(
            code="not_linked",
            message="Application is no longer linked to its external ATS",
            retriable=False,
        )

    prior_assessment = (
        db.query(Assessment)
        .filter(
            Assessment.application_id == int(application.id),
            Assessment.organization_id == int(organization_id),
            Assessment.role_id == int(role.id),
            Assessment.is_voided.is_(False),
        )
        .order_by(Assessment.id.desc())
        .first()
    )
    if prior_assessment is not None and not assessment_task_is_current(
        db, assessment=prior_assessment, role=role
    ):
        raise _dispatch_error(
            code="superseded_assessment_task",
            message=(
                "Assessment result belongs to a task that is no longer "
                "active/assignable for the role"
            ),
            retriable=False,
        )

    org = (
        db.query(Organization)
        .filter(Organization.id == int(organization_id))
        .one_or_none()
    )
    if org is None:
        raise _dispatch_error(
            code="not_configured",
            message="Organization is unavailable for ATS writeback",
            retriable=False,
        )
    return AutoAdvanceContext(
        decision=decision,
        application=application,
        organization=org,
        role=role,
    )


def complete(
    db: Session,
    *,
    context: AutoAdvanceContext,
    reason: str | None = None,
) -> AgentDecision:
    """Resolve local state after the provider move has been confirmed."""

    decision = context.decision
    app = context.application
    role = context.role
    actor = Actor.system()
    metadata = {
        "agent_decision_id": int(decision.id),
        "agent_run_id": int(decision.agent_run_id) if decision.agent_run_id else None,
        "agent_reasoning": decision.reasoning,
        "model_version": decision.model_version,
        "prompt_version": decision.prompt_version,
        "auto_toggle": "auto_advance",
        "ats_writeback_confirmed": True,
    }
    resolved_reason = (reason or "").strip() or (
        f"Auto-approved per role.auto_advance (decision #{decision.id})"
    )
    advance_stage.run(
        db,
        actor,
        organization_id=int(decision.organization_id),
        application_id=int(app.id),
        to_stage="advanced",
        reason=resolved_reason,
        idempotency_key=f"approve_decision:{decision.id}",
        metadata=metadata,
    )
    decision.status = "approved"
    decision.resolved_at = datetime.now(timezone.utc)
    decision.resolved_by_user_id = None
    decision.resolution_note = resolved_reason
    decision.human_disposition = "auto_approved"
    evidence = dict(decision.evidence or {})
    dispatch = dict(evidence.get("auto_advance_dispatch") or {})
    dispatch.update({"status": "confirmed", "confirmed_at": decision.resolved_at.isoformat()})
    evidence["auto_advance_dispatch"] = dispatch
    evidence.pop("auto_execute_hold", None)
    decision.evidence = evidence
    db.add(decision)

    # The move handler already confirmed and recorded the provider write. Keep
    # the remaining note/graph effects, but never perform a second stage move.
    apply_decision_side_effects(
        db,
        actor,
        decision=decision,
        app=app,
        org=context.organization,
        role=role,
        disposition="approved",
        note=resolved_reason,
        reject_notify=False,
        ats_writeback_already_confirmed=True,
    )
    try:
        from ..agent_runtime import outcome_learning

        outcome_learning.record_outcome_for_approved_decision(
            db, decision=decision, application=app
        )
    except Exception:  # pragma: no cover - learning never blocks execution
        import logging

        logging.getLogger("taali.agent.autonomy").exception(
            "auto-approved outcome recording failed decision_id=%s", decision.id
        )
    return decision


__all__ = ["AutoAdvanceContext", "complete", "preflight"]
