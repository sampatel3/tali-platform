"""Source validation, roster accounting, and cost previews for related roles."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models.role import Role
from .ats_role_lifecycle import ats_job_lifecycle
from .related_role_source import (
    related_role_ats_owner,
    related_role_source_fingerprint,
    select_related_role_source_members,
)


ESTIMATED_SCORE_COST_USD = 0.083


class RelatedRoleError(ValueError):
    """A user-correctable related-role validation error."""


def get_related_role_source(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
    lock_for_update: bool = False,
) -> Role:
    query = db.query(Role).filter(
        Role.id == int(role_id),
        Role.organization_id == int(organization_id),
        Role.deleted_at.is_(None),
    )
    if lock_for_update:
        query = query.with_for_update(of=Role)
    role = query.first()
    if role is None:
        raise RelatedRoleError("Role not found.")
    return role


def related_role_roster_counts(db: Session, source: Role) -> dict[str, Any]:
    members = select_related_role_source_members(db, source)
    total = len(members)
    with_cv = sum(
        1
        for member in members
        if (
            str(member.source_application.cv_text or "").strip()
            or str(
                getattr(member.source_application.candidate, "cv_text", "") or ""
            ).strip()
        )
    )
    return {
        "total": total,
        "with_cv": with_cv,
        "missing_cv": total - with_cv,
        "snapshot_fingerprint": related_role_source_fingerprint(members),
    }


def preview_related_role(
    db: Session,
    *,
    role_id: int,
    organization_id: int,
) -> dict[str, Any]:
    source = get_related_role_source(
        db, role_id=role_id, organization_id=organization_id
    )
    counts = related_role_roster_counts(db, source)
    source_ats_provider = ats_job_lifecycle(
        related_role_ats_owner(db, source)
    ).provider
    provider_label = (
        "Bullhorn"
        if source_ats_provider == "bullhorn"
        else "Workable"
        if source_ats_provider == "workable"
        else "ATS"
    )
    return {
        "type": "related_role_preview",
        "source_role_id": int(source.id),
        "source_role_name": source.name,
        "source_ats_provider": source_ats_provider,
        "candidates_total": counts["total"],
        "candidates_with_cv": counts["with_cv"],
        "candidates_missing_cv": counts["missing_cv"],
        "source_snapshot_fingerprint": counts["snapshot_fingerprint"],
        "estimated_cost_usd": round(
            counts["with_cv"] * ESTIMATED_SCORE_COST_USD, 2
        ),
        "message": (
            f"The related role will start with a snapshot of {counts['total']} "
            f"candidates from {source.name} #{source.id}; {counts['with_cv']} can "
            "be scored now. From creation onward it owns its candidate membership, "
            f"funnel, decisions, and Agent. Any {provider_label} link is only a "
            "write-back transport and may restrict external actions."
        ),
    }


__all__ = [
    "RelatedRoleError",
    "get_related_role_source",
    "preview_related_role",
    "related_role_roster_counts",
]
