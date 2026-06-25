"""Per-org pipeline-stage resolution.

The single source of truth for an organization's funnel stages, replacing the
hard-coded ``pipeline_service.PIPELINE_STAGES`` tuple. Reads the per-org
``pipeline_stages`` table and FALLS BACK to the canonical legacy stages when an
org has no rows yet (un-seeded) — so the switch-over is behaviour-preserving by
construction: every org is seeded (migration 120) with the exact legacy 5
stages, and any un-seeded org transparently gets the same defaults.

P0 MIGRATE STEP: ``pipeline_service`` and its callers are pointed at this
resolver instead of the tuple; the tuple stays as the canonical fallback until
the contract step removes it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from ...models.pipeline_stage import (
    CANONICAL_SEED_STAGES,
    LEGACY_STAGE_KIND,
    STAGE_KINDS,
    PipelineStage,
)


@dataclass(frozen=True)
class StageDef:
    """A resolved pipeline stage (data-source agnostic)."""

    slug: str
    name: str
    kind: str
    position: int


def _canonical_stage_defs() -> list[StageDef]:
    return [
        StageDef(slug=slug, name=name, kind=kind, position=position)
        for slug, name, kind, position in CANONICAL_SEED_STAGES
    ]


def ensure_org_stages_seeded(db: Session, organization_id: int) -> int:
    """Idempotently seed the canonical stages for an org that has none.

    Returns the number of stages inserted (0 if already seeded). Safe to call as
    a backfill safety net and at org-creation time (a fresh ``create_all`` DB
    does not run migration 120's seed). Does NOT commit — the caller owns the
    transaction.
    """
    has_any = (
        db.query(PipelineStage.id)
        .filter(PipelineStage.organization_id == organization_id)
        .first()
    )
    if has_any:
        return 0
    for slug, name, kind, position in CANONICAL_SEED_STAGES:
        db.add(
            PipelineStage(
                organization_id=organization_id,
                slug=slug,
                name=name,
                kind=kind,
                position=position,
                is_default=True,
                is_active=True,
            )
        )
    db.flush()
    return len(CANONICAL_SEED_STAGES)


def resolve_org_stages(db: Session, organization_id: int) -> list[StageDef]:
    """The ordered, active pipeline stages for an org.

    Reads the per-org ``pipeline_stages`` table; returns the canonical legacy
    stages when the org has no active rows (un-seeded). This is what the reader
    refactor consults instead of the hard-coded ``PIPELINE_STAGES`` tuple.
    """
    rows = (
        db.query(PipelineStage)
        .filter(
            PipelineStage.organization_id == organization_id,
            PipelineStage.is_active.is_(True),
        )
        .order_by(PipelineStage.position, PipelineStage.id)
        .all()
    )
    if not rows:
        return _canonical_stage_defs()
    return [
        StageDef(slug=row.slug, name=row.name, kind=row.kind, position=row.position)
        for row in rows
    ]


def resolve_stage_slugs(db: Session, organization_id: int) -> tuple[str, ...]:
    """Ordered active stage slugs for an org (the configurable analogue of the
    ``PIPELINE_STAGES`` tuple)."""
    return tuple(stage.slug for stage in resolve_org_stages(db, organization_id))


def stage_kind_for(db: Session, organization_id: int, slug: str | None) -> str | None:
    """The ``kind`` of a stage slug for an org. Falls back to the legacy
    slug->kind mapping for unknown/legacy slugs, else None."""
    if not slug:
        return None
    for stage in resolve_org_stages(db, organization_id):
        if stage.slug == slug:
            return stage.kind
    return LEGACY_STAGE_KIND.get(slug)


# --- Management (CRUD) ------------------------------------------------------
# Recruiter-facing stage management. Returns/raises HTTPException on validation
# (consistent with the rest of the domain). All mutators flush but do NOT commit.


def _slugify_stage(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")


def list_org_stages(
    db: Session, organization_id: int, *, include_inactive: bool = False
) -> list[PipelineStage]:
    """The org's materialized stage rows (for management), ordered by position.
    Unlike ``resolve_org_stages`` this does NOT fall back to canonical — call
    ``ensure_org_stages_seeded`` first when you need guaranteed rows."""
    query = db.query(PipelineStage).filter(
        PipelineStage.organization_id == organization_id
    )
    if not include_inactive:
        query = query.filter(PipelineStage.is_active.is_(True))
    return query.order_by(PipelineStage.position, PipelineStage.id).all()


def _next_position(db: Session, organization_id: int) -> int:
    current_max = (
        db.query(sa_func.max(PipelineStage.position))
        .filter(PipelineStage.organization_id == organization_id)
        .scalar()
    )
    return int(current_max) + 1 if current_max is not None else 0


def create_org_stage(
    db: Session,
    organization_id: int,
    *,
    name: str,
    kind: str,
    slug: str | None = None,
    position: int | None = None,
) -> PipelineStage:
    """Create a custom stage. Validates kind + unique slug per org."""
    clean_name = (name or "").strip()
    if not clean_name:
        raise HTTPException(status_code=422, detail="Stage name is required")
    if kind not in STAGE_KINDS:
        raise HTTPException(status_code=422, detail=f"Unsupported stage kind={kind!r}")
    stage_slug = _slugify_stage(slug or clean_name)
    if not stage_slug:
        raise HTTPException(status_code=422, detail="Could not derive a stage slug")
    clash = (
        db.query(PipelineStage.id)
        .filter(
            PipelineStage.organization_id == organization_id,
            PipelineStage.slug == stage_slug,
        )
        .first()
    )
    if clash:
        raise HTTPException(
            status_code=409, detail=f"Stage slug {stage_slug!r} already exists"
        )
    row = PipelineStage(
        organization_id=organization_id,
        slug=stage_slug,
        name=clean_name,
        kind=kind,
        position=position
        if position is not None
        else _next_position(db, organization_id),
        is_default=False,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def _get_org_stage(db: Session, organization_id: int, stage_id: int) -> PipelineStage:
    row = (
        db.query(PipelineStage)
        .filter(
            PipelineStage.id == stage_id,
            PipelineStage.organization_id == organization_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Pipeline stage not found")
    return row


def update_org_stage(
    db: Session,
    organization_id: int,
    stage_id: int,
    *,
    name: str | None = None,
    kind: str | None = None,
    position: int | None = None,
    is_active: bool | None = None,
) -> PipelineStage:
    """Update a stage's name / kind / position / is_active."""
    row = _get_org_stage(db, organization_id, stage_id)
    if name is not None:
        clean = name.strip()
        if not clean:
            raise HTTPException(status_code=422, detail="Stage name cannot be empty")
        row.name = clean
    if kind is not None:
        if kind not in STAGE_KINDS:
            raise HTTPException(
                status_code=422, detail=f"Unsupported stage kind={kind!r}"
            )
        row.kind = kind
    if position is not None:
        row.position = int(position)
    if is_active is not None:
        row.is_active = bool(is_active)
    db.flush()
    return row


def reorder_org_stages(
    db: Session, organization_id: int, ordered_ids: list[int]
) -> list[PipelineStage]:
    """Set stage positions to the given id order (0-based). Ids must all belong
    to the org."""
    rows = {
        row.id: row
        for row in db.query(PipelineStage).filter(
            PipelineStage.organization_id == organization_id,
            PipelineStage.id.in_(ordered_ids or []),
        )
    }
    if len(rows) != len(set(ordered_ids or [])):
        raise HTTPException(
            status_code=422, detail="ordered_ids contains unknown stage ids"
        )
    for index, stage_id in enumerate(ordered_ids):
        rows[stage_id].position = index
    db.flush()
    return list_org_stages(db, organization_id, include_inactive=True)
