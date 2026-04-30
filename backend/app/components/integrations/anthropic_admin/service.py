"""Anthropic Admin API wrapper for provisioning per-org workspace keys.

Surfaced through ``claude_client_resolver`` — most of the codebase doesn't
import this directly. Provisions one workspace + one key per org and
returns the new key's plaintext (Anthropic only returns it on creation;
we encrypt and store it on the org row).

API surface used:
- POST /v1/organizations/workspaces — create a workspace
- POST /v1/organizations/api_keys — create a workspace-scoped API key

Failure modes are non-fatal: on any error the resolver falls back to the
shared ``settings.ANTHROPIC_API_KEY`` and stamps
``Organization.anthropic_workspace_provisioning_failed_at`` so we can retry
later.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from ....platform.config import settings

logger = logging.getLogger("taali.anthropic_admin")


_ADMIN_BASE_URL = "https://api.anthropic.com"
_ANTHROPIC_VERSION = "2023-06-01"
_HTTP_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class ProvisionedWorkspace:
    workspace_id: str
    api_key_plaintext: str


class AnthropicAdminError(Exception):
    """Any failure interacting with the Admin API."""


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
    return bool((settings.ANTHROPIC_ADMIN_API_KEY or "").strip())


def provision_workspace_for_org(
    *,
    org_id: int,
    org_slug: Optional[str] = None,
) -> ProvisionedWorkspace:
    """Create a workspace + API key for an org. Returns the new
    ``ProvisionedWorkspace`` on success; raises ``AnthropicAdminError``
    otherwise.

    The workspace name is deterministic so a retry after a partial failure
    can detect the prior workspace (manual cleanup needed in that rare case).
    """
    workspace_name = f"taali-org-{(org_slug or '').strip() or org_id}-{org_id}"
    headers = _admin_headers()

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            ws_resp = client.post(
                f"{_ADMIN_BASE_URL}/v1/organizations/workspaces",
                headers=headers,
                json={"name": workspace_name},
            )
            if ws_resp.status_code >= 400:
                raise AnthropicAdminError(
                    f"create_workspace failed: {ws_resp.status_code} {ws_resp.text[:200]}"
                )
            ws_payload = ws_resp.json()
            workspace_id = str(ws_payload.get("id") or "").strip()
            if not workspace_id:
                raise AnthropicAdminError(
                    f"create_workspace returned no id: {ws_payload}"
                )

            key_resp = client.post(
                f"{_ADMIN_BASE_URL}/v1/organizations/api_keys",
                headers=headers,
                json={
                    "name": f"taali-org-{org_id}",
                    "workspace_id": workspace_id,
                },
            )
            if key_resp.status_code >= 400:
                raise AnthropicAdminError(
                    f"create_api_key failed: {key_resp.status_code} {key_resp.text[:200]}"
                )
            key_payload = key_resp.json()
            api_key = str(key_payload.get("key") or key_payload.get("api_key") or "").strip()
            if not api_key:
                raise AnthropicAdminError(
                    f"create_api_key returned no key: {list(key_payload.keys())}"
                )
            return ProvisionedWorkspace(
                workspace_id=workspace_id, api_key_plaintext=api_key
            )
    except httpx.HTTPError as exc:
        raise AnthropicAdminError(f"network_error: {exc}") from exc
