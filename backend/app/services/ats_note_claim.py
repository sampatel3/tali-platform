"""Exact application-scoped claims for non-replayable ATS notes."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from .application_lifecycle_restore import (
    UnresolvedProviderOperation,
    require_no_other_unresolved_provider_operation,
)
from .ats_note_provider import (
    AtsNoteProviderFailure,
    AtsNoteProviderPlan,
    note_provider_failure,
    require_ats_note_provider_enabled,
)
from .ats_note_audit import confirmed_note_event_status
from .ats_note_receipt import (
    ATS_NOTE_WRITEBACK_KEY,
    UNRESOLVED_NOTE_STATUSES,
    note_body_preview,
    note_receipt,
    note_receipt_matches,
    note_receipt_now,
    note_receipt_scope_matches,
    write_note_receipt,
)
from .ats_note_plan import build_ats_note_plan

MAX_ATS_NOTE_LENGTH = 8_000


def normalize_ats_note_body(body: str) -> str:
    """Return the one exact body used for identity, recovery, and delivery."""

    return str(body or "").strip()


def note_body_fingerprint(body: str) -> str:
    return hashlib.sha256(normalize_ats_note_body(body).encode("utf-8")).hexdigest()


def ensure_note_operation_payload(
    payload: dict, *, organization_id: int, stable_key: str | None = None
) -> dict:
    """Return a payload carrying a body-bound operation identity."""

    body = normalize_ats_note_body(str(payload.get("body") or ""))
    if len(body) > MAX_ATS_NOTE_LENGTH:
        raise note_provider_failure(
            "note_too_long",
            f"ATS note body must be {MAX_ATS_NOTE_LENGTH} characters or fewer",
        )
    body_sha256 = note_body_fingerprint(body)
    queued_body_sha256 = str(payload.get("note_body_sha256") or "").strip()
    if queued_body_sha256 and queued_body_sha256 != body_sha256:
        raise note_provider_failure(
            "invalid_note_operation", "Exact ATS note identity is required"
        )
    operation_id = str(payload.get("note_operation_id") or "").strip()
    if not operation_id:
        seed = str(stable_key or "").strip() or uuid4().hex
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
        operation_id = (
            f"ats-note:{int(organization_id)}:"
            f"{int(payload.get('application_id') or 0)}:{digest}"
        )
    if len(operation_id) > 200:
        raise note_provider_failure(
            "invalid_note_operation",
            "ATS note operation identity must be 200 characters or fewer",
        )
    return {
        **payload,
        "body": body,
        "note_operation_id": operation_id,
        "note_body_sha256": body_sha256,
    }


def _locked_note_roster(
    db: Session, *, organization_id: int, application_id: int
) -> tuple[CandidateApplication, Candidate, Role]:
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.deleted_at.is_(None),
        )
        .populate_existing()
        .with_for_update(of=CandidateApplication)
        .one_or_none()
    )
    if app is None:
        raise note_provider_failure(
            "application_unavailable", "The application is unavailable"
        )
    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.id == int(app.candidate_id),
            Candidate.organization_id == organization_id,
            Candidate.deleted_at.is_(None),
        )
        .populate_existing()
        .with_for_update(of=Candidate)
        .one_or_none()
    )
    role = (
        db.query(Role)
        .filter(
            Role.id == int(app.role_id),
            Role.organization_id == organization_id,
            Role.deleted_at.is_(None),
        )
        .populate_existing()
        .with_for_update(of=Role)
        .one_or_none()
    )
    if candidate is None:
        raise note_provider_failure(
            "candidate_unavailable", "The ATS note candidate is unavailable"
        )
    if role is None:
        raise note_provider_failure(
            "role_unavailable", "The ATS note role is unavailable"
        )
    return app, candidate, role


def _locked_note_context(
    db: Session, *, organization_id: int, application_id: int
) -> tuple[CandidateApplication, Organization, Role, Candidate]:
    app, candidate, role = _locked_note_roster(
        db,
        organization_id=organization_id,
        application_id=application_id,
    )
    org = (
        db.query(Organization)
        .filter(Organization.id == organization_id)
        .populate_existing()
        .with_for_update(of=Organization)
        .one_or_none()
    )
    if org is None:
        raise note_provider_failure(
            "organization_unavailable", "The ATS note organization is unavailable"
        )
    return app, org, role, candidate


def lock_ats_note_provider_scope(
    db: Session,
    *,
    plan: AtsNoteProviderPlan,
) -> AtsNoteProviderPlan:
    """Revalidate and lock exact note authority through provider checkpoint.

    A roster deletion that committed after the claim is observed here; one
    that starts later waits for the provider checkpoint. Workable config is
    row-locked with the roster. Bullhorn config is re-read and frozen without
    locking the Organization row so refresh-token rotation can persist through
    its independent CAS and provider mutex namespace.
    """

    app, candidate, role = _locked_note_roster(
        db,
        organization_id=int(plan.organization_id),
        application_id=int(plan.application_id),
    )
    organization_query = (
        db.query(Organization)
        .filter(Organization.id == int(plan.organization_id))
        .populate_existing()
    )
    if plan.provider == "workable":
        organization_query = organization_query.with_for_update(of=Organization)
    org = organization_query.one_or_none()
    if org is None:
        raise note_provider_failure(
            "organization_unavailable", "The ATS note organization is unavailable"
        )
    current_plan = build_ats_note_plan(
        app=app,
        org=org,
        role=role,
        candidate=candidate,
        payload={
            "provider": plan.provider,
            "provider_target_id": plan.application_provider_target_id,
            "candidate_provider_id": plan.provider_target_id,
        },
        operation_id=plan.operation_id,
        body=plan.body,
        body_sha256=plan.body_sha256,
    )
    if (
        current_plan.scope_fingerprint != plan.scope_fingerprint
        or current_plan.connection_authority_fingerprint
        != plan.connection_authority_fingerprint
        or current_plan.snapshot_fingerprint != plan.snapshot_fingerprint
    ):
        raise note_provider_failure(
            "note_authority_changed",
            "ATS note authority changed before provider delivery",
        )
    current = note_receipt(app)
    if (
        current is None
        or not note_receipt_matches(current, plan)
        or str(current.get("status") or "") != "provider_call_started"
    ):
        raise note_provider_failure(
            "note_operation_changed",
            "ATS note ownership changed before provider delivery",
        )
    require_ats_note_provider_enabled(current_plan.provider)
    return current_plan


def _attempts(items: list[dict[str, Any]]) -> int:
    values = [0]
    for item in items:
        try:
            values.append(max(0, int(item.get("attempts") or 0)))
        except (TypeError, ValueError):
            continue
    return 1 + max(values)


def prepare_ats_note_delivery(
    db: Session, *, organization_id: int, payload: dict
) -> tuple[AtsNoteProviderPlan | None, dict[str, Any] | None]:
    """Claim an exact note and commit before returning its detached plan."""

    from .ats_note_dispatch_identity import prepare_note_dispatch_identity

    payload, operation_id, _identity = prepare_note_dispatch_identity(
        payload,
        organization_id=int(organization_id),
        dispatch_key=str(payload.get("note_operation_id") or "") or None,
    )
    body = normalize_ats_note_body(str(payload.get("body") or ""))
    if len(body) > MAX_ATS_NOTE_LENGTH:
        raise note_provider_failure(
            "note_too_long",
            f"ATS note body must be {MAX_ATS_NOTE_LENGTH} characters or fewer",
        )
    body_sha256 = note_body_fingerprint(body)
    if (
        not operation_id
        or len(operation_id) > 200
        or not body
        or str(payload.get("note_body_sha256") or body_sha256) != body_sha256
    ):
        raise note_provider_failure(
            "invalid_note_operation", "Exact ATS note identity is required"
        )
    try:
        application_id = int(payload.get("application_id") or 0)
    except (TypeError, ValueError):
        application_id = 0
    if application_id <= 0:
        raise note_provider_failure(
            "invalid_note_operation", "Exact ATS note identity is required"
        )
    confirmed_status = confirmed_note_event_status(
        db,
        organization_id=int(organization_id),
        application_id=application_id,
        operation_id=operation_id,
        note_intent_sha256=str(payload.get("note_intent_sha256") or ""),
    )
    if confirmed_status == "exact":
        db.rollback()
        return None, {"status": "already_completed", "application_id": application_id}
    if confirmed_status == "mismatch":
        db.rollback()
        return None, {
            "status": "manual_reconciliation_required",
            "application_id": application_id,
            "failed": 1,
        }
    app, org, role, candidate = _locked_note_context(
        db,
        organization_id=int(organization_id),
        application_id=application_id,
    )
    plan = build_ats_note_plan(
        app=app,
        org=org,
        role=role,
        candidate=candidate,
        payload=payload,
        operation_id=operation_id,
        body=body,
        body_sha256=body_sha256,
    )
    try:
        require_no_other_unresolved_provider_operation(
            app,
            receipt_key=ATS_NOTE_WRITEBACK_KEY,
            operation_id=operation_id,
        )
    except UnresolvedProviderOperation as exc:
        raise note_provider_failure(
            "conflicting_provider_operation",
            f"Another ATS operation still owns this application ({exc.receipt_key})",
        ) from None
    current = note_receipt(app)
    attempt_sources: list[dict[str, Any]] = []
    if current and note_receipt_matches(current, plan):
        status = str(current.get("status") or "")
        if status == "confirmed":
            db.rollback()
            return None, {"status": "already_completed", "application_id": int(app.id)}
        if status == "provider_succeeded":
            db.rollback()
            return replace(plan, provider_call_required=False), None
        if status in UNRESOLVED_NOTE_STATUSES or (
            status != "failed" or current.get("provider_called") is not False
        ):
            db.rollback()
            return None, {
                "status": "manual_reconciliation_required",
                "application_id": int(app.id),
                "failed": 1,
            }
        if current.get("retriable") is not True:
            db.rollback()
            return None, {
                "status": "failed",
                "application_id": int(app.id),
                "failed": 1,
                "provider_called": False,
                "retriable": False,
                "code": str(current.get("failure_code") or "provider_rejected"),
            }
        attempt_sources.append(current)
    elif current and note_receipt_scope_matches(current, plan):
        status = str(current.get("status") or "")
        if status == "confirmed":
            db.rollback()
            return None, {"status": "already_completed", "application_id": int(app.id)}
        if status == "provider_succeeded":
            checkpoint_plan = replace(
                plan,
                connection_authority_fingerprint=str(
                    current.get("connection_authority_fingerprint") or ""
                ),
                snapshot_fingerprint=str(current.get("snapshot_fingerprint") or ""),
                provider_call_required=False,
            )
            db.rollback()
            return checkpoint_plan, None
        db.rollback()
        return None, {
            "status": "manual_reconciliation_required",
            "application_id": int(app.id),
            "failed": 1,
        }
    elif current:
        if (
            str(current.get("operation_id") or "") == operation_id
            or str(current.get("status") or "") in UNRESOLVED_NOTE_STATUSES
        ):
            db.rollback()
            return None, {
                "status": "manual_reconciliation_required",
                "application_id": int(app.id),
                "failed": 1,
            }
        if str(current.get("status") or "") not in {"confirmed", "failed"} or (
            str(current.get("status") or "") == "failed"
            and current.get("provider_called") is not False
        ):
            db.rollback()
            return None, {
                "status": "manual_reconciliation_required",
                "application_id": int(app.id),
                "failed": 1,
            }
    require_ats_note_provider_enabled(plan.provider)
    now = note_receipt_now()
    write_note_receipt(
        app,
        {
            "operation_id": operation_id,
            "application_id": int(app.id),
            "provider": plan.provider,
            "provider_target_id": plan.provider_target_id,
            "application_provider_target_id": plan.application_provider_target_id,
            "body_sha256": body_sha256,
            "note_intent_sha256": str(payload.get("note_intent_sha256") or ""),
            "body_preview": note_body_preview(body),
            "scope_fingerprint": plan.scope_fingerprint,
            "connection_authority_fingerprint": (plan.connection_authority_fingerprint),
            "snapshot_fingerprint": plan.snapshot_fingerprint,
            "attempts": _attempts(attempt_sources),
            "status": "provider_call_started",
            "provider_called": None,
            "provider_succeeded": None,
            "provider_outcome_uncertain": True,
            "manual_reconciliation_required": False,
            "job_run_id": payload.get("_job_run_id"),
            "provider_call_started_at": now,
            "updated_at": now,
        },
    )
    db.commit()
    return plan, None


__all__ = [
    "AtsNoteProviderFailure",
    "MAX_ATS_NOTE_LENGTH",
    "ensure_note_operation_payload",
    "normalize_ats_note_body",
    "note_body_fingerprint",
    "prepare_ats_note_delivery",
]
