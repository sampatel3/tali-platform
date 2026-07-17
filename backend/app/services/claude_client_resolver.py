"""Per-org Anthropic client resolver.

Single chokepoint for instantiating ``anthropic.Anthropic(api_key=...)``.
Routes that have an org context should call ``get_client_for_org(org)``;
flows without an org context (admin tools, scripts) can call
``get_shared_client()`` for the Taali-wide key.

Per-org auth prefers an existing encrypted workspace key for backwards
compatibility, then an explicitly configured workspace-scoped Workload
Identity Federation (WIF) credential.  The current Anthropic Admin API does
not expose key creation, so request-time lazy provisioning is intentionally
not attempted.  Incomplete per-org configuration degrades to the same shared,
metered key path while production activation readiness reports the drift.
"""
from __future__ import annotations

import logging
from typing import Optional

from anthropic import Anthropic

from ..models.organization import Organization
from ..platform.config import settings
from ..platform.database import SessionLocal
from ..platform.secrets import decrypt_text
from .anthropic_workspace_auth import (
    WorkspaceAuthConfigurationError,
    build_workspace_wif_credentials,
    workspace_auth_enabled,
    workspace_wif_configuration,
)
from .metered_anthropic_client import MeteredAnthropicClient

logger = logging.getLogger("taali.claude_client_resolver")


def _shared_api_key() -> str:
    key = (settings.ANTHROPIC_API_KEY or "").strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    return key


# Anthropic's prompt-caching ``ttl`` parameter is currently in beta.
# Requests that include ``cache_control={"type": "ephemeral", "ttl":
# "1h"}`` are SILENTLY ignored unless the request also carries this
# beta header — which manifested in prod as ``cache_read_tokens=0``
# on every pre-screen call despite the prompt being structured for
# caching, doubling token cost. The shorter (default 5m) cache works
# without the header, but pre-screen batches that span >5 minutes
# benefit from the longer window, and the header is harmless when
# cache_control isn't set, so we send it on every client.
# Docs: https://docs.anthropic.com/en/api/prompt-caching
_ANTHROPIC_BETA_HEADER = "extended-cache-ttl-2025-04-11"

# Default SDK timeout is 600s (10 min) with up to 2 silent retries, which
# means one hung TCP/TLS connection can stall a worker for 15-20 min
# producing zero observability. Real-world impact (2026-05-22, role 31):
# 32 watchdog-killed cycles in one day, each averaging ~15 min, ALL with
# zero tokens recorded — the call never returned and never errored.
#
# A 120s per-request timeout with 1 retry (= worst-case 240s) gives
# transient hiccups room to recover while ensuring the worker breaks
# out fast and surfaces a categorised ``timeout`` error in
# claude_call_log. Hard ceiling stays well under the 10-min watchdog.
_REQUEST_TIMEOUT_SECONDS = 120.0
_MAX_RETRIES = 1


def _build_inner_client(api_key: str) -> Anthropic:
    """Construct an Anthropic SDK client with the prompt-caching beta
    header set on every request, a 120s per-request timeout, and a
    single retry. The timeout was added 2026-05-22 after production
    cycles hung 15+ minutes on stuck connections — see module docstring.
    """
    return Anthropic(
        api_key=api_key,
        default_headers={"anthropic-beta": _ANTHROPIC_BETA_HEADER},
        timeout=_REQUEST_TIMEOUT_SECONDS,
        max_retries=_MAX_RETRIES,
    )


def build_bounded_anthropic_client(api_key: str) -> Anthropic:
    """Build a raw keyed client with the platform timeout/retry policy.

    This is for already-metered call paths that must use an explicitly
    resolved key. Most callers should continue to use ``get_metered_client``.
    """

    normalized = str(api_key or "").strip()
    if not normalized:
        raise RuntimeError("Anthropic API key is not configured")
    return _build_inner_client(normalized)


def _build_workspace_wif_inner_client(org: Organization) -> Anthropic:
    """Construct an explicit WIF client without consulting SDK env precedence."""

    config = workspace_wif_configuration(org, settings_obj=settings)
    return Anthropic(
        credentials=build_workspace_wif_credentials(config),
        default_headers={"anthropic-beta": _ANTHROPIC_BETA_HEADER},
        timeout=_REQUEST_TIMEOUT_SECONDS,
        max_retries=_MAX_RETRIES,
    )


def get_shared_client(
    *, organization_id: Optional[int] = None
) -> MeteredAnthropicClient:
    """Anthropic client using the Taali-wide ``ANTHROPIC_API_KEY``. Use only
    for flows without an org context (admin scripts, archetype synthesis
    that's shared across orgs, etc.).

    If the caller *does* know the org context (even though it's using the
    shared key), pass ``organization_id`` so the meter still attributes
    the spend correctly. Otherwise the wrapper will skip recording with
    a logged warning.
    """
    inner = _build_inner_client(_shared_api_key())
    return MeteredAnthropicClient(
        inner=inner, organization_id=organization_id
    )


def get_raw_shared_client() -> Anthropic:
    """Bare ``anthropic.Anthropic`` client with no metering wrapper.

    Reserved for flows that intentionally bypass metering (e.g. internal
    admin tools, the reconciliation service hitting the Admin API). Most
    code should use ``get_client_for_org`` or ``get_shared_client``.
    """
    return _build_inner_client(_shared_api_key())


def _decrypted_workspace_key(org: Organization) -> Optional[str]:
    encrypted = (getattr(org, "anthropic_workspace_key_encrypted", None) or "").strip()
    if not encrypted:
        return None
    plaintext = decrypt_text(encrypted, settings.SECRET_KEY)
    return plaintext or None


def _provision_for_org_safe(org: Organization) -> Optional[str]:
    """Compatibility re-check for a previously persisted encrypted key.

    The historical helper performed Admin API calls under a row lock.  It is
    retained for callers/tests that import it, but now performs only a short
    read transaction and never provisions remotely.
    """

    org_id = int(org.id)
    try:
        with SessionLocal() as session:
            stored = (
                session.query(Organization.anthropic_workspace_key_encrypted)
                .filter(Organization.id == org_id)
                .scalar()
            )
            session.rollback()
        if not str(stored or "").strip():
            return None
        return decrypt_text(str(stored), settings.SECRET_KEY) or None
    except Exception:
        logger.warning(
            "Stored Anthropic workspace credential could not be resolved for org=%s",
            org_id,
        )
        return None


def get_metered_client(
    *, organization_id: Optional[int] = None
) -> MeteredAnthropicClient:
    """The single gated entry point every billable call path should use.

    - Per-workspace auth OFF (default) → shared Taali key, with
      ``organization_id`` bound for metering attribution.
    - ON, with an org → route through its existing encrypted workspace key or
      validated WIF credentials, with graceful shared-key fallback.

    Dormant until the flag is flipped, so wiring call sites to this entry point
    now is zero behaviour change.
    """
    if organization_id is not None and workspace_auth_enabled(settings):
        try:
            with SessionLocal() as session:
                org = (
                    session.query(Organization)
                    .filter(Organization.id == int(organization_id))
                    .first()
                )
                if org is not None:
                    session.expunge(org)
                session.rollback()
            if org is not None:
                return get_client_for_org(org)
        except Exception as exc:
            logger.warning(
                "get_metered_client: per-org routing failed for org=%s; "
                "falling back to shared key (error_type=%s)",
                organization_id,
                type(exc).__name__,
            )
    return get_shared_client(organization_id=organization_id)


def get_client_for_org(org: Optional[Organization]) -> MeteredAnthropicClient:
    """Return a metered client using a legacy workspace key or explicit WIF.

    Most call paths should use ``get_metered_client``.  Existing encrypted keys
    retain their historical direct-call behaviour; new WIF routing always
    requires the preferred/legacy master gate as well as its explicit WIF gate.

    The returned client auto-records ``usage_events`` rows for every
    ``messages.create`` / ``messages.stream`` call when the caller passes
    a ``metering={...}`` kwarg. See ``metered_anthropic_client`` for the
    full kwarg schema.

    No provider call occurs here.  Existing encrypted keys are preserved; WIF
    exchanges happen later when the returned SDK client makes a request.
    Missing or malformed per-org configuration falls back to the shared key.
    """
    org_id = int(org.id) if org is not None else None

    def _wrap(inner: Anthropic) -> MeteredAnthropicClient:
        return MeteredAnthropicClient(inner=inner, organization_id=org_id)

    if org is None:
        return _wrap(_build_inner_client(_shared_api_key()))

    try:
        existing = _decrypted_workspace_key(org)
    except Exception:
        logger.warning(
            "Encrypted Anthropic workspace credential is invalid for org=%s",
            org_id,
        )
        existing = None
    if existing:
        return _wrap(_build_inner_client(existing))

    if not workspace_auth_enabled(settings):
        return _wrap(_build_inner_client(_shared_api_key()))

    try:
        return _wrap(_build_workspace_wif_inner_client(org))
    except WorkspaceAuthConfigurationError:
        logger.info(
            "Workspace WIF is unavailable for org=%s; using shared metered auth",
            org_id,
        )
    except Exception:
        logger.warning(
            "Workspace WIF client construction failed for org=%s; using shared metered auth",
            org_id,
        )
    return _wrap(_build_inner_client(_shared_api_key()))
