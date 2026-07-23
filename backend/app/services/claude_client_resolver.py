"""Per-org Anthropic client resolver.

Single chokepoint for instantiating ``anthropic.Anthropic(api_key=...)``.
Routes that have an org context should call ``get_client_for_org(org)``;
flows without an org context (admin tools, scripts) can call
``get_shared_client()`` for the Taali-wide key.

Workspace keys are **not** provisioned automatically. The Admin API has no
create-API-key endpoint (``POST /v1/organizations/api_keys`` returns 405) —
keys can only be minted in the Console. The old auto-provisioner created the
workspace, failed on the key, and orphaned the workspace on every call; ~185
dead ``taali-org-*`` workspaces accumulated that way before it was removed.

So an org uses its own key only if one was minted by hand in the Console and
stored (encrypted) on ``Organization.anthropic_workspace_key_encrypted``.
Everything else falls back to the shared key, with ``organization_id`` bound
for metering attribution. The customer-facing flow never breaks either way.
"""
from __future__ import annotations

import logging
from typing import Optional

from anthropic import Anthropic

from ..models.organization import Organization
from ..platform.config import settings
from ..platform.database import SessionLocal
from ..platform.secrets import decrypt_text
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


def _build_inner_client(
    api_key: str,
    *,
    timeout: float = _REQUEST_TIMEOUT_SECONDS,
    max_retries: int = _MAX_RETRIES,
) -> Anthropic:
    """Construct an Anthropic SDK client with prompt caching and caller-
    appropriate timeout/retry settings. The defaults were added 2026-05-22 after production
    cycles hung 15+ minutes on stuck connections — see module docstring.
    """
    return Anthropic(
        api_key=api_key,
        default_headers={"anthropic-beta": _ANTHROPIC_BETA_HEADER},
        timeout=timeout,
        max_retries=max_retries,
    )


def get_shared_client(
    *,
    organization_id: Optional[int] = None,
    timeout: float = _REQUEST_TIMEOUT_SECONDS,
    max_retries: int = _MAX_RETRIES,
) -> MeteredAnthropicClient:
    """Anthropic client using the Taali-wide ``ANTHROPIC_API_KEY``. Use only
    for flows without an org context (admin scripts, archetype synthesis
    that's shared across orgs, etc.).

    If the caller *does* know the org context (even though it's using the
    shared key), pass ``organization_id`` so the meter still attributes
    the spend correctly. Otherwise the wrapper will skip recording with
    a logged warning.
    """
    inner = _build_inner_client(
        _shared_api_key(),
        timeout=timeout,
        max_retries=max_retries,
    )
    return MeteredAnthropicClient(
        inner=inner,
        organization_id=organization_id,
        sdk_max_retries=max_retries,
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


def get_metered_client(
    *,
    organization_id: Optional[int] = None,
    timeout: float = _REQUEST_TIMEOUT_SECONDS,
    max_retries: int = _MAX_RETRIES,
) -> MeteredAnthropicClient:
    """The single gated entry point every billable call path should use.

    - ``ANTHROPIC_WORKSPACE_KEYS_ENABLED`` OFF (default) → shared Taali key,
      with ``organization_id`` bound for metering attribution. Same behaviour
      as the previous ``get_shared_client(organization_id=...)``.
    - ON, with an org → route through that org's workspace key (lazily
      provisioned, graceful shared-key fallback). This is what makes Anthropic
      report cost per-workspace so per-org reconciliation becomes a measurement
      rather than an allocation.

    Dormant until the flag is flipped, so wiring call sites to this entry point
    now is zero behaviour change.
    """
    if organization_id is not None and bool(
        getattr(settings, "ANTHROPIC_WORKSPACE_KEYS_ENABLED", False)
    ):
        try:
            with SessionLocal() as session:
                org = (
                    session.query(Organization)
                    .filter(Organization.id == int(organization_id))
                    .first()
                )
                if org is not None:
                    return get_client_for_org(
                        org,
                        timeout=timeout,
                        max_retries=max_retries,
                    )
        except Exception:
            logger.exception(
                "get_metered_client: per-org routing failed for org=%s; "
                "falling back to shared key",
                organization_id,
            )
    return get_shared_client(
        organization_id=organization_id,
        timeout=timeout,
        max_retries=max_retries,
    )


def get_client_for_org(
    org: Optional[Organization],
    *,
    timeout: float = _REQUEST_TIMEOUT_SECONDS,
    max_retries: int = _MAX_RETRIES,
) -> MeteredAnthropicClient:
    """Return a metered Anthropic client scoped to ``org``'s workspace key
    when available, otherwise the shared Taali key. Most call paths should use
    ``get_metered_client`` (which gates this behind ``ANTHROPIC_WORKSPACE_KEYS_
    ENABLED``); call this directly only when per-org routing is intentional
    regardless of the flag.

    The returned client auto-records ``usage_events`` rows for every
    ``messages.create`` / ``messages.stream`` call when the caller passes
    a ``metering={...}`` kwarg. See ``metered_anthropic_client`` for the
    full kwarg schema.

    No provisioning happens here: an org either already has a hand-minted
    workspace key stored on its row, or it uses the shared key.
    """
    org_id = int(org.id) if org is not None else None

    def _client(api_key: str) -> Anthropic:
        return _build_inner_client(
            api_key, timeout=timeout, max_retries=max_retries
        )

    def _wrap(inner: Anthropic) -> MeteredAnthropicClient:
        return MeteredAnthropicClient(
            inner=inner,
            organization_id=org_id,
            sdk_max_retries=max_retries,
        )

    if org is None:
        return _wrap(_client(_shared_api_key()))

    existing = _decrypted_workspace_key(org)
    if existing:
        return _wrap(_client(existing))

    return _wrap(_client(_shared_api_key()))
