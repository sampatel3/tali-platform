"""Recommended threshold algorithm covers all three signal tiers."""

from __future__ import annotations

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.services.auto_threshold_service import compute_recommended_threshold


def _make_world(db):
    org = Organization(name="ATR Org", slug=f"atr-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Backend",
        source="manual",
    )
    db.add(role)
    db.flush()
    return org, role


def _add_app(db, *, org, role, pre, outcome="open", stage="applied"):
    candidate = Candidate(
        organization_id=org.id,
        email=f"c{id(role)}-{pre}@x.test",
        full_name=f"C {pre}",
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage=stage,
        pipeline_stage_source="recruiter",
        application_outcome=outcome,
        pre_screen_score_100=pre,
        cv_text="x",
    )
    db.add(app)
    db.flush()
    return app


def test_fallback_when_no_scored_candidates(db):
    org, role = _make_world(db)
    rec = compute_recommended_threshold(db, role=role)
    assert rec.source == "fallback"
    assert rec.value == 50
    assert rec.sample_size == 0


def test_distribution_tier_picks_30th_percentile(db):
    org, role = _make_world(db)
    # Scores 10..100 step 10 → 30th percentile is around 37.
    for score in range(10, 101, 10):
        _add_app(db, org=org, role=role, pre=float(score))
    rec = compute_recommended_threshold(db, role=role)
    assert rec.source == "distribution"
    assert rec.sample_size == 10
    # Clamped to [30, 75]; 30th percentile of 10..100 with step 10 is ~37.
    assert 30 <= rec.value <= 50


def test_distribution_clamps_to_floor(db):
    org, role = _make_world(db)
    # All scores extremely low. 30th percentile would be far below the
    # 30 floor — clamp must kick in.
    for score in (5, 6, 7, 8, 9, 10):
        _add_app(db, org=org, role=role, pre=float(score))
    rec = compute_recommended_threshold(db, role=role)
    assert rec.value == 30  # floor


def test_labelled_tier_anchors_on_advanced_median(db):
    org, role = _make_world(db)
    # 5 advanced candidates with scores 60..80.
    for score, stage in (
        (60.0, "invited"),
        (65.0, "in_assessment"),
        (70.0, "review"),
        (75.0, "technical_interview"),
        (80.0, "technical_interview"),
    ):
        _add_app(db, org=org, role=role, pre=score, stage=stage)
    # Plus 20 applied/below candidates that shouldn't affect the labelled tier.
    for score in range(5, 25):
        _add_app(db, org=org, role=role, pre=float(score))
    rec = compute_recommended_threshold(db, role=role)
    assert rec.source == "labelled"
    assert rec.sample_size == 5
    # median(60,65,70,75,80) = 70; pstdev ≈ 7.07; 70 - 7 = 63.
    assert 55 <= rec.value <= 70
