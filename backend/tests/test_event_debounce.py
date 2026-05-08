"""Tests for the per-role event-debounce primitive.

Covers the atomic-claim semantics of try_claim_event_window and the
release semantics of clear_event_window, plus an end-to-end test that
on_application_created only enqueues one Celery task when fired
repeatedly inside the debounce window.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.agent_runtime import event_debounce
from app.agent_runtime.event_debounce import (
    DEFAULT_DEBOUNCE_SECONDS,
    clear_event_window,
    try_claim_event_window,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role


def _make_org(db) -> Organization:
    org = Organization(name="Debounce Org", slug=f"debounce-org-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _make_role(db, org: Organization, *, agentic: bool = True) -> Role:
    role = Role(
        organization_id=org.id,
        name="Backend",
        source="manual",
        agentic_mode_enabled=agentic,
    )
    db.add(role)
    db.commit()
    return role


def _make_app(db, *, org: Organization, role: Role) -> CandidateApplication:
    cand = Candidate(
        organization_id=org.id, email=f"c-{id(db)}-{role.id}@x.test", full_name="C"
    )
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="applied",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(app)
    db.commit()
    return app


# ---------------------------------------------------------------------------
# Atomic claim semantics
# ---------------------------------------------------------------------------


def test_first_claim_succeeds_and_sets_deadline(db):
    org = _make_org(db)
    role = _make_role(db, org)

    now = datetime.now(timezone.utc)
    won = try_claim_event_window(db, role=role, debounce_seconds=60, now=now)

    assert won is True
    db.refresh(role)
    assert role.agent_next_run_at is not None
    # Deadline is in the future (within ~60s of now).
    deadline = role.agent_next_run_at
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    assert deadline >= now
    assert deadline <= now + timedelta(seconds=61)


def test_second_claim_inside_window_fails(db):
    """Subsequent events in the same window must no-op."""
    org = _make_org(db)
    role = _make_role(db, org)
    now = datetime.now(timezone.utc)

    first = try_claim_event_window(db, role=role, debounce_seconds=60, now=now)
    second = try_claim_event_window(db, role=role, debounce_seconds=60, now=now)

    assert first is True
    assert second is False


def test_claim_succeeds_after_window_expires(db):
    """A stale claim (deadline in the past) is recoverable — the next event
    re-claims the slot. Guards against an orphaned task that never cleared."""
    org = _make_org(db)
    role = _make_role(db, org)

    # Plant a stale deadline.
    role.agent_next_run_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db.commit()

    now = datetime.now(timezone.utc)
    won = try_claim_event_window(db, role=role, debounce_seconds=60, now=now)
    assert won is True
    db.refresh(role)
    deadline = role.agent_next_run_at
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    assert deadline > now


def test_clear_event_window_releases_slot(db):
    """clear_event_window should set agent_next_run_at back to NULL so a new
    event can claim a fresh window."""
    org = _make_org(db)
    role = _make_role(db, org)

    won = try_claim_event_window(db, role=role)
    assert won is True

    clear_event_window(db, role=role)
    db.refresh(role)
    assert role.agent_next_run_at is None

    # And another claim immediately succeeds.
    won2 = try_claim_event_window(db, role=role)
    assert won2 is True


def test_claim_only_targets_specified_role(db):
    """Atomic UPDATE must scope to the role id; siblings stay untouched."""
    org = _make_org(db)
    role_a = _make_role(db, org)
    role_b = _make_role(db, org)

    won = try_claim_event_window(db, role=role_a)
    assert won is True

    db.refresh(role_a)
    db.refresh(role_b)
    assert role_a.agent_next_run_at is not None
    assert role_b.agent_next_run_at is None

    # And role_b can still claim independently.
    assert try_claim_event_window(db, role=role_b) is True


# ---------------------------------------------------------------------------
# Integration: on_application_created enqueues at most once per window
# ---------------------------------------------------------------------------


def test_on_application_created_enqueues_only_first_event_in_window(db):
    """Three apps for one role within the same window → one Celery enqueue."""
    from app.services import application_events

    org = _make_org(db)
    role = _make_role(db, org)
    apps = [_make_app(db, org=org, role=role) for _ in range(3)]

    with patch(
        "app.tasks.agent_tasks.agent_react_to_event"
    ) as mock_task:
        for a in apps:
            application_events.on_application_created(a)

    assert mock_task.apply_async.call_count == 1
    # First event was the one that won the claim.
    kwargs = mock_task.apply_async.call_args.kwargs
    assert kwargs["kwargs"]["role_id"] == role.id
    assert kwargs["kwargs"]["application_id"] == apps[0].id
    assert kwargs["countdown"] == DEFAULT_DEBOUNCE_SECONDS


def test_on_application_created_skips_when_agentic_mode_off(db):
    """No claim + no enqueue when agentic mode is disabled — must not write
    to agent_next_run_at, otherwise a stale claim would block future cycles
    if recruiter later toggles agentic mode on."""
    from app.services import application_events

    org = _make_org(db)
    role = _make_role(db, org, agentic=False)
    app = _make_app(db, org=org, role=role)

    with patch(
        "app.services.cv_score_orchestrator.enqueue_score", return_value=None
    ), patch(
        "app.tasks.agent_tasks.agent_react_to_event"
    ) as mock_task:
        application_events.on_application_created(app)

    assert mock_task.apply_async.call_count == 0
    db.refresh(role)
    assert role.agent_next_run_at is None


def test_on_application_created_skips_when_role_paused(db):
    from app.services import application_events

    org = _make_org(db)
    role = _make_role(db, org)
    role.agent_paused_at = datetime.now(timezone.utc)
    role.agent_paused_reason = "monthly cap"
    db.commit()
    app = _make_app(db, org=org, role=role)

    with patch(
        "app.services.cv_score_orchestrator.enqueue_score", return_value=None
    ), patch(
        "app.tasks.agent_tasks.agent_react_to_event"
    ) as mock_task:
        application_events.on_application_created(app)

    assert mock_task.apply_async.call_count == 0
    db.refresh(role)
    assert role.agent_next_run_at is None
