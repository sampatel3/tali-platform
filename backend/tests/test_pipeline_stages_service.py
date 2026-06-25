"""P0: per-org pipeline-stage resolution (pipeline_stages_service)."""
from app.domains.assessments_runtime.pipeline_stages_service import (
    ensure_org_stages_seeded,
    resolve_org_stages,
    resolve_stage_slugs,
    stage_kind_for,
)
from app.models import Organization, PipelineStage

CANONICAL_SLUGS = ("applied", "invited", "in_assessment", "review", "advanced")


def _make_org(db, slug="acme"):
    org = Organization(name=slug.title(), slug=slug)
    db.add(org)
    db.flush()
    return org


def test_resolve_falls_back_to_canonical_when_unseeded(db):
    org = _make_org(db)
    stages = resolve_org_stages(db, org.id)
    assert tuple(s.slug for s in stages) == CANONICAL_SLUGS
    assert [s.position for s in stages] == [0, 1, 2, 3, 4]
    assert [s.kind for s in stages] == [
        "applied",
        "assessment",
        "assessment",
        "review",
        "interview",
    ]


def test_seed_is_idempotent(db):
    org = _make_org(db)
    assert ensure_org_stages_seeded(db, org.id) == 5
    assert ensure_org_stages_seeded(db, org.id) == 0
    assert (
        db.query(PipelineStage).filter_by(organization_id=org.id).count() == 5
    )


def test_resolve_reads_table_after_seed(db):
    org = _make_org(db)
    ensure_org_stages_seeded(db, org.id)
    assert resolve_stage_slugs(db, org.id) == CANONICAL_SLUGS


def test_custom_stage_ordering_and_active_filter(db):
    org = _make_org(db)
    ensure_org_stages_seeded(db, org.id)
    db.add(
        PipelineStage(
            organization_id=org.id,
            slug="hired",
            name="Hired",
            kind="hired",
            position=5,
            is_default=False,
            is_active=True,
        )
    )
    db.flush()
    assert resolve_stage_slugs(db, org.id) == (*CANONICAL_SLUGS, "hired")
    # Deactivating a stage removes it from resolution (without deleting history).
    review = (
        db.query(PipelineStage)
        .filter_by(organization_id=org.id, slug="review")
        .one()
    )
    review.is_active = False
    db.flush()
    assert "review" not in resolve_stage_slugs(db, org.id)


def test_stage_kind_for(db):
    org = _make_org(db)
    ensure_org_stages_seeded(db, org.id)
    assert stage_kind_for(db, org.id, "review") == "review"
    assert stage_kind_for(db, org.id, "advanced") == "interview"
    assert stage_kind_for(db, org.id, "applied") == "applied"
    assert stage_kind_for(db, org.id, "nonexistent") is None
    assert stage_kind_for(db, org.id, None) is None


def test_two_orgs_are_isolated(db):
    a = _make_org(db, "a")
    b = _make_org(db, "b")
    ensure_org_stages_seeded(db, a.id)
    assert resolve_stage_slugs(db, a.id) == CANONICAL_SLUGS  # from table
    assert resolve_stage_slugs(db, b.id) == CANONICAL_SLUGS  # canonical fallback
    db.add(
        PipelineStage(
            organization_id=a.id,
            slug="x",
            name="X",
            kind="review",
            position=9,
            is_default=False,
            is_active=True,
        )
    )
    db.flush()
    assert "x" in resolve_stage_slugs(db, a.id)
    assert "x" not in resolve_stage_slugs(db, b.id)
