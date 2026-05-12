"""Tests for the outcome-learning feedback loop.

Covers:
- ``transition_stage(to=advanced)`` records "interviewed"
  outcome on a recently-approved advance decision
- ``transition_outcome(to=hired)`` records "hired" outcome on a
  recently-approved advance decision
- ``transition_outcome(to=rejected)`` records "rejected_confirmed" on
  a recently-approved reject decision
- Stage / outcome transitions on applications with NO matching agent
  decision are no-ops (most pipeline transitions are recruiter-driven,
  not agent-recommended)
- Outcomes appear in the next cycle's calibration summary as a
  "track record" line the agent can read
- Bounded growth: the outcomes list caps at _MAX_OUTCOMES
- Hook failures never block the underlying transition (best-effort)
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import event

from app.agent_runtime import calibration as calibration_mod
from app.agent_runtime import outcome_learning
from app.domains.assessments_runtime.pipeline_service import (
    transition_outcome,
    transition_stage,
)
from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role


# Standard SQLite BigInteger PK workaround.
_BIG_PK_COUNTERS: dict[str, int] = {"agent_runs": 0, "agent_decisions": 0}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover
    table = target.__table__.name
    if target.id is None and table in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[table] += 1
        target.id = _BIG_PK_COUNTERS[table]


event.listen(AgentDecision, "before_insert", _assign_big_pk)


def _make_org(db) -> Organization:
    org = Organization(name="Outcome Org", slug=f"outcome-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _make_role(db, org: Organization) -> Role:
    role = Role(organization_id=org.id, name="Backend", source="manual")
    db.add(role)
    db.flush()
    return role


def _make_application(
    db, *, org: Organization, role: Role, stage: str = "review", outcome: str = "open"
) -> CandidateApplication:
    cand = Candidate(
        organization_id=org.id, email=f"c-{id(db)}@x.test", full_name="Candidate"
    )
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage=stage,
        pipeline_stage_source="recruiter",
        application_outcome=outcome,
        source="manual",
    )
    db.add(app)
    db.flush()
    return app


def _approved_decision(
    db,
    *,
    org: Organization,
    role: Role,
    application: CandidateApplication,
    decision_type: str,
) -> AgentDecision:
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=application.id,
        agent_run_id=None,
        decision_type=decision_type,
        recommendation=decision_type,
        status="approved",
        reasoning="test reasoning",
        confidence=0.85,
        model_version="test-model",
        prompt_version="test-prompt",
        idempotency_key=f"test:{application.id}:{decision_type}",
        resolved_at=datetime.now(timezone.utc),
    )
    db.add(decision)
    db.flush()
    return decision


# ---------------------------------------------------------------------------
# Stage transitions → "interviewed" outcome
# ---------------------------------------------------------------------------


def test_stage_transition_to_interview_records_interviewed_outcome(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, stage="review")
    _approved_decision(
        db, org=org, role=role, application=app, decision_type="advance_to_interview"
    )

    transition_stage(
        db,
        app=app,
        to_stage="advanced",
        source="recruiter",
        actor_type="recruiter",
        actor_id=1,
    )
    db.commit()

    db.refresh(role)
    outcomes = (role.agent_calibration or {}).get("outcomes") or []
    assert len(outcomes) == 1
    entry = outcomes[0]
    assert entry["decision_type"] == "advance_to_interview"
    assert entry["outcome"] == "interviewed"
    assert entry["application_id"] == app.id


def test_stage_transition_to_other_stage_does_not_record(db):
    """Only advanced is the meaningful 'advance' destination."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, stage="applied")
    _approved_decision(
        db, org=org, role=role, application=app, decision_type="advance_to_interview"
    )

    transition_stage(
        db,
        app=app,
        to_stage="invited",
        source="recruiter",
        actor_type="recruiter",
        actor_id=1,
    )
    db.commit()

    db.refresh(role)
    outcomes = (role.agent_calibration or {}).get("outcomes") or []
    assert outcomes == []


def test_stage_transition_with_no_matching_decision_is_a_noop(db):
    """Most transitions are recruiter-driven, no agent decision behind them."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, stage="review")
    # No agent decision at all.

    transition_stage(
        db,
        app=app,
        to_stage="advanced",
        source="recruiter",
        actor_type="recruiter",
        actor_id=1,
    )
    db.commit()

    db.refresh(role)
    outcomes = (role.agent_calibration or {}).get("outcomes") or []
    assert outcomes == []


# ---------------------------------------------------------------------------
# Outcome transitions → "hired" / "rejected_confirmed"
# ---------------------------------------------------------------------------


def test_outcome_transition_to_hired_records_hired(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, stage="advanced")
    _approved_decision(
        db, org=org, role=role, application=app, decision_type="advance_to_interview"
    )

    transition_outcome(
        db,
        app=app,
        to_outcome="hired",
        actor_type="recruiter",
        actor_id=1,
    )
    db.commit()

    db.refresh(role)
    outcomes = (role.agent_calibration or {}).get("outcomes") or []
    assert len(outcomes) == 1
    assert outcomes[0]["outcome"] == "hired"
    assert outcomes[0]["decision_type"] == "advance_to_interview"


def test_outcome_transition_to_rejected_records_confirmation(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, stage="review")
    _approved_decision(
        db, org=org, role=role, application=app, decision_type="reject"
    )

    transition_outcome(
        db,
        app=app,
        to_outcome="rejected",
        actor_type="recruiter",
        actor_id=1,
    )
    db.commit()

    db.refresh(role)
    outcomes = (role.agent_calibration or {}).get("outcomes") or []
    assert len(outcomes) == 1
    assert outcomes[0]["outcome"] == "rejected_confirmed"
    assert outcomes[0]["decision_type"] == "reject"


def test_outcome_transition_to_rejected_matches_skip_assessment_reject(db):
    """Both reject decision types should match on outcome=rejected."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, stage="applied")
    _approved_decision(
        db, org=org, role=role, application=app, decision_type="skip_assessment_reject"
    )

    transition_outcome(
        db,
        app=app,
        to_outcome="rejected",
        actor_type="recruiter",
        actor_id=1,
    )
    db.commit()

    db.refresh(role)
    outcomes = (role.agent_calibration or {}).get("outcomes") or []
    assert len(outcomes) == 1
    assert outcomes[0]["decision_type"] == "skip_assessment_reject"


def test_outcome_transition_to_withdrawn_does_not_record(db):
    """Withdrew is candidate-driven, not a signal on the agent's call."""
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, stage="review")
    _approved_decision(
        db, org=org, role=role, application=app, decision_type="advance_to_interview"
    )

    transition_outcome(
        db,
        app=app,
        to_outcome="withdrawn",
        actor_type="recruiter",
        actor_id=1,
    )
    db.commit()

    db.refresh(role)
    outcomes = (role.agent_calibration or {}).get("outcomes") or []
    assert outcomes == []


# ---------------------------------------------------------------------------
# Calibration render: track record line
# ---------------------------------------------------------------------------


def test_render_summary_includes_track_record_line():
    calibration = {
        "decisions_total": 3,
        "decisions_approved": 3,
        "decisions_overridden": 0,
        "score_observations": [],
        "recent_decisions": [],
        "override_patterns": [],
        "outcomes": [
            {"decision_type": "advance_to_interview", "outcome": "interviewed", "observed_at": "x", "application_id": 1},
            {"decision_type": "advance_to_interview", "outcome": "interviewed", "observed_at": "x", "application_id": 2},
            {"decision_type": "advance_to_interview", "outcome": "hired", "observed_at": "x", "application_id": 3},
        ],
    }
    summary = calibration_mod.render_summary(calibration)
    assert "track record" in summary
    assert "3 advance recommendation" in summary
    assert "2 reached interview" in summary
    assert "1 hired" in summary


def test_render_summary_handles_mixed_outcomes():
    calibration = {
        "outcomes": [
            {"decision_type": "advance_to_interview", "outcome": "interviewed", "observed_at": "x", "application_id": 1},
            {"decision_type": "reject", "outcome": "rejected_confirmed", "observed_at": "x", "application_id": 2},
            {"decision_type": "skip_assessment_reject", "outcome": "rejected_confirmed", "observed_at": "x", "application_id": 3},
        ],
    }
    summary = calibration_mod.render_summary(calibration)
    assert "1 advance recommendation" in summary
    assert "2 reject recommendation" in summary
    assert "2 confirmed by recruiter" in summary


def test_render_summary_says_no_realized_outcomes_when_empty():
    summary = calibration_mod.render_summary(calibration_mod._DEFAULT)
    assert "no realized outcomes yet" in summary


# ---------------------------------------------------------------------------
# Bounded growth
# ---------------------------------------------------------------------------


def test_outcomes_list_bounded_at_max(db):
    """Calibration save bounds outcomes at _MAX_OUTCOMES (FIFO)."""
    org = _make_org(db)
    role = _make_role(db, org)

    # Push more than the cap directly via calibration.save
    over_cap = calibration_mod._MAX_OUTCOMES + 5
    entries = [
        {
            "decision_type": "advance_to_interview",
            "outcome": "interviewed",
            "observed_at": f"t-{i}",
            "application_id": i,
        }
        for i in range(over_cap)
    ]
    calibration_mod.save(db, role=role, updates={"outcomes": entries})
    db.commit()

    db.refresh(role)
    stored = (role.agent_calibration or {}).get("outcomes") or []
    assert len(stored) == calibration_mod._MAX_OUTCOMES
    # FIFO — most-recent (highest application_id) wins
    assert stored[-1]["application_id"] == over_cap - 1


# ---------------------------------------------------------------------------
# Best-effort: hook failure must not block the underlying transition
# ---------------------------------------------------------------------------


def test_outcome_learning_hook_failure_does_not_block_stage_transition(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, stage="review")

    with patch.object(
        outcome_learning,
        "record_advance_outcome_on_stage",
        side_effect=RuntimeError("boom"),
    ):
        # Must not raise — pipeline_service swallows hook failures.
        transition_stage(
            db,
            app=app,
            to_stage="advanced",
            source="recruiter",
            actor_type="recruiter",
            actor_id=1,
        )
    db.commit()
    db.refresh(app)
    assert app.pipeline_stage == "advanced"


def test_outcome_learning_hook_failure_does_not_block_outcome_transition(db):
    org = _make_org(db)
    role = _make_role(db, org)
    app = _make_application(db, org=org, role=role, stage="advanced")

    with patch.object(
        outcome_learning,
        "record_outcome_on_outcome_change",
        side_effect=RuntimeError("boom"),
    ):
        transition_outcome(
            db,
            app=app,
            to_outcome="hired",
            actor_type="recruiter",
            actor_id=1,
        )
    db.commit()
    db.refresh(app)
    assert app.application_outcome == "hired"
