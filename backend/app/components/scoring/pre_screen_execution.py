"""Linearizable direct pre-screen execution against mutable role inputs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ...services.role_intent_fingerprint import role_intent_fingerprint
from .candidate_inputs import (
    candidate_input_fingerprint,
    candidate_input_fingerprint_from_db,
)


class _RoleGenerationSuperseded(RuntimeError):
    pass


class _CandidateGenerationSuperseded(RuntimeError):
    pass


def _load_role(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
    lock: bool,
) -> Role | None:
    query = (
        db.query(Role)
        .filter(
            Role.id == int(role_id),
            Role.organization_id == int(organization_id),
            Role.deleted_at.is_(None),
        )
        .populate_existing()
    )
    if lock:
        query = query.with_for_update()
    return query.one_or_none()


def execute_pre_screen_with_role_fence(
    db: Session,
    *,
    application: CandidateApplication,
    role: Role,
    execute: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Run and commit one pre-screen only if its role inputs stay current.

    The provider call is intentionally lock-free. After it returns, the Role
    row is locked and the complete scoring-input fingerprint is recomputed.
    This orders a concurrent recruiter edit on one side of the score commit:
    an earlier edit makes this savepoint roll back; a later edit waits and then
    invalidates the just-committed score itself.
    """
    captured_role = _load_role(
        db,
        role_id=int(role.id),
        organization_id=int(role.organization_id),
        lock=False,
    )
    if captured_role is None:
        return {"status": "superseded", "reason": "role_unavailable"}
    captured_application = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(application.id),
            CandidateApplication.organization_id == int(role.organization_id),
            CandidateApplication.role_id == int(role.id),
            CandidateApplication.deleted_at.is_(None),
        )
        .populate_existing()
        .one_or_none()
    )
    if captured_application is None:
        return {"status": "superseded", "reason": "application_unavailable"}
    captured_candidate = (
        db.query(Candidate)
        .filter(
            Candidate.id == int(captured_application.candidate_id),
            Candidate.organization_id == int(role.organization_id),
            Candidate.deleted_at.is_(None),
        )
        .populate_existing()
        .one_or_none()
    )
    if captured_candidate is None:
        return {"status": "superseded", "reason": "candidate_unavailable"}
    captured_fingerprint = role_intent_fingerprint(captured_role, db=db)
    captured_candidate_fingerprint = candidate_input_fingerprint(
        captured_application, captured_candidate
    )

    try:
        with db.begin_nested():
            result = execute()
            # Never flush provider-derived application mutations before taking
            # the Role generation lock. A recruiter intent edit takes that lock
            # first and then invalidates applications; reversing the order here
            # would deadlock (score: Application -> Role, edit: Role ->
            # Application). ``no_autoflush`` makes the ordering explicit even
            # for sessions whose default changes in future.
            with db.no_autoflush:
                current_role = _load_role(
                    db,
                    role_id=int(role.id),
                    organization_id=int(role.organization_id),
                    lock=True,
                )
                current_fingerprint = (
                    role_intent_fingerprint(current_role, db=db)
                    if current_role is not None
                    else None
                )
                current_candidate_fingerprint = candidate_input_fingerprint_from_db(
                    db,
                    application_id=int(captured_application.id),
                    candidate_id=int(captured_candidate.id),
                    organization_id=int(role.organization_id),
                    role_id=int(role.id),
                    lock=True,
                )
            if current_fingerprint != captured_fingerprint:
                raise _RoleGenerationSuperseded
            if current_candidate_fingerprint != captured_candidate_fingerprint:
                raise _CandidateGenerationSuperseded
            # Materialize every application mutation inside the savepoint only
            # after the generation comparison, while the Role lock is held.
            db.flush()
    except _RoleGenerationSuperseded:
        db.rollback()
        return {
            "status": "superseded",
            "reason": "role_intent_changed_during_pre_screen",
        }
    except _CandidateGenerationSuperseded:
        db.rollback()
        return {
            "status": "superseded",
            "reason": "candidate_inputs_changed_during_pre_screen",
        }
    except Exception:
        db.rollback()
        raise

    # Release the Role lock immediately. A later recruiter edit then runs its
    # normal invalidation after this committed score, preserving ordering.
    db.commit()
    return result


__all__ = ["execute_pre_screen_with_role_fence"]
