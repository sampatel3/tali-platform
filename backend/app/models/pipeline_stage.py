from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..platform.database import Base

# Stage KIND — a coarse, ATS-generic category for each configurable stage.
# Used for grouping, stage automations and analytics; the fine-grained stage is
# the per-org row (slug/name/position). These categories mirror what every major
# ATS exposes (Workable / Greenhouse / Lever) so an imported pipeline maps
# cleanly onto Taali's own stages.
STAGE_KIND_SOURCED = "sourced"
STAGE_KIND_APPLIED = "applied"
STAGE_KIND_SCREENING = "screening"
STAGE_KIND_ASSESSMENT = "assessment"
STAGE_KIND_REVIEW = "review"
STAGE_KIND_INTERVIEW = "interview"
STAGE_KIND_OFFER = "offer"
STAGE_KIND_HIRED = "hired"
STAGE_KIND_REJECTED = "rejected"
STAGE_KINDS = (
    STAGE_KIND_SOURCED,
    STAGE_KIND_APPLIED,
    STAGE_KIND_SCREENING,
    STAGE_KIND_ASSESSMENT,
    STAGE_KIND_REVIEW,
    STAGE_KIND_INTERVIEW,
    STAGE_KIND_OFFER,
    STAGE_KIND_HIRED,
    STAGE_KIND_REJECTED,
)

# The canonical seed pipeline — EXACTLY mirrors the legacy hard-coded
# ``pipeline_service.PIPELINE_STAGES`` tuple (applied/invited/in_assessment/
# review/advanced) so that switching readers from the tuple to this table is
# behaviour-preserving. Each entry is ``(slug, name, kind, position)``. ``slug``
# is the value stored on ``candidate_applications.pipeline_stage`` (unchanged).
CANONICAL_SEED_STAGES = (
    ("applied", "Applied", STAGE_KIND_APPLIED, 0),
    ("invited", "Invited", STAGE_KIND_ASSESSMENT, 1),
    ("in_assessment", "In assessment", STAGE_KIND_ASSESSMENT, 2),
    ("review", "Review", STAGE_KIND_REVIEW, 3),
    ("advanced", "Advanced", STAGE_KIND_INTERVIEW, 4),
)

# Map a legacy stage slug -> its canonical kind. Used by the migration backfill
# and by code that needs the kind of a stored ``pipeline_stage`` before the
# per-org table is consulted.
LEGACY_STAGE_KIND = {slug: kind for slug, _name, kind, _pos in CANONICAL_SEED_STAGES}


class PipelineStage(Base):
    """A per-organization, configurable funnel stage.

    Replaces the hard-coded ``pipeline_service.PIPELINE_STAGES`` tuple: each org
    owns an ordered list of stages. ``slug`` is the stable key stored on
    ``candidate_applications.pipeline_stage`` (unchanged), ``kind`` is the coarse
    ATS category (``STAGE_KINDS``) used for automation/analytics and for mapping
    imported Workable/Greenhouse pipelines, and ``position`` orders the funnel.

    Seeded with ``CANONICAL_SEED_STAGES`` (identical to the legacy tuple) so the
    switch-over is behaviour-preserving.

    P0 EXPAND STEP: this table is additive and currently INERT — nothing reads it
    yet. ``pipeline_service`` is migrated onto it in the follow-up (migrate step),
    behind a flag, before the legacy tuple is removed (contract step).
    """

    __tablename__ = "pipeline_stages"
    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_pipeline_stage_org_slug"),
        Index("ix_pipeline_stages_org_position", "organization_id", "position"),
    )

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), index=True, nullable=False
    )
    slug = Column(String, nullable=False)
    name = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    position = Column(Integer, nullable=False, server_default="0")
    # True for stages in the canonical seeded set; False for stages a recruiter
    # adds. Lets us evolve the canonical set without clobbering custom stages.
    is_default = Column(Boolean, nullable=False, server_default="false")
    # Hide/retire a stage without breaking historical rows that reference it.
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    organization = relationship("Organization", back_populates="pipeline_stages")
