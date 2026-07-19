"""Provider-authority snapshots for exact ATS note delivery."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from ..models.organization import Organization
from ..models.role import Role
from ..platform.config import settings
from .ats_note_provider import AtsNoteProviderPlan, note_provider_failure
from .workable_actions_service import (
    resolve_workable_actor_member_id,
    workable_can_write_candidates,
    workable_writeback_enabled,
)


def _fingerprint(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _connection_authority(
    org: Organization,
    *,
    provider: str,
    workable_actor_member_id: str | None,
    bullhorn_note_action: str,
) -> str:
    if provider == "workable":
        value = {
            "connected": bool(org.workable_connected),
            "subdomain": str(org.workable_subdomain or ""),
            "token": hashlib.sha256(
                str(org.workable_access_token or "").encode("utf-8")
            ).hexdigest(),
            "writeback": workable_writeback_enabled(org),
            "write_scope": workable_can_write_candidates(org),
            "actor_member_id": str(workable_actor_member_id or ""),
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
            # Refresh token/rest URL rotate normally within one generation.
            "credential_generation": int(org.bullhorn_credential_generation or 0),
            "note_action": bullhorn_note_action,
        }
    return _fingerprint(value)


def _require_expected_snapshot(
    payload: dict,
    *,
    actor_member_id: str | None,
    bullhorn_job_order_id: str | None,
    bullhorn_note_action: str,
) -> None:
    expected = {
        "provider_actor_member_id": str(actor_member_id or "") or None,
        "provider_job_order_id": str(bullhorn_job_order_id or "") or None,
        "provider_note_action": str(bullhorn_note_action or "") or None,
    }
    for key, live_value in expected.items():
        if key not in payload:
            continue
        queued_value = str(payload.get(key) or "").strip() or None
        if queued_value != live_value:
            raise note_provider_failure(
                "note_authority_changed",
                "ATS note authority changed before provider delivery",
            )


def build_ats_note_plan(
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
    """Validate live authority and freeze primitive provider-call inputs."""

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
        org.workable_connected
        and workable_writeback_enabled(org)
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

    config = org.bullhorn_config if isinstance(org.bullhorn_config, dict) else {}
    bullhorn_job_order_id = str(role.bullhorn_job_order_id or "").strip() or None
    bullhorn_note_action = str(config.get("note_action") or "").strip() or "Other"
    if provider == "bullhorn" and (
        not application_target.isdigit()
        or not provider_target.isdigit()
        or (
            bullhorn_job_order_id is not None
            and not bullhorn_job_order_id.isdigit()
        )
    ):
        raise note_provider_failure("not_linked", "Bullhorn note targets are invalid")
    _require_expected_snapshot(
        payload,
        actor_member_id=actor_member_id if provider == "workable" else None,
        bullhorn_job_order_id=(
            bullhorn_job_order_id if provider == "bullhorn" else None
        ),
        bullhorn_note_action=(bullhorn_note_action if provider == "bullhorn" else ""),
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
            "bullhorn_job_order_id": bullhorn_job_order_id,
            "bullhorn_note_action": bullhorn_note_action,
        }
    )
    connection_fingerprint = _connection_authority(
        org,
        provider=provider,
        workable_actor_member_id=actor_member_id,
        bullhorn_note_action=bullhorn_note_action,
    )
    snapshot_fingerprint = _fingerprint(
        {"scope": scope_fingerprint, "connection_authority": connection_fingerprint}
    )
    return AtsNoteProviderPlan(
        operation_id=operation_id,
        application_id=int(app.id),
        organization_id=int(org.id),
        provider=provider,
        provider_target_id=provider_target,
        application_provider_target_id=application_target,
        body_sha256=body_sha256,
        scope_fingerprint=scope_fingerprint,
        connection_authority_fingerprint=connection_fingerprint,
        snapshot_fingerprint=snapshot_fingerprint,
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
        bullhorn_job_order_id=bullhorn_job_order_id,
        bullhorn_note_action=bullhorn_note_action,
    )


__all__ = ["build_ats_note_plan"]
