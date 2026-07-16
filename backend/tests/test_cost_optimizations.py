"""Cost-reduction PR: pre-screen retry backoff, auto-score per-tick cap,
cohort early-exit, validation-failure visibility.

Each test pins one behavior. Together they kill the four biggest waste
patterns identified from the 2026-05-21 cost breakdown:

- 7,668 pre-screen retries hammering Anthropic on every cohort tick →
  backoff stops them after one error per 6h
- ~1,500 candidates per role queued on every tick → capped at 50
- ~$0.05 of Sonnet 4.5 per no-op cycle × 4 roles × 48 ticks/day → skipped
- cv_match retry rate previously invisible → recorded on UsageEvent.metadata
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch


from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.services.pre_screening_service import (
    PRE_SCREEN_ERROR_BACKOFF,
    _persist_pre_screen_error,
    application_needs_pre_screen,
)


def _seed_app(db, *, cv_text: str = "cv content here", pre_screen_run_at=None, pre_screen_error_reason=None, cv_uploaded_at=None):
    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual", agentic_mode_enabled=True, monthly_usd_budget_cents=5000, job_spec_text="hire")
    db.add(role); db.flush()
    cand = Candidate(organization_id=org.id, email="c@x.test", full_name="C")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
        application_outcome="open", source="manual",
        cv_text=cv_text,
        pre_screen_run_at=pre_screen_run_at,
        pre_screen_error_reason=pre_screen_error_reason,
        cv_uploaded_at=cv_uploaded_at,
    )
    db.add(app); db.flush()
    return org, role, app


# ---------------------------------------------------------------------------
# P1a — pre-screen retry backoff
# ---------------------------------------------------------------------------

def test_pre_screen_with_recent_error_skipped_by_backoff(db):
    """An app whose last attempt errored within the backoff window must
    NOT be re-attempted. This is the fix for 7,668 cohort-tick retries
    burning Anthropic credits on the same candidates every 30 min."""
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    org, role, app = _seed_app(
        db,
        pre_screen_run_at=recent,
        pre_screen_error_reason="claude_call_failed: rate limited",
    )
    assert application_needs_pre_screen(app) is False


def test_pre_screen_with_old_error_retried_after_backoff(db):
    """Once the backoff window has elapsed, the app retries — transient
    Anthropic errors (rate limits, brief outages) self-heal eventually."""
    old = datetime.now(timezone.utc) - PRE_SCREEN_ERROR_BACKOFF - timedelta(minutes=5)
    org, role, app = _seed_app(
        db,
        pre_screen_run_at=old,
        pre_screen_error_reason="claude_call_failed: rate limited",
    )
    assert application_needs_pre_screen(app) is True


def test_pre_screen_fresh_cv_upload_beats_backoff(db):
    """A candidate uploading a new CV is an explicit signal to retry —
    don't make them wait 6h after an old error to get a score."""
    error_time = datetime.now(timezone.utc) - timedelta(hours=1)
    org, role, app = _seed_app(
        db,
        pre_screen_run_at=error_time,
        pre_screen_error_reason="claude_call_failed: rate limited",
        cv_uploaded_at=datetime.now(timezone.utc) - timedelta(minutes=2),
    )
    assert application_needs_pre_screen(app) is True


def test_persist_pre_screen_error_stamps_run_at(db):
    """The whole backoff system relies on every error path stamping
    ``pre_screen_run_at`` so the helper has a timestamp to compare. The
    original implementation deliberately did NOT stamp on error; that's
    what produced the 7,668-retries-per-day problem."""
    org, role, app = _seed_app(db)
    assert app.pre_screen_run_at is None

    _persist_pre_screen_error(app, reason="claude_call_failed: rate limited")
    assert app.pre_screen_run_at is not None
    # Stamped within the last few seconds.
    elapsed = (datetime.now(timezone.utc) - app.pre_screen_run_at).total_seconds()
    assert 0 <= elapsed < 5


def test_pre_screen_successful_does_not_retry(db):
    """A successful pre-screen on the current CV should not be retried.
    This is the existing behaviour — guard against regression."""
    run = datetime.now(timezone.utc) - timedelta(minutes=10)
    org, role, app = _seed_app(db, pre_screen_run_at=run)  # no error_reason
    assert application_needs_pre_screen(app) is False


# ---------------------------------------------------------------------------
# P1b — auto-scoring per-tick cap
# ---------------------------------------------------------------------------

def test_auto_enqueue_scoring_respects_per_tick_cap(db):
    """The first version of this helper called enqueue_score on every
    unscored candidate every tick. On a role with 1,500 unscored apps
    that meant 1,500 Celery tasks every 30 min — burning Anthropic
    credits faster than the worker pool could keep up. The cap fixes
    that; backlog drains over many ticks at a steady rate."""
    from app.tasks.agent_tasks import AUTO_SCORE_PER_TICK_CAP, _auto_enqueue_scoring

    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual", agentic_mode_enabled=True, monthly_usd_budget_cents=5000, job_spec_text="hire")
    db.add(role); db.flush()

    # Seed 100 unscored apps — more than the cap.
    for i in range(100):
        cand = Candidate(organization_id=org.id, email=f"c{i}@x.test", full_name=f"C{i}")
        db.add(cand); db.flush()
        db.add(CandidateApplication(
            organization_id=org.id, candidate_id=cand.id, role_id=role.id,
            status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
            application_outcome="open", source="manual",
            cv_text="content",
        ))
    db.commit()

    with patch("app.services.cv_score_orchestrator.enqueue_score", return_value=object()) as m:
        touched = _auto_enqueue_scoring(db, role=role)

    assert touched == AUTO_SCORE_PER_TICK_CAP
    assert m.call_count == AUTO_SCORE_PER_TICK_CAP


def test_auto_enqueue_scoring_skips_backoff_blocked_apps(db):
    """Apps with a recent pre-screen error are mirrored at the SQL level
    so we don't even enqueue. Without this the cap fills with errored
    apps that would immediately fail again."""
    from app.tasks.agent_tasks import _auto_enqueue_scoring

    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual", agentic_mode_enabled=True, monthly_usd_budget_cents=5000, job_spec_text="hire")
    db.add(role); db.flush()

    # One eligible, one within backoff, one outside backoff.
    recent_error = datetime.now(timezone.utc) - timedelta(hours=1)
    old_error = datetime.now(timezone.utc) - PRE_SCREEN_ERROR_BACKOFF - timedelta(minutes=5)
    for i, (ps_run, ps_err) in enumerate([
        (None, None),               # eligible: never attempted
        (recent_error, "rate limit"),  # blocked: backoff
        (old_error, "rate limit"),     # eligible: backoff expired
    ]):
        cand = Candidate(organization_id=org.id, email=f"c{i}@x.test", full_name=f"C{i}")
        db.add(cand); db.flush()
        db.add(CandidateApplication(
            organization_id=org.id, candidate_id=cand.id, role_id=role.id,
            status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
            application_outcome="open", source="manual",
            cv_text="content", pre_screen_run_at=ps_run, pre_screen_error_reason=ps_err,
        ))
    db.commit()

    with patch("app.services.cv_score_orchestrator.enqueue_score", return_value=object()) as m:
        touched = _auto_enqueue_scoring(db, role=role)

    assert touched == 2  # the eligible + the past-backoff one
    assert m.call_count == 2


# ---------------------------------------------------------------------------
# P2b — cohort tick early-exit
# ---------------------------------------------------------------------------

def test_cycle_would_be_noop_when_survey_empty(db):
    """If the survey shows no decision-eligible candidates, no open
    questions, and no intent gaps, the cycle would just call Claude
    for a 'nothing to do' summary. Skip it."""
    from app.tasks.agent_tasks import _cycle_would_be_noop

    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual", agentic_mode_enabled=True, monthly_usd_budget_cents=5000, job_spec_text="hire")
    db.add(role); db.flush()

    fake_survey = {
        "counts": {"ready_for_assessment_decision": 0, "ready_for_advance_decision": 0, "needs_pre_screen": 0, "needs_score": 0},
        "open_recruiter_questions": [],
        "intent_gaps": [],
    }
    with patch("app.agent_runtime.cohort_tools.survey_role_state", return_value=fake_survey):
        assert _cycle_would_be_noop(db, role=role) is True


def test_cycle_runs_when_actionable_candidates_exist(db):
    """Don't skip when there's actual work for the agent to do."""
    from app.tasks.agent_tasks import _cycle_would_be_noop

    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual", agentic_mode_enabled=True, monthly_usd_budget_cents=5000, job_spec_text="hire")
    db.add(role); db.flush()

    fake_survey = {
        "counts": {"ready_for_assessment_decision": 5, "ready_for_advance_decision": 0},
        "open_recruiter_questions": [],
        "intent_gaps": [],
    }
    with patch("app.agent_runtime.cohort_tools.survey_role_state", return_value=fake_survey):
        assert _cycle_would_be_noop(db, role=role) is False


def test_cycle_runs_when_open_recruiter_questions(db):
    """Open recruiter inputs need the agent to acknowledge/use them."""
    from app.tasks.agent_tasks import _cycle_would_be_noop

    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual", agentic_mode_enabled=True, monthly_usd_budget_cents=5000, job_spec_text="hire")
    db.add(role); db.flush()

    fake_survey = {
        "counts": {"ready_for_assessment_decision": 0, "ready_for_advance_decision": 0},
        "open_recruiter_questions": [{"id": 1, "kind": "intent_clarification"}],
        "intent_gaps": [],
    }
    with patch("app.agent_runtime.cohort_tools.survey_role_state", return_value=fake_survey):
        assert _cycle_would_be_noop(db, role=role) is False


# ---------------------------------------------------------------------------
# P2a — retry telemetry on UsageEvent
# ---------------------------------------------------------------------------

def test_cvmatch_output_carries_retry_telemetry():
    """The runner populates retry_count + validation_failures so
    cv_score_orchestrator can stamp them on the UsageEvent metadata.
    Bug B (just shipped) was hiding this because the per-attempt token
    overwrite made retries invisible; now the rate is queryable."""
    from app.cv_matching.schemas import CVMatchOutput

    out = CVMatchOutput(prompt_version="v1", retry_count=1, validation_failures=1)
    assert out.retry_count == 1
    assert out.validation_failures == 1
