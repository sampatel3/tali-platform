"""Anthropic organization/auth slice of role activation readiness."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models.organization import Organization
from ..models.role import Role
from .anthropic_workspace_auth import workspace_auth_readiness


def organization_and_model_auth_reason(
    session: Session | None,
    role: Role,
    *,
    settings_obj: Any,
) -> tuple[Organization | None, dict[str, str] | None]:
    """Load the owning org and return any fail-closed workspace-auth reason."""

    if session is None or getattr(role, "organization_id", None) is None:
        return None, None
    org = (
        session.query(Organization)
        .filter(Organization.id == int(role.organization_id))
        .one_or_none()
    )
    if org is None:
        return None, None
    ready, detail = workspace_auth_readiness(org, settings_obj=settings_obj)
    if ready:
        return org, None
    return org, {
        "code": "workspace_model_auth_unready",
        "detail": detail or "per-workspace Anthropic authentication is incomplete",
    }
