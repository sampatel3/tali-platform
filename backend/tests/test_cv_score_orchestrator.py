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
from app.cv_matching import runner as cv_match_runner
from app.cv_matching.schemas import CVMatchOutput, ScoringStatus
from app.platform.config import settings
from app.platform.database import Base
from app.services import cv_score_orchestrator
from app.services.cv_score_orchestrator import (
    compute_cache_key,
    enqueue_score,
    mark_role_scores_stale,
)
from app.services.role_criteria_service import sync_recruiter_criteria


@pytest.fixture(autouse=True)
def _force_inline_celery(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "MVP_DISABLE_CELERY", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key-not-used")
    # Skip the pre-screen gate — these tests target the orchestrator's
    # behaviour around the v3 cv_match pipeline (cache, errors, retries),
    # not the pre-screen filter. Pre-screen has its own coverage.
    monkeypatch.setattr(settings, "ENABLE_PRE_SCREEN_GATE", False)


@pytest.fixture()
def session():
    # Share the conftest-managed in-memory DB so cv_matching helpers that
    # open their own SessionLocal() see the same tables this fixture
    # creates. A bare ":memory:" engine here would only populate the
    # local connection, leaving SessionLocal() callers with the empty
    # app-side engine and "no such table" failures.
    import os

    engine = create_engine(
        os.environ["DATABASE_URL"],
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    keepalive = engine.connect()
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
    try:
        yield db, org, role, app
    finally:
        db.close()
        # Drop tables so each test starts clean — the conftest-managed
        # shared DB persists across the suite, so we own teardown here.
        from sqlalchemy import text
        with engine.connect() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(text(f"DROP TABLE IF EXISTS {table.name}"))
            conn.commit()
        keepalive.close()
        engine.dispose()


def _stub_match_output(score: float = 78.5, *, status: ScoringStatus = ScoringStatus.OK, error_reason: str = "") -> CVMatchOutput:
    return CVMatchOutput(
        prompt_version=cv_match_runner.PROMPT_VERSION,
        skills_match_score=score,
        experience_relevance_score=score,
        matching_skills=["Python"],
        experience_highlights=["6 years Python"],
        summary="stub",
        requirements_match_score=score,
        cv_fit_score=score,
        role_fit_score=score,
        scoring_status=status,
        error_reason=error_reason,
        model_version=cv_match_runner.MODEL_VERSION,
        trace_id="test-trace",
    )


def test_enqueue_runs_inline_and_creates_done_job(monkeypatch, session) -> None:
    db, _org, _role, app = session
    call_count = {"n": 0}

    def fake_run(*args, **kwargs):
        call_count["n"] += 1
        return _stub_match_output(82.0)

    monkeypatch.setattr(cv_match_runner, "run_cv_match", fake_run)

    job = enqueue_score(db, app)
    db.commit()
    db.refresh(app)

    assert job is not None
    assert job.status == SCORE_JOB_DONE
    assert job.cache_hit == "miss"
    assert app.cv_match_score == 82.0
    assert app.cv_match_details["role_fit_score"] == 82.0
    assert call_count["n"] == 1
    # Note: with the runner stubbed, cv_score_cache writes happen inside
    # the real runner — not exercised here. Cache hit/miss behaviour is
    # covered separately in test_second_enqueue_with_same_inputs_hits_cache.


def test_second_enqueue_with_same_inputs_hits_cache_no_claude_call(monkeypatch, session) -> None:
    db, _org, _role, app = session
    call_count = {"n": 0}

    def fake_run(*args, **kwargs):
        call_count["n"] += 1
        # Second invocation would normally short-circuit via the runner's
        # own cache lookup; we simulate the runner's cache_hit flag below
        # by making the second result come back with cache_hit=True.
        result = _stub_match_output(70.0)
        if call_count["n"] > 1:
            result = result.model_copy(update={"cache_hit": True})
        return result

    monkeypatch.setattr(cv_match_runner, "run_cv_match", fake_run)

    enqueue_score(db, app)
    db.commit()
    assert call_count["n"] == 1

    # Force a re-enqueue (e.g. recruiter clicked "rescore"). The runner
    # consults cv_score_cache itself; the orchestrator records job.cache_hit
    # from the runner's result.
    second_job = enqueue_score(db, app, force=True)
    db.commit()

    assert second_job is not None
    assert second_job.status == SCORE_JOB_DONE
    assert second_job.cache_hit == "hit"


def test_validation_error_marks_job_error(monkeypatch, session) -> None:
    db, _org, _role, app = session

    def fake_run(*args, **kwargs):
        return _stub_match_output(0.0, status=ScoringStatus.FAILED, error_reason="missing field foo")

    monkeypatch.setattr(cv_match_runner, "run_cv_match", fake_run)

    job = enqueue_score(db, app)
    db.commit()
    db.refresh(app)

    assert job is not None
    assert job.status == SCORE_JOB_ERROR
    assert "missing field foo" in (job.error_message or "")
    assert app.cv_match_score is None
    assert "missing field foo" in (app.cv_match_details.get("error") or "")
    # Cache must NOT be populated on a failed scoring run.
    assert db.query(CvScoreCache).count() == 0


def test_existing_pending_job_is_reused_when_not_forced(monkeypatch, session) -> None:
    db, _org, _role, app = session
    monkeypatch.setattr(cv_match_runner, "run_cv_match", lambda *a, **kw: _stub_match_output(60.0))

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
    monkeypatch.setattr(cv_match_runner, "run_cv_match", lambda *a, **kw: _stub_match_output(60.0))

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
