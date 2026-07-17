"""Synchronous Workable delivery for a recruiter outcome mutation."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.organization import Organization
from ...models.role import Role
from ...models.user import User
from ...schemas.role import ApplicationOutcomeUpdate
from ...services.workable_actions_service import (
    disqualify_candidate_in_workable,
    revert_candidate_disqualification_in_workable,
)
from . import related_role_actions


def application_is_workable_linked(app: CandidateApplication) -> bool:
    return bool(str(getattr(app, "workable_candidate_id", None) or "").strip())


def _workable_delivery_snapshot(db: Session, claim: dict) -> tuple | None:
    row = (
        db.query(
            CandidateApplication.version.label("application_version"),
            CandidateApplication.application_outcome.label("application_outcome"),
            CandidateApplication.workable_candidate_id.label("workable_candidate_id"),
            CandidateApplication.deleted_at.label("deleted_at"),
            CandidateApplication.pre_screen_score_100.label("pre_screen_score_100"),
            CandidateApplication.pre_screen_recommendation.label(
                "pre_screen_recommendation"
            ),
            Organization.workable_connected.label("workable_connected"),
            Organization.workable_access_token.label("workable_access_token"),
            Organization.workable_subdomain.label("workable_subdomain"),
            Organization.workable_config.label("workable_config"),
            Role.name.label("role_name"),
            Role.workable_actor_member_id.label("workable_actor_member_id"),
            Role.workable_job_data.label("workable_job_data"),
            Candidate.full_name.label("candidate_name"),
            Candidate.email.label("candidate_email"),
        )
        .join(Organization, Organization.id == CandidateApplication.organization_id)
        .join(Role, Role.id == CandidateApplication.role_id)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .filter(
            CandidateApplication.id == int(claim["application_id"]),
            CandidateApplication.organization_id == int(claim["organization_id"]),
        )
        .one_or_none()
    )
    if row is None or not (
        row.deleted_at is None
        and int(row.application_version or 0)
        == int(claim["expected_application_version"])
        and str(row.application_outcome or "open").strip().lower()
        == str(claim["expected_local_outcome"])
        and str(row.workable_candidate_id or "").strip()
        == str(claim["provider_target_id"])
    ):
        return None
    org = SimpleNamespace(
        workable_connected=bool(row.workable_connected),
        workable_access_token=row.workable_access_token,
        workable_subdomain=row.workable_subdomain,
        workable_config=deepcopy(row.workable_config),
    )
    role = SimpleNamespace(
        name=row.role_name,
        workable_actor_member_id=row.workable_actor_member_id,
        workable_job_data=deepcopy(row.workable_job_data),
    )
    candidate = SimpleNamespace(
        full_name=row.candidate_name,
        email=row.candidate_email,
    )
    application = SimpleNamespace(
        workable_candidate_id=str(claim["provider_target_id"]),
        pre_screen_score_100=row.pre_screen_score_100,
        pre_screen_recommendation=row.pre_screen_recommendation,
        candidate=candidate,
    )
    return org, role, application


def sync_workable_outcome_change(
    *,
    db: Session,
    app: CandidateApplication,
    target_outcome: str,
    current_user: User,
    reason: str | None = None,
    claim: dict | None = None,
) -> dict | None:
    if claim is not None:
        try:
            snapshot = _workable_delivery_snapshot(db, claim)
        finally:
            # Provider HTTP must never borrow a connection or hold a DB
            # transaction open. The durable provider-started claim was already
            # committed by ``begin_synchronous_workable_outcome``.
            db.rollback()
        if snapshot is None:
            return {
                "success": False,
                "action": "manual_outcome",
                "code": "lifecycle_changed",
                "message": "The application changed before Workable delivery began.",
            }
        org, role, provider_app = snapshot
        assert not db.in_transaction()
        target = str(target_outcome or "").strip().lower()
        if target == "rejected":
            return disqualify_candidate_in_workable(
                org=org,
                app=provider_app,
                role=role,
                reason=reason or "Rejected in TAALI",
                withdrew=False,
            )
        return revert_candidate_disqualification_in_workable(
            org=org,
            app=provider_app,
            role=role,
        )

    current = str(app.application_outcome or "open").strip().lower()
    target = str(target_outcome or current).strip().lower()
    if (
        not application_is_workable_linked(app)
        or target == current
        or (current, target) not in {("open", "rejected"), ("rejected", "open")}
    ):
        return None
    org = (
        db.query(Organization)
        .filter(Organization.id == current_user.organization_id)
        .one_or_none()
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if target == "rejected":
        return disqualify_candidate_in_workable(
            org=org,
            app=app,
            role=app.role,
            reason=reason or "Rejected in TAALI",
            withdrew=False,
        )
    return revert_candidate_disqualification_in_workable(
        org=org,
        app=app,
        role=app.role,
    )


def run_synchronous_workable_outcome(
    db: Session,
    *,
    app: CandidateApplication,
    application_id: int,
    data: ApplicationOutcomeUpdate,
    current_user: User,
    sync_outcome: Callable[..., dict | None],
) -> CandidateApplication:
    from ...services.synchronous_workable_outcome import (
        begin_synchronous_workable_outcome,
        complete_synchronous_workable_outcome,
        surface_synchronous_workable_failure,
        surface_synchronous_workable_success_drift,
    )

    organization_id = int(current_user.organization_id)
    actor_id = int(current_user.id)
    claim = begin_synchronous_workable_outcome(
        db,
        app,
        organization_id=organization_id,
        target_outcome=data.application_outcome,
        idempotency_key=data.idempotency_key,
    )
    try:
        result = sync_outcome(
            db=db,
            app=app,
            target_outcome=data.application_outcome,
            current_user=current_user,
            reason=data.reason,
            claim=claim,
        )
    except Exception as exc:
        surface_synchronous_workable_failure(
            db,
            organization_id=organization_id,
            claim=claim,
            error_code="api_error",
            error_message=type(exc).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail="Workable outcome delivery failed; verify its status before retrying.",
        ) from None
    delivered = bool(
        result
        and (
            result.get("success")
            or str(result.get("status") or "").strip().lower()
            in {"ok", "succeeded"}
        )
    )
    if not delivered:
        error_code = str((result or {}).get("code") or "api_error")
        error_message = str(
            (result or {}).get("message")
            or "Failed to update the outcome in Workable"
        )
        surface_synchronous_workable_failure(
            db,
            organization_id=organization_id,
            claim=claim,
            error_code=error_code,
            error_message=error_message,
        )
        raise HTTPException(status_code=502, detail=error_message)
    try:
        app = related_role_actions.require_application_outcome_action(
            db,
            current_user=current_user,
            application_id=application_id,
            acting_role_id=data.acting_role_id,
            target_outcome=data.application_outcome,
            expected_role_family=data.expected_role_family,
        )
    except HTTPException:
        db.rollback()
        surface_synchronous_workable_success_drift(
            db,
            organization_id=organization_id,
            claim=claim,
            reason=(
                "Workable confirmed the outcome, but application or role-family "
                "authority changed during delivery. The local state was preserved."
            ),
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "Workable changed, but hiring-team authority changed concurrently. "
                "The local outcome was preserved and needs reconciliation."
            ),
        ) from None
    return complete_synchronous_workable_outcome(
        db,
        app,
        claim,
        actor_id=actor_id,
        reason=data.reason,
        idempotency_key=data.idempotency_key,
        acting_role_id=data.acting_role_id,
        provider_result=result,
    )


__all__ = [
    "application_is_workable_linked",
    "run_synchronous_workable_outcome",
    "sync_workable_outcome_change",
]
