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


def test_role_intent_shape_counts_buckets(db):
    """survey_role_state.role_intent_shape exposes per-bucket counts +
    examples so the agent can judge whether intent is rich enough."""
    from app.models.role_criterion import RoleCriterion

    org, role, _, _ = make_world(db)
    # Wipe the fixture's defaults so this is deterministic.
    db.query(RoleCriterion).filter(RoleCriterion.role_id == role.id).delete()
    db.flush()
    db.add_all([
        RoleCriterion(role_id=role.id, source="recruiter", bucket="must", text="Python 5y", weight=1.0),
        RoleCriterion(role_id=role.id, source="recruiter", bucket="preferred", text="AWS", weight=0.5),
        RoleCriterion(role_id=role.id, source="recruiter", bucket="preferred", text="Docker", weight=0.5),
        # Derived chips don't count — only recruiter-set intent.
        RoleCriterion(role_id=role.id, source="derived_from_spec", bucket="preferred", text="Git", weight=0.3),
    ])
    db.flush()

    out = cohort_tools.survey_role_state(db, organization_id=int(org.id), role_id=int(role.id))
    shape = out["role_intent_shape"]
    assert shape["must_count"] == 1
    assert shape["preferred_count"] == 2
    assert shape["constraints_count"] == 0
    assert shape["must_examples"] == ["Python 5y"]
    assert set(shape["preferred_examples"]) == {"AWS", "Docker"}
    assert shape["constraints_examples"] == []


def test_role_intent_shape_counts_constraints_with_canonical_bucket(db):
    """Constraint chips use bucket='constraint' (singular). Earlier my
    code keyed off 'constraints' (plural) and silently dropped them —
    Codex #190. This test pins the canonical-bucket behaviour."""
    from app.models.role_criterion import RoleCriterion

    org, role, _, _ = make_world(db)
    db.query(RoleCriterion).filter(RoleCriterion.role_id == role.id).delete()
    db.flush()
    db.add(
        RoleCriterion(role_id=role.id, source="recruiter", bucket="constraint", text="Remote only", weight=1.0)
    )
    db.flush()
    out = cohort_tools.survey_role_state(db, organization_id=int(org.id), role_id=int(role.id))
    shape = out["role_intent_shape"]
    assert shape["constraints_count"] == 1
    assert shape["constraints_examples"] == ["Remote only"]


def test_unparseable_threshold_answer_keeps_gap_open(db):
    """Codex #187: a manual threshold answer must parse as numeric.

    Automatic threshold mode needs no recruiter question; manual mode keeps
    the configuration gap open until it receives a usable value.
    """
    org, role, _, _ = make_world(db)
    role.score_threshold = None
    role.auto_reject_threshold_mode = "manual"
    db.flush()
    unparseable = AgentNeedsInput(
        organization_id=org.id,
        role_id=role.id,
        kind="threshold_ambiguous",
        prompt="x",
        resolved_at=datetime.now(timezone.utc),
        response={"value": "around fifty"},
    )
    db.add(unparseable)
    db.flush()
    out = cohort_tools.survey_role_state(db, organization_id=int(org.id), role_id=int(role.id))
    assert out["effective_score_threshold"] is None
    # Gap stays open so the agent re-asks.
    assert "score_threshold is unset" in out["intent_gaps"]


def test_intent_clarification_keeps_agent_prompt(db):
    """ask_recruiter.open for intent_clarification doesn't override the
    agent's prompt — it only injects the settings-tab link."""
    from app.actions import ask_recruiter
    from app.actions.types import Actor
    from app.models.agent_needs_input import AgentNeedsInput

    org, role, _, _ = make_world(db)
    run = _make_run(db, role)
    actor = Actor.agent(int(run.id))
    agent_question = "You have 2 preferreds but zero must-haves — what's non-negotiable for this hire?"
    row = ask_recruiter.open(
        db,
        actor,
        organization_id=int(org.id),
        role_id=int(role.id),
        kind="intent_clarification",
        prompt=agent_question,
    )
    db.flush()
    assert row.prompt == agent_question
    # Settings link still rides through via response_schema → API surface.
    assert row.response_schema is not None
    assert row.response_schema.get("link_url") == f"/jobs/{int(role.id)}?tab=agent-settings"


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
