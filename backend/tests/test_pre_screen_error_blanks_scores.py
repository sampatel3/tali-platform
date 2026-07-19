"""Pre-screen LLM errors must NOT silently fall through to v3 cv_match.

This was the production bug we shipped: when Anthropic credits ran out
during the post-deploy rescore wave, the pre-screen LLM call returned
``decision = "error"``. The orchestrator treated that as "maybe" and
ran v3 cv_match, which scored on raw CV-vs-JD fit. The refresh helpers
then mirrored that v3 score into ``pre_screen_score_100``, hiding the
error and making it look like pre-screen passed — even for candidates
with hard-constraint violations the LLM never got to see.

After the fix:
- ``execute_pre_screen_only`` persists ``pre_screen_error_reason`` and
  leaves both ``pre_screen_score_100`` and ``cv_match_score`` as NULL.
- The orchestrator's v3 gate detects the error state and bails before
  running v3 — preserving the blank-score signal end-to-end.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from app.services.pre_screening_service import execute_pre_screen_only

from tests.sub_agents.conftest import make_full_application


_JD = "Hiring a senior backend engineer with deep Python + payments experience."
_CV = "Senior Python engineer with 8 years SaaS experience at scale."


@dataclass
class _ErroredLLMResult:
    decision: str = "error"
    reason: str = "claude_call_failed: Error code: 400 - credit balance too low"
    score: float | None = None
    cache_hit: bool = False
    prompt_version: str = "cv_pre_screen_v2.1"
    trace_id: str = "trace-error-test"
    input_tokens: int = 200
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclass
class _SuccessfulLLMResult:
    decision: str = "yes"
    reason: str = "looks like a strong match"
    score: float | None = 82.0
    unverified_claim: bool = False
    cache_hit: bool = False
    prompt_version: str = "cv_pre_screen_v2.1"
    trace_id: str = "trace-success"
    input_tokens: int = 200
    output_tokens: int = 50
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


def test_pre_screen_error_leaves_scores_null_and_records_reason(db):
    """The headline regression: an errored LLM call must NOT produce a score."""
    org, role, _, app = make_full_application(db, cv_text=_CV, jd_text=_JD)
    role.job_spec_text = _JD
    # Seed prior healthy scores to prove the error handler clears them.
    app.pre_screen_score_100 = 75.0
    app.cv_match_score = 85.0
    app.pre_screen_recommendation = "Proceed to screening"
    db.flush()

    with patch(
        "app.cv_matching.runner_pre_screen.run_pre_screen",
        return_value=_ErroredLLMResult(),
    ):
        result = execute_pre_screen_only(app)

    assert result["status"] == "error"
    assert "credit balance" in result["reason"]
    # Scores wiped — UI must show "needs rescore", not a stale value.
    assert app.pre_screen_score_100 is None
    assert app.cv_match_score is None
    assert app.pre_screen_recommendation is None
    # Error reason is persisted so the UI / sweeper can surface it.
    assert app.pre_screen_error_reason is not None
    assert "credit balance" in app.pre_screen_error_reason
    # Evidence row keeps the decision=error marker for downstream consumers.
    assert isinstance(app.pre_screen_evidence, dict)
    assert app.pre_screen_evidence["decision"] == "error"


def test_pre_screen_success_clears_prior_error_reason(db):
    """Once the LLM call succeeds, ``pre_screen_error_reason`` must be
    cleared so the UI stops showing "needs rescore"."""
    org, role, _, app = make_full_application(db, cv_text=_CV, jd_text=_JD)
    role.job_spec_text = _JD
    # Seed an existing error from a prior attempt.
    app.pre_screen_error_reason = "claude_call_failed: previous run"
    app.pre_screen_score_100 = None
    db.flush()

    with patch(
        "app.cv_matching.runner_pre_screen.run_pre_screen",
        return_value=_SuccessfulLLMResult(),
    ):
        result = execute_pre_screen_only(app)

    assert result["status"] == "ok"
    assert app.pre_screen_error_reason is None
    assert app.pre_screen_score_100 is not None
    assert app.pre_screen_score_100 > 0


def test_pre_screen_error_backoff_then_retries_after_window(db):
    """A deterministic error stamps ``pre_screen_run_at`` and backs off 6h.

    Superseded the original "error must NOT stamp run_at" behaviour:
    that caused 7,668 burned Anthropic retries on 2026-05-21 because
    every errored app re-fired on every 30-min cohort tick. The backoff
    keeps the self-heal property but bounds deterministic retry after
    PRE_SCREEN_ERROR_BACKOFF instead of every tick.
    """
    from datetime import timedelta
    from app.services.pre_screening_service import (
        PRE_SCREEN_ERROR_BACKOFF,
        application_needs_pre_screen,
    )

    org, role, _, app = make_full_application(db, cv_text=_CV, jd_text=_JD)
    role.job_spec_text = _JD
    # First attempt errors.
    with patch(
        "app.cv_matching.runner_pre_screen.run_pre_screen",
        return_value=_ErroredLLMResult(),
    ):
        execute_pre_screen_only(app)

    # Score stays NULL (no leaked stale value), error recorded, run_at stamped.
    assert app.pre_screen_score_100 is None
    assert app.pre_screen_error_reason is not None
    assert app.pre_screen_run_at is not None

    # Within the backoff window → do NOT retry (this is the fix for the
    # retry storm).
    assert application_needs_pre_screen(app) is False

    # After the backoff window elapses → retry fires (self-heal).
    app.pre_screen_run_at = app.pre_screen_run_at - PRE_SCREEN_ERROR_BACKOFF - timedelta(minutes=1)
    assert application_needs_pre_screen(app) is True


def test_pre_screen_error_then_success_recovers_cleanly(db):
    """End-to-end: error → retry succeeds → scores populated, error
    reason cleared. This is the recovery path Codex P1 #1+#5 unlocks."""
    org, role, _, app = make_full_application(db, cv_text=_CV, jd_text=_JD)
    role.job_spec_text = _JD

    # First attempt: error.
    with patch(
        "app.cv_matching.runner_pre_screen.run_pre_screen",
        return_value=_ErroredLLMResult(),
    ):
        execute_pre_screen_only(app)
    assert app.pre_screen_score_100 is None
    assert app.pre_screen_error_reason is not None

    # Second attempt: success.
    with patch(
        "app.cv_matching.runner_pre_screen.run_pre_screen",
        return_value=_SuccessfulLLMResult(),
    ):
        result = execute_pre_screen_only(app)
    assert result["status"] == "ok"
    assert app.pre_screen_score_100 is not None
    assert app.pre_screen_score_100 > 0
    assert app.pre_screen_error_reason is None


def test_pre_screen_none_score_treated_as_error(db):
    """``decision != "error"`` but ``score is None`` (malformed LLM JSON)
    must also be treated as an error, not a passthrough."""
    org, role, _, app = make_full_application(db, cv_text=_CV, jd_text=_JD)
    role.job_spec_text = _JD

    @dataclass
    class _NoScoreResult:
        decision: str = "yes"  # would normally pass
        reason: str = "looks fine"
        score: float | None = None  # but no parseable score
        cache_hit: bool = False
        prompt_version: str = "cv_pre_screen_v2.1"
        trace_id: str = "trace-noscore"
        input_tokens: int = 100
        output_tokens: int = 5
        cache_read_tokens: int = 0
        cache_creation_tokens: int = 0

    with patch(
        "app.cv_matching.runner_pre_screen.run_pre_screen",
        return_value=_NoScoreResult(),
    ):
        result = execute_pre_screen_only(app)

    assert result["status"] == "error"
    assert app.pre_screen_score_100 is None
    assert app.pre_screen_error_reason is not None
