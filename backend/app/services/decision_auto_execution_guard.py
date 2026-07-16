"""Row-lock and policy guards for automatic candidate decision actions."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from .agent_policy_settings import automation_enabled_for_decision
from .role_execution_guard import (
    automatic_role_action_block_reason,
    lock_live_role,
)

_POSITIVE_TYPES = frozenset({"send_assessment", "advance_to_interview"})


def recompute_persisted_verdict(
    db: Session, *, role: Role, app: CandidateApplication
) -> str | None:
    """Lazily delegate to the deterministic decision core.

    ``bulk_decision_service._shared`` imports the agent runtime's lightweight
    decision translation helpers. Importing it at module load time closes a
    cycle through ``agent_runtime.tool_registry`` when the process is cold.
    Keep this named proxy so existing callers and test patch points retain the
    same behavior while the dependency is resolved only when recomputation is
    actually requested.
    """
    from .bulk_decision_service._shared import (
        recompute_persisted_verdict as _recompute_persisted_verdict,
    )

    return _recompute_persisted_verdict(db, role=role, app=app)


def application_action_block_reason(
    application: CandidateApplication | None,
) -> str | None:
    if application is None:
        return "application is unavailable"
    if getattr(application, "deleted_at", None) is not None:
        return "application is deleted"
    outcome = str(application.application_outcome or "open").strip().lower()
    if outcome != "open":
        return f"application outcome is {outcome}"
    stage = str(application.pipeline_stage or "").strip().lower()
    if stage == "advanced":
        return "application is already advanced"
    return None


def _record_hold(
    db: Session,
    *,
    decision: AgentDecision,
    status: str,
    detail: str,
    **context,
) -> None:
    evidence = dict(decision.evidence or {})
    evidence["auto_execute_hold"] = {
        "status": status,
        "detail": detail,
        **context,
    }
    decision.evidence = evidence
    db.add(decision)


def lock_auto_execution_application(
    db: Session,
    *,
    role: Role,
    decision: AgentDecision,
) -> CandidateApplication | None:
    """Take the application lock before workspace and Role authority locks."""

    application = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(decision.application_id),
            CandidateApplication.organization_id == int(role.organization_id),
            CandidateApplication.role_id == int(role.id),
        )
        .populate_existing()
        .with_for_update(of=CandidateApplication)
        .one_or_none()
    )
    return application


def positive_auto_execution_is_current(
    db: Session,
    *,
    role: Role,
    application: CandidateApplication,
    decision: AgentDecision,
    decision_type: str,
) -> bool:
    """Fail closed when assessment-stage policy changed after card creation."""

    if decision_type not in _POSITIVE_TYPES:
        return True
    try:
        current_type = recompute_persisted_verdict(
            db,
            role=role,
            app=application,
        )
    except Exception:
        _record_hold(
            db,
            decision=decision,
            status="decision_policy_refresh_failed",
            detail="current policy could not be recomputed; human review is required",
        )
        return False
    if current_type == decision_type:
        return True
    _record_hold(
        db,
        decision=decision,
        status=(
            "assessment_stage_decision_stale"
            if current_type in _POSITIVE_TYPES
            else "decision_policy_stale"
        ),
        detail=(
            f"stored {decision_type}; current policy requires "
            f"{current_type or 'human review/no automatic action'}"
        ),
        current_decision_type=current_type,
    )
    return False


def auto_execution_application_is_actionable(
    db: Session,
    *,
    application: CandidateApplication,
    decision: AgentDecision,
) -> bool:
    """Record an eligibility hold only after the caller locked the Decision."""

    reason = application_action_block_reason(application)
    if reason is None:
        return True
    _record_hold(
        db,
        decision=decision,
        status="application_not_actionable",
        detail=reason,
    )
    return False


def lock_actionable_auto_execution_decision(
    db: Session,
    *,
    decision: AgentDecision,
) -> AgentDecision | None:
    """Lock and refresh the decision last; only pending rows may execute."""

    locked = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.id == int(decision.id),
            AgentDecision.organization_id == int(decision.organization_id),
            AgentDecision.role_id == int(decision.role_id),
            AgentDecision.application_id == int(decision.application_id),
        )
        .populate_existing()
        .with_for_update(of=AgentDecision)
        .one_or_none()
    )
    if locked is None or str(locked.status) != "pending":
        return None
    return locked


def lock_authorized_auto_execution_role(
    db: Session,
    *,
    role: Role,
    decision: AgentDecision,
    decision_type: str,
    auto_toggle: str | None,
) -> Role | None:
    """Lock live execution authority and retain a reason when it is held."""

    live_role = lock_live_role(
        db,
        role_id=int(role.id),
        organization_id=int(role.organization_id),
    )
    block = automatic_role_action_block_reason(live_role, db=db)
    if (
        block is None
        and auto_toggle
        and not automation_enabled_for_decision(live_role, decision_type)
    ):
        block = f"role.{auto_toggle} is disabled"
    if block:
        locked_decision = lock_actionable_auto_execution_decision(
            db,
            decision=decision,
        )
        if locked_decision is not None:
            _record_hold(
                db,
                decision=locked_decision,
                status="role_not_runnable",
                detail=block,
            )
        return None
    return live_role


__all__ = [
    "application_action_block_reason",
    "auto_execution_application_is_actionable",
    "lock_actionable_auto_execution_decision",
    "lock_auto_execution_application",
    "lock_authorized_auto_execution_role",
    "positive_auto_execution_is_current",
]
