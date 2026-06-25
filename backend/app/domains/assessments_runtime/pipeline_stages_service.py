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

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ...models.pipeline_stage import (
    CANONICAL_SEED_STAGES,
    LEGACY_STAGE_KIND,
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
