"""Score invalidation semantics for the agent: when inputs change,
scores go blank until the agent rescores.

User requirement (verbatim): "we need to be VERY careful of scoring
people when there are issues and then giving decisions - if the agent
couldn't score them, then the score should be returned blank until
[rescored]. We also agreed when there are changes (candidate updates
e.g. new cv, new comments from recruites etc, or even recuirter intent
updates etc.) that the agent should pick these up aagain and assess
whether a rescore should be done. This asssessment should revert
scores back to blank if the assessment is that they need to be
rescored - if recruiter intent is changed, i would expect that all
candidates need rescoring. if candidate updates, then only those
candidates should be assessed/ go blank score."
"""

from __future__ import annotations

from app.models.cv_score_job import CvScoreJob
from app.services.cv_score_orchestrator import (
    mark_application_scores_stale,
    mark_role_scores_stale,
)

from tests.sub_agents.conftest import make_full_application


def _seed_scored_app(db):
    """Standard fixture: org + role + candidate + application with both
    pre-screen and cv_match scores populated."""
    org, role, candidate, app = make_full_application(db)
    role.job_spec_text = "Hiring a senior engineer."
    app.pre_screen_score_100 = 75.0
    app.requirements_fit_score_100 = 75.0
    app.cv_match_score = 82.0
    app.cv_match_details = {"summary": "Looks good"}
    app.pre_screen_recommendation = "Proceed to screening"
    app.rank_score = 75.0
    db.flush()
    return org, role, candidate, app


def test_mark_role_scores_stale_actually_nulls_score_fields(db):
    """The headline behavior: invalidation NULLs the visible score
    fields, not just adds a tracking row. UI then shows 'needs rescore'
    instead of a stale numeric."""
    _, role, _, app = _seed_scored_app(db)
    assert app.pre_screen_score_100 == 75.0
    assert app.cv_match_score == 82.0

    marked = mark_role_scores_stale(db, role.id)
    assert marked == 1

    # All score-shaped fields wiped.
    assert app.pre_screen_score_100 is None
    assert app.requirements_fit_score_100 is None
    assert app.cv_match_score is None
    assert app.cv_match_details is None
    assert app.cv_match_scored_at is None
    assert app.pre_screen_recommendation is None

    # And a stale CvScoreJob row exists for the worker to pick up.
    stale = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == app.id, CvScoreJob.status == "stale")
        .all()
    )
    assert len(stale) == 1


def test_mark_role_scores_stale_skips_apps_never_scored(db):
    """Apps that were never scored stay untouched — invalidation only
    affects apps the agent has already produced an opinion on."""
    org, role, candidate, app = make_full_application(db)
    role.job_spec_text = "Hiring."
    # No pre_screen_score, no cv_match_score.
    db.flush()

    marked = mark_role_scores_stale(db, role.id)
    assert marked == 0
    stale = db.query(CvScoreJob).filter(CvScoreJob.application_id == app.id).all()
    assert stale == []


def test_mark_role_scores_stale_is_idempotent(db):
    """Re-running invalidation doesn't pile up duplicate stale rows."""
    _, role, _, app = _seed_scored_app(db)
    mark_role_scores_stale(db, role.id)
    # First run created one stale job. Second run finds it already
    # there and skips.
    second = mark_role_scores_stale(db, role.id)
    assert second == 0
    stale = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == app.id, CvScoreJob.status == "stale")
        .all()
    )
    assert len(stale) == 1


def test_mark_application_scores_stale_scopes_to_single_app(db):
    """Per-candidate invalidation (used by CV upload + Workable digest
    changes) only touches that one application."""
    from app.models.candidate import Candidate
    from app.models.candidate_application import CandidateApplication

    _, role, _, app_a = _seed_scored_app(db)

    # A second application on the same role — must remain untouched.
    # Reuse role/org to avoid the slug-uniqueness collision in the test
    # fixture's slug=f"sa-org-{id(db)}" recipe.
    candidate_b = Candidate(
        organization_id=app_a.organization_id, email="b@x.test", full_name="B"
    )
    db.add(candidate_b)
    db.flush()
    app_b = CandidateApplication(
        organization_id=app_a.organization_id,
        candidate_id=candidate_b.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        cv_text="Another senior engineer.",
        pre_screen_score_100=75.0,
        cv_match_score=82.0,
    )
    db.add(app_b)
    db.flush()

    ok = mark_application_scores_stale(db, app_a.id)
    assert ok is True
    db.flush()

    # Re-read explicitly via fresh queries — the test's session is the
    # same one ``mark_application_scores_stale`` mutated, but force a
    # round-trip-shaped check to mirror what a downstream worker would
    # see when it pulls the app.
    from app.models.candidate_application import CandidateApplication as CA
    fresh_a = db.query(CA).filter(CA.id == app_a.id).one()
    fresh_b = db.query(CA).filter(CA.id == app_b.id).one()
    assert fresh_a.pre_screen_score_100 is None
    assert fresh_a.cv_match_score is None
    # Other app's scores untouched.
    assert fresh_b.pre_screen_score_100 == 75.0
    assert fresh_b.cv_match_score == 82.0


def test_rank_score_falls_back_to_workable_score_on_invalidation(db):
    """During the rescore window the directory still needs ordering;
    rank_score falls back to workable_score (Workable's raw rating) so
    the candidate doesn't drop to the bottom of the list."""
    _, role, _, app = _seed_scored_app(db)
    app.workable_score = 60.0
    db.flush()

    mark_role_scores_stale(db, role.id)
    assert app.rank_score == 60.0


def test_mark_application_scores_stale_no_op_when_no_prior_stale_job(db):
    """If the same app was already marked stale, re-marking returns
    False (idempotent)."""
    _, _, _, app = _seed_scored_app(db)
    assert mark_application_scores_stale(db, app.id) is True
    # Already stale → second call returns False.
    assert mark_application_scores_stale(db, app.id) is False
