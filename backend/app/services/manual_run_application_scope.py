"""Fail-closed application scoping for recruiter-triggered agent runs."""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session, contains_eager

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, ROLE_KIND_STANDARD, Role
from ..models.sister_role_evaluation import SisterRoleEvaluation


def resolve_manual_run_application(
    db: Session,
    *,
    role: Role,
    organization_id: int,
    application_id: int,
    include_candidate: bool = False,
) -> CandidateApplication | None:
    """Resolve a live application in ``role``'s executable roster.

    Standard roles own application rows directly. Related roles retain the
    source ATS application's id and require a matching evaluation row. Invalid
    role state and every unavailable scope return ``None`` so callers expose
    one non-disclosing not-found response.
    """

    try:
        org_id = int(organization_id)
        app_id = int(application_id)
        role_id = int(role.id)
        role_org_id = int(role.organization_id)
    except (AttributeError, TypeError, ValueError):
        return None
    if (
        org_id <= 0
        or app_id <= 0
        or role_id <= 0
        or role_org_id != org_id
        or getattr(role, "deleted_at", None) is not None
    ):
        return None

    role_kind = str(getattr(role, "role_kind", None) or ROLE_KIND_STANDARD)
    owner_role_id = getattr(role, "ats_owner_role_id", None)
    if role_kind == ROLE_KIND_SISTER:
        try:
            applications_role_id = int(owner_role_id)
        except (TypeError, ValueError):
            return None
        if applications_role_id <= 0:
            return None
    elif role_kind == ROLE_KIND_STANDARD and owner_role_id is None:
        applications_role_id = role_id
    else:
        return None

    query = (
        db.query(CandidateApplication)
        .join(Role, Role.id == CandidateApplication.role_id)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplication.id == app_id,
            CandidateApplication.organization_id == org_id,
            CandidateApplication.role_id == applications_role_id,
            CandidateApplication.deleted_at.is_(None),
            or_(
                CandidateApplication.application_outcome.is_(None),
                CandidateApplication.application_outcome == "open",
            ),
            CandidateApplication.workable_disqualified.is_not(True),
            Role.id == applications_role_id,
            Role.organization_id == org_id,
            Role.role_kind == ROLE_KIND_STANDARD,
            Role.ats_owner_role_id.is_(None),
            Role.deleted_at.is_(None),
            Candidate.organization_id == org_id,
            Candidate.deleted_at.is_(None),
        )
    )
    if role_kind == ROLE_KIND_SISTER:
        query = query.join(
            SisterRoleEvaluation,
            SisterRoleEvaluation.source_application_id
            == CandidateApplication.id,
        ).filter(
            SisterRoleEvaluation.role_id == role_id,
            SisterRoleEvaluation.organization_id == org_id,
        )
    if include_candidate:
        query = query.options(contains_eager(CandidateApplication.candidate))
    return query.one_or_none()


def admit_native_manual_run_worker(
    db: Session,
    *,
    role_id: int,
    application_id: int | None,
    dispatch_key: str | None,
    organization_id: int | None,
) -> tuple[Role | None, dict | None]:
    """Re-read every revocable native-run authority before paid work starts."""

    from .manual_agent_run_dispatch import finish_manual_run_intent
    from .role_execution_guard import automatic_role_action_block_reason

    role = (
        db.query(Role)
        .filter(Role.id == int(role_id), Role.deleted_at.is_(None))
        .first()
    )
    try:
        expected_organization_id = (
            int(organization_id)
            if organization_id is not None
            else int(role.organization_id)
            if role is not None
            else None
        )
    except (TypeError, ValueError):
        expected_organization_id = None

    def abort(error: str) -> None:
        if expected_organization_id is None:
            return
        if finish_manual_run_intent(
            db,
            dispatch_key=dispatch_key,
            organization_id=expected_organization_id,
            role_id=role_id,
            application_id=application_id,
            status="aborted",
            error=error,
        ):
            db.commit()

    if role is None:
        abort("role_not_found")
        return None, {
            "status": "skipped",
            "reason": "role_not_found",
            "role_id": role_id,
        }
    if expected_organization_id != int(role.organization_id):
        return None, {
            "status": "skipped",
            "reason": "dispatch_scope_mismatch",
            "role_id": role_id,
        }
    if not bool(role.agentic_mode_enabled):
        abort("agent_disabled")
        return None, {
            "status": "skipped",
            "reason": "agent_disabled",
            "role_id": role_id,
        }
    if role.agent_paused_at is not None:
        abort("agent_paused")
        return None, {
            "status": "skipped",
            "reason": "agent_paused",
            "role_id": role_id,
            "paused_reason": role.agent_paused_reason,
        }
    role_block = automatic_role_action_block_reason(role, db=db)
    if role_block:
        reason = (
            "workspace_paused"
            if role_block == "workspace agent is paused"
            else "role_not_runnable"
        )
        abort(reason)
        return None, {
            "status": "skipped",
            "reason": reason,
            "detail": role_block,
            "role_id": role_id,
        }
    if application_id is not None and resolve_manual_run_application(
        db,
        role=role,
        organization_id=expected_organization_id,
        application_id=application_id,
    ) is None:
        abort("application_unavailable")
        return None, {
            "status": "skipped",
            "reason": "application_unavailable",
            "role_id": role_id,
            "application_id": int(application_id),
        }
    return role, None


__all__ = ["admit_native_manual_run_worker", "resolve_manual_run_application"]
