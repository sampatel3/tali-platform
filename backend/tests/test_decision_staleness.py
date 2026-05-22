"""Decision integrity: A1 fingerprint, A2 staleness, A6 terminal-state freeze.

These lock in the recruiter-trust invariants:
- A queued decision snapshots the inputs it cited (A1).
- The Hub flags a pending decision as stale when those inputs shift (A2).
- Resolved candidates (rejected / hired / advanced) are frozen — their
  decision is never re-evaluated and never flagged stale (A6).
- queue_decision refuses to act on resolved candidates (A6) and dedups a
  recently-discarded re-emit (C3).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import event

import pytest
from fastapi import HTTPException

from app.actions import queue_decision
from app.actions.types import Actor
from app.domains.assessments_runtime.role_support import is_resolved
from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.role_criterion import RoleCriterion
from app.services import decision_staleness


# Same BigInteger PK workaround the other agent_runtime tests use — SQLite
# doesn't autoincrement BigInteger PKs.
_BIG_PK = {"agent_decisions": 0, "agent_runs": 0}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover — SQLA hook
    table = target.__table__.name
    if target.id is None and table in _BIG_PK:
        _BIG_PK[table] += 1
        target.id = _BIG_PK[table]


event.listen(AgentDecision, "before_insert", _assign_big_pk)
event.listen(AgentRun, "before_insert", _assign_big_pk)


def _seed(db, *, outcome="open", stage="review", cv="some cv text"):
    org = Organization(name="O", slug=f"o-{id(db)}-{_BIG_PK['agent_decisions']}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id,
        name="R",
        source="manual",
        job_spec_text="hire an engineer",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
        auto_reject=False,
    )
    db.add(role); db.flush()
    crit = RoleCriterion(role_id=role.id, text="5y Python", bucket="must_have", weight=2.0)
    db.add(crit); db.flush()
    cand = Candidate(organization_id=org.id, email="c@x.test", full_name="C")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage=stage,
        pipeline_stage_source="recruiter",
        application_outcome=outcome,
        source="manual",
        cv_text=cv,
        pre_screen_score_100=72.0,
        cv_match_score=80.0,
    )
    db.add(app); db.flush()
    return org, role, crit, app


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


def _queue(db, org, role, app):
    run = _agent_run(db, role)
    db.commit()
    decision = queue_decision.run(
        db, Actor.agent(int(run.id)),
        organization_id=int(org.id), role_id=int(role.id), application_id=int(app.id),
        decision_type="advance_to_interview",
        reasoning="Strong CV.", confidence=0.9, model_version="m", prompt_version="p",
    )
    db.commit()
    return decision


# ---------------------------------------------------------------------------
# A6: is_resolved helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "outcome,stage,expected",
    [
        ("open", "review", False),
        ("open", "applied", False),
        ("rejected", "review", True),
        ("hired", "review", True),
        ("open", "advanced", True),
    ],
)
def test_is_resolved(db, outcome, stage, expected):
    _, _, _, app = _seed(db, outcome=outcome, stage=stage)
    assert is_resolved(app) is expected


# ---------------------------------------------------------------------------
# A1: fingerprint capture at queue time
# ---------------------------------------------------------------------------

def test_queue_captures_input_fingerprint(db):
    org, role, _, app = _seed(db)
    decision = _queue(db, org, role, app)
    fp = decision.input_fingerprint
    assert isinstance(fp, dict) and fp  # non-empty
    assert decision.criteria_fingerprint  # scalar shortcut populated
    assert fp["pre_screen_score_at_emit"] == 72.0
    assert decision.decision_dedup_key  # C4 key populated


# ---------------------------------------------------------------------------
# A2: staleness detection
# ---------------------------------------------------------------------------

def test_fresh_decision_not_stale(db):
    org, role, _, app = _seed(db)
    decision = _queue(db, org, role, app)
    report = decision_staleness.evaluate(db, decision)
    assert report.is_stale is False
    assert report.reasons == []


def test_criteria_edit_marks_stale(db):
    org, role, crit, app = _seed(db)
    decision = _queue(db, org, role, app)
    # Recruiter edits the must-have criterion text after the decision queued.
    crit.text = "8y Python + Go"
    db.add(crit); db.commit()
    report = decision_staleness.evaluate(db, decision)
    assert report.is_stale is True
    assert "criteria_changed" in report.reasons
    assert report.summary  # human label present


def test_pre_screen_score_swing_marks_stale(db):
    org, role, _, app = _seed(db)
    decision = _queue(db, org, role, app)
    app.pre_screen_score_100 = 50.0  # was 72 → 22pt drop, well over the 5pt band
    db.add(app); db.commit()
    report = decision_staleness.evaluate(db, decision)
    assert report.is_stale is True
    assert "pre_screen_score_shifted" in report.reasons


def test_sub_band_score_noise_not_stale(db):
    org, role, _, app = _seed(db)
    decision = _queue(db, org, role, app)
    app.pre_screen_score_100 = 74.0  # 2pt jitter, under the 5pt band
    db.add(app); db.commit()
    report = decision_staleness.evaluate(db, decision)
    assert "pre_screen_score_shifted" not in report.reasons


def test_resolved_decision_never_stale(db):
    """A6: once the candidate is resolved, the decision is frozen — even
    if criteria change, it's the immutable audit record, not stale."""
    org, role, crit, app = _seed(db)
    decision = _queue(db, org, role, app)
    crit.text = "totally different"
    db.add(crit)
    app.application_outcome = "rejected"  # candidate resolved after queue
    db.add(app); db.commit()
    report = decision_staleness.evaluate(db, decision)
    assert report.is_stale is False


def test_pre_a1_decision_not_stale(db):
    """Decisions queued before A1 (empty fingerprint) have no baseline —
    we must not flag them as stale."""
    org, role, crit, app = _seed(db)
    decision = _queue(db, org, role, app)
    decision.input_fingerprint = {}
    decision.criteria_fingerprint = None
    db.add(decision); db.commit()
    crit.text = "changed"
    db.add(crit); db.commit()
    report = decision_staleness.evaluate(db, decision)
    assert report.is_stale is False


# ---------------------------------------------------------------------------
# A6: queue_decision refuses resolved candidates
# ---------------------------------------------------------------------------

def test_queue_decision_refuses_resolved_app(db):
    org, role, _, app = _seed(db, outcome="rejected")
    run = _agent_run(db, role)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        queue_decision.run(
            db, Actor.agent(int(run.id)),
            organization_id=int(org.id), role_id=int(role.id), application_id=int(app.id),
            decision_type="advance_to_interview",
            reasoning="x", confidence=0.9, model_version="m", prompt_version="p",
        )
    assert exc.value.status_code == 422
    assert "resolved" in str(exc.value.detail).lower()


# ---------------------------------------------------------------------------
# C3: recently-discarded suppression
# ---------------------------------------------------------------------------

def test_list_agent_decisions_route_returns_pending_with_staleness(db):
    """Regression: list_agent_decisions must execute end-to-end with a
    pending decision in the queue. A function-local re-import of
    CandidateApplication once shadowed the module-level name and raised
    UnboundLocalError at runtime (prod queue went dark). This exercises
    the exact path — the join + the staleness batch-load — so it can't
    regress silently again.
    """
    from types import SimpleNamespace
    from app.domains.agentic import routes as agentic_routes

    org, role, _, app = _seed(db)
    _queue(db, org, role, app)

    current_user = SimpleNamespace(organization_id=int(org.id), id=1)
    # Pass every param explicitly — calling the route fn directly bypasses
    # FastAPI's Query(...) default resolution.
    payloads = agentic_routes.list_agent_decisions(
        role_id=int(role.id),
        status="pending",
        decision_type=None,
        q=None,
        since=None,
        limit=50,
        db=db,
        current_user=current_user,
    )
    assert len(payloads) == 1
    p = payloads[0]
    assert p.status == "pending"
    # Trust-signal fields the Hub renders are populated.
    assert p.confidence_band in {"high", "medium", "low", None}
    assert p.age_seconds >= 0
    assert p.is_stale is False  # fresh decision


def test_approve_route_409s_on_stale_decision(db):
    """A4: approving a stale decision returns 409 (unless force=true).
    Exercises the route end-to-end — the class of bug (runtime error in
    a route my isolation tests skipped) that took the queue down."""
    from types import SimpleNamespace
    from app.domains.agentic import routes as agentic_routes

    org, role, crit, app = _seed(db)
    decision = _queue(db, org, role, app)
    crit.text = "changed materially"
    db.add(crit); db.commit()

    user = SimpleNamespace(organization_id=int(org.id), id=1)
    with pytest.raises(HTTPException) as exc:
        agentic_routes.approve(
            decision_id=int(decision.id),
            body=agentic_routes.ApproveBody(),
            force=False,
            db=db,
            current_user=user,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail.get("code") == "decision_stale"


def test_re_evaluate_route_discards_and_requeues(db, monkeypatch):
    """A4: re-evaluate discards the pending decision and enqueues a fresh
    cycle. Mock the Celery dispatch so we don't run a real cycle."""
    from types import SimpleNamespace
    from app.domains.agentic import routes as agentic_routes
    from app.tasks import agent_tasks

    org, role, _, app = _seed(db)
    decision = _queue(db, org, role, app)

    monkeypatch.setattr(
        agent_tasks.agent_manual_run, "delay",
        lambda **kw: SimpleNamespace(id="fake-task-id"),
    )
    user = SimpleNamespace(organization_id=int(org.id), id=1)
    result = agentic_routes.re_evaluate(
        decision_id=int(decision.id), db=db, current_user=user,
    )
    assert result.superseded >= 1
    assert result.queued is True
    db.refresh(decision)
    assert decision.status == "discarded"


def test_re_evaluate_route_409s_on_resolved_app(db):
    """A6: a resolved candidate's decision is frozen — re-evaluate 409s."""
    from types import SimpleNamespace
    from app.domains.agentic import routes as agentic_routes

    org, role, _, app = _seed(db)
    decision = _queue(db, org, role, app)
    app.application_outcome = "hired"  # resolved after queue
    db.add(app); db.commit()

    user = SimpleNamespace(organization_id=int(org.id), id=1)
    with pytest.raises(HTTPException) as exc:
        agentic_routes.re_evaluate(
            decision_id=int(decision.id), db=db, current_user=user,
        )
    assert exc.value.status_code == 409


def test_recently_discarded_decision_suppresses_reemit(db):
    org, role, _, app = _seed(db)
    decision = _queue(db, org, role, app)
    # Recruiter discards it.
    decision.status = "discarded"
    decision.resolved_at = datetime.now(timezone.utc)
    db.add(decision); db.commit()

    # Agent re-emits the same type within the 10-min window.
    run2 = _agent_run(db, role)
    second = queue_decision.run(
        db, Actor.agent(int(run2.id)),
        organization_id=int(org.id), role_id=int(role.id), application_id=int(app.id),
        decision_type="advance_to_interview",
        reasoning="again", confidence=0.9, model_version="m", prompt_version="p",
    )
    assert second.id == decision.id  # returned the discarded row, no new pending
    pending = db.query(AgentDecision).filter(
        AgentDecision.application_id == app.id,
        AgentDecision.status == "pending",
    ).count()
    assert pending == 0
