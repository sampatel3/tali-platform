"""Safe Anthropic Admin API workspace lookup.

The current Admin API can create workspaces, but its API-key surface only
documents get/list/update operations.  It does not expose an API-key creation
endpoint.  Runtime model authentication therefore uses either an already
encrypted workspace key or Workload Identity Federation (WIF); this module
never attempts to mint a key.

``provision_workspace_for_org`` is retained for source compatibility.  Its
safe current behaviour is lookup-only: it reuses one exact, pre-created
workspace and otherwise asks the operator to configure a workspace id.  It
does not create a workspace, which avoids duplicate partial provisioning when
multiple workers start concurrently.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx

from ....platform.config import settings

logger = logging.getLogger("taali.anthropic_admin")


_ADMIN_BASE_URL = "https://api.anthropic.com"
_ANTHROPIC_VERSION = "2023-06-01"
_HTTP_TIMEOUT_SECONDS = 15.0
_WORKSPACE_LIST_LIMIT = 100


@dataclass(frozen=True)
class ProvisionedWorkspace:
    """Compatibility result for an existing, operator-created workspace."""

    workspace_id: str
    # Retained so older callers can inspect the historical attribute without a
    # schema break.  The Admin API no longer supplies plaintext key material.
    api_key_plaintext: Optional[str] = field(default=None, repr=False)


class AnthropicAdminError(Exception):
    """A redacted failure interacting with the Admin API."""


def _admin_headers() -> dict[str, str]:
    admin_key = (settings.ANTHROPIC_ADMIN_API_KEY or "").strip()
    if not admin_key:
        raise AnthropicAdminError("ANTHROPIC_ADMIN_API_KEY is not configured")
    return {
        "x-api-key": admin_key,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }


def is_configured() -> bool:
    """Whether read-only Admin API workspace lookup is configured."""

    return bool((settings.ANTHROPIC_ADMIN_API_KEY or "").strip())


def _workspace_name(*, org_id: int, org_slug: Optional[str]) -> str:
    return f"taali-org-{(org_slug or '').strip() or org_id}-{org_id}"


def find_existing_workspace_for_org(
    *,
    org_id: int,
    org_slug: Optional[str] = None,
) -> ProvisionedWorkspace:
    """Return the one exact active workspace created for ``org_id``.

    No mutating endpoint is called.  Ambiguous or absent matches fail closed so
    an operator can persist the intended ``Organization.anthropic_workspace_id``.
    Response bodies and transport exception details are deliberately omitted
    from errors because provider responses can contain operational metadata.
    """

    expected_name = _workspace_name(org_id=org_id, org_slug=org_slug)
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            response = client.get(
                f"{_ADMIN_BASE_URL}/v1/organizations/workspaces",
                headers=_admin_headers(),
                params={
                    "limit": _WORKSPACE_LIST_LIMIT,
                    "include_archived": "false",
                },
            )
    except httpx.HTTPError:
        raise AnthropicAdminError("workspace_lookup network error") from None

    if response.status_code >= 400:
        raise AnthropicAdminError(
            f"workspace_lookup failed with status {response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError:
        raise AnthropicAdminError("workspace_lookup returned invalid JSON") from None
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise AnthropicAdminError("workspace_lookup returned an invalid data shape")
    matches = [
        str(row.get("id") or "").strip()
        for row in rows
        if isinstance(row, dict)
        and str(row.get("name") or "").strip() == expected_name
        and str(row.get("id") or "").strip().startswith("wrkspc_")
    ]
    if len(matches) > 1:
        raise AnthropicAdminError("workspace_lookup found duplicate exact matches")
    if not matches:
        raise AnthropicAdminError(
            "workspace is not preconfigured; create it administratively and "
            "persist Organization.anthropic_workspace_id"
        )
    return ProvisionedWorkspace(workspace_id=matches[0])


def provision_workspace_for_org(
    *,
    org_id: int,
    org_slug: Optional[str] = None,
) -> ProvisionedWorkspace:
    """Compatibility alias for safe lookup of a pre-created workspace.

    Historically this function created a workspace and then attempted an
    unsupported API-key creation call.  It now performs no writes and returns
    no plaintext key.
    """

    return find_existing_workspace_for_org(org_id=org_id, org_slug=org_slug)
