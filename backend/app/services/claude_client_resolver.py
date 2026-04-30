"""Per-org Anthropic client resolver.

Single chokepoint for instantiating ``anthropic.Anthropic(api_key=...)``.
Routes that have an org context should call ``get_client_for_org(org)``;
flows without an org context (admin tools, scripts) can call
``get_shared_client()`` for the Taali-wide key.

Workspace keys are provisioned **lazily** on first call: avoids signup
latency, and orgs that never make a billable Claude call never get a
workspace at all (saving Admin API quota).

Failures degrade gracefully: any provisioning error is logged,
``Organization.anthropic_workspace_provisioning_failed_at`` is stamped, and
we fall back to the shared key. The customer-facing flow never breaks.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from anthropic import Anthropic

from ..components.integrations.anthropic_admin.service import (
    AnthropicAdminError,
    is_configured as admin_is_configured,
    provision_workspace_for_org,
)
from ..models.organization import Organization
from ..platform.config import settings
from ..platform.database import SessionLocal
from ..platform.secrets import decrypt_text, encrypt_text

logger = logging.getLogger("taali.claude_client_resolver")


def _shared_api_key() -> str:
    key = (settings.ANTHROPIC_API_KEY or "").strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    return key


def get_shared_client() -> Anthropic:
    """Anthropic client using the Taali-wide ``ANTHROPIC_API_KEY``. Use only
    for flows without an org context (admin scripts, archetype synthesis
    that's shared across orgs, etc.)."""
    return Anthropic(api_key=_shared_api_key())


def _decrypted_workspace_key(org: Organization) -> Optional[str]:
    encrypted = (getattr(org, "anthropic_workspace_key_encrypted", None) or "").strip()
    if not encrypted:
        return None
    plaintext = decrypt_text(encrypted, settings.SECRET_KEY)
    return plaintext or None


def _record_provisioning_failure(org_id: int) -> None:
    """Stamp the org so we don't hammer Admin API on every call. Uses a
    fresh session so the caller's transaction isn't tied to telemetry."""
    try:
        with SessionLocal() as session:
            org = (
                session.query(Organization)
                .filter(Organization.id == org_id)
                .first()
            )
            if org is not None:
                org.anthropic_workspace_provisioning_failed_at = datetime.now(timezone.utc)
                session.commit()
    except Exception:
        logger.exception(
            "Failed to record workspace-provisioning failure for org=%s", org_id
        )


def _persist_workspace(
    *,
    org_id: int,
    workspace_id: str,
    api_key_plaintext: str,
) -> None:
    encrypted = encrypt_text(api_key_plaintext, settings.SECRET_KEY)
    try:
        with SessionLocal() as session:
            org = (
                session.query(Organization)
                .filter(Organization.id == org_id)
                .with_for_update()
                .first()
            )
            if org is None:
                return
            org.anthropic_workspace_id = workspace_id
            org.anthropic_workspace_key_encrypted = encrypted
            org.anthropic_workspace_provisioning_failed_at = None
            session.commit()
    except Exception:
        logger.exception("Failed to persist workspace key for org=%s", org_id)


def _provision_for_org_safe(org: Organization) -> Optional[str]:
    """Try to provision a workspace + key for an org. Returns plaintext key
    on success, ``None`` on failure (caller falls back to shared key)."""
    if not admin_is_configured():
        return None
    try:
        provisioned = provision_workspace_for_org(
            org_id=int(org.id),
            org_slug=getattr(org, "slug", None),
        )
    except AnthropicAdminError as exc:
        logger.warning(
            "Admin API provisioning failed for org=%s: %s", org.id, exc
        )
        _record_provisioning_failure(int(org.id))
        return None
    except Exception:
        logger.exception(
            "Unexpected error during workspace provisioning for org=%s", org.id
        )
        _record_provisioning_failure(int(org.id))
        return None

    _persist_workspace(
        org_id=int(org.id),
        workspace_id=provisioned.workspace_id,
        api_key_plaintext=provisioned.api_key_plaintext,
    )
    return provisioned.api_key_plaintext


def get_client_for_org(org: Optional[Organization]) -> Anthropic:
    """Return an Anthropic client scoped to ``org``'s workspace key when
    available, otherwise the shared Taali key.

    Lazy provisioning: if the org has no workspace key and Admin API is
    configured, attempt to provision one now. Any failure falls back to
    the shared key without raising.
    """
    if org is None:
        return Anthropic(api_key=_shared_api_key())

    existing = _decrypted_workspace_key(org)
    if existing:
        return Anthropic(api_key=existing)

    # Skip retry if a recent attempt failed — checked at the call site so
    # we don't hammer Admin API on every Claude call. A scheduled retry
    # task can clear the timestamp later.
    failed_at = getattr(org, "anthropic_workspace_provisioning_failed_at", None)
    if failed_at is not None:
        return Anthropic(api_key=_shared_api_key())

    plaintext = _provision_for_org_safe(org)
    if plaintext:
        return Anthropic(api_key=plaintext)
    return Anthropic(api_key=_shared_api_key())
