"""Canonical one-time source selection for independent related roles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from typing import Sequence

from sqlalchemy.orm import Session, joinedload

from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import SisterRoleEvaluation


def text_fingerprint(value: str | None) -> str:
    return hashlib.sha256((value or "").strip().encode("utf-8")).hexdigest()


def application_cv_text(application: CandidateApplication) -> str:
    return (
        (application.cv_text or "").strip()
        or (
            (application.candidate.cv_text or "").strip()
            if application.candidate is not None
            else ""
        )
    )


@dataclass(frozen=True, slots=True)
class RelatedRoleSourceMember:
    """One logical source-role member captured for a one-time snapshot."""

    candidate_id: int
    source_application: CandidateApplication
    ats_application_id: int | None
    pipeline_stage: str
    pipeline_stage_updated_at: datetime | None
    pipeline_stage_source: str
    application_outcome: str
    application_outcome_updated_at: datetime | None
    application_outcome_source: str


def related_role_ats_owner(db: Session, source: Role) -> Role | None:
    """Resolve the optional ultimate ATS transport without defining membership.

    Legacy data may contain a related-to-related owner chain. Traverse it with a
    cycle guard so every newly created role stores only the ultimate transport
    role. Soft deletion deliberately does not erase the restriction link.
    """

    current = source
    visited: set[int] = set()
    while str(current.role_kind or "") == ROLE_KIND_SISTER:
        current_id = int(current.id)
        if current_id in visited:
            return None
        visited.add(current_id)
        owner_id = getattr(current, "ats_owner_role_id", None)
        if owner_id is None:
            return None
        owner = db.get(Role, int(owner_id))
        if (
            owner is None
            or int(owner.organization_id) != int(source.organization_id)
        ):
            return None
        current = owner
    from .ats_role_lifecycle import ats_job_lifecycle

    return current if ats_job_lifecycle(current).external_job_id else None


def select_related_role_source_members(
    db: Session,
    source: Role,
) -> list[RelatedRoleSourceMember]:
    """Select the source role's logical roster exactly once.

    Ordinary roles are represented by their live direct applications. Related
    roles are represented by their live explicit evaluation rows plus any live
    direct related-role applications not yet materialized as evaluations. The
    optional ATS owner's broader pool is never consulted. Evaluation evidence
    remains selectable when its source application was soft-deleted because the
    membership, not the evidence row lifecycle, is authoritative.
    """

    if source.deleted_at is not None:
        raise ValueError("Related-role source is unavailable")

    direct_applications = (
        db.query(CandidateApplication)
        .options(joinedload(CandidateApplication.candidate))
        .filter(
            CandidateApplication.organization_id == int(source.organization_id),
            CandidateApplication.role_id == int(source.id),
            CandidateApplication.deleted_at.is_(None),
        )
        .order_by(CandidateApplication.id.asc())
        .all()
    )
    owner = related_role_ats_owner(db, source)
    if str(source.role_kind or "") != ROLE_KIND_SISTER:
        return [
            RelatedRoleSourceMember(
                candidate_id=int(application.candidate_id),
                source_application=application,
                ats_application_id=(
                    int(application.id) if owner is not None else None
                ),
                # A standard role supplies evidence and transport, not local
                # workflow state for the newly independent related role.
                pipeline_stage="applied",
                pipeline_stage_updated_at=None,
                pipeline_stage_source="system",
                application_outcome="open",
                application_outcome_updated_at=None,
                application_outcome_source="system",
            )
            for application in direct_applications
        ]

    evaluations = (
        db.query(SisterRoleEvaluation)
        .options(
            joinedload(SisterRoleEvaluation.source_application).joinedload(
                CandidateApplication.candidate
            )
        )
        .filter(
            SisterRoleEvaluation.organization_id == int(source.organization_id),
            SisterRoleEvaluation.role_id == int(source.id),
            SisterRoleEvaluation.deleted_at.is_(None),
        )
        .order_by(SisterRoleEvaluation.id.asc())
        .all()
    )
    selected: dict[int, RelatedRoleSourceMember] = {}
    for evaluation in evaluations:
        application = evaluation.source_application
        if application is None:
            continue
        candidate_id = int(evaluation.candidate_id or application.candidate_id)
        selected[candidate_id] = RelatedRoleSourceMember(
            candidate_id=candidate_id,
            source_application=application,
            ats_application_id=(
                int(evaluation.ats_application_id)
                if owner is not None and evaluation.ats_application_id is not None
                else None
            ),
            pipeline_stage=str(evaluation.pipeline_stage or "applied"),
            pipeline_stage_updated_at=evaluation.pipeline_stage_updated_at,
            pipeline_stage_source=str(evaluation.pipeline_stage_source or "system"),
            application_outcome=str(evaluation.application_outcome or "open"),
            application_outcome_updated_at=evaluation.application_outcome_updated_at,
            application_outcome_source=str(
                evaluation.application_outcome_source or "system"
            ),
        )

    missing_direct = [
        application
        for application in direct_applications
        if int(application.candidate_id) not in selected
    ]
    ats_by_candidate: dict[int, int] = {}
    if owner is not None and missing_direct:
        owner_rows = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.organization_id == int(source.organization_id),
                CandidateApplication.role_id == int(owner.id),
                CandidateApplication.candidate_id.in_(
                    [int(application.candidate_id) for application in missing_direct]
                ),
                CandidateApplication.deleted_at.is_(None),
            )
            .order_by(CandidateApplication.id.desc())
            .all()
        )
        for application in owner_rows:
            ats_by_candidate.setdefault(
                int(application.candidate_id), int(application.id)
            )
    for application in missing_direct:
        candidate_id = int(application.candidate_id)
        selected[candidate_id] = RelatedRoleSourceMember(
            candidate_id=candidate_id,
            source_application=application,
            ats_application_id=ats_by_candidate.get(candidate_id),
            pipeline_stage=str(application.pipeline_stage or "applied"),
            pipeline_stage_updated_at=application.pipeline_stage_updated_at,
            pipeline_stage_source=str(application.pipeline_stage_source or "system"),
            application_outcome=str(application.application_outcome or "open"),
            application_outcome_updated_at=(
                application.application_outcome_updated_at
            ),
            application_outcome_source="system",
        )
    return [selected[candidate_id] for candidate_id in sorted(selected)]


def related_role_source_fingerprint(
    members: Sequence[RelatedRoleSourceMember],
) -> str:
    """Stable identity/state/evidence hash for preview-to-create confirmation."""

    rows = []
    for member in sorted(
        members,
        key=lambda item: (int(item.candidate_id), int(item.source_application.id)),
    ):
        application = member.source_application
        rows.append(
            "|".join(
                (
                    str(int(member.candidate_id)),
                    str(int(application.id)),
                    str(int(member.ats_application_id or 0)),
                    member.pipeline_stage,
                    member.pipeline_stage_updated_at.isoformat()
                    if member.pipeline_stage_updated_at is not None
                    else "",
                    member.application_outcome,
                    member.application_outcome_updated_at.isoformat()
                    if member.application_outcome_updated_at is not None
                    else "",
                    text_fingerprint(application_cv_text(application)),
                )
            )
        )
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


__all__ = [
    "RelatedRoleSourceMember",
    "application_cv_text",
    "related_role_ats_owner",
    "related_role_source_fingerprint",
    "select_related_role_source_members",
    "text_fingerprint",
]
