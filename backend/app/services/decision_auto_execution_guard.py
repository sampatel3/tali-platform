"""Row-lock and policy guards for automatic candidate decision actions."""

from __future__ import annotations

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, aliased

from ..models.agent_decision import AgentDecision
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import SisterRoleEvaluation
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
    """Take the application lock before workspace and Role authority locks.

    Standard roles own their application row directly. Related roles instead
    act through the one application owned by their persisted ATS-family owner;
    accepting only an id supplied by the caller would let a stale/malformed
    related role cross into another owner's roster. Resolve that relationship
    in SQL while locking only the canonical application row.
    """

    if int(decision.role_id) != int(role.id) or int(decision.organization_id) != int(
        role.organization_id
    ):
        return None

    execution_role = aliased(Role)
    standard_application = and_(
        or_(
            execution_role.role_kind.is_(None),
            execution_role.role_kind != ROLE_KIND_SISTER,
        ),
        execution_role.ats_owner_role_id.is_(None),
        CandidateApplication.role_id == execution_role.id,
    )
    related_application = and_(
        execution_role.role_kind == ROLE_KIND_SISTER,
        execution_role.ats_owner_role_id.is_not(None),
        CandidateApplication.role_id == execution_role.ats_owner_role_id,
    )

    application = (
        db.query(CandidateApplication)
        .join(
            execution_role,
            and_(
                execution_role.id == int(role.id),
                execution_role.organization_id == CandidateApplication.organization_id,
                execution_role.deleted_at.is_(None),
                or_(standard_application, related_application),
            ),
        )
        .filter(
            CandidateApplication.id == int(decision.application_id),
            CandidateApplication.organization_id == int(role.organization_id),
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
    if str(getattr(role, "role_kind", "") or "") == ROLE_KIND_SISTER and int(
        application.role_id
    ) != int(role.id):
        # Keep the application -> role -> decision -> evaluation lock order
        # used by the related-role decision runtime. The role-local score may
        # be replaced independently of the shared owner application, so the
        # standard application's cached verdict is never valid authority here.
        evaluation = (
            db.query(SisterRoleEvaluation)
            .filter(
                SisterRoleEvaluation.organization_id == int(decision.organization_id),
                SisterRoleEvaluation.role_id == int(decision.role_id),
                SisterRoleEvaluation.source_application_id
                == int(decision.application_id),
            )
            .populate_existing()
            .with_for_update(of=SisterRoleEvaluation)
            .one_or_none()
        )
        from .decision_role_context import related_decision_staleness

        report = related_decision_staleness(
            db,
            decision,
            evaluation,
            application=application,
            role=role,
        )
        if not report.is_stale:
            return True
        _record_hold(
            db,
            decision=decision,
            status="assessment_stage_decision_stale",
            detail=(
                report.summary
                or "the related-role evaluation changed; human review is required"
            ),
            stale_reasons=list(report.reasons),
        )
        return False
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
