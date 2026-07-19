"""Exact cohort and role-family proof for recruiter CV-gap rejection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..domains.assessments_runtime.job_authorization import (
    JobPermission,
    require_job_permission,
)
from ..domains.assessments_runtime.role_family_support import (
    role_family_load_options,
    role_family_response,
)
from ..models.candidate_application import CandidateApplication
from ..models.role import Role
from ..models.user import User
from ..schemas.role import RoleFamilyResponse
from .role_family_reject_authority import lock_current_role_families


MAX_CV_GAP_REJECTION_BATCH = 200
CV_GAP_REJECTION_SPECS: dict[str, dict[str, str]] = {
    "missing_cv": {"reason": "No CV on file", "trigger": "reject_missing_cv"},
    "cv_unreadable": {
        "reason": "CV could not be read",
        "trigger": "reject_cv_unreadable",
    },
}
CV_GAP_COHORT_CHANGED = "CV_GAP_COHORT_CHANGED"
CV_GAP_ROLE_CHANGED = "CV_GAP_ROLE_CHANGED"
CV_GAP_CARD_CHANGED = "CV_GAP_CARD_CHANGED"
ROLE_FAMILY_CHANGED = "ROLE_FAMILY_CHANGED"
ROLE_VERSION_CONFLICT = "ROLE_VERSION_CONFLICT"


@dataclass(frozen=True)
class CvGapAuthorityConflict(RuntimeError):
    """Structured optimistic-authority failure shared by HTTP and workers."""

    code: str
    message: str
    current_preview: dict[str, Any] | None = None

    def __str__(self) -> str:  # pragma: no cover - trivial convenience
        return self.message


def _family_signature(value: RoleFamilyResponse | dict[str, Any] | None) -> tuple:
    if value is None:
        return ()
    family = (
        value
        if isinstance(value, RoleFamilyResponse)
        else RoleFamilyResponse.model_validate(value)
    )
    return (
        (int(family.owner.id), str(family.owner.name)),
        tuple(sorted((int(role.id), str(role.name)) for role in family.related)),
    )


def _cv_gap_query(
    db: Session,
    *,
    organization_id: int,
    owner_role_id: int,
    kind: str,
):
    """Canonical eligibility query shared by preview and mutation."""

    if kind not in CV_GAP_REJECTION_SPECS:
        raise ValueError(f"unsupported CV-gap kind={kind!r}")
    query = db.query(CandidateApplication.id).filter(
        CandidateApplication.organization_id == int(organization_id),
        CandidateApplication.role_id == int(owner_role_id),
        CandidateApplication.deleted_at.is_(None),
        CandidateApplication.application_outcome == "open",
        or_(
            CandidateApplication.cv_text.is_(None),
            func.trim(CandidateApplication.cv_text) == "",
        ),
    )
    no_file = or_(
        CandidateApplication.cv_file_url.is_(None),
        func.trim(CandidateApplication.cv_file_url) == "",
    )
    return (
        query.filter(no_file)
        if kind == "missing_cv"
        else query.filter(
            CandidateApplication.cv_file_url.isnot(None),
            func.trim(CandidateApplication.cv_file_url) != "",
        )
    )


def _current_family(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    lock: bool,
) -> RoleFamilyResponse | None:
    if lock:
        return lock_current_role_families(
            db,
            organization_id=int(organization_id),
            role_ids=[int(role_id)],
        ).get(int(role_id))
    role = (
        db.query(Role)
        .options(*role_family_load_options(organization_id=int(organization_id)))
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .one_or_none()
    )
    return role_family_response(role) if role is not None else None


def _authority_snapshot(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    kind: str,
    lock: bool,
) -> tuple[dict[str, Any], Role] | None:
    if kind not in CV_GAP_REJECTION_SPECS:
        return None
    family = _current_family(
        db,
        organization_id=organization_id,
        role_id=role_id,
        lock=lock,
    )
    if family is None:
        return None
    owner_role_id = int(family.owner.id)
    query = db.query(Role).filter(
        Role.id == owner_role_id,
        Role.organization_id == int(organization_id),
        Role.deleted_at.is_(None),
    )
    if lock:
        query = query.populate_existing().with_for_update(of=Role)
    owner = query.one_or_none()
    if owner is None:
        return None
    return (
        {
            "kind": kind,
            "owner_role_id": owner_role_id,
            "expected_owner_role_version": int(owner.version or 1),
            "expected_role_family": family.model_dump(),
        },
        owner,
    )


def _add_cohort(
    db: Session,
    *,
    organization_id: int,
    authority: dict[str, Any],
) -> dict[str, Any]:
    rows = (
        _cv_gap_query(
            db,
            organization_id=organization_id,
            owner_role_id=int(authority["owner_role_id"]),
            kind=str(authority["kind"]),
        )
        .order_by(CandidateApplication.id.asc())
        .limit(MAX_CV_GAP_REJECTION_BATCH + 1)
        .all()
    )
    ids = [int(row[0]) for row in rows[:MAX_CV_GAP_REJECTION_BATCH]]
    return {
        **authority,
        "application_ids": ids,
        "eligible_count": len(ids),
        "has_more": len(rows) > MAX_CV_GAP_REJECTION_BATCH,
    }


def cv_gap_rejection_preview(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    kind: str,
    lock: bool = False,
) -> dict[str, Any] | None:
    """Return the exact ascending next batch and its authority snapshot."""

    snapshot = _authority_snapshot(
        db,
        organization_id=int(organization_id),
        role_id=int(role_id),
        kind=kind,
        lock=lock,
    )
    if snapshot is None:
        return None
    authority, _ = snapshot
    return _add_cohort(
        db,
        organization_id=int(organization_id),
        authority=authority,
    )


def lock_and_validate_cv_gap_authority(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
    kind: str,
    current_user: User,
    expected_owner_role_version: int,
    expected_role_family: RoleFamilyResponse | dict[str, Any],
    expected_application_ids: list[int] | None = None,
    lock: bool = True,
) -> tuple[dict[str, Any], Role]:
    """Validate family/version and optionally lock the authority boundary."""

    snapshot = _authority_snapshot(
        db,
        organization_id=int(organization_id),
        role_id=int(role_id),
        kind=kind,
        lock=lock,
    )
    if snapshot is None:
        raise CvGapAuthorityConflict(
            CV_GAP_ROLE_CHANGED,
            "The job or its ATS owner is no longer available.",
        )
    authority, owner = snapshot
    current_family = RoleFamilyResponse.model_validate(
        authority["expected_role_family"]
    )

    def current_preview() -> dict[str, Any]:
        return (
            _add_cohort(
                db,
                organization_id=int(organization_id),
                authority=authority,
            )
            if expected_application_ids is not None
            else authority
        )

    if _family_signature(expected_role_family) != _family_signature(current_family):
        raise CvGapAuthorityConflict(
            ROLE_FAMILY_CHANGED,
            "The linked role family changed. Review the current roles before confirming again.",
            current_preview(),
        )
    if int(expected_owner_role_version) != int(
        authority["expected_owner_role_version"]
    ):
        raise CvGapAuthorityConflict(
            ROLE_VERSION_CONFLICT,
            "The ATS-owning job changed. Review the latest version before confirming again.",
            current_preview(),
        )

    require_job_permission(
        db,
        current_user=current_user,
        role_id=int(role_id),
        permission=JobPermission.CONTROL_AGENT,
        lock_for_update=lock,
    )
    owner = require_job_permission(
        db,
        current_user=current_user,
        role_id=int(authority["owner_role_id"]),
        permission=JobPermission.CONTROL_AGENT,
        lock_for_update=lock,
    )
    if expected_application_ids is not None:
        current = current_preview()
        if [int(value) for value in expected_application_ids] != current[
            "application_ids"
        ]:
            raise CvGapAuthorityConflict(
                CV_GAP_COHORT_CHANGED,
                "The CV-gap cohort changed. Review the current candidates before confirming again.",
                current,
            )
        authority = current
    return authority, owner


__all__ = [
    "CV_GAP_CARD_CHANGED",
    "CV_GAP_COHORT_CHANGED",
    "CV_GAP_REJECTION_SPECS",
    "CV_GAP_ROLE_CHANGED",
    "CvGapAuthorityConflict",
    "MAX_CV_GAP_REJECTION_BATCH",
    "cv_gap_rejection_preview",
    "lock_and_validate_cv_gap_authority",
]
