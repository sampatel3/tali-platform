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
        (75.0, "advanced"),
        (80.0, "advanced"),
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


# ---------------------------------------------------------------------------
# Role-fit SEND threshold (dynamic send/advance bar)
# ---------------------------------------------------------------------------

from app.services.auto_threshold_service import (  # noqa: E402
    compute_role_fit_send_threshold,
    resolve_role_fit_threshold,
)


def _add_scored(db, *, org, role, cv, pre=70.0, workable_stage=None, outcome="open"):
    candidate = Candidate(
        organization_id=org.id,
        email=f"rf{id(role)}-{cv}-{id(db)}-{workable_stage}-{outcome}-{cv*7%101}@x.test",
        full_name=f"C {cv}",
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=candidate.id, role_id=role.id,
        status="applied", pipeline_stage="applied", pipeline_stage_source="recruiter",
        application_outcome=outcome, source="manual", cv_text="x",
        cv_match_score=cv, pre_screen_score_100=pre, workable_stage=workable_stage,
    )
    db.add(app)
    db.flush()
    return app


def _pct_above(pool, threshold):
    return 100.0 * sum(1 for s in pool if s >= threshold) / len(pool)


def test_send_threshold_fallback_no_scored(db):
    org, role = _make_world(db)
    rec = compute_role_fit_send_threshold(db, role=role)
    assert rec.source == "fallback"
    assert rec.value == 55


def test_send_threshold_targets_about_top_fifth(db):
    org, role = _make_world(db)
    # Graded pool so ~20% is achievable: 60 low, 20 mid, 20 high.
    for _ in range(60):
        _add_scored(db, org=org, role=role, cv=30.0)
    for _ in range(20):
        _add_scored(db, org=org, role=role, cv=50.0)
    for _ in range(20):
        _add_scored(db, org=org, role=role, cv=70.0)
    db.flush()
    rec = compute_role_fit_send_threshold(db, role=role)
    pool = [30.0] * 60 + [50.0] * 20 + [70.0] * 20
    pct = _pct_above(pool, rec.value)
    assert 12.0 <= pct <= 28.0, f"sent {pct}% (threshold {rec.value}, source {rec.source})"
    # 30 (the old floor) is far too low; the dynamic value must be well above it.
    assert rec.value > 30


def test_send_threshold_anchors_on_strong_stage_but_respects_volume(db):
    org, role = _make_world(db)
    # 70 weak applied, 20 mid; 10 reached Technical Interview (the strong signal).
    for _ in range(70):
        _add_scored(db, org=org, role=role, cv=25.0)
    for _ in range(20):
        _add_scored(db, org=org, role=role, cv=50.0)
    for _ in range(10):
        _add_scored(db, org=org, role=role, cv=65.0, workable_stage="Technical Interview")
    db.flush()
    rec = compute_role_fit_send_threshold(db, role=role)
    assert rec.source == "labelled_volume_balanced"
    pool = [25.0] * 70 + [50.0] * 20 + [65.0] * 10
    pct = _pct_above(pool, rec.value)
    assert 10.0 <= pct <= 28.0, f"sent {pct}% (threshold {rec.value})"


def test_resolve_role_fit_threshold_auto_uses_send_calibrator(db):
    org, role = _make_world(db)
    role.auto_reject_threshold_mode = "auto"
    role.score_threshold = 30  # stale manual value must be ignored in auto mode
    for _ in range(80):
        _add_scored(db, org=org, role=role, cv=30.0)
    for _ in range(20):
        _add_scored(db, org=org, role=role, cv=70.0)
    db.flush()
    eff = resolve_role_fit_threshold(db, role=role)
    assert eff is not None and eff > 30, f"auto mode must use the dynamic bar, got {eff}"
