"""Exact application-scoped claims for non-replayable ATS notes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from ..platform.config import settings
from .application_lifecycle_restore import (
    UnresolvedProviderOperation,
    require_no_other_unresolved_provider_operation,
)
from .ats_note_provider import (
    AtsNoteProviderFailure,
    AtsNoteProviderPlan,
    note_provider_failure,
)
from .ats_note_receipt import (
    ATS_NOTE_WRITEBACK_KEY,
    UNRESOLVED_NOTE_STATUSES,
    archive_note_receipt,
    note_body_preview,
    note_receipt,
    note_receipt_history,
    note_receipt_matches,
    note_receipt_now,
    note_receipt_scope_matches,
    write_note_receipt,
)
from .workable_actions_service import (
    resolve_workable_actor_member_id,
    workable_can_write_candidates,
    workable_writeback_enabled,
)


def note_body_fingerprint(body: str) -> str:
    return hashlib.sha256(str(body or "").strip().encode("utf-8")).hexdigest()


def ensure_note_operation_payload(
    payload: dict, *, organization_id: int, stable_key: str | None = None
) -> dict:
    """Return a payload carrying a body-bound operation identity."""

    body_sha256 = note_body_fingerprint(str(payload.get("body") or ""))
    operation_id = str(payload.get("note_operation_id") or "").strip()
    if not operation_id:
        seed = str(stable_key or "").strip() or uuid4().hex
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
        operation_id = (
            f"ats-note:{int(organization_id)}:"
            f"{int(payload.get('application_id') or 0)}:{digest}"
        )
    return {
        **payload,
        "note_operation_id": operation_id[:200],
        "note_body_sha256": body_sha256,
    }


def _fingerprint(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _connection_authority(org: Organization, *, provider: str) -> str:
    if provider == "workable":
        value = {
            "connected": bool(org.workable_connected),
            "subdomain": str(org.workable_subdomain or ""),
            "token": hashlib.sha256(
                str(org.workable_access_token or "").encode("utf-8")
            ).hexdigest(),
            "writeback": workable_writeback_enabled(org),
            "write_scope": workable_can_write_candidates(org),
        }
    else:
        value = {
            "enabled": bool(settings.BULLHORN_ENABLED),
            "connected": bool(org.bullhorn_connected),
            "username": str(org.bullhorn_username or ""),
            "client_id": str(org.bullhorn_client_id or ""),
            "client_secret": hashlib.sha256(
                str(org.bullhorn_client_secret or "").encode("utf-8")
            ).hexdigest(),
            "refresh_token": hashlib.sha256(
                str(org.bullhorn_refresh_token or "").encode("utf-8")
            ).hexdigest(),
            "rest_url": str(org.bullhorn_rest_url or ""),
            "credential_generation": int(org.bullhorn_credential_generation or 0),
        }
    return _fingerprint(value)


def _build_plan(
    *,
    app: CandidateApplication,
    org: Organization,
    role: Role,
    candidate: Candidate,
    payload: dict,
    operation_id: str,
    body: str,
    body_sha256: str,
) -> AtsNoteProviderPlan:
    provider = str(payload.get("provider") or "").strip().lower()
    if provider not in {"workable", "bullhorn"}:
        raise note_provider_failure(
            "invalid_provider", "An explicit ATS note provider is required"
        )
    if provider == "workable":
        provider_target = str(app.workable_candidate_id or "").strip()
        application_target = provider_target
    else:
        provider_target = str(candidate.bullhorn_candidate_id or "").strip()
        application_target = str(app.bullhorn_job_submission_id or "").strip()
    expected_application_target = str(payload.get("provider_target_id") or "").strip()
    expected_candidate_target = str(payload.get("candidate_provider_id") or "").strip()
    if (
        not provider_target
        or not application_target
        or not expected_application_target
        or not expected_candidate_target
        or expected_application_target != application_target
        or expected_candidate_target != provider_target
    ):
        raise note_provider_failure("not_linked", "The exact ATS note target changed")
    actor_member_id = resolve_workable_actor_member_id(org, role)
    if provider == "workable" and not (
        workable_writeback_enabled(org)
        and workable_can_write_candidates(org)
        and org.workable_access_token
        and org.workable_subdomain
        and actor_member_id
    ):
        raise note_provider_failure(
            "not_configured", "Workable note delivery is not configured"
        )
    if provider == "bullhorn" and not (
        settings.BULLHORN_ENABLED
        and org.bullhorn_connected
        and org.bullhorn_username
        and org.bullhorn_client_id
        and org.bullhorn_client_secret
        and org.bullhorn_refresh_token
    ):
        raise note_provider_failure(
            "not_configured", "Bullhorn note delivery is not configured"
        )
    scope_fingerprint = _fingerprint(
        {
            "application_id": int(app.id),
            "application_version": int(app.version or 0),
            "role_id": int(role.id),
            "candidate_id": int(candidate.id),
            "provider": provider,
            "provider_target_id": provider_target,
            "application_provider_target_id": application_target,
        }
    )
    connection_authority_fingerprint = _connection_authority(org, provider=provider)
    snapshot = _fingerprint(
        {
            "scope": scope_fingerprint,
            "connection_authority": connection_authority_fingerprint,
        }
    )
    config = org.bullhorn_config if isinstance(org.bullhorn_config, dict) else {}
    return AtsNoteProviderPlan(
        operation_id=operation_id,
        application_id=int(app.id),
        organization_id=int(org.id),
        provider=provider,
        provider_target_id=provider_target,
        application_provider_target_id=application_target,
        body_sha256=body_sha256,
        scope_fingerprint=scope_fingerprint,
        connection_authority_fingerprint=connection_authority_fingerprint,
        snapshot_fingerprint=snapshot,
        body=body,
        workable_access_token=org.workable_access_token,
        workable_subdomain=org.workable_subdomain,
        workable_actor_member_id=actor_member_id,
        bullhorn_username=org.bullhorn_username,
        bullhorn_client_id=org.bullhorn_client_id,
        bullhorn_client_secret=org.bullhorn_client_secret,
        bullhorn_refresh_token=org.bullhorn_refresh_token,
        bullhorn_rest_url=org.bullhorn_rest_url,
        bullhorn_credential_generation=int(org.bullhorn_credential_generation or 0),
        bullhorn_job_order_id=str(role.bullhorn_job_order_id or "").strip() or None,
        bullhorn_note_action=str(config.get("note_action") or "").strip() or "Other",
    )


def _locked_note_context(
    db: Session, *, organization_id: int, application_id: int
) -> tuple[CandidateApplication, Organization, Role, Candidate]:
    app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == organization_id,
        )
        .populate_existing()
        .with_for_update(of=CandidateApplication)
        .one_or_none()
    )
    if app is None:
        raise note_provider_failure("not_linked", "The application is unavailable")
    org = (
        db.query(Organization)
        .filter(Organization.id == organization_id)
        .populate_existing()
        .with_for_update(of=Organization)
        .one_or_none()
    )
    role = (
        db.query(Role)
        .filter(Role.id == int(app.role_id), Role.organization_id == organization_id)
        .populate_existing()
        .with_for_update(of=Role)
        .one_or_none()
    )
    candidate = (
        db.query(Candidate)
        .filter(
            Candidate.id == int(app.candidate_id),
            Candidate.organization_id == organization_id,
        )
        .populate_existing()
        .with_for_update(of=Candidate)
        .one_or_none()
    )
    if org is None or role is None or candidate is None:
        raise note_provider_failure("not_linked", "ATS note context is unavailable")
    return app, org, role, candidate


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

    operation_id = str(payload.get("note_operation_id") or "").strip()
    body = str(payload.get("body") or "").strip()
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
    app, org, role, candidate = _locked_note_context(
        db,
        organization_id=int(organization_id),
        application_id=int(payload["application_id"]),
    )
    plan = _build_plan(
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
    history = note_receipt_history(app)
    same_history = [
        item for item in history if str(item.get("operation_id") or "") == operation_id
    ]
    if any(
        note_receipt_scope_matches(item, plan) and item.get("status") == "confirmed"
        for item in same_history
    ):
        db.rollback()
        return None, {"status": "already_completed", "application_id": int(app.id)}
    if any(not note_receipt_scope_matches(item, plan) for item in same_history) or any(
        note_receipt_scope_matches(item, plan)
        and str(item.get("status") or "") in UNRESOLVED_NOTE_STATUSES
        for item in same_history
    ):
        db.rollback()
        return None, {
            "status": "manual_reconciliation_required",
            "application_id": int(app.id),
            "failed": 1,
        }
    attempt_sources = list(same_history)
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
        attempt_sources.append(current)
        archive_note_receipt(app, current)
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
        archive_note_receipt(app, current)
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
    "ensure_note_operation_payload",
    "note_body_fingerprint",
    "prepare_ats_note_delivery",
]
