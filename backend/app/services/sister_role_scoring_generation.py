"""Exact input-generation fencing for durable related-role scoring."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import (
    SISTER_EVAL_RUNNING,
    SisterRoleEvaluation,
)
from . import workable_context_service
from .sister_role_service import text_fingerprint
from .workspace_agent_control import workspace_agent_control_snapshot


@dataclass(frozen=True)
class SisterScoreLocator:
    evaluation_id: int
    organization_id: int
    role_id: int
    ats_owner_role_id: int
    application_id: int
    candidate_id: int


@dataclass(frozen=True)
class SisterScoreInputs:
    cv_text: str
    job_spec: str
    workable_context: str | None
    spec_fingerprint: str
    cv_fingerprint: str
    context_fingerprint: str


@dataclass(frozen=True)
class SisterScoreGeneration:
    locator: SisterScoreLocator
    attempts: int
    started_at: datetime
    spec_fingerprint: str
    cv_fingerprint: str
    context_fingerprint: str


@dataclass(frozen=True)
class LockedSisterScoreRows:
    role: Role
    candidate: Candidate
    application: CandidateApplication
    evaluation: SisterRoleEvaluation


def locate_sister_score(
    db: Session, *, evaluation_id: int
) -> SisterScoreLocator | None:
    """Read immutable row identities without taking a row lock."""

    row = (
        db.query(
            SisterRoleEvaluation.organization_id,
            SisterRoleEvaluation.role_id,
            Role.ats_owner_role_id,
            SisterRoleEvaluation.source_application_id,
            CandidateApplication.candidate_id,
        )
        .join(Role, Role.id == SisterRoleEvaluation.role_id)
        .join(
            CandidateApplication,
            CandidateApplication.id == SisterRoleEvaluation.source_application_id,
        )
        .filter(SisterRoleEvaluation.id == int(evaluation_id))
        .one_or_none()
    )
    if row is None or row[2] is None:
        return None
    return SisterScoreLocator(
        evaluation_id=int(evaluation_id),
        organization_id=int(row[0]),
        role_id=int(row[1]),
        ats_owner_role_id=int(row[2]),
        application_id=int(row[3]),
        candidate_id=int(row[4]),
    )


def lock_sister_score_rows(
    db: Session,
    *,
    locator: SisterScoreLocator,
    skip_locked: bool = True,
) -> LockedSisterScoreRows | None:
    """Lock Organization -> ordered Roles -> Candidate -> Application -> evaluation."""

    workspace_agent_control_snapshot(
        db, organization_id=locator.organization_id, lock=True
    )
    role_ids = sorted({locator.role_id, locator.ats_owner_role_id})
    roles = (
        db.query(Role)
        .filter(
            Role.id.in_(role_ids),
            Role.organization_id == locator.organization_id,
            Role.deleted_at.is_(None),
        )
        .order_by(Role.id.asc())
        .with_for_update(of=Role)
        .populate_existing()
        .all()
    )
    by_id = {int(item.id): item for item in roles}
    role = by_id.get(locator.role_id)
    owner_role = by_id.get(locator.ats_owner_role_id)
    if (
        role is None
        or owner_role is None
        or str(role.role_kind or "") != ROLE_KIND_SISTER
        or int(role.ats_owner_role_id or 0) != locator.ats_owner_role_id
    ):
        return None
    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.id == locator.candidate_id,
            Candidate.organization_id == locator.organization_id,
            Candidate.deleted_at.is_(None),
        )
        .with_for_update(of=Candidate)
        .populate_existing()
        .one_or_none()
    )
    if candidate is None:
        return None
    application = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == locator.application_id,
            CandidateApplication.organization_id == locator.organization_id,
            CandidateApplication.candidate_id == locator.candidate_id,
            CandidateApplication.role_id == locator.ats_owner_role_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .with_for_update(of=CandidateApplication)
        .populate_existing()
        .one_or_none()
    )
    if application is None:
        return None
    evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.id == locator.evaluation_id,
            SisterRoleEvaluation.organization_id == locator.organization_id,
            SisterRoleEvaluation.role_id == locator.role_id,
            SisterRoleEvaluation.source_application_id == locator.application_id,
        )
        .with_for_update(
            of=SisterRoleEvaluation,
            skip_locked=bool(skip_locked),
        )
        .populate_existing()
        .one_or_none()
    )
    if evaluation is None:
        return None
    return LockedSisterScoreRows(
        role=role,
        candidate=candidate,
        application=application,
        evaluation=evaluation,
    )


def capture_sister_score_inputs(rows: LockedSisterScoreRows) -> SisterScoreInputs:
    """Capture the exact strings consumed by the provider call."""

    cv_text = (
        str(rows.application.cv_text or "").strip()
        or str(rows.candidate.cv_text or "").strip()
    )
    job_spec = str(rows.role.job_spec_text or "").strip()
    context = (
        workable_context_service.format_workable_context(
            rows.candidate, rows.application
        )
        or None
    )
    return SisterScoreInputs(
        cv_text=cv_text,
        job_spec=job_spec,
        workable_context=context,
        spec_fingerprint=text_fingerprint(job_spec),
        cv_fingerprint=text_fingerprint(cv_text),
        context_fingerprint=hashlib.sha256(
            (context or "").encode("utf-8")
        ).hexdigest(),
    )


def capture_sister_score_generation(
    rows: LockedSisterScoreRows,
    inputs: SisterScoreInputs,
) -> SisterScoreGeneration:
    evaluation = rows.evaluation
    if evaluation.started_at is None:
        raise ValueError("related-role score attempt has no start time")
    return SisterScoreGeneration(
        locator=SisterScoreLocator(
            evaluation_id=int(evaluation.id),
            organization_id=int(evaluation.organization_id),
            role_id=int(evaluation.role_id),
            ats_owner_role_id=int(rows.role.ats_owner_role_id),
            application_id=int(evaluation.source_application_id),
            candidate_id=int(rows.application.candidate_id),
        ),
        attempts=int(evaluation.attempts or 0),
        started_at=evaluation.started_at,
        spec_fingerprint=inputs.spec_fingerprint,
        cv_fingerprint=inputs.cv_fingerprint,
        context_fingerprint=inputs.context_fingerprint,
    )


def _normalized_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def sister_score_generation_is_current(
    rows: LockedSisterScoreRows,
    *,
    expected: SisterScoreGeneration,
) -> tuple[bool, SisterScoreInputs]:
    """Compare the locked durable lease and every effective provider input."""

    current_inputs = capture_sister_score_inputs(rows)
    evaluation = rows.evaluation
    current = bool(
        sister_score_attempt_is_current(rows, expected=expected)
        and evaluation.spec_fingerprint == expected.spec_fingerprint
        and evaluation.cv_fingerprint == expected.cv_fingerprint
        and current_inputs.spec_fingerprint == expected.spec_fingerprint
        and current_inputs.cv_fingerprint == expected.cv_fingerprint
        and current_inputs.context_fingerprint == expected.context_fingerprint
    )
    return current, current_inputs


def sister_score_attempt_is_current(
    rows: LockedSisterScoreRows,
    *,
    expected: SisterScoreGeneration,
) -> bool:
    """Whether the locked row is still the exact provider-call lease."""

    evaluation = rows.evaluation
    return bool(
        evaluation.status == SISTER_EVAL_RUNNING
        and int(evaluation.attempts or 0) == expected.attempts
        and _normalized_timestamp(evaluation.started_at)
        == _normalized_timestamp(expected.started_at)
    )


__all__ = [
    "LockedSisterScoreRows",
    "SisterScoreGeneration",
    "SisterScoreInputs",
    "capture_sister_score_generation",
    "capture_sister_score_inputs",
    "locate_sister_score",
    "lock_sister_score_rows",
    "sister_score_attempt_is_current",
    "sister_score_generation_is_current",
]
