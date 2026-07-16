"""Score-derivation parity between analytics dashboards and client report PDFs.

Regression guard for the 2026-06-25 de-bloat drift: ``_score_100`` /
``_extract_category_scores`` used to live per-file in
``analytics_routes`` and ``candidate_feedback_engine`` and had drifted, so the
same assessment could surface one overall score on the analytics dashboard and a
different one on the client report PDF. They are now a single source of truth in
``components/scoring/assessment_metrics`` (``taali``-first, non-inflating clamp).

These tests assert the Python-backed analytics/report call sites cannot disagree.
The aggregate analytics endpoint now derives the same policy as a SQL expression;
its behavioral parity is covered in ``test_collection_query_bounds``.
"""

from datetime import datetime, timezone

from app.components.scoring import assessment_metrics
from app.domains.assessments_runtime import analytics_routes
from app.models.assessment import Assessment, AssessmentStatus
from app.services import candidate_feedback_engine
from app.services.candidate_feedback_engine import (
    _assessment_score_components_100,
    build_client_assessment_report_payload,
)
from tests.conftest import TestingSessionLocal, setup_full_environment


def _assessment(**columns) -> Assessment:
    """A bare (unpersisted) Assessment with just the score columns set."""
    asmt = Assessment()
    for key, value in columns.items():
        setattr(asmt, key, value)
    return asmt


# ---------------------------------------------------------------------------
# Single-source-of-truth: the two surfaces share the *same* function objects.
# ---------------------------------------------------------------------------


def test_score_helpers_are_a_single_source_of_truth():
    # Same function object on both call sites — not copies that can re-drift.
    assert analytics_routes._score_100 is assessment_metrics.score_100
    assert candidate_feedback_engine._score_100 is assessment_metrics.score_100
    assert (
        analytics_routes._extract_category_scores
        is candidate_feedback_engine._extract_category_scores
        is assessment_metrics.extract_category_scores
    )


# ---------------------------------------------------------------------------
# Headline agreement: analytics overall score == client report "TAALI score".
# ---------------------------------------------------------------------------


def test_analytics_overall_matches_client_report_headline():
    # The divergence case that motivated the unification: ``taali_score`` (the
    # blended headline) differs from ``final_score`` / ``assessment_score``.
    # The OLD analytics helper returned taali (78); the OLD feedback helper
    # returned final (74) for the benchmark — so the dashboard and the PDF's
    # own "Top N%" were computed off different numbers. They must now agree.
    asmt = _assessment(
        score=7.4,
        final_score=74.0,
        assessment_score=74.0,
        taali_score=78.0,
        cv_job_match_score=84.0,
        cv_job_match_details={"requirements_match_score_100": 80.0},
        score_breakdown={
            "score_components": {
                "assessment_score": 74.0,
                "taali_score": 78.0,
                "role_fit_score": 82.0,
            },
            "category_scores": {"task_completion": 7.8, "role_fit": 8.0},
        },
    )

    analytics_overall = analytics_routes._score_100(asmt)
    report_headline = _assessment_score_components_100(asmt)["taali_score"]

    assert analytics_overall == report_headline == 78.0
    # And the 0-10 view stays consistent with the 0-100 headline.
    assert assessment_metrics.score_10(asmt) == 7.8


def test_overall_score_falls_back_consistently_when_taali_absent():
    # No taali/assessment columns: both surfaces fall through to final_score,
    # then to the legacy 0-10 ``score`` column (rescaled ×10).
    final_only = _assessment(score=7.4, final_score=74.0)
    assert analytics_routes._score_100(final_only) == 74.0

    legacy_only = _assessment(score=7.0)
    assert analytics_routes._score_100(legacy_only) == 70.0
    # Same object drives the feedback engine's benchmark distribution.
    assert candidate_feedback_engine._score_100(legacy_only) == 70.0


def test_overall_score_is_non_inflating_and_clamped():
    # The banned ``<=10 → ×10`` heuristic must NOT fire: a genuinely-weak
    # 0-100 taali_score of 4 stays 4, it is not inflated to 40.
    weak = _assessment(taali_score=4.0)
    assert analytics_routes._score_100(weak) == 4.0
    # Out-of-range values are clamped, negatives treated as missing.
    assert analytics_routes._score_100(_assessment(taali_score=105.0)) == 100.0
    assert analytics_routes._score_100(_assessment(taali_score=-3.0)) is None


# ---------------------------------------------------------------------------
# Category-score parity: identical canonicalization (alias map + 2dp rounding).
# ---------------------------------------------------------------------------


def test_category_scores_match_across_modules():
    asmt = _assessment(
        score_breakdown={"category_scores": {"task_completion": 7.844, "cv_match": 6.0}},
    )
    analytics_out = analytics_routes._extract_category_scores(asmt)
    feedback_out = candidate_feedback_engine._extract_category_scores(asmt)

    assert analytics_out == feedback_out
    # 2dp rounding (the canonical, non-inflating behaviour) ...
    assert analytics_out["task_completion"] == 7.84
    # ... and the legacy ``cv_match`` alias canonicalizes to ``role_fit``.
    assert analytics_out["role_fit"] == 6.0


# ---------------------------------------------------------------------------
# End-to-end: the real client report payload agrees with the analytics helper.
# ---------------------------------------------------------------------------


def test_client_report_payload_and_analytics_agree_on_overall_score(client):
    env = setup_full_environment(client)
    assessment_id = env["assessment"]["id"]

    with TestingSessionLocal() as db:
        assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
        assert assessment is not None
        assessment.status = AssessmentStatus.COMPLETED
        assessment.score = 7.4
        assessment.assessment_score = 74.0
        assessment.final_score = 74.0
        assessment.taali_score = 78.0
        assessment.completed_at = datetime.now(timezone.utc)
        assessment.cv_job_match_score = 84.0
        assessment.cv_job_match_details = {
            "summary": "Strong platform and data engineering background.",
            "requirements_match_score_100": 80.0,
            "requirements_coverage": {"total": 2, "met": 1, "partially_met": 1, "missing": 0},
            "matching_skills": ["Python", "Airflow"],
            "requirements_assessment": [
                {"requirement": "Data pipelines", "status": "met", "evidence": "Led batch systems."},
                {"requirement": "Glue depth", "status": "partially_met", "evidence": "Adjacent AWS only."},
            ],
        }
        assessment.score_breakdown = {
            "score_formula_version": "taali_v3_role_fit_blended",
            "category_scores": {"task_completion": 7.8, "role_fit": 8.0},
            "score_components": {
                "assessment_score": 74.0,
                "taali_score": 78.0,
                "role_fit_score": 82.0,
            },
        }
        db.commit()
        db.refresh(assessment)

        payload = build_client_assessment_report_payload(
            db, assessment, organization_name="Acme Inc"
        )
        report_headline = payload["scores"]["taali_score"]
        analytics_overall = analytics_routes._score_100(assessment)

    # What the recruiter sees as the headline on the client report PDF is the
    # same number the analytics dashboard derives for this assessment.
    assert report_headline == analytics_overall == 78.0
