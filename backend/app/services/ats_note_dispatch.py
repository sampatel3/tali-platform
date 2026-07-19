"""Canonical enqueue boundary for exact ATS activity notes."""

from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from ..platform.config import settings
from .ats_job_run_errors import AtsJobRunDispatchConflict
from .ats_note_claim import MAX_ATS_NOTE_LENGTH, normalize_ats_note_body
from .workable_actions_service import (
    resolve_workable_actor_member_id,
    workable_can_write_candidates,
    workable_writeback_enabled,
)


class AtsNoteQueueError(ValueError):
    """Expected pre-provider refusal at the canonical note queue boundary."""

    def __init__(self, code: str, message: str):
        self.code = str(code)
        self.message = str(message)
        super().__init__(f"{self.code}: {self.message}")


def recruiter_note_dispatch_key(
    *,
    organization_id: int,
    actor_id: int,
    application_id: int,
    request_key: str,
) -> str:
    """Bind a caller key to one recruiter, organization, and application."""

    normalized = str(request_key or "").strip()
    if not normalized:
        raise AtsNoteQueueError(
            "invalid_request_key", "Idempotency-Key cannot be empty"
        )
    if len(normalized) > 128:
        raise AtsNoteQueueError(
            "invalid_request_key", "Idempotency-Key must be 128 characters or fewer"
        )
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return (
        f"ats-note-request/{int(organization_id)}/{int(actor_id)}/"
        f"{int(application_id)}/{digest}"
    )


def _live_note_scope(
    db: Session,
    *,
    organization_id: int,
    application_id: int,
) -> tuple[CandidateApplication, Candidate, Role, Organization]:
    """Reload the three live roster rows immediately before durable enqueue."""

    org_id = int(organization_id)
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == org_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .populate_existing()
        .one_or_none()
    )
    if app is None:
        raise AtsNoteQueueError(
            "application_unavailable", "The application is no longer available"
        )
    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.id == int(app.candidate_id),
            Candidate.organization_id == org_id,
            Candidate.deleted_at.is_(None),
        )
        .populate_existing()
        .one_or_none()
    )
    if candidate is None:
        raise AtsNoteQueueError(
            "candidate_unavailable", "The candidate is no longer available"
        )
    role = (
        db.query(Role)
        .filter(
            Role.id == int(app.role_id),
            Role.organization_id == org_id,
            Role.deleted_at.is_(None),
        )
        .populate_existing()
        .one_or_none()
    )
    if role is None:
        raise AtsNoteQueueError(
            "role_unavailable", "The role is no longer available"
        )
    organization = (
        db.query(Organization)
        .filter(Organization.id == org_id)
        .populate_existing()
        .one_or_none()
    )
    if organization is None:
        raise AtsNoteQueueError(
            "organization_unavailable", "The organization is no longer available"
        )
    return app, candidate, role, organization


def _require_provider_authority(
    *,
    provider: str,
    organization: Organization,
    role: Role,
) -> None:
    """Fail before persistence when current provider authority cannot write."""

    if provider == "workable":
        configured = bool(
            organization.workable_connected
            and str(organization.workable_access_token or "").strip()
            and str(organization.workable_subdomain or "").strip()
            and workable_writeback_enabled(organization)
            and workable_can_write_candidates(organization)
            and resolve_workable_actor_member_id(organization, role=role)
        )
        if not configured:
            raise AtsNoteQueueError(
                "workable_not_configured",
                "Workable note delivery is not currently configured",
            )
        return
    if not settings.BULLHORN_ENABLED:
        raise AtsNoteQueueError(
            "bullhorn_disabled", "Bullhorn note delivery is disabled"
        )
    configured = bool(
        organization.bullhorn_connected
        and str(organization.bullhorn_username or "").strip()
        and str(organization.bullhorn_client_id or "").strip()
        and str(organization.bullhorn_client_secret or "").strip()
        and str(organization.bullhorn_refresh_token or "").strip()
    )
    if not configured:
        raise AtsNoteQueueError(
            "bullhorn_not_configured",
            "Bullhorn note delivery is not currently configured",
        )


def prepare_application_ats_note_payload(
    db: Session,
    *,
    organization_id: int,
    application_id: int,
    body: str,
    provider: str,
    actor_type: str,
    actor_id: int | None,
    expected_provider_target_id: str | None = None,
    expected_candidate_provider_id: str | None = None,
) -> dict:
    """Return one exact provider payload after fresh live-authority checks.

    Authorization deliberately remains with the API/action/agent caller. This
    service owns live roster/provider authority and exact provider inputs.
    """

    normalized_body = normalize_ats_note_body(body)
    if not normalized_body:
        raise AtsNoteQueueError("empty_note", "Note body cannot be empty")
    if len(normalized_body) > MAX_ATS_NOTE_LENGTH:
        raise AtsNoteQueueError(
            "note_too_long",
            f"ATS note body must be {MAX_ATS_NOTE_LENGTH} characters or fewer",
        )
    provider_name = str(provider or "").strip().lower()
    if provider_name not in {"workable", "bullhorn"}:
        raise AtsNoteQueueError(
            "invalid_provider", "An explicit ATS note provider is required"
        )
    if provider_name == "workable" and settings.MVP_DISABLE_WORKABLE:
        raise AtsNoteQueueError(
            "workable_disabled", "Workable note delivery is disabled"
        )
    app, candidate, role, organization = _live_note_scope(
        db,
        organization_id=int(organization_id),
        application_id=int(application_id),
    )
    _require_provider_authority(
        provider=provider_name,
        organization=organization,
        role=role,
    )
    if provider_name == "workable":
        application_target = str(app.workable_candidate_id or "").strip()
        candidate_target = application_target
        provider_actor_member_id = resolve_workable_actor_member_id(
            organization,
            role=role,
        )
        provider_job_order_id = None
        provider_note_action = None
    elif provider_name == "bullhorn":
        application_target = str(app.bullhorn_job_submission_id or "").strip()
        candidate_target = str(candidate.bullhorn_candidate_id or "").strip()
        provider_actor_member_id = None
        provider_job_order_id = str(role.bullhorn_job_order_id or "").strip() or None
        config = (
            organization.bullhorn_config
            if isinstance(organization.bullhorn_config, dict)
            else {}
        )
        provider_note_action = str(config.get("note_action") or "").strip() or "Other"
    if not application_target or not candidate_target:
        raise AtsNoteQueueError(
            "not_linked", "The application is not linked to the selected ATS"
        )
    expected_application_target = str(expected_provider_target_id or "").strip()
    expected_candidate_target = str(expected_candidate_provider_id or "").strip()
    if (
        expected_provider_target_id is not None
        and expected_application_target != application_target
    ) or (
        expected_candidate_provider_id is not None
        and expected_candidate_target != candidate_target
    ):
        raise AtsNoteQueueError(
            "target_changed", "The exact ATS note target changed before dispatch"
        )
    if provider_name == "bullhorn" and (
        not application_target.isdigit()
        or not candidate_target.isdigit()
        or (provider_job_order_id is not None and not provider_job_order_id.isdigit())
    ):
        raise AtsNoteQueueError(
            "not_linked", "The application has invalid Bullhorn note targets"
        )
    payload = {
        "application_id": int(app.id),
        "body": normalized_body,
        "provider": provider_name,
        "provider_target_id": application_target,
        "candidate_provider_id": candidate_target,
        "provider_actor_member_id": provider_actor_member_id,
        "provider_job_order_id": provider_job_order_id,
        "provider_note_action": provider_note_action,
        "actor_type": str(actor_type or "recruiter")[:32],
        "actor_id": int(actor_id) if actor_id is not None else None,
    }
    if str(actor_type or "").strip().lower() == "recruiter" and actor_id is not None:
        payload["user_id"] = int(actor_id)
    return payload


def enqueue_application_ats_note(
    db: Session,
    *,
    organization_id: int,
    application_id: int,
    body: str,
    provider: str,
    actor_type: str,
    actor_id: int | None,
    request_key: str | None = None,
    dispatch_key: str | None = None,
    expected_provider_target_id: str | None = None,
    expected_candidate_provider_id: str | None = None,
) -> int:
    """Validate, normalize, bind, and durably queue one ATS note."""

    payload = prepare_application_ats_note_payload(
        db,
        organization_id=int(organization_id),
        application_id=int(application_id),
        body=body,
        provider=provider,
        actor_type=actor_type,
        actor_id=actor_id,
        expected_provider_target_id=expected_provider_target_id,
        expected_candidate_provider_id=expected_candidate_provider_id,
    )

    stable_dispatch_key = str(dispatch_key or "").strip() or None
    if request_key is not None:
        if actor_id is None or int(actor_id) <= 0:
            raise AtsNoteQueueError(
                "invalid_actor", "A recruiter identity is required for request replay"
            )
        stable_dispatch_key = recruiter_note_dispatch_key(
            organization_id=int(organization_id),
            actor_id=int(actor_id),
            application_id=int(payload["application_id"]),
            request_key=request_key,
        )

    from .workable_op_runner import OP_POST_NOTE, enqueue_workable_op

    try:
        return int(
            enqueue_workable_op(
                organization_id=int(organization_id),
                op_type=OP_POST_NOTE,
                payload=payload,
                scope_id=int(payload["application_id"]),
                dispatch_key=stable_dispatch_key,
            )
        )
    except AtsJobRunDispatchConflict as exc:
        raise AtsNoteQueueError(
            "idempotency_conflict",
            "That idempotency key already belongs to a different note",
        ) from exc


__all__ = [
    "AtsNoteQueueError",
    "enqueue_application_ats_note",
    "prepare_application_ats_note_payload",
    "recruiter_note_dispatch_key",
]
