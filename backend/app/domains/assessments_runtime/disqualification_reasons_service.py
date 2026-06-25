"""Per-org disqualification (disposition) reason resolution + management.

Mirrors ``pipeline_stages_service``: reads the per-org ``disqualification_reasons``
table with a canonical-default fallback when un-seeded, plus CRUD for recruiter
management. The structured reason a recruiter picks when rejecting/withdrawing a
candidate (stored on ``candidate_applications.disposition_reason_id`` +
``disposition_category``) is the basis for rejection/source analytics.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from ...models.disqualification_reason import (
    CANONICAL_DISQUALIFICATION_REASONS,
    DISPOSITION_CATEGORIES,
    DisqualificationReason,
)


@dataclass(frozen=True)
class ReasonDef:
    label: str
    category: str
    position: int


def _canonical_reason_defs() -> list[ReasonDef]:
    return [
        ReasonDef(label=label, category=category, position=position)
        for label, category, position in CANONICAL_DISQUALIFICATION_REASONS
    ]


def ensure_org_reasons_seeded(db: Session, organization_id: int) -> int:
    """Idempotently seed the canonical reason set for an org that has none.
    Returns the number inserted (0 if already seeded). Does NOT commit."""
    has_any = (
        db.query(DisqualificationReason.id)
        .filter(DisqualificationReason.organization_id == organization_id)
        .first()
    )
    if has_any:
        return 0
    for label, category, position in CANONICAL_DISQUALIFICATION_REASONS:
        db.add(
            DisqualificationReason(
                organization_id=organization_id,
                label=label,
                category=category,
                position=position,
                is_default=True,
                is_active=True,
            )
        )
    db.flush()
    return len(CANONICAL_DISQUALIFICATION_REASONS)


def resolve_org_reasons(db: Session, organization_id: int) -> list[ReasonDef]:
    """Ordered active reasons for an org; canonical default when un-seeded."""
    rows = (
        db.query(DisqualificationReason)
        .filter(
            DisqualificationReason.organization_id == organization_id,
            DisqualificationReason.is_active.is_(True),
        )
        .order_by(DisqualificationReason.position, DisqualificationReason.id)
        .all()
    )
    if not rows:
        return _canonical_reason_defs()
    return [
        ReasonDef(label=row.label, category=row.category, position=row.position)
        for row in rows
    ]


def list_org_reasons(
    db: Session, organization_id: int, *, include_inactive: bool = False
) -> list[DisqualificationReason]:
    query = db.query(DisqualificationReason).filter(
        DisqualificationReason.organization_id == organization_id
    )
    if not include_inactive:
        query = query.filter(DisqualificationReason.is_active.is_(True))
    return query.order_by(
        DisqualificationReason.position, DisqualificationReason.id
    ).all()


def _next_position(db: Session, organization_id: int) -> int:
    current_max = (
        db.query(sa_func.max(DisqualificationReason.position))
        .filter(DisqualificationReason.organization_id == organization_id)
        .scalar()
    )
    return int(current_max) + 1 if current_max is not None else 0


def create_org_reason(
    db: Session,
    organization_id: int,
    *,
    label: str,
    category: str,
    position: int | None = None,
) -> DisqualificationReason:
    clean_label = (label or "").strip()
    if not clean_label:
        raise HTTPException(status_code=422, detail="Reason label is required")
    if category not in DISPOSITION_CATEGORIES:
        raise HTTPException(
            status_code=422, detail=f"Unsupported disposition category={category!r}"
        )
    clash = (
        db.query(DisqualificationReason.id)
        .filter(
            DisqualificationReason.organization_id == organization_id,
            DisqualificationReason.label == clean_label,
        )
        .first()
    )
    if clash:
        raise HTTPException(
            status_code=409, detail=f"Reason {clean_label!r} already exists"
        )
    row = DisqualificationReason(
        organization_id=organization_id,
        label=clean_label,
        category=category,
        position=position
        if position is not None
        else _next_position(db, organization_id),
        is_default=False,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def _get_org_reason(
    db: Session, organization_id: int, reason_id: int
) -> DisqualificationReason:
    row = (
        db.query(DisqualificationReason)
        .filter(
            DisqualificationReason.id == reason_id,
            DisqualificationReason.organization_id == organization_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Disqualification reason not found")
    return row


def update_org_reason(
    db: Session,
    organization_id: int,
    reason_id: int,
    *,
    label: str | None = None,
    category: str | None = None,
    position: int | None = None,
    is_active: bool | None = None,
) -> DisqualificationReason:
    row = _get_org_reason(db, organization_id, reason_id)
    if label is not None:
        clean = label.strip()
        if not clean:
            raise HTTPException(status_code=422, detail="Reason label cannot be empty")
        row.label = clean
    if category is not None:
        if category not in DISPOSITION_CATEGORIES:
            raise HTTPException(
                status_code=422, detail=f"Unsupported disposition category={category!r}"
            )
        row.category = category
    if position is not None:
        row.position = int(position)
    if is_active is not None:
        row.is_active = bool(is_active)
    db.flush()
    return row


def reorder_org_reasons(
    db: Session, organization_id: int, ordered_ids: list[int]
) -> list[DisqualificationReason]:
    rows = {
        row.id: row
        for row in db.query(DisqualificationReason).filter(
            DisqualificationReason.organization_id == organization_id,
            DisqualificationReason.id.in_(ordered_ids or []),
        )
    }
    if len(rows) != len(set(ordered_ids or [])):
        raise HTTPException(
            status_code=422, detail="ordered_ids contains unknown reason ids"
        )
    for index, reason_id in enumerate(ordered_ids):
        rows[reason_id].position = index
    db.flush()
    return list_org_reasons(db, organization_id, include_inactive=True)
