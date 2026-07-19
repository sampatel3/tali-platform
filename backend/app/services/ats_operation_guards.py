"""Shared actor and state guards for durable ATS operations."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..actions.types import ACTOR_RECRUITER, Actor
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_STANDARD, Role
from .workable_actions_service import WorkableWritebackError


def recruiter_actor(user_id: int | None) -> Actor:
    return Actor(type=ACTOR_RECRUITER, user_id=int(user_id) if user_id else None)


def require_open_application_move(application: CandidateApplication) -> None:
    """Fail before provider I/O when a queued move lost its open-state race."""

    if (
        getattr(application, "deleted_at", None) is None
        and str(application.application_outcome or "open").strip().lower()
        == "open"
        and not bool(application.workable_disqualified)
    ):
        return
    raise WorkableWritebackError(
        action="move",
        code="application_closed",
        message="The application closed before the ATS move could run",
        retriable=False,
    )


def lock_live_application_move(
    db: Session,
    *,
    organization_id: int,
    application_id: int,
) -> CandidateApplication:
    """Lock and return a currently executable canonical ATS application.

    The application, candidate, and owning role stay locked through the
    caller's provider request. A close, deletion, or reassignment that commits
    first is observed here; one that starts later waits until this operation
    has durably recorded the confirmed provider result.
    """

    org_id = int(organization_id)
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == org_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .with_for_update()
        .one_or_none()
    )
    if app is None:
        raise WorkableWritebackError(
            action="move",
            code="application_unavailable",
            message="The application is no longer available for an ATS move",
            retriable=False,
        )

    candidate_id = getattr(app, "candidate_id", None)
    owner_role_id = getattr(app, "role_id", None)
    if candidate_id is None or owner_role_id is None:
        raise WorkableWritebackError(
            action="move",
            code="application_scope_changed",
            message="The application roster changed before the ATS move could run",
            retriable=False,
        )
    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.id == int(candidate_id),
            Candidate.organization_id == org_id,
            Candidate.deleted_at.is_(None),
        )
        .with_for_update()
        .one_or_none()
    )
    owner_role = (
        db.query(Role)
        .filter(
            Role.id == int(owner_role_id),
            Role.organization_id == org_id,
            Role.role_kind == ROLE_KIND_STANDARD,
            Role.ats_owner_role_id.is_(None),
            Role.deleted_at.is_(None),
        )
        .with_for_update()
        .one_or_none()
    )
    if candidate is None or owner_role is None:
        raise WorkableWritebackError(
            action="move",
            code="application_scope_changed",
            message="The application roster changed before the ATS move could run",
            retriable=False,
        )
    require_open_application_move(app)
    return app


__all__ = [
    "lock_live_application_move",
    "recruiter_actor",
    "require_open_application_move",
]
