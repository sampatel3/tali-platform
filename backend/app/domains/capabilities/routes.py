"""Admin HTTP surface for v10 capability flags.

Endpoints (admin-only, audit-logged via the ``rolled_out_by`` column):

  GET  /admin/capabilities
       List every capability with its current rollout state for the
       caller's org. Shows the registry metadata (description, risk,
       review_required) alongside the live ``capability_flags`` row.

  POST /admin/capabilities/{name}/enable
       Enable a capability for the caller's org with a structured scope.
       The body matches ``FlagScope``. Rejects unknown capabilities and
       refuses to enable when any ``requires`` dependency isn't already
       enabled at the same org scope.

  POST /admin/capabilities/{name}/disable
       Disable. Within the ~30s flag-client refresh window no new
       decisions use the capability. Stores ``rollback_reason``.

Why scope is per-org-row, not a single global row with org_ids list:
the addendum's PK is (capability, org_id). One row per (capability,
org) keeps the cache key tight and the admin dashboard query cheap.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ...capabilities.flags import get_shared
from ...capabilities.registry import ALL_CAPABILITIES, CAPABILITIES
from ...deps import get_current_user
from ...models.capability_flag import CapabilityFlag
from ...models.user import User
from ...platform.database import get_db


router = APIRouter(prefix="/admin/capabilities", tags=["capabilities"])
logger = logging.getLogger("taali.capabilities.routes")


def _require_admin(user: User) -> None:
    if not getattr(user, "is_superuser", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )


# ---------------------------------------------------------------------------
# Payload schemas
# ---------------------------------------------------------------------------


class FlagScopeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_ids: list[int] | None = None
    role_families: list[str] | None = None
    percentage: float = Field(default=100.0, ge=0.0, le=100.0)
    cohort_tags: list[str] | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class EnableBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: FlagScopeBody = Field(default_factory=FlagScopeBody)


class DisableBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


class CapabilityRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    capability: str
    description: str
    risk: str
    review_required: list[str]
    requires: list[str]
    available: bool
    unavailable_reason: str | None = None
    enabled: bool
    rolled_out_by: str | None = None
    rolled_out_at: datetime | None = None
    rollback_reason: str | None = None
    scope: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# GET /admin/capabilities
# ---------------------------------------------------------------------------


@router.get("", response_model=list[CapabilityRow])
def list_capabilities(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CapabilityRow]:
    _require_admin(current_user)
    org_id = int(current_user.organization_id)
    rows = (
        db.query(CapabilityFlag)
        .filter(CapabilityFlag.organization_id == org_id)
        .all()
    )
    by_name = {r.capability: r for r in rows}
    out: list[CapabilityRow] = []
    for name in ALL_CAPABILITIES:
        cap = CAPABILITIES[name]
        row = by_name.get(name)
        out.append(
            CapabilityRow(
                capability=name,
                description=cap.description,
                risk=cap.risk,
                review_required=list(cap.review_required),
                requires=list(cap.requires),
                available=cap.available,
                unavailable_reason=cap.unavailable_reason,
                # A stale database flag must not present scaffold-only code as
                # an enabled product capability.  Keep the row so operators
                # can disable/audit it, but report the effective state.
                enabled=bool(row.enabled and cap.available) if row else False,
                rolled_out_by=str(row.rolled_out_by) if row else None,
                rolled_out_at=row.rolled_out_at if row else None,
                rollback_reason=row.rollback_reason if row else None,
                scope=row.scope_json if row else None,
            )
        )
    return out


# ---------------------------------------------------------------------------
# POST /admin/capabilities/{name}/enable
# ---------------------------------------------------------------------------


def _ensure_dependencies(
    db: Session, *, capability: str, organization_id: int
) -> None:
    """Refuse to enable when any ``requires`` dep is not already enabled
    for the same org. This is enforced at *write* time as well as at
    *read* time — write-time enforcement makes the dashboard's "you
    can't enable A without enabling B first" UX direct.
    """
    cap = CAPABILITIES.get(capability)
    if cap is None:
        return
    for dep in cap.requires:
        row = (
            db.query(CapabilityFlag)
            .filter(
                CapabilityFlag.capability == dep,
                CapabilityFlag.organization_id == organization_id,
                CapabilityFlag.enabled.is_(True),
            )
            .first()
        )
        if row is None:
            raise HTTPException(
                status_code=409,
                detail=f"dependency '{dep}' must be enabled before '{capability}'",
            )


@router.post("/{name}/enable", response_model=CapabilityRow)
def enable_capability(
    name: str,
    body: EnableBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CapabilityRow:
    _require_admin(current_user)
    if name not in CAPABILITIES:
        raise HTTPException(status_code=404, detail=f"unknown capability '{name}'")
    cap = CAPABILITIES[name]
    if not cap.available:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "code": "CAPABILITY_NOT_READY",
                "capability": name,
                "message": cap.unavailable_reason or "Capability is not ready for rollout.",
            },
        )
    org_id = int(current_user.organization_id)
    _ensure_dependencies(db, capability=name, organization_id=org_id)

    row = (
        db.query(CapabilityFlag)
        .filter(
            CapabilityFlag.capability == name,
            CapabilityFlag.organization_id == org_id,
        )
        .first()
    )
    scope_json = body.scope.model_dump(mode="json", exclude_none=True)
    if row is None:
        row = CapabilityFlag(
            capability=name,
            organization_id=org_id,
            enabled=True,
            scope_json=scope_json,
            requires_json=list(cap.requires),
            rolled_out_by=str(getattr(current_user, "email", current_user.id)),
            rolled_out_at=datetime.now(timezone.utc),
        )
        db.add(row)
    else:
        row.enabled = True
        row.scope_json = scope_json
        row.requires_json = list(cap.requires)
        row.rolled_out_by = str(getattr(current_user, "email", current_user.id))
        row.rolled_out_at = datetime.now(timezone.utc)
        row.rollback_reason = None
    try:
        db.commit()
        db.refresh(row)
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to enable capability %s for org %s", name, org_id)
        raise HTTPException(status_code=500, detail="Failed to enable capability") from exc
    # Force shared client to re-read on the next call so subsequent
    # decisions in the same process pick up the change immediately
    # instead of waiting for the 30s refresh.
    get_shared().invalidate()
    return CapabilityRow(
        capability=name,
        description=cap.description,
        risk=cap.risk,
        review_required=list(cap.review_required),
        requires=list(cap.requires),
        available=cap.available,
        unavailable_reason=cap.unavailable_reason,
        enabled=True,
        rolled_out_by=row.rolled_out_by,
        rolled_out_at=row.rolled_out_at,
        rollback_reason=None,
        scope=row.scope_json,
    )


# ---------------------------------------------------------------------------
# POST /admin/capabilities/{name}/disable
# ---------------------------------------------------------------------------


@router.post("/{name}/disable", response_model=CapabilityRow)
def disable_capability(
    name: str,
    body: DisableBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CapabilityRow:
    _require_admin(current_user)
    if name not in CAPABILITIES:
        raise HTTPException(status_code=404, detail=f"unknown capability '{name}'")
    cap = CAPABILITIES[name]
    org_id = int(current_user.organization_id)
    row = (
        db.query(CapabilityFlag)
        .filter(
            CapabilityFlag.capability == name,
            CapabilityFlag.organization_id == org_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=409, detail=f"capability '{name}' not enabled for this org")
    row.enabled = False
    row.rollback_reason = body.reason
    try:
        db.commit()
        db.refresh(row)
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to disable capability %s for org %s", name, org_id)
        raise HTTPException(status_code=500, detail="Failed to disable capability") from exc
    get_shared().invalidate()
    return CapabilityRow(
        capability=name,
        description=cap.description,
        risk=cap.risk,
        review_required=list(cap.review_required),
        requires=list(cap.requires),
        available=cap.available,
        unavailable_reason=cap.unavailable_reason,
        enabled=False,
        rolled_out_by=row.rolled_out_by,
        rolled_out_at=row.rolled_out_at,
        rollback_reason=row.rollback_reason,
        scope=row.scope_json,
    )
