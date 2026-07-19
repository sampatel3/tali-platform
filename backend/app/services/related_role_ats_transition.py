"""Project confirmed shared-ATS moves into a related role's local funnel."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role
from ..models.sister_role_evaluation import SisterRoleEvaluation
from .sister_role_service import (
    related_role_advance_note,
    source_application_is_globally_closed,
    transition_related_role_stage,
)
from .workable_actions_service import WorkableWritebackError


@dataclass(frozen=True)
class PreparedRelatedRoleTransition:
    """Rows locked before a shared provider mutation begins."""

    role: Role
    evaluation: SisterRoleEvaluation


def prepare_related_role_ats_transition(
    db: Session,
    *,
    acting_role_id: int | None,
    application: CandidateApplication,
) -> PreparedRelatedRoleTransition | None:
    """Validate and lock requested related-role attribution before provider I/O."""

    if acting_role_id is None:
        return None
    acting_role = (
        db.query(Role)
        .filter(
            Role.id == int(acting_role_id),
            Role.organization_id == int(application.organization_id),
            Role.role_kind == ROLE_KIND_SISTER,
            Role.ats_owner_role_id == int(application.role_id),
            Role.deleted_at.is_(None),
        )
        .with_for_update()
        .one_or_none()
    )
    evaluation = None
    if acting_role is not None:
        evaluation = (
            db.query(SisterRoleEvaluation)
            .filter(
                SisterRoleEvaluation.organization_id
                == int(application.organization_id),
                SisterRoleEvaluation.role_id == int(acting_role.id),
                SisterRoleEvaluation.source_application_id == int(application.id),
            )
            .with_for_update()
            .one_or_none()
        )
    if (
        acting_role is None
        or evaluation is None
        or source_application_is_globally_closed(application)
    ):
        raise WorkableWritebackError(
            action="move",
            code="related_scope_unavailable",
            message=(
                "The related-role application changed before the ATS move could run"
            ),
            retriable=False,
        )
    return PreparedRelatedRoleTransition(
        role=acting_role,
        evaluation=evaluation,
    )


def advance_prepared_related_role_transition(
    prepared: PreparedRelatedRoleTransition | None,
) -> Role | None:
    """Apply a provider-confirmed transition to the already-locked local row."""

    if prepared is None:
        return None
    evaluation = prepared.evaluation
    if str(evaluation.pipeline_stage or "applied").strip().lower() == "advanced":
        return None
    transition_related_role_stage(
        evaluation,
        to_stage="advanced",
        source="recruiter",
    )
    return prepared.role


def finalize_prepared_workable_related_role_transition(
    db: Session,
    *,
    organization_id: int,
    prepared: PreparedRelatedRoleTransition | None,
    application: CandidateApplication,
    owner_role: Role | None,
    user_id: int | None,
    post_note: Callable[[Session, int, dict], dict],
) -> None:
    """Advance the local funnel and emit its provider-confirmed audit note.

    Workable's comment API has no idempotency key. Persisting the related stage
    suppresses ordinary replays, while a crash after provider acceptance stays
    an explicit at-least-once boundary for this informational note.
    """

    acting_role = advance_prepared_related_role_transition(prepared)
    if acting_role is None:
        return
    post_note(
        db,
        int(organization_id),
        {
            "application_id": int(application.id),
            "user_id": user_id,
            "body": related_role_advance_note(acting_role, owner_role),
        },
    )


def advance_related_role_after_confirmed_ats_move(
    db: Session,
    *,
    acting_role_id: int | None,
    application: CandidateApplication,
) -> Role | None:
    """Advance one related funnel after its shared ATS write is confirmed.

    The caller owns the transaction. Replayed ATS operations are intentionally
    idempotent: an already-advanced evaluation keeps its original transition
    timestamp and source.
    """

    prepared = prepare_related_role_ats_transition(
        db,
        acting_role_id=acting_role_id,
        application=application,
    )
    return advance_prepared_related_role_transition(prepared)


__all__ = [
    "PreparedRelatedRoleTransition",
    "advance_prepared_related_role_transition",
    "advance_related_role_after_confirmed_ats_move",
    "finalize_prepared_workable_related_role_transition",
    "prepare_related_role_ats_transition",
]
