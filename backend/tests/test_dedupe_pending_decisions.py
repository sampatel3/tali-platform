"""One pending decision per application at any time.

A second emit (any decision_type, any agent_run, any source — agent or
system) returns the existing pending row instead of creating a duplicate.
User reported seeing the same candidate twice in their Review queue
(an "advance" and a "send_assessment" both pending on Abiola); this
suite locks the dedup in.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import event

from app.actions import queue_decision
from app.actions.types import Actor
from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.services.pre_screen_decision_emitter import (
    backfill_existing_below_threshold,
    queue_pre_screen_reject,
)


# Same BigInteger PK workaround the other agent_runtime tests use.
_BIG_PK = {"agent_decisions": 0, "agent_runs": 0}

def _assign_big_pk(mapper, connection, target):  # pragma: no cover — SQLA hook
    table = target.__table__.name
    if target.id is None and table in _BIG_PK:
        _BIG_PK[table] += 1
        target.id = _BIG_PK[table]

event.listen(AgentDecision, "before_insert", _assign_big_pk)
event.listen(AgentRun, "before_insert", _assign_big_pk)


def _seed(db):
    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id,
        name="R",
        source="manual",
        job_spec_text="hire",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
        auto_reject=False,
    )
    db.add(role); db.flush()
    cand = Candidate(organization_id=org.id, email="c@x.test", full_name="C")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        cv_text="some cv",
        pre_screen_run_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(app); db.flush()
    return org, role, app


def _agent_run(db, role: Role) -> AgentRun:
    run = AgentRun(
        organization_id=role.organization_id,
        role_id=role.id,
        trigger="cron",
        status="running",
        model_version="m",
        prompt_version="p",
    )
    db.add(run); db.flush()
    return run


def test_queue_decision_returns_existing_pending_on_same_app_different_type(db):
    """The Abiola case: agent queues advance_to_interview in cycle 1,
    then in cycle 2 reconsiders and tries to queue send_assessment for
    the same candidate. Second call must return the existing decision,
    not create a duplicate."""
    org, role, app = _seed(db)
    run1 = _agent_run(db, role)
    db.commit()  # cycle 1 commits its row

    first = queue_decision.run(
        db, Actor.agent(int(run1.id)),
        organization_id=int(org.id), role_id=int(role.id), application_id=int(app.id),
        decision_type="advance_to_interview",
        reasoning="Strong CV.", confidence=0.92, model_version="m", prompt_version="p",
    )
    db.commit()
    assert first.decision_type == "advance_to_interview"

    # Cycle 2: same application, different decision_type
    run2 = _agent_run(db, role)
    second = queue_decision.run(
        db, Actor.agent(int(run2.id)),
        organization_id=int(org.id), role_id=int(role.id), application_id=int(app.id),
        decision_type="send_assessment",
        reasoning="Reconsidering.", confidence=0.85, model_version="m", prompt_version="p",
    )
    assert second.id == first.id  # same row returned
    assert second.decision_type == "advance_to_interview"  # original stands

    n = db.query(AgentDecision).filter(
        AgentDecision.application_id == app.id,
        AgentDecision.status == "pending",
    ).count()
    assert n == 1


def test_pre_screen_emitter_skips_when_any_pending_decision_exists(db):
    """The backfill collision: agent emitted a 'reject' decision already;
    pre-screen path tries to add a 'skip_assessment_reject'. The original
    backfill only deduped on its own idempotency key — this caught it."""
    org, role, app = _seed(db)
    run = _agent_run(db, role)
    db.commit()
    # Agent's reject lands first
    queue_decision.run(
        db, Actor.agent(int(run.id)),
        organization_id=int(org.id), role_id=int(role.id), application_id=int(app.id),
        decision_type="reject",
        reasoning="Bad fit.", confidence=0.9, model_version="m", prompt_version="p",
    )
    db.commit()

    # Now pre-screen path tries to emit
    result = queue_pre_screen_reject(
        db,
        organization_id=int(org.id),
        role=role,
        application=app,
        pre_screen_score=30.0,
        threshold=50.0,
    )
    assert result is not None
    assert result.decision_type == "reject"  # returned the existing agent reject

    n = db.query(AgentDecision).filter(
        AgentDecision.application_id == app.id,
        AgentDecision.status == "pending",
    ).count()
    assert n == 1


def test_backfill_skips_apps_with_existing_pending_decision(db):
    """When the backfill runs and an app already has a pending decision
    of any type, it must not create a duplicate skip_assessment_reject."""
    org, role, app = _seed(db)
    app.pre_screen_score_100 = 30.0  # below threshold so backfill would touch it
    db.flush()
    run = _agent_run(db, role)
    db.commit()
    queue_decision.run(
        db, Actor.agent(int(run.id)),
        organization_id=int(org.id), role_id=int(role.id), application_id=int(app.id),
        decision_type="reject",
        reasoning="Bad fit.", confidence=0.9, model_version="m", prompt_version="p",
    )
    db.commit()

    summary = backfill_existing_below_threshold(db, organization_id=int(org.id))
    assert summary["created"] == 0
    assert summary["skipped_existing"] == 1

    n = db.query(AgentDecision).filter(
        AgentDecision.application_id == app.id,
        AgentDecision.status == "pending",
    ).count()
    assert n == 1


def test_queue_decision_derives_reasoning_from_cv_match_when_empty(db):
    """One card shape for every producer: when a producer (e.g. the LLM agent)
    omits a per-candidate reasoning, queue_decision derives it from the
    candidate's cv_match analysis — the same source the deterministic bulk pass
    uses — instead of leaving a generic placeholder."""
    org, role, app = _seed(db)
    app.cv_match_details = {"summary": "Strong AWS Glue + PySpark fit; gaps in CDC evidence."}
    app.cv_match_score = 72.0
    db.flush()
    run = _agent_run(db, role)
    db.commit()

    d = queue_decision.run(
        db, Actor.agent(int(run.id)),
        organization_id=int(org.id), role_id=int(role.id), application_id=int(app.id),
        decision_type="send_assessment",
        reasoning="",  # producer left it blank
        confidence=0.85, model_version="m", prompt_version="p",
    )
    assert d.reasoning == "Strong AWS Glue + PySpark fit; gaps in CDC evidence."


def test_queue_decision_keeps_explicit_reasoning(db):
    """A producer that DID write a rationale keeps it verbatim."""
    org, role, app = _seed(db)
    app.cv_match_details = {"summary": "cv summary"}
    db.flush()
    run = _agent_run(db, role)
    db.commit()

    d = queue_decision.run(
        db, Actor.agent(int(run.id)),
        organization_id=int(org.id), role_id=int(role.id), application_id=int(app.id),
        decision_type="send_assessment",
        reasoning="Hand-written agent rationale.",
        confidence=0.9, model_version="m", prompt_version="p",
    )
    assert d.reasoning == "Hand-written agent rationale."
