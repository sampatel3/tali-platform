"""Role-scoped application commands for Agent Chat.

This module is the model-facing boundary around application operations that
already exist elsewhere in the product.  It deliberately does not implement
confirmation receipts: ``agent_chat.tools`` owns that later-turn contract.
Instead, each externally visible or potentially costly operation has a pure
preview and a separate execution function that re-checks the role scope.

The mutation paths stay canonical:

* application creation delegates to :mod:`app.actions.create_application`
  with a recruiter actor;
* internal notes delegate to :mod:`app.services.application_notes`;
* Workable notes are only enqueued through the serialized ``OP_POST_NOTE``
  runner (there is no inline provider request here); and
* manual cycles are handed to the existing ``agent_manual_run`` task.

None of the functions commit the caller's SQLAlchemy transaction.  Agent Chat
persists a tool mutation and the surrounding transcript atomically.  Queueing
functions dispatch their existing background task and return a compact,
JSON-safe acknowledgement.
"""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session, joinedload

from ..actions import Actor, create_application as _create_application_action
from ..domains.assessments_runtime.role_support import role_has_job_spec
from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from ..models.user import User
from ..schemas.role import ApplicationCreate, ApplicationNoteCreate
from ..services.application_notes import create_recruiter_note
from ..services.workspace_agent_control import workspace_agent_control_snapshot


MAX_WORKABLE_NOTE_LENGTH = 8_000


class ApplicationCommandError(ValueError):
    """Expected, recruiter-actionable failure from an application command."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = str(code)
        self.message = str(message)
        self.details = dict(details or {})
        super().__init__(f"{self.code}: {self.message}")


def _positive_id(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ApplicationCommandError(
            "invalid_id", f"{field} must be a positive integer."
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ApplicationCommandError(
            "invalid_id", f"{field} must be a positive integer."
        ) from exc
    if parsed <= 0 or (isinstance(value, float) and not value.is_integer()):
        raise ApplicationCommandError(
            "invalid_id", f"{field} must be a positive integer."
        )
    return parsed


def _ensure_context(role: Role, user: User) -> int:
    """Validate the conversation role against the recruiter's organization."""

    role_org = getattr(role, "organization_id", None)
    user_org = getattr(user, "organization_id", None)
    role_id = getattr(role, "id", None)
    user_id = getattr(user, "id", None)
    try:
        valid = (
            int(role_org) > 0
            and int(role_org) == int(user_org)
            and int(role_id) > 0
            and int(user_id) > 0
        )
    except (TypeError, ValueError):
        valid = False
    if not valid or getattr(role, "deleted_at", None) is not None:
        # Intentionally non-disclosing: a role in another organization and an
        # unavailable role produce the same boundary failure.
        raise ApplicationCommandError(
            "scope_mismatch",
            "This role is not available in the recruiter's organization.",
        )
    return int(role_org)


def _candidate_label(app: CandidateApplication) -> str:
    candidate = getattr(app, "candidate", None)
    return str(
        getattr(candidate, "full_name", None)
        or getattr(candidate, "email", None)
        or f"Application {int(app.id)}"
    )


def _scoped_application(
    db: Session,
    role: Role,
    user: User,
    application_id: Any,
) -> CandidateApplication:
    """Load one live application without revealing cross-role ids."""

    org_id = _ensure_context(role, user)
    app_id = _positive_id(application_id, field="application_id")
    app = (
        db.query(CandidateApplication)
        .options(joinedload(CandidateApplication.candidate))
        .filter(
            CandidateApplication.id == app_id,
            CandidateApplication.organization_id == org_id,
            CandidateApplication.role_id == int(role.id),
            CandidateApplication.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if app is None:
        raise ApplicationCommandError(
            "application_not_found",
            f"Application {app_id} was not found in this role.",
        )
    return app


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "The application details are invalid."
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc") or ())
    message = str(first.get("msg") or "is invalid")
    return f"{location}: {message}" if location else message


def _application_input(
    *,
    candidate_email: str,
    candidate_name: str | None = None,
    candidate_position: str | None = None,
    notes: str | None = None,
) -> ApplicationCreate:
    """Reuse the recruiter API's validation and normalized email contract."""

    try:
        return ApplicationCreate(
            candidate_email=candidate_email,
            candidate_name=candidate_name,
            candidate_position=candidate_position,
            notes=notes,
        )
    except ValidationError as exc:
        raise ApplicationCommandError(
            "invalid_application", _validation_message(exc)
        ) from exc


def _note_body(body: str, *, maximum: int, field: str) -> str:
    cleaned = str(body or "").strip()
    if not cleaned:
        raise ApplicationCommandError("empty_note", f"{field} cannot be empty.")
    if len(cleaned) > maximum:
        raise ApplicationCommandError(
            "note_too_long",
            f"{field} cannot exceed {maximum} characters.",
            details={"maximum": maximum, "actual": len(cleaned)},
        )
    return cleaned


def _translate_create_error(exc: HTTPException) -> ApplicationCommandError:
    detail = str(exc.detail or "Application creation failed.")
    lowered = detail.lower()
    if "already has an application" in lowered:
        code = "application_exists"
    elif "job spec" in lowered:
        code = "job_spec_required"
    elif exc.status_code == 422:
        code = "invalid_application"
    else:
        code = "create_application_failed"
    return ApplicationCommandError(
        code,
        detail,
        details={"status_code": int(exc.status_code)},
    )


def preview_create_application(
    db: Session,
    role: Role,
    user: User,
    *,
    candidate_email: str,
    candidate_name: str | None = None,
    candidate_position: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Inspect the role-local effect of creating an application by email."""

    org_id = _ensure_context(role, user)
    data = _application_input(
        candidate_email=candidate_email,
        candidate_name=candidate_name,
        candidate_position=candidate_position,
        notes=notes,
    )
    email = str(data.candidate_email)
    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.organization_id == org_id,
            Candidate.email == email,
        )
        .first()
    )
    existing = None
    if candidate is not None:
        # Match the canonical action exactly: even a soft-deleted historical
        # application currently occupies the unique candidate/role pair.
        existing = (
            db.query(CandidateApplication)
            .filter(
                CandidateApplication.organization_id == org_id,
                CandidateApplication.role_id == int(role.id),
                CandidateApplication.candidate_id == int(candidate.id),
            )
            .first()
        )

    has_spec = bool(role_has_job_spec(role))
    can_create = has_spec and existing is None
    if existing is not None:
        reason = "application_exists"
    elif not has_spec:
        reason = "job_spec_required"
    else:
        reason = None

    would_update_profile = bool(
        candidate is not None
        and (
            (data.candidate_name and data.candidate_name != candidate.full_name)
            or (
                data.candidate_position
                and data.candidate_position != candidate.position
            )
        )
    )
    return {
        "type": "create_application_preview",
        "role_id": int(role.id),
        "candidate_email": email,
        "candidate_name": data.candidate_name,
        "candidate_position": data.candidate_position,
        "candidate_exists": candidate is not None,
        "candidate_id": int(candidate.id) if candidate is not None else None,
        "application_exists": existing is not None,
        "application_id": int(existing.id) if existing is not None else None,
        "would_update_candidate_profile": would_update_profile,
        "can_create": can_create,
        "blocked_reason": reason,
    }


def inspect_application_by_email(
    db: Session,
    role: Role,
    user: User,
    *,
    candidate_email: str,
    candidate_name: str | None = None,
    candidate_position: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Readable alias for callers using an inspect-then-create flow."""

    return preview_create_application(
        db,
        role,
        user,
        candidate_email=candidate_email,
        candidate_name=candidate_name,
        candidate_position=candidate_position,
        notes=notes,
    )


def create_application(
    db: Session,
    role: Role,
    user: User,
    *,
    candidate_email: str,
    candidate_name: str | None = None,
    candidate_position: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Create one application through the canonical recruiter action."""

    org_id = _ensure_context(role, user)
    data = _application_input(
        candidate_email=candidate_email,
        candidate_name=candidate_name,
        candidate_position=candidate_position,
        notes=notes,
    )
    try:
        result = _create_application_action.run(
            db,
            Actor.recruiter(user),
            organization_id=org_id,
            role_id=int(role.id),
            candidate_email=str(data.candidate_email),
            candidate_name=data.candidate_name,
            candidate_position=data.candidate_position,
            notes=data.notes,
        )
        db.flush()
    except HTTPException as exc:
        raise _translate_create_error(exc) from exc

    return {
        "type": "application_created",
        "status": str(result.status),
        "role_id": int(role.id),
        "application_id": int(result.application_id),
        "candidate_id": int(result.candidate_id),
        "candidate_email": str(data.candidate_email),
    }


def add_internal_note(
    db: Session,
    role: Role,
    user: User,
    *,
    application_id: int,
    note: str,
    for_agent: bool = True,
) -> dict[str, Any]:
    """Append an internal recruiter note; never writes it to an ATS."""

    app = _scoped_application(db, role, user, application_id)
    try:
        data = ApplicationNoteCreate(
            note=note,
            for_agent=bool(for_agent),
            kind="note",
        )
    except ValidationError as exc:
        raise ApplicationCommandError(
            "invalid_internal_note", _validation_message(exc)
        ) from exc

    event = create_recruiter_note(
        db,
        app=app,
        note=data.note,
        author=user,
        for_agent=data.for_agent,
        kind="note",
    )
    db.flush()
    return {
        "type": "internal_note_added",
        "status": "added",
        "role_id": int(role.id),
        "application_id": int(app.id),
        "candidate": _candidate_label(app),
        "event_id": int(event.id),
        "for_agent": bool(data.for_agent),
    }


def _workable_delivery_checks(
    db: Session,
    *,
    role: Role,
    organization_id: int,
    app: CandidateApplication,
) -> dict[str, bool]:
    """Return boolean readiness only; never expose integration credentials."""

    from ..services.workable_actions_service import (
        resolve_workable_actor_member_id,
        workable_writeback_enabled,
    )

    org = (
        db.query(Organization)
        .filter(Organization.id == int(organization_id))
        .one_or_none()
    )
    return {
        "application_linked": bool(app.workable_candidate_id),
        "organization_connected": bool(
            org
            and org.workable_connected
            and org.workable_access_token
            and org.workable_subdomain
        ),
        "writeback_enabled": bool(workable_writeback_enabled(org)),
        "actor_configured": bool(resolve_workable_actor_member_id(org, role=role)),
    }


def preview_workable_note(
    db: Session,
    role: Role,
    user: User,
    *,
    application_id: int,
    body: str,
) -> dict[str, Any]:
    """Preview a Workable activity note without making or queueing a write."""

    org_id = _ensure_context(role, user)
    app = _scoped_application(db, role, user, application_id)
    cleaned = _note_body(
        body, maximum=MAX_WORKABLE_NOTE_LENGTH, field="Workable note body"
    )
    checks = _workable_delivery_checks(
        db, role=role, organization_id=org_id, app=app
    )
    expected_to_post = all(checks.values())
    return {
        "type": "workable_note_preview",
        "role_id": int(role.id),
        "application_id": int(app.id),
        "candidate": _candidate_label(app),
        "body_preview": cleaned[:240],
        "body_length": len(cleaned),
        # A linked application can enter the serialized runner.  The runner is
        # still authoritative for live integration state and may report skip.
        "can_queue": bool(checks["application_linked"]),
        "expected_to_post": expected_to_post,
        "delivery_checks": checks,
    }


def queue_workable_note(
    db: Session,
    role: Role,
    user: User,
    *,
    application_id: int,
    body: str,
) -> dict[str, Any]:
    """Queue a Workable note through the per-organization serialized runner."""

    org_id = _ensure_context(role, user)
    app = _scoped_application(db, role, user, application_id)
    cleaned = _note_body(
        body, maximum=MAX_WORKABLE_NOTE_LENGTH, field="Workable note body"
    )
    if not app.workable_candidate_id:
        raise ApplicationCommandError(
            "workable_not_linked",
            "This application is not linked to a Workable candidate.",
        )

    # Lazy import is intentional.  This must remain a queue boundary; importing
    # or calling actions.post_workable_note.run here would perform external HTTP
    # on the Agent Chat worker and bypass serialization/retry bookkeeping.
    from ..services.workable_op_runner import OP_POST_NOTE, enqueue_workable_op

    job_run_id = enqueue_workable_op(
        organization_id=org_id,
        op_type=OP_POST_NOTE,
        payload={
            "application_id": int(app.id),
            "user_id": int(user.id),
            "body": cleaned,
        },
    )
    return {
        "type": "workable_note_queued",
        "status": "queued",
        "role_id": int(role.id),
        "application_id": int(app.id),
        "job_run_id": int(job_run_id) if job_run_id is not None else None,
    }


def preview_manual_run(
    db: Session,
    role: Role,
    user: User,
    *,
    application_id: int | None = None,
) -> dict[str, Any]:
    """Preview a role-wide or application-focused one-shot agent cycle."""

    _ensure_context(role, user)
    app = None
    if application_id is not None:
        app = _scoped_application(db, role, user, application_id)
    workspace_paused, _workspace_version = workspace_agent_control_snapshot(
        db,
        organization_id=int(role.organization_id),
    )
    role_paused = role.agent_paused_at is not None
    paused = workspace_paused or role_paused
    pause_scope = "workspace" if workspace_paused else ("role" if role_paused else None)
    blocked_reason = None
    if workspace_paused:
        blocked_reason = "workspace agent is paused"
    elif role_paused:
        blocked_reason = str(role.agent_paused_reason or "agent is paused")
    return {
        "type": "manual_agent_run_preview",
        "role_id": int(role.id),
        "scope": "application" if app is not None else "role",
        "application_id": int(app.id) if app is not None else None,
        "candidate": _candidate_label(app) if app is not None else None,
        "agent_enabled": bool(role.agentic_mode_enabled),
        "agent_paused": paused,
        "pause_scope": pause_scope,
        "can_queue": not paused,
        "blocked_reason": blocked_reason,
    }


def enqueue_manual_run(
    db: Session,
    role: Role,
    user: User,
    *,
    application_id: int | None = None,
) -> dict[str, Any]:
    """Enqueue the existing manual-cycle task after rechecking role scope."""

    _ensure_context(role, user)
    app = None
    if application_id is not None:
        app = _scoped_application(db, role, user, application_id)
    workspace_paused, _workspace_version = workspace_agent_control_snapshot(
        db,
        organization_id=int(role.organization_id),
        lock=True,
    )
    role_paused = role.agent_paused_at is not None
    if workspace_paused or role_paused:
        pause_scope = "workspace" if workspace_paused else "role"
        pause_reason = (
            "workspace agent is paused"
            if workspace_paused
            else str(role.agent_paused_reason or "unspecified")
        )
        return {
            "type": "manual_agent_run",
            "status": "not_queued",
            "queued": False,
            "role_id": int(role.id),
            "application_id": int(app.id) if app is not None else None,
            "pause_scope": pause_scope,
            "detail": f"agent is paused: {pause_reason}",
        }

    from ..tasks.agent_tasks import agent_manual_run

    async_result = agent_manual_run.delay(
        role_id=int(role.id),
        application_id=int(app.id) if app is not None else None,
    )
    raw_task_id = getattr(async_result, "id", None)
    return {
        "type": "manual_agent_run",
        "status": "queued",
        "queued": True,
        "role_id": int(role.id),
        "application_id": int(app.id) if app is not None else None,
        "task_id": str(raw_task_id) if raw_task_id is not None else None,
    }


__all__ = [
    "ApplicationCommandError",
    "MAX_WORKABLE_NOTE_LENGTH",
    "add_internal_note",
    "create_application",
    "enqueue_manual_run",
    "inspect_application_by_email",
    "preview_create_application",
    "preview_manual_run",
    "preview_workable_note",
    "queue_workable_note",
]
