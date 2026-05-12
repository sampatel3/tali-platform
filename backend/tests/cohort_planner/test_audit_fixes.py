"""Robustness coverage for the agent runtime audit fixes.

These tests pin behaviour for the regressions caught in the
2026-05-12 architecture audit so they don't silently reappear.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.agent_runtime import cohort_tools
from app.agent_runtime.tool_registry import _existing_decision_for_subject
from app.models.agent_decision import AgentDecision
from app.models.agent_needs_input import AgentNeedsInput
from app.models.agent_run import AgentRun

from .conftest import make_world


def _make_run(db, role) -> AgentRun:
    run = AgentRun(
        organization_id=role.organization_id,
        role_id=role.id,
        trigger="cron",
        status="running",
        model_version="m",
        prompt_version="p",
    )
    db.add(run)
    db.flush()
    return run


# ---------------------------------------------------------------------------
# effective_score_threshold / effective_monthly_budget_cents
# ---------------------------------------------------------------------------


def test_effective_threshold_uses_role_column_when_set(db):
    org, role, _, _ = make_world(db)
    # make_world sets score_threshold=65 by default.
    out = cohort_tools.survey_role_state(db, organization_id=int(org.id), role_id=int(role.id))
    assert out["effective_score_threshold"] == 65
    assert out["score_threshold"] == 65


def test_effective_threshold_falls_back_to_resolved_answer(db):
    org, role, _, _ = make_world(db)
    role.score_threshold = None
    db.flush()
    # Resolved threshold question with value=70.
    answered = AgentNeedsInput(
        organization_id=org.id,
        role_id=role.id,
        kind="threshold_ambiguous",
        prompt="x",
        resolved_at=datetime.now(timezone.utc),
        response={"value": "70"},
    )
    db.add(answered)
    db.flush()
    out = cohort_tools.survey_role_state(db, organization_id=int(org.id), role_id=int(role.id))
    assert out["score_threshold"] is None  # role column NOT mutated
    assert out["effective_score_threshold"] == 70
    # And the gap is closed so the agent doesn't re-ask.
    assert "score_threshold is unset" not in out["intent_gaps"]


def test_effective_threshold_clamps_invalid_answer(db):
    org, role, _, _ = make_world(db)
    role.score_threshold = None
    db.flush()
    bad = AgentNeedsInput(
        organization_id=org.id,
        role_id=role.id,
        kind="threshold_ambiguous",
        prompt="x",
        resolved_at=datetime.now(timezone.utc),
        response={"value": "not-a-number"},
    )
    db.add(bad)
    db.flush()
    out = cohort_tools.survey_role_state(db, organization_id=int(org.id), role_id=int(role.id))
    # Falls back to None; agent will still see the unanswered effective value.
    assert out["effective_score_threshold"] is None


def test_effective_budget_parses_dollars_and_cents(db):
    org, role, _, _ = make_world(db)
    role.monthly_usd_budget_cents = None
    db.flush()
    answered = AgentNeedsInput(
        organization_id=org.id,
        role_id=role.id,
        kind="monthly_budget_missing",
        prompt="x",
        resolved_at=datetime.now(timezone.utc),
        response={"value": "$75"},
    )
    db.add(answered)
    db.flush()
    out = cohort_tools.survey_role_state(db, organization_id=int(org.id), role_id=int(role.id))
    assert out["effective_monthly_budget_cents"] == 7500
    assert "monthly_usd_budget_cents is unset" not in out["intent_gaps"]


# ---------------------------------------------------------------------------
# _existing_decision_for_subject — HITL dedup
# ---------------------------------------------------------------------------


def test_existing_decision_for_subject_picks_latest_non_discarded(db):
    org, role, _, app = make_world(db, send_requires_approval=True)
    run = _make_run(db, role)
    # Two send_assessment decisions on the same app: one discarded, one
    # pending. Dedup should return the pending one.
    discarded = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        agent_run_id=run.id,
        decision_type="send_assessment",
        recommendation="send_assessment",
        status="discarded",
        reasoning="r",
        confidence=0.5,
        model_version="m",
        prompt_version="p",
        idempotency_key=f"{run.id}:{app.id}:send_assessment:discarded",
    )
    pending = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        agent_run_id=run.id,
        decision_type="send_assessment",
        recommendation="send_assessment",
        status="pending",
        reasoning="r",
        confidence=0.7,
        model_version="m",
        prompt_version="p",
        idempotency_key=f"{run.id}:{app.id}:send_assessment:pending",
    )
    db.add(discarded)
    db.add(pending)
    db.flush()
    found = _existing_decision_for_subject(
        db,
        role=role,
        application_id=int(app.id),
        decision_type="send_assessment",
    )
    assert found is not None
    assert int(found.id) == int(pending.id)


def test_existing_decision_for_subject_returns_none_when_all_discarded(db):
    """A previous discarded decision must NOT block a fresh queue —
    otherwise the agent can never re-queue after a recruiter rejects."""
    org, role, _, app = make_world(db, send_requires_approval=True)
    run = _make_run(db, role)
    db.add(
        AgentDecision(
            organization_id=org.id,
            role_id=role.id,
            application_id=app.id,
            agent_run_id=run.id,
            decision_type="send_assessment",
            recommendation="send_assessment",
            status="discarded",
            reasoning="r",
            confidence=0.5,
            model_version="m",
            prompt_version="p",
            idempotency_key=f"{run.id}:{app.id}:send_assessment:discarded",
        )
    )
    db.flush()
    assert (
        _existing_decision_for_subject(
            db,
            role=role,
            application_id=int(app.id),
            decision_type="send_assessment",
        )
        is None
    )
