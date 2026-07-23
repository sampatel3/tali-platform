"""Locking and membership helpers for role-local related-role re-screening."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.role import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FILLED,
    JOB_STATUS_FILLED_EXTERNAL,
    ROLE_KIND_SISTER,
    Role,
)
from ..models.sister_role_evaluation import SisterRoleEvaluation
from .workspace_agent_control import workspace_agent_control_snapshot

_TERMINAL_JOB_STATUSES = frozenset(
    {JOB_STATUS_CANCELLED, JOB_STATUS_FILLED, JOB_STATUS_FILLED_EXTERNAL}
)


class RelatedRoleRescreenUnavailableError(RuntimeError):
    """The requested logical role cannot currently own a re-screen."""


@dataclass(frozen=True)
class MembershipIdentity:
    evaluation_id: int
    candidate_id: int
    source_application_id: int
    ats_application_id: int | None


def is_related_role(role: Role) -> bool:
    return bool(
        str(getattr(role, "role_kind", "") or "") == ROLE_KIND_SISTER
        or getattr(role, "ats_owner_role_id", None) is not None
    )


def locked_role_snapshot(
    db: Session,
    *,
    role: Role,
) -> SimpleNamespace:
    """Lock current role authority without making its ATS owner authoritative."""

    role_id = int(role.id)
    organization_id = int(role.organization_id)
    observed_owner_id = (
        int(role.ats_owner_role_id)
        if getattr(role, "ats_owner_role_id", None) is not None
        else None
    )

    # This is the platform-wide paid-work order. Agent Chat takes the same
    # Organization lock before its authorization Role lock, so entering here
    # from a tool cannot invert the order.
    workspace_agent_control_snapshot(
        db,
        organization_id=organization_id,
        lock=True,
    )
    role_ids = {role_id}
    if observed_owner_id is not None:
        role_ids.add(observed_owner_id)
    rows = (
        db.query(
            Role.id,
            Role.organization_id,
            Role.role_kind,
            Role.ats_owner_role_id,
            Role.job_status,
            Role.job_spec_text,
            Role.deleted_at,
        )
        .filter(
            Role.id.in_(sorted(role_ids)),
            Role.organization_id == organization_id,
        )
        .order_by(Role.id.asc())
        .with_for_update(of=Role)
        .all()
    )
    by_id = {int(row.id): row for row in rows}
    current = by_id.get(role_id)
    if (
        current is None
        or current.deleted_at is not None
        or str(current.role_kind or "") != ROLE_KIND_SISTER
    ):
        raise RelatedRoleRescreenUnavailableError(
            "The related role is unavailable. No candidates were re-screened."
        )
    current_owner_id = (
        int(current.ats_owner_role_id)
        if current.ats_owner_role_id is not None
        else None
    )
    if current_owner_id != observed_owner_id:
        # The acting role is locked now, so a retry can acquire the new ordered
        # role set safely. Never continue after locking the wrong transport row.
        raise RelatedRoleRescreenUnavailableError(
            "The related role's ATS linkage changed. Refresh and confirm again."
        )
    if str(current.job_status or "").strip().lower() in _TERMINAL_JOB_STATUSES:
        raise RelatedRoleRescreenUnavailableError(
            "This role is no longer open. No candidates were re-screened."
        )
    return SimpleNamespace(
        id=role_id,
        organization_id=organization_id,
        ats_owner_role_id=current_owner_id,
        job_spec_text=str(current.job_spec_text or ""),
    )


def membership_identities(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
    application_ids: list[int] | None,
) -> list[MembershipIdentity]:
    query = (
        db.query(
            SisterRoleEvaluation.id,
            func.coalesce(
                SisterRoleEvaluation.candidate_id,
                CandidateApplication.candidate_id,
            ).label("candidate_id"),
            SisterRoleEvaluation.source_application_id,
            SisterRoleEvaluation.ats_application_id,
        )
        .join(
            CandidateApplication,
            CandidateApplication.id
            == SisterRoleEvaluation.source_application_id,
        )
        .filter(
            SisterRoleEvaluation.organization_id == int(organization_id),
            SisterRoleEvaluation.role_id == int(role_id),
            SisterRoleEvaluation.deleted_at.is_(None),
            CandidateApplication.organization_id == int(organization_id),
        )
    )
    if application_ids is not None:
        query = query.filter(
            SisterRoleEvaluation.source_application_id.in_(application_ids)
        )
    return [
        MembershipIdentity(
            evaluation_id=int(row.id),
            candidate_id=int(row.candidate_id),
            source_application_id=int(row.source_application_id),
            ats_application_id=(
                int(row.ats_application_id)
                if row.ats_application_id is not None
                else None
            ),
        )
        for row in query.order_by(SisterRoleEvaluation.id.asc()).all()
    ]


def score_is_outdated(evaluation: SisterRoleEvaluation) -> bool:
    """Recheck old-engine authority while the evaluation row is locked."""

    if evaluation.role_fit_score is None:
        return False
    from .cv_score_orchestrator import score_is_outdated as check_score_is_outdated

    return bool(
        check_score_is_outdated(
            SimpleNamespace(
                organization_id=int(evaluation.organization_id),
                cv_match_details=(
                    evaluation.details
                    if isinstance(evaluation.details, dict)
                    else {}
                ),
            )
        )
    )


__all__ = [
    "MembershipIdentity",
    "RelatedRoleRescreenUnavailableError",
    "is_related_role",
    "locked_role_snapshot",
    "membership_identities",
    "score_is_outdated",
]
