"""Unit tests for ``_last_activity_at`` — the "Last updated" value behind the
pipeline column + sort.

It must surface the most recent moment across the application row's own
timestamps (CV upload, every scoring pass, stage/outcome/notes edits) AND any
linked assessment (where a recruiter comment lands on the timeline → bumps the
assessment's ``updated_at``), tolerate naive/aware datetime mixes, and return
``None`` when nothing has a timestamp.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.domains.assessments_runtime.role_support import _last_activity_at


def _app(**overrides):
    base = dict(
        created_at=None,
        updated_at=None,
        pipeline_stage_updated_at=None,
        application_outcome_updated_at=None,
        cv_uploaded_at=None,
        cv_match_scored_at=None,
        pre_screen_run_at=None,
        score_cached_at=None,
        auto_reject_triggered_at=None,
        assessments=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_picks_latest_across_application_columns():
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    app = _app(
        created_at=base,
        updated_at=base + timedelta(days=1),
        cv_uploaded_at=base + timedelta(days=5),  # newest — a fresh CV upload
        cv_match_scored_at=base + timedelta(days=3),
    )
    assert _last_activity_at(app) == base + timedelta(days=5)


def test_includes_linked_assessment_activity():
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    # A recruiter comment bumps the assessment's updated_at, NOT the app row —
    # without folding assessments in, the column would miss new comments.
    assessment = SimpleNamespace(
        updated_at=base + timedelta(days=10),
        scored_at=None,
        completed_at=None,
        created_at=base,
    )
    app = _app(
        created_at=base,
        updated_at=base + timedelta(days=1),
        assessments=[assessment],
    )
    assert _last_activity_at(app) == base + timedelta(days=10)


def test_handles_naive_and_aware_mix_without_raising():
    aware = datetime(2026, 5, 1, tzinfo=timezone.utc)
    naive_newer = datetime(2026, 5, 2)  # naive → normalized to UTC → newer
    app = _app(created_at=aware, updated_at=naive_newer)
    assert _last_activity_at(app) == naive_newer


def test_returns_none_when_no_timestamps():
    assert _last_activity_at(_app()) is None
