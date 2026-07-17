"""Detached ATS note provider plans and network calls."""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AtsNoteProviderPlan:
    operation_id: str
    application_id: int
    organization_id: int
    provider: str
    body_sha256: str
    scope_fingerprint: str
    connection_authority_fingerprint: str
    snapshot_fingerprint: str
    provider_target_id: str = field(repr=False)
    application_provider_target_id: str = field(repr=False)
    body: str = field(repr=False)
    provider_call_required: bool = field(default=True, repr=False)
    workable_access_token: str | None = field(default=None, repr=False)
    workable_subdomain: str | None = field(default=None, repr=False)
    workable_actor_member_id: str | None = field(default=None, repr=False)
    bullhorn_username: str | None = field(default=None, repr=False)
    bullhorn_client_id: str | None = field(default=None, repr=False)
    bullhorn_client_secret: str | None = field(default=None, repr=False)
    bullhorn_refresh_token: str | None = field(default=None, repr=False)
    bullhorn_rest_url: str | None = field(default=None, repr=False)
    bullhorn_credential_generation: int = field(default=0, repr=False)
    bullhorn_job_order_id: str | None = field(default=None, repr=False)
    bullhorn_note_action: str = field(default="Other", repr=False)


class AtsNoteProviderFailure(RuntimeError):
    """Sanitized provider failure with exact call-boundary evidence."""

    def __init__(self, *, code: str, message: str, provider_called: bool | None):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.provider_called = provider_called


def note_provider_failure(code: str, message: str) -> AtsNoteProviderFailure:
    return AtsNoteProviderFailure(
        code=code,
        message=message,
        provider_called=False,
    )


def _bullhorn_client(plan: AtsNoteProviderPlan):
    from ..components.integrations.bullhorn.auth import BullhornAuth
    from ..components.integrations.bullhorn.credential_state import (
        persist_rotated_credentials,
    )
    from ..components.integrations.bullhorn.service import BullhornService
    from ..platform.secrets import decrypt_integration_secret

    try:
        secret = decrypt_integration_secret(plan.bullhorn_client_secret)
        refresh = decrypt_integration_secret(plan.bullhorn_refresh_token)
    except Exception:
        raise note_provider_failure(
            "not_configured", "Stored Bullhorn credentials are unavailable"
        ) from None

    def _persist(*, refresh_token: str, rest_url: str | None = None) -> None:
        persist_rotated_credentials(
            org_id=plan.organization_id,
            expected_generation=plan.bullhorn_credential_generation,
            refresh_token=refresh_token,
            rest_url=rest_url,
        )

    auth = BullhornAuth(
        username=str(plan.bullhorn_username or ""),
        client_id=str(plan.bullhorn_client_id or ""),
        client_secret=secret,
        refresh_token=refresh or None,
        persist_tokens=_persist,
        rest_url=plan.bullhorn_rest_url,
    )
    return BullhornService(auth, client_id=str(plan.bullhorn_client_id or ""))


def _perform_workable_note(plan: AtsNoteProviderPlan) -> dict[str, Any]:
    if not all(
        str(value or "").strip()
        for value in (
            plan.workable_access_token,
            plan.workable_subdomain,
            plan.workable_actor_member_id,
            plan.provider_target_id,
        )
    ):
        raise note_provider_failure(
            "not_configured", "Workable note delivery is not configured"
        )
    from ..components.integrations.workable.service import WorkableService

    client = WorkableService(
        access_token=str(plan.workable_access_token),
        subdomain=str(plan.workable_subdomain),
    )
    try:
        result = client.post_candidate_comment(
            candidate_id=plan.provider_target_id,
            member_id=str(plan.workable_actor_member_id),
            body=plan.body,
        )
    except Exception:
        raise AtsNoteProviderFailure(
            code="api_error",
            message="Workable note delivery is uncertain; verify it before retrying",
            provider_called=None,
        ) from None
    if not isinstance(result, dict) or result.get("success") is not True:
        raise AtsNoteProviderFailure(
            code="api_error",
            message="Workable note delivery is uncertain; verify it before retrying",
            provider_called=None,
        )
    return {"provider": "workable", "provider_confirmed": True}


def _perform_bullhorn_note(plan: AtsNoteProviderPlan) -> dict[str, Any]:
    if not str(plan.provider_target_id).isdigit() or (
        plan.bullhorn_job_order_id and not str(plan.bullhorn_job_order_id).isdigit()
    ):
        raise note_provider_failure("not_linked", "Bullhorn note targets are invalid")
    try:
        response = _bullhorn_client(plan).create_note(
            comments=html.escape(plan.body).replace("\n", "<br />"),
            person_reference_id=plan.provider_target_id,
            job_order_id=plan.bullhorn_job_order_id,
            action=plan.bullhorn_note_action,
        )
    except AtsNoteProviderFailure:
        raise
    except Exception:
        raise AtsNoteProviderFailure(
            code="api_error",
            message="Bullhorn note delivery is uncertain; verify it before retrying",
            provider_called=None,
        ) from None
    receipt_id = response.get("changedEntityId") if isinstance(response, dict) else None
    if receipt_id in {None, ""}:
        raise AtsNoteProviderFailure(
            code="malformed_response",
            message="Bullhorn returned no note receipt; verify delivery before retrying",
            provider_called=None,
        )
    return {
        "provider": "bullhorn",
        "provider_confirmed": True,
        "provider_receipt_id": str(receipt_id)[:200],
    }


def perform_ats_note_provider_call(plan: AtsNoteProviderPlan) -> dict[str, Any]:
    """Post one note using only detached primitive inputs."""

    try:
        if not plan.provider_call_required:
            raise note_provider_failure(
                "provider_already_succeeded",
                "The provider call is already checkpointed",
            )
        if plan.provider == "workable":
            return _perform_workable_note(plan)
        if plan.provider == "bullhorn":
            return _perform_bullhorn_note(plan)
        raise note_provider_failure("invalid_provider", "Unsupported ATS note provider")
    except AtsNoteProviderFailure:
        raise
    except Exception:
        raise AtsNoteProviderFailure(
            code="api_error",
            message="ATS note delivery is uncertain; verify it before retrying",
            provider_called=None,
        ) from None


__all__ = [
    "AtsNoteProviderFailure",
    "AtsNoteProviderPlan",
    "note_provider_failure",
    "perform_ats_note_provider_call",
]
