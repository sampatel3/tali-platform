"""Focused contracts for bounded pre-screen retries and cache-session reuse."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.cv_matching import MODEL_VERSION
from app.cv_matching.prompts_pre_screen import PRE_SCREEN_PROMPT_VERSION
from app.cv_matching.runner_pre_screen import (
    compute_pre_screen_cache_key,
    run_pre_screen,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_cache import CvScoreCache
from app.models.organization import Organization
from app.models.role import Role
from app.services.pre_screen_retry_policy import (
    PRE_SCREEN_DETERMINISTIC_ERROR_BACKOFF,
    PRE_SCREEN_TRANSIENT_ERROR_BACKOFF,
    _TRANSIENT_TEXT_MARKERS,
    classify_pre_screen_error,
    pre_screen_error_retry_due_clause,
)
from app.services.pre_screening_service import (
    _persist_pre_screen_error,
    application_needs_pre_screen,
)


def _application(db, *, index: int = 1) -> CandidateApplication:
    org = Organization(name="Retry org", slug=f"retry-org-{index}-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Backend engineer",
        source="manual",
        job_spec_text="Build reliable Python services",
    )
    db.add(role)
    db.flush()
    candidate = Candidate(
        organization_id=org.id,
        email=f"retry-{index}-{id(db)}@example.test",
        full_name="Retry Candidate",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="applied",
        application_outcome="open",
        cv_text="Eight years building Python services.",
    )
    db.add(application)
    db.flush()
    return application


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("claude_call_failed: Error code: 429", "transient"),
        ("claude_call_failed: request timed out", "transient"),
        ("claude_call_failed: Error code: 503", "transient"),
        ("claude_call_failed: connection reset by peer", "transient"),
        ("json_parse_failed: missing delimiter", "deterministic"),
        ("json_parse_failed at character 429", "deterministic"),
        ("validation failed: timeout field missing", "deterministic"),
        ("budget_admission_failed: role cap exhausted", "deterministic"),
        ("credit balance is too low", "deterministic"),
        ("unclassified provider failure", "deterministic"),
    ],
)
def test_error_classification_fails_unknowns_to_long_backoff(reason, expected):
    assert classify_pre_screen_error(reason) == expected


def test_transient_error_gets_one_short_retry_then_long_guard(db):
    application = _application(db)
    _persist_pre_screen_error(
        application,
        reason="claude_call_failed: request timed out",
    )
    assert application.pre_screen_evidence["error_retry_class"] == "transient"
    assert application.pre_screen_evidence["transient_error_streak"] == 1

    application.pre_screen_run_at -= PRE_SCREEN_TRANSIENT_ERROR_BACKOFF + timedelta(
        minutes=1
    )
    assert application_needs_pre_screen(application) is True

    _persist_pre_screen_error(
        application,
        reason="claude_call_failed: Error code: 503",
    )
    assert application.pre_screen_evidence["transient_error_streak"] == 2
    application.pre_screen_run_at -= PRE_SCREEN_TRANSIENT_ERROR_BACKOFF + timedelta(
        minutes=1
    )
    assert application_needs_pre_screen(application) is False
    application.pre_screen_run_at -= PRE_SCREEN_DETERMINISTIC_ERROR_BACKOFF
    assert application_needs_pre_screen(application) is True


def test_fresh_cv_resets_transient_retry_streak(db):
    application = _application(db, index=2)
    _persist_pre_screen_error(application, reason="claude_call_failed: timeout")
    _persist_pre_screen_error(application, reason="claude_call_failed: timeout")
    assert application.pre_screen_evidence["transient_error_streak"] == 2

    application.cv_uploaded_at = application.pre_screen_run_at + timedelta(seconds=1)
    _persist_pre_screen_error(application, reason="claude_call_failed: timeout")
    assert application.pre_screen_evidence["transient_error_streak"] == 1


def test_deterministic_error_keeps_six_hour_guard(db):
    application = _application(db, index=3)
    _persist_pre_screen_error(
        application,
        reason="json_parse_failed: malformed response",
    )
    assert application.pre_screen_evidence["error_retry_class"] == "deterministic"
    application.pre_screen_run_at -= PRE_SCREEN_TRANSIENT_ERROR_BACKOFF + timedelta(
        minutes=1
    )
    assert application_needs_pre_screen(application) is False
    application.pre_screen_run_at -= PRE_SCREEN_DETERMINISTIC_ERROR_BACKOFF
    assert application_needs_pre_screen(application) is True


def test_sql_selector_matches_bounded_python_retry_policy(db):
    now = datetime.now(timezone.utc)
    first_transient = _application(db, index=4)
    first_transient.pre_screen_error_reason = "claude_call_failed: timeout"
    first_transient.pre_screen_evidence = {
        "error_retry_class": "transient",
        "transient_error_streak": 1,
    }
    first_transient.pre_screen_run_at = now - timedelta(minutes=31)

    repeated_transient = _application(db, index=5)
    repeated_transient.pre_screen_error_reason = "claude_call_failed: timeout"
    repeated_transient.pre_screen_evidence = {
        "error_retry_class": "transient",
        "transient_error_streak": 2,
    }
    repeated_transient.pre_screen_run_at = now - timedelta(minutes=31)

    deterministic = _application(db, index=6)
    deterministic.pre_screen_error_reason = "json_parse_failed"
    deterministic.pre_screen_evidence = {"error_retry_class": "deterministic"}
    deterministic.pre_screen_run_at = now - timedelta(minutes=31)

    legacy_transient = _application(db, index=7)
    legacy_transient.pre_screen_error_reason = "Error code: 503"
    legacy_transient.pre_screen_evidence = {"decision": "error"}
    legacy_transient.pre_screen_run_at = now - timedelta(minutes=31)

    old_deterministic = _application(db, index=8)
    old_deterministic.pre_screen_error_reason = "json_parse_failed"
    old_deterministic.pre_screen_evidence = {"error_retry_class": "deterministic"}
    old_deterministic.pre_screen_run_at = now - timedelta(hours=6, minutes=1)

    legacy_numeric_false_positive = _application(db, index=9)
    legacy_numeric_false_positive.pre_screen_error_reason = (
        "json_parse_failed at character 429"
    )
    legacy_numeric_false_positive.pre_screen_evidence = {"decision": "error"}
    legacy_numeric_false_positive.pre_screen_run_at = now - timedelta(minutes=31)
    db.flush()

    selected = {
        row_id
        for (row_id,) in db.query(CandidateApplication.id)
        .filter(pre_screen_error_retry_due_clause(CandidateApplication, now=now))
        .all()
    }
    assert selected == {
        first_transient.id,
        legacy_transient.id,
        old_deterministic.id,
    }


@pytest.mark.parametrize("marker", _TRANSIENT_TEXT_MARKERS)
def test_legacy_sql_selector_matches_every_python_transient_marker(db, marker):
    now = datetime.now(timezone.utc)
    application = _application(db, index=100)
    application.pre_screen_error_reason = f"provider failure: {marker}"
    application.pre_screen_evidence = {"decision": "error"}
    application.pre_screen_run_at = now - timedelta(minutes=31)
    db.flush()

    assert classify_pre_screen_error(application.pre_screen_error_reason) == "transient"
    assert (
        db.query(CandidateApplication.id)
        .filter(CandidateApplication.id == application.id)
        .filter(pre_screen_error_retry_due_clause(CandidateApplication, now=now))
        .scalar()
        == application.id
    )


@pytest.mark.parametrize(
    "reason",
    [
        "json_parse_failed at gateway timeout",
        "budget_admission_failed after http 503",
        "missing cv after connection reset",
        "missing_inputs after api connection error",
        "validation failed: timeout field missing",
        "schema rejected status code: 529",
        "unclassified provider failure",
    ],
)
def test_legacy_sql_selector_keeps_deterministic_and_unknown_errors_on_long_guard(
    db, reason
):
    now = datetime.now(timezone.utc)
    application = _application(db, index=101)
    application.pre_screen_error_reason = reason
    application.pre_screen_evidence = {"decision": "error"}
    application.pre_screen_run_at = now - timedelta(minutes=31)
    db.flush()

    assert classify_pre_screen_error(reason) == "deterministic"
    assert (
        db.query(CandidateApplication.id)
        .filter(CandidateApplication.id == application.id)
        .filter(pre_screen_error_retry_due_clause(CandidateApplication, now=now))
        .scalar()
        is None
    )


def test_cache_hit_reuses_caller_session_for_hit_bookkeeping(db):
    cv_text = "Eight years building Python services."
    job_spec = "Build reliable Python services"
    cache_key = compute_pre_screen_cache_key(
        cv_text=cv_text,
        jd_text=job_spec,
        requirements=None,
    )
    row = CvScoreCache(
        cache_key=cache_key,
        prompt_version=PRE_SCREEN_PROMPT_VERSION,
        model=MODEL_VERSION,
        score_100=84.0,
        result={
            "decision": "yes",
            "score": 84.0,
            "reason": "cached match",
            "trace_id": "cached-trace",
            "unverified_extraordinary_claim": False,
        },
        hit_count=0,
    )
    db.add(row)
    db.commit()
    client = MagicMock()

    with patch(
        "app.platform.database.SessionLocal",
        side_effect=AssertionError("cache hit opened a redundant session"),
    ):
        result = run_pre_screen(
            cv_text,
            job_spec,
            client=client,
            cache_read_session=db,
        )

    assert result.cache_hit is True
    assert result.score == 84.0
    assert row.hit_count == 1
    client.messages.create.assert_not_called()
