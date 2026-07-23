"""One pending decision per application at any time.

A second emit (any decision_type, any agent_run, any source — agent or
system) returns the existing pending row instead of creating a duplicate.
User reported seeing the same candidate twice in their Review queue
(an "advance" and a "send_assessment" both pending on Abiola); this
suite locks the dedup in.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import event

from app.actions import queue_decision
from app.actions.types import Actor
from app.components.scoring.freshness import capture_score_generation
from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import CvScoreJob
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
        pre_screen_score_100=75.0,
        cv_match_score=75.0,
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


def _score_generation(db, role: Role, app: CandidateApplication):
    return capture_score_generation(db, role=role, application_id=int(app.id))


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
        expected_score_generation=_score_generation(db, role, app),
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
        expected_score_generation=_score_generation(db, role, app),
    )
    assert second.id == first.id  # same row returned
    assert second.decision_type == "advance_to_interview"  # original stands

    n = db.query(AgentDecision).filter(
        AgentDecision.application_id == app.id,
        AgentDecision.status == "pending",
    ).count()
    assert n == 1


def test_queue_decision_returns_existing_taught_card_instead_of_duplicate(db):
    """A taught card remains live until the recruiter resolves it."""

    org, role, app = _seed(db)
    first_run = _agent_run(db, role)
    taught = queue_decision.run(
        db,
        Actor.agent(int(first_run.id)),
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(app.id),
        decision_type="advance_to_interview",
        reasoning="Initial recommendation.",
        confidence=0.8,
        model_version="m",
        prompt_version="p",
        expected_score_generation=_score_generation(db, role, app),
    )
    taught.status = "reverted_for_feedback"
    db.commit()

    second_run = _agent_run(db, role)
    returned = queue_decision.run(
        db,
        Actor.agent(int(second_run.id)),
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(app.id),
        decision_type="send_assessment",
        reasoning="A later cycle must not duplicate the taught card.",
        confidence=0.9,
        model_version="m",
        prompt_version="p",
        expected_score_generation=_score_generation(db, role, app),
    )

    assert returned.id == taught.id
    assert returned.status == "reverted_for_feedback"
    assert (
        db.query(AgentDecision)
        .filter(
            AgentDecision.role_id == role.id,
            AgentDecision.application_id == app.id,
            AgentDecision.status.in_(
                ("pending", "processing", "reverted_for_feedback")
            ),
        )
        .count()
        == 1
    )


def test_queue_decision_refuses_older_done_score_when_newer_attempt_is_stale(db):
    org, role, app = _seed(db)
    run = _agent_run(db, role)
    db.add_all(
        [
            CvScoreJob(
                application_id=int(app.id),
                role_id=int(role.id),
                status="done",
                queued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
            CvScoreJob(
                application_id=int(app.id),
                role_id=int(role.id),
                status="stale",
                queued_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            ),
        ]
    )
    db.commit()

    with pytest.raises(HTTPException) as exc:
        queue_decision.run(
            db,
            Actor.agent(int(run.id)),
            organization_id=int(org.id),
            role_id=int(role.id),
            application_id=int(app.id),
            decision_type="advance_to_interview",
            reasoning="Old score cleared the bar.",
            confidence=0.9,
            model_version="m",
            prompt_version="p",
            expected_score_generation=_score_generation(db, role, app),
        )

    assert exc.value.status_code == 409
    assert "score refresh" in str(exc.value.detail)
    assert db.query(AgentDecision).filter_by(application_id=int(app.id)).count() == 0


def test_queue_decision_refuses_a_verdict_after_replacement_score_is_done(db):
    org, role, app = _seed(db)
    run = _agent_run(db, role)
    first = CvScoreJob(
        application_id=int(app.id),
        role_id=int(role.id),
        status="done",
        queued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(first)
    db.flush()
    generation_a = _score_generation(db, role, app)
    db.add(
        CvScoreJob(
            application_id=int(app.id),
            role_id=int(role.id),
            status="done",
            queued_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    )
    db.commit()

    with pytest.raises(HTTPException) as exc:
        queue_decision.run(
            db,
            Actor.agent(int(run.id)),
            organization_id=int(org.id),
            role_id=int(role.id),
            application_id=int(app.id),
            decision_type="advance_to_interview",
            reasoning="Generation A cleared the bar.",
            confidence=0.9,
            model_version="m",
            prompt_version="p",
            expected_score_generation=generation_a,
        )

    assert exc.value.status_code == 409
    assert db.query(AgentDecision).filter_by(application_id=int(app.id)).count() == 0


def test_queue_decision_refuses_cold_no_job_application(db):
    org, role, app = _seed(db)
    app.pre_screen_score_100 = None
    app.cv_match_score = None
    app.genuine_pre_screen_score_100 = None
    app.role_fit_score_cache_100 = None
    run = _agent_run(db, role)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        queue_decision.run(
            db,
            Actor.agent(int(run.id)),
            organization_id=int(org.id),
            role_id=int(role.id),
            application_id=int(app.id),
            decision_type="send_assessment",
            reasoning="Ephemeral cold verdict.",
            confidence=0.9,
            model_version="m",
            prompt_version="p",
            expected_score_generation=_score_generation(db, role, app),
        )

    assert exc.value.status_code == 409
    assert "score refresh" in str(exc.value.detail)
    assert db.query(AgentDecision).filter_by(application_id=int(app.id)).count() == 0


def test_queue_generation_lock_always_uses_canonical_org_role_order(db):
    from app.services import role_execution_guard

    org, role, app = _seed(db)
    run = _agent_run(db, role)
    db.commit()
    with patch.object(
        role_execution_guard,
        "lock_live_role",
        wraps=role_execution_guard.lock_live_role,
    ) as lock_live:
        queued = queue_decision.run(
            db,
            Actor.agent(int(run.id)),
            organization_id=int(org.id),
            role_id=int(role.id),
            application_id=int(app.id),
            decision_type="advance_to_interview",
            reasoning="Fresh score.",
            confidence=0.9,
            model_version="m",
            prompt_version="p",
            expected_score_generation=_score_generation(db, role, app),
        )
    assert queued.status == "pending"
    lock_live.assert_called_once_with(
        db,
        role_id=int(role.id),
        organization_id=int(org.id),
    )


def test_live_role_guard_locks_org_then_role_before_flushing_mutations(db):
    from app.services.role_execution_guard import lock_live_role

    org, role, _app = _seed(db)
    db.commit()
    role.auto_promote = True
    organization_id = int(org.id)
    role_id = int(role.id)
    order: list[str] = []
    bind = db.get_bind()

    def before_cursor(_conn, _cursor, statement, *_args):
        normalized = " ".join(str(statement).lower().split())
        if "from organizations" in normalized:
            order.append("organization_lock")
        elif "select roles.id" in normalized and "from roles" in normalized:
            order.append("role_lock")

    def before_flush(*_args):
        order.append("flush")

    event.listen(bind, "before_cursor_execute", before_cursor)
    event.listen(db, "before_flush", before_flush)
    try:
        locked = lock_live_role(
            db,
            role_id=role_id,
            organization_id=organization_id,
        )
    finally:
        event.remove(db, "before_flush", before_flush)
        event.remove(bind, "before_cursor_execute", before_cursor)

    assert locked is not None and locked.auto_promote is True
    assert order.index("organization_lock") < order.index("role_lock")
    assert order.index("role_lock") < order.index("flush")


def test_pre_screen_emitter_skips_when_any_pending_decision_exists(db):
    """The backfill collision: agent emitted a 'reject' decision already;
    pre-screen path tries to add a 'skip_assessment_reject'. The original
    backfill only deduped on its own idempotency key — this caught it."""
    org, role, app = _seed(db)
    # Keep this on the genuine pre-screen path; a full CV score correctly
    # delegates card ownership to the full-scoring decision producer.
    app.cv_match_score = None
    app.pre_screen_score_100 = 30.0
    role.score_threshold = 50
    role.auto_reject_threshold_mode = "manual"
    run = _agent_run(db, role)
    db.commit()
    # Agent's reject lands first
    queue_decision.run(
        db, Actor.agent(int(run.id)),
        organization_id=int(org.id), role_id=int(role.id), application_id=int(app.id),
        decision_type="reject",
        reasoning="Bad fit.", confidence=0.9, model_version="m", prompt_version="p",
        expected_score_generation=_score_generation(db, role, app),
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
        expected_score_generation=_score_generation(db, role, app),
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
        expected_score_generation=_score_generation(db, role, app),
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
        expected_score_generation=_score_generation(db, role, app),
    )
    assert d.reasoning == "Hand-written agent rationale."
