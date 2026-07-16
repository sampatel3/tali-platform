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


def test_mark_role_scores_stale_keeps_score_values_visible(db):
    """Invalidation must keep the existing score values populated so
    the UI can show "Strong match — 87 (stale)" until the rescore lands.
    Blanking the score causes the recruiter to see hundreds of orphan
    candidates after a single criterion edit, which destroys trust."""
    _, role, _, app = _seed_scored_app(db)
    assert app.pre_screen_score_100 == 75.0
    assert app.cv_match_score == 82.0

    marked = mark_role_scores_stale(db, role.id)
    assert marked == 1

    # Score values must still be visible.
    assert app.pre_screen_score_100 == 75.0
    assert app.cv_match_score == 82.0
    assert app.requirements_fit_score_100 == 75.0
    assert app.cv_match_details == {"summary": "Looks good"}
    assert app.pre_screen_recommendation == "Proceed to screening"
    assert app.rank_score == 75.0
    # But pre_screen_run_at IS cleared so the next orchestrator pass
    # re-runs Stage-1 against the updated criteria.
    assert app.pre_screen_run_at is None

    # And a stale CvScoreJob row exists so the listing endpoint
    # surfaces score_status="stale" → frontend renders the badge.
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


def test_invalidation_supersedes_pending_agent_decisions(db):
    """Pending agent decisions reference an underlying score. When that
    score is invalidated, the decision is stale — the agent will likely
    flip its mind once the rescore lands. Leaving the decision in
    'pending' means the recruiter could approve a recommendation the
    agent itself would reverse. Supersede on invalidation."""
    from datetime import datetime, timezone
    from app.models.agent_decision import AgentDecision

    _, role, _, app = _seed_scored_app(db)
    # Seed a pending decision (and an already-resolved one to prove the
    # supersede only touches pending rows).
    pending = AgentDecision(
        organization_id=app.organization_id,
        role_id=role.id,
        application_id=app.id,
        decision_type="advance_to_interview",
        recommendation="Strong match — advance to interview",
        status="pending",
        reasoning="Score 87, comfortably above threshold",
        model_version="v3",
        prompt_version="v3",
        idempotency_key=f"pending-{app.id}",
    )
    resolved = AgentDecision(
        organization_id=app.organization_id,
        role_id=role.id,
        application_id=app.id,
        decision_type="reject",
        recommendation="Reject",
        status="approved",
        reasoning="Earlier attempt — recruiter approved",
        model_version="v3",
        prompt_version="v3",
        resolved_at=datetime.now(timezone.utc),
        idempotency_key=f"approved-{app.id}",
    )
    db.add(pending)
    db.add(resolved)
    db.flush()

    mark_role_scores_stale(db, role.id, reason="salary_cap_lowered")

    db.refresh(pending)
    db.refresh(resolved)
    # Pending → discarded with audit trail.
    assert pending.status == "discarded"
    assert pending.resolved_at is not None
    assert "salary_cap_lowered" in (pending.resolution_note or "")
    # Already-resolved decisions are NOT touched (audit history preserved).
    assert resolved.status == "approved"


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

    from app.models.candidate_application import CandidateApplication as CA
    from app.models.cv_score_job import CvScoreJob
    fresh_a = db.query(CA).filter(CA.id == app_a.id).one()
    fresh_b = db.query(CA).filter(CA.id == app_b.id).one()
    # app_a's scores stay VISIBLE (kept as stale numbers, badge from
    # CvScoreJob status). pre_screen_run_at is cleared so Stage-1 reruns.
    assert fresh_a.pre_screen_score_100 == 75.0
    assert fresh_a.cv_match_score == 82.0
    assert fresh_a.pre_screen_run_at is None
    stale_jobs_a = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == fresh_a.id, CvScoreJob.status == "stale")
        .count()
    )
    assert stale_jobs_a == 1
    # Other app's scores untouched AND no stale job row added for it.
    assert fresh_b.pre_screen_score_100 == 75.0
    assert fresh_b.cv_match_score == 82.0
    stale_jobs_b = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == fresh_b.id, CvScoreJob.status == "stale")
        .count()
    )
    assert stale_jobs_b == 0


def test_rank_score_preserved_on_invalidation(db):
    """``rank_score`` powers the directory ordering. The old behavior
    fell it back to workable_score on invalidation; the new "honest
    stale" UX keeps the agent's rank visible (with a stale badge from
    the CvScoreJob row) so the candidate stays in their familiar
    position while the rescore lands."""
    _, role, _, app = _seed_scored_app(db)
    app.workable_score = 60.0
    db.flush()

    mark_role_scores_stale(db, role.id)
    # rank_score stays at the prior agent score (75) — not 60.
    assert app.rank_score == 75.0


def test_invalidation_resets_pre_screen_run_at_so_next_pass_reruns_stage1(db):
    """Codex P1 (post-merge): if invalidation leaves ``pre_screen_run_at``
    populated, ``application_needs_pre_screen`` returns False on the
    next orchestrator pass — meaning Stage-1 is skipped and the
    orchestrator falls through to v3 cv_match scoring without ever
    re-evaluating the updated must/constraint criteria. Invalidation
    must clear the timestamp."""
    from datetime import datetime, timezone

    from app.services.pre_screening_service import application_needs_pre_screen

    _, role, _, app = _seed_scored_app(db)
    # Seed a "previously screened" timestamp.
    app.pre_screen_run_at = datetime.now(timezone.utc)
    db.flush()
    assert application_needs_pre_screen(app) is False

    mark_role_scores_stale(db, role.id)

    # After invalidation, the next orchestrator pass MUST re-run Stage-1.
    assert app.pre_screen_run_at is None
    assert application_needs_pre_screen(app) is True


def test_invalidation_preserves_aggregate_score_caches(db):
    """Under the new "honest stale" UX the aggregate cache columns
    (taali_score_cache_100, assessment_score_cache_100,
    role_fit_score_cache_100) stay populated so list/detail endpoints
    keep rendering the stale number alongside a stale badge — instead
    of orphaning hundreds of candidates with no number at all whenever
    a recruiter edits a must-have criterion."""
    _, role, _, app = _seed_scored_app(db)
    app.taali_score_cache_100 = 80.0
    app.assessment_score_cache_100 = 70.0
    app.role_fit_score_cache_100 = 82.0
    app.score_mode_cache = "v3"
    db.flush()

    mark_role_scores_stale(db, role.id)

    # Cached aggregates stay visible. The UI uses the CvScoreJob
    # status="stale" row (added by invalidation) to render the badge.
    assert app.taali_score_cache_100 == 80.0
    assert app.assessment_score_cache_100 == 70.0
    assert app.role_fit_score_cache_100 == 82.0
    assert app.score_mode_cache == "v3"


def test_sweeper_skips_apps_whose_latest_job_is_no_longer_stale(db):
    """Codex P1 #4: ``CvScoreJob`` rows are append-only. A successful
    rescore adds a fresh ``pending``/``done`` row but doesn't update
    the old ``stale`` row. The sweeper must filter to apps whose
    LATEST job is stale, not "any historical stale row exists",
    otherwise it re-enqueues already-fixed apps every 30 min.
    """
    from datetime import datetime, timedelta, timezone

    from app.models.cv_score_job import CvScoreJob
    from sqlalchemy import func

    from app.models.candidate import Candidate
    from app.models.candidate_application import CandidateApplication

    _, role, _, app_a = _seed_scored_app(db)
    # Second app on the SAME role to avoid the make_full_application
    # slug-uniqueness collision.
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
        cv_text="Another engineer CV.",
        pre_screen_score_100=75.0,
        cv_match_score=82.0,
    )
    db.add(app_b)
    db.flush()

    now = datetime.now(timezone.utc)

    # app_a: stale → succeeded since. Latest job = done.
    db.add(CvScoreJob(application_id=app_a.id, role_id=role.id, status="stale", queued_at=now - timedelta(hours=1)))
    db.add(CvScoreJob(application_id=app_a.id, role_id=role.id, status="done", queued_at=now))
    # app_b: stale and not yet picked up.
    db.add(CvScoreJob(application_id=app_b.id, role_id=role.id, status="stale", queued_at=now))
    db.flush()

    # Mirror the sweeper's window query inline so the test is hermetic.
    latest_subq = (
        db.query(
            CvScoreJob.application_id,
            func.max(CvScoreJob.queued_at).label("max_queued"),
        )
        .group_by(CvScoreJob.application_id)
        .subquery()
    )
    latest_stale = (
        db.query(CvScoreJob)
        .join(
            latest_subq,
            (CvScoreJob.application_id == latest_subq.c.application_id)
            & (CvScoreJob.queued_at == latest_subq.c.max_queued),
        )
        .filter(CvScoreJob.status == "stale")
        .all()
    )
    app_ids = {j.application_id for j in latest_stale}
    assert app_b.id in app_ids
    assert app_a.id not in app_ids, (
        "app_a was rescored since the stale row was added; sweeper must not re-enqueue it"
    )


def test_mark_application_scores_stale_no_op_when_no_prior_stale_job(db):
    """If the same app was already marked stale, re-marking returns
    False (idempotent)."""
    _, _, _, app = _seed_scored_app(db)
    assert mark_application_scores_stale(db, app.id) is True
    # Already stale → second call returns False.
    assert mark_application_scores_stale(db, app.id) is False
