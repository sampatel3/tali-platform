"""Reconcile requisition-owned criteria onto a materialized role."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models.org_criterion import BUCKET_CONSTRAINT, BUCKET_MUST, BUCKET_PREFERRED
from ..models.role import Role
from ..models.role_brief import RoleBrief
from ..models.role_criterion import (
    CRITERION_SOURCE_RECRUITER,
    CRITERION_SOURCE_REQUISITION,
    RoleCriterion,
)


def _criterion_text(item) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("text") or item.get("label") or "").strip()
    return str(item).strip()


def materialize_brief_criteria(db: Session, brief: RoleBrief, role: Role) -> None:
    """Update only the criteria owned by the requisition that created the role."""
    desired: list[tuple[str, str, bool]] = []
    for items, bucket, must in (
        (brief.must_haves, BUCKET_MUST, True),
        (brief.preferred, BUCKET_PREFERRED, False),
        (brief.dealbreakers, BUCKET_CONSTRAINT, False),
    ):
        for item in items or []:
            text = _criterion_text(item)
            if text:
                desired.append((text, bucket, must))

    owned = (
        db.query(RoleCriterion)
        .filter(
            RoleCriterion.role_id == role.id,
            RoleCriterion.source == CRITERION_SOURCE_REQUISITION,
        )
        .order_by(RoleCriterion.ordering, RoleCriterion.id)
        .all()
    )

    # Adopt legacy recruiter rows only when their complete active shape exactly
    # matches the requisition. Any mismatch is treated as a recruiter edit.
    if not owned and desired:
        legacy = (
            db.query(RoleCriterion)
            .filter(
                RoleCriterion.role_id == role.id,
                RoleCriterion.source == CRITERION_SOURCE_RECRUITER,
                RoleCriterion.deleted_at.is_(None),
                RoleCriterion.org_criterion_id.is_(None),
                RoleCriterion.customized_at.is_(None),
            )
            .order_by(RoleCriterion.ordering, RoleCriterion.id)
            .all()
        )
        legacy_shape = [
            (row.text.strip(), row.bucket, bool(row.must_have)) for row in legacy
        ]
        if legacy_shape == desired:
            for row in legacy:
                row.source = CRITERION_SOURCE_REQUISITION
            owned = legacy

    for ordering, (text, bucket, must) in enumerate(desired):
        if ordering < len(owned):
            row = owned[ordering]
            row.text = text
            row.bucket = bucket
            row.must_have = must
            row.ordering = ordering
            row.deleted_at = None
        else:
            db.add(
                RoleCriterion(
                    role_id=role.id,
                    text=text,
                    bucket=bucket,
                    must_have=must,
                    source=CRITERION_SOURCE_REQUISITION,
                    ordering=ordering,
                )
            )

    now = datetime.now(timezone.utc)
    for row in owned[len(desired) :]:
        row.deleted_at = now
    db.flush()
