"""Tests for the async + cached CV scoring orchestrator.

These exercise the orchestration layer end-to-end with the Claude call
monkeypatched: cache hit short-circuits Claude, cache miss persists to the
cache, validation errors mark the job ``error``, and ``mark_role_scores_stale``
adds stale rows for already-scored apps.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Candidate,
    CandidateApplication,
    CvScoreCache,
    CvScoreJob,
    Organization,
    Role,
    SCORE_JOB_DONE,
    SCORE_JOB_ERROR,
)
from app.platform.config import settings
from app.platform.database import Base
from app.services import cv_score_orchestrator
from app.services.cv_score_orchestrator import (
    compute_cache_key,
    enqueue_score,
    mark_role_scores_stale,
)
from app.services.fit_matching_service import CvMatchValidationError
from app.services.role_criteria_service import sync_recruiter_criteria


@pytest.fixture(autouse=True)
def _force_inline_celery(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "MVP_DISABLE_CELERY", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key-not-used")


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    org = Organization(name="Acme", slug="acme")
    db.add(org)
    db.commit()
    db.refresh(org)
    role = Role(
        organization_id=org.id,
        name="Backend Engineer",
        job_spec_text="Description\nA backend role.\nRequirements\n- 5+ years Python\n",
        additional_requirements="- 5+ years Python\n- AWS",
    )
    db.add(role)
    db.flush()
    sync_recruiter_criteria(db, role)
    db.commit()
    db.refresh(role)
    candidate = Candidate(organization_id=org.id, email="cand@example.com")
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        cv_text="Senior backend engineer with 6 years of Python and AWS experience.",
    )
    db.add(app)
    db.commit()
    db.refresh(app)
    yield db, org, role, app
    db.close()


def _stub_v4_result(score: float = 78.5) -> dict:
    return {
        "cv_job_match_score": score,
        "match_details": {
            "scoring_version": "cv_match_v4",
            "model_overall_score_100": score,
            "final_score_100": score,
            "recommendation": "yes",
            "summary": "stub",
            "matching_skills": ["Python"],
            "missing_skills": [],
            "experience_highlights": ["6 years Python"],
            "concerns": [],
            "requirements_assessment": [],
            "requirements_coverage": {"met": 1, "partially_met": 0, "missing": 0, "unknown": 0},
            "must_have_blocked": False,
            "score_scale": "0-100",
        },
    }


def test_enqueue_runs_inline_and_creates_done_job(monkeypatch, session) -> None:
    db, _org, _role, app = session
    call_count = {"n": 0}

    def fake_v4(**kwargs):
        call_count["n"] += 1
        return _stub_v4_result(82.0)

    monkeypatch.setattr(cv_score_orchestrator, "calculate_cv_job_match_v4_sync", fake_v4)

    job = enqueue_score(db, app)
    db.commit()
    db.refresh(app)

    assert job is not None
    assert job.status == SCORE_JOB_DONE
    assert job.cache_hit == "miss"
    assert app.cv_match_score == 82.0
    assert app.cv_match_details["scoring_version"] == "cv_match_v4"
    assert call_count["n"] == 1
    cached = db.query(CvScoreCache).first()
    assert cached is not None
    assert cached.score_100 == 82.0


def test_second_enqueue_with_same_inputs_hits_cache_no_claude_call(monkeypatch, session) -> None:
    db, _org, _role, app = session
    call_count = {"n": 0}

    def fake_v4(**kwargs):
        call_count["n"] += 1
        return _stub_v4_result(70.0)

    monkeypatch.setattr(cv_score_orchestrator, "calculate_cv_job_match_v4_sync", fake_v4)

    enqueue_score(db, app)
    db.commit()
    assert call_count["n"] == 1

    # Force a re-enqueue (e.g. recruiter clicked "rescore").
    second_job = enqueue_score(db, app, force=True)
    db.commit()

    assert second_job is not None
    assert second_job.status == SCORE_JOB_DONE
    assert second_job.cache_hit == "hit"
    assert call_count["n"] == 1, "cache hit must not trigger a second Claude call"

    cached = db.query(CvScoreCache).first()
    assert cached.hit_count == 2


def test_validation_error_marks_job_error(monkeypatch, session) -> None:
    db, _org, _role, app = session

    def fake_v4(**kwargs):
        raise CvMatchValidationError("missing field foo")

    monkeypatch.setattr(cv_score_orchestrator, "calculate_cv_job_match_v4_sync", fake_v4)

    job = enqueue_score(db, app)
    db.commit()
    db.refresh(app)

    assert job is not None
    assert job.status == SCORE_JOB_ERROR
    assert "missing field foo" in (job.error_message or "")
    assert app.cv_match_score is None
    assert app.cv_match_details["error"].startswith("CV match validation failed")
    # Cache must NOT be populated on error.
    assert db.query(CvScoreCache).count() == 0


def test_existing_pending_job_is_reused_when_not_forced(monkeypatch, session) -> None:
    db, _org, _role, app = session
    monkeypatch.setattr(cv_score_orchestrator, "calculate_cv_job_match_v4_sync", lambda **kw: _stub_v4_result(60.0))

    first = enqueue_score(db, app)
    db.commit()
    assert first is not None
    assert first.status == SCORE_JOB_DONE

    # Done jobs don't block re-enqueue, but pending jobs do — simulate one.
    pending = CvScoreJob(application_id=app.id, role_id=app.role_id, status="pending")
    db.add(pending)
    db.flush()

    reused = enqueue_score(db, app)
    assert reused is not None
    assert reused.id == pending.id, "pending job must be returned, not duplicated"


def test_force_creates_new_job_even_when_pending_exists(monkeypatch, session) -> None:
    db, _org, _role, app = session
    monkeypatch.setattr(cv_score_orchestrator, "calculate_cv_job_match_v4_sync", lambda **kw: _stub_v4_result(60.0))

    pending = CvScoreJob(application_id=app.id, role_id=app.role_id, status="pending")
    db.add(pending)
    db.flush()

    forced = enqueue_score(db, app, force=True)
    assert forced is not None
    assert forced.id != pending.id


def test_returns_none_when_cv_or_spec_missing(session) -> None:
    db, _org, _role, app = session
    app.cv_text = None
    db.flush()
    assert enqueue_score(db, app) is None


def test_cache_key_changes_when_criteria_change(session) -> None:
    db, _org, role, _app = session

    base_kwargs = dict(
        cv_text="cv",
        spec_description="d",
        spec_requirements="r",
        prompt_version="cv_match_v4",
        model="claude-x",
    )
    crit_a = [{"id": 1, "text": "Python", "must_have": True}]
    crit_b = [{"id": 1, "text": "Python", "must_have": False}]
    crit_c = [{"id": 1, "text": "TypeScript", "must_have": True}]

    key_a = compute_cache_key(criteria=crit_a, **base_kwargs)
    key_b = compute_cache_key(criteria=crit_b, **base_kwargs)
    key_c = compute_cache_key(criteria=crit_c, **base_kwargs)

    assert key_a != key_b, "must_have flag must affect cache key"
    assert key_a != key_c, "criterion text must affect cache key"


def test_mark_role_scores_stale_adds_stale_rows_for_scored_apps(session) -> None:
    db, _org, role, app = session
    app.cv_match_score = 75.0
    db.flush()

    marked = mark_role_scores_stale(db, role.id)
    db.commit()

    assert marked == 1
    stale_rows = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == app.id, CvScoreJob.status == "stale")
        .all()
    )
    assert len(stale_rows) == 1


def test_mark_role_scores_stale_skips_unscored_apps(session) -> None:
    db, _org, role, app = session
    # No cv_match_score → app is unscored → no stale row.
    marked = mark_role_scores_stale(db, role.id)
    db.commit()
    assert marked == 0
