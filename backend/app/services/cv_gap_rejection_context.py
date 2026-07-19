"""Short transaction boundaries for one CV-gap rejection item."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..models.agent_needs_input import AgentNeedsInput
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from ..models.user import User
from ..schemas.role import RoleFamilyResponse
from .cv_gap_rejection import cv_gap_provider_snapshot
from .cv_gap_rejection_authority import (
    CV_GAP_CARD_CHANGED,
    CV_GAP_ROLE_CHANGED,
    CvGapAuthorityConflict,
    lock_and_validate_cv_gap_authority,
)


@dataclass
class CvGapExecutionContext:
    user: User
    owner: Role
    card: AgentNeedsInput
    app: CandidateApplication
    org: Organization | None
    provider_snapshot: dict[str, Any]


def cv_gap_operation_id(
    *,
    job_run_id: int | None,
    needs_input_id: int,
    kind: str,
    application_id: int,
) -> str:
    receipt_scope = int(job_run_id) if job_run_id else int(needs_input_id)
    return f"cv-gap:{receipt_scope}:{kind}:{int(application_id)}"[:180]


def cv_gap_ineligibility_reason(
    app: CandidateApplication | None,
    *,
    owner_role_id: int,
    kind: str,
) -> str | None:
    if app is None:
        return "application is no longer available"
    if app.deleted_at is not None or int(app.role_id) != int(owner_role_id):
        return "application left the approved job roster"
    if str(app.application_outcome or "").strip().lower() != "open":
        return "application outcome is no longer open"
    if str(app.cv_text or "").strip():
        return "CV text became available"
    has_file = bool(str(app.cv_file_url or "").strip())
    if kind == "missing_cv" and has_file:
        return "a CV file became available"
    if kind == "cv_unreadable" and not has_file:
        return "the unreadable CV file is no longer present"
    return None


def load_cv_gap_execution_context(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    needs_input_id: int,
    kind: str,
    user_id: int,
    expected_owner_role_version: int,
    expected_role_family: RoleFamilyResponse,
    application_id: int,
    lock: bool,
) -> tuple[CvGapExecutionContext | None, str | None]:
    """Validate authority/card/app and optionally take short row locks."""

    user_query = db.query(User).filter(
        User.id == int(user_id),
        User.organization_id == int(organization_id),
    )
    if lock:
        user_query = user_query.populate_existing()
    user = user_query.one_or_none()
    if user is None or not bool(user.is_active):
        raise CvGapAuthorityConflict(
            CV_GAP_ROLE_CHANGED,
            "The approving recruiter no longer has access to this workspace.",
        )
    _, owner = lock_and_validate_cv_gap_authority(
        db,
        organization_id=int(organization_id),
        role_id=int(role_id),
        kind=kind,
        current_user=user,
        expected_owner_role_version=int(expected_owner_role_version),
        expected_role_family=expected_role_family,
        lock=lock,
    )
    card_query = db.query(AgentNeedsInput).filter(
        AgentNeedsInput.id == int(needs_input_id),
        AgentNeedsInput.organization_id == int(organization_id),
    )
    if lock:
        card_query = card_query.populate_existing().with_for_update(of=AgentNeedsInput)
    card = card_query.one_or_none()
    if (
        card is None
        or not card.is_open
        or int(card.role_id) != int(role_id)
        or str(card.kind) != kind
    ):
        raise CvGapAuthorityConflict(
            CV_GAP_CARD_CHANGED,
            "The CV-gap request changed before the batch completed.",
        )
    app_query = db.query(CandidateApplication).filter(
        CandidateApplication.id == int(application_id),
        CandidateApplication.organization_id == int(organization_id),
    )
    if lock:
        app_query = app_query.populate_existing().with_for_update(
            of=CandidateApplication
        )
    app = app_query.one_or_none()
    ineligible = cv_gap_ineligibility_reason(
        app,
        owner_role_id=int(owner.id),
        kind=kind,
    )
    if ineligible is not None or app is None:
        return None, ineligible
    candidate_query = db.query(Candidate).filter(
        Candidate.id == int(app.candidate_id),
        Candidate.organization_id == int(organization_id),
        Candidate.deleted_at.is_(None),
    )
    if lock:
        candidate_query = candidate_query.with_for_update(of=Candidate)
    if candidate_query.one_or_none() is None:
        return None, "candidate is no longer available"
    org = (
        db.query(Organization)
        .filter(Organization.id == int(organization_id))
        .one_or_none()
    )
    return (
        CvGapExecutionContext(
            user=user,
            owner=owner,
            card=card,
            app=app,
            org=org,
            provider_snapshot=cv_gap_provider_snapshot(db, org=org, app=app),
        ),
        None,
    )


def lock_cv_gap_application(
    db: Session,
    *,
    organization_id: int,
    application_id: int,
) -> CandidateApplication | None:
    return (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == int(organization_id),
        )
        .populate_existing()
        .with_for_update(of=CandidateApplication)
        .one_or_none()
    )


__all__ = [
    "CvGapExecutionContext",
    "cv_gap_ineligibility_reason",
    "cv_gap_operation_id",
    "load_cv_gap_execution_context",
    "lock_cv_gap_application",
]
