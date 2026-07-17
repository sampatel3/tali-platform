"""Validated, secret-safe Anthropic per-workspace authentication config."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from anthropic import IdentityTokenFile, WorkloadIdentityCredentials


class WorkspaceAuthConfigurationError(ValueError):
    """A stable, redacted workspace-auth configuration failure."""


@dataclass(frozen=True)
class WorkspaceWifConfiguration:
    """Non-token WIF coordinates used to construct rotating credentials."""

    federation_rule_id: str = field(repr=False)
    organization_id: str = field(repr=False)
    service_account_id: str = field(repr=False)
    workspace_id: str = field(repr=False)
    identity_token_file: str = field(repr=False)


def workspace_auth_enabled(settings_obj: Any) -> bool:
    """Resolve the preferred gate while preserving the legacy setting."""

    preferred = getattr(settings_obj, "ANTHROPIC_WORKSPACE_AUTH_ENABLED", None)
    if preferred is not None:
        return bool(preferred)
    return bool(getattr(settings_obj, "ANTHROPIC_WORKSPACE_KEYS_ENABLED", False))


def _required_setting(settings_obj: Any, name: str) -> str:
    value = str(getattr(settings_obj, name, None) or "").strip()
    if not value:
        raise WorkspaceAuthConfigurationError(f"{name} is required for workspace WIF")
    return value


def _require_tagged_id(value: str, *, prefix: str, setting_name: str) -> None:
    if not value.startswith(prefix) or len(value) <= len(prefix):
        raise WorkspaceAuthConfigurationError(
            f"{setting_name} must start with {prefix} and include an id"
        )


def _validate_token_file(raw_path: str) -> str:
    path = Path(raw_path)
    if not path.is_absolute():
        raise WorkspaceAuthConfigurationError(
            "ANTHROPIC_IDENTITY_TOKEN_FILE must be an absolute path"
        )
    try:
        file_stat = path.stat()
    except OSError as exc:
        raise WorkspaceAuthConfigurationError(
            "ANTHROPIC_IDENTITY_TOKEN_FILE is unavailable"
        ) from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise WorkspaceAuthConfigurationError(
            "ANTHROPIC_IDENTITY_TOKEN_FILE must resolve to a regular file"
        )
    if file_stat.st_size <= 0:
        raise WorkspaceAuthConfigurationError(
            "ANTHROPIC_IDENTITY_TOKEN_FILE is empty"
        )
    readable_bits = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
    if not file_stat.st_mode & readable_bits or not os.access(path, os.R_OK):
        raise WorkspaceAuthConfigurationError(
            "ANTHROPIC_IDENTITY_TOKEN_FILE is not readable"
        )
    return str(path)


def workspace_wif_configuration(
    org: Any,
    *,
    settings_obj: Any,
) -> WorkspaceWifConfiguration:
    """Validate explicit, workspace-scoped WIF configuration.

    The token file is only stat-ed here.  Its JWT is never read into application
    state; ``IdentityTokenFile`` reads the rotating value at exchange time.
    """

    if not bool(getattr(settings_obj, "ANTHROPIC_WORKSPACE_WIF_ENABLED", False)):
        raise WorkspaceAuthConfigurationError(
            "ANTHROPIC_WORKSPACE_WIF_ENABLED must be true"
        )
    federation_rule_id = _required_setting(
        settings_obj, "ANTHROPIC_FEDERATION_RULE_ID"
    )
    _require_tagged_id(
        federation_rule_id,
        prefix="fdrl_",
        setting_name="ANTHROPIC_FEDERATION_RULE_ID",
    )
    organization_id = _required_setting(settings_obj, "ANTHROPIC_ORGANIZATION_ID")
    try:
        UUID(organization_id)
    except ValueError as exc:
        raise WorkspaceAuthConfigurationError(
            "ANTHROPIC_ORGANIZATION_ID must be a UUID"
        ) from exc
    service_account_id = _required_setting(
        settings_obj, "ANTHROPIC_SERVICE_ACCOUNT_ID"
    )
    _require_tagged_id(
        service_account_id,
        prefix="svac_",
        setting_name="ANTHROPIC_SERVICE_ACCOUNT_ID",
    )
    workspace_id = str(getattr(org, "anthropic_workspace_id", None) or "").strip()
    if not workspace_id.startswith("wrkspc_") or len(workspace_id) <= len(
        "wrkspc_"
    ):
        raise WorkspaceAuthConfigurationError(
            "a persisted wrkspc_ Anthropic workspace id is required for this organization"
        )
    token_file = _validate_token_file(
        _required_setting(settings_obj, "ANTHROPIC_IDENTITY_TOKEN_FILE")
    )
    return WorkspaceWifConfiguration(
        federation_rule_id=federation_rule_id,
        organization_id=organization_id,
        service_account_id=service_account_id,
        workspace_id=workspace_id,
        identity_token_file=token_file,
    )


def build_workspace_wif_credentials(
    config: WorkspaceWifConfiguration,
) -> WorkloadIdentityCredentials:
    """Build SDK-managed rotating WIF credentials without reading the JWT."""

    return WorkloadIdentityCredentials(
        identity_token_provider=IdentityTokenFile(config.identity_token_file),
        federation_rule_id=config.federation_rule_id,
        organization_id=config.organization_id,
        service_account_id=config.service_account_id,
        workspace_id=config.workspace_id,
    )


def workspace_auth_readiness(org: Any, *, settings_obj: Any) -> tuple[bool, str | None]:
    """Return fail-closed readiness for an enabled per-workspace path."""

    if not workspace_auth_enabled(settings_obj):
        return True, None
    workspace_id = str(getattr(org, "anthropic_workspace_id", None) or "").strip()
    encrypted_key = str(
        getattr(org, "anthropic_workspace_key_encrypted", None) or ""
    ).strip()
    if encrypted_key:
        if not workspace_id.startswith("wrkspc_"):
            return False, "the encrypted workspace key has no persisted wrkspc_ id"
        return True, None
    try:
        workspace_wif_configuration(org, settings_obj=settings_obj)
    except WorkspaceAuthConfigurationError as exc:
        return False, str(exc)
    return True, None
