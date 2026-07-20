"""BUG-2: stale pending decisions & escalations must not rot forever.

``expired`` was a valid AgentDecision status that nothing ever set, so a
pending verdict — especially an ``escalate_low_confidence`` the recruiter
MUST adjudicate — could sit in the Hub queue indefinitely with no SLA.

``agent_expire_stale_decisions`` ages out stale pending verdicts to
``expired`` and re-surfaces stale escalations (re-prioritised, never silently
expired) so the human-decide signal is preserved.

The task runs on its own SessionLocal, so the test commits seed rows first
(shared in-memory SQLite) and re-reads through that session after.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import event

from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import Role
from app.tasks.agent_tasks import (
    DECISION_PENDING_SLA_DAYS,
    ESCALATION_REESCALATE_AFTER_DAYS,
    agent_expire_stale_decisions,
)


_RUN_PK = {"n": 0}


def _assign_run_pk(mapper, connection, target):  # pragma: no cover — SQLA hook
    if getattr(target, "id", None) is None:
        _RUN_PK["n"] += 1
        target.id = _RUN_PK["n"]


event.listen(AgentRun, "before_insert", _assign_run_pk)


def _seed(db):
    org = Organization(name="O", slug=f"o-{id(db)}-{_RUN_PK['n']}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="R",
        source="manual",
        job_spec_text="hire",
        agentic_mode_enabled=True,
    )
    db.add(role)
    db.flush()
    cand = Candidate(organization_id=org.id, email=f"c-{id(db)}@x.test", full_name="C")
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        cv_text="cv",
    )
    db.add(app)
    db.flush()
    return org, role, app


_KEY = {"n": 0}


def _decision(
    db,
    org,
    role,
    app,
    *,
    decision_type,
    age_days,
    status="pending",
    snoozed_until=None,
):
    _KEY["n"] += 1
    created = datetime.now(timezone.utc) - timedelta(days=age_days)
    d = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        agent_run_id=None,
        decision_type=decision_type,
        recommendation=decision_type,
        status=status,
        reasoning="x",
        model_version="m",
        prompt_version="p",
        idempotency_key=f"k-{_KEY['n']}",
        created_at=created,
        snoozed_until=snoozed_until,
    )
    db.add(d)
    db.flush()
    return d


def _run_sweep():
    return agent_expire_stale_decisions.apply().get()


def test_stale_pending_decision_expires_when_candidate_no_longer_open(db):
    # The SLA sweep only cleans up cards whose candidate has MOVED ON
    # (outcome != 'open') — a lingering pending verdict on a rejected/hired
    # candidate is obsolete, so it's aged out.
    org, role, app = _seed(db)
    app.application_outcome = "rejected"
    stale = _decision(
        db, org, role, app, decision_type="reject", age_days=DECISION_PENDING_SLA_DAYS + 5
    )
    db.commit()
    stale_id = int(stale.id)

    result = _run_sweep()
    assert result["status"] == "ok"
    assert stale_id in result["expired_decision_ids"]

    db.expire_all()
    row = db.get(AgentDecision, stale_id)
    assert row.status == "expired"
    assert row.resolved_at is not None
    assert "SLA" in (row.resolution_note or "")


def test_open_candidate_stale_decision_is_preserved(db):
    # A deterministic verdict on a still-OPEN candidate stays valid until the
    # recruiter acts (the score hasn't changed, so the recommendation hasn't
    # either). Expiring it silently stranded the candidate as "not yet
    # decided" — the limbo the funnel must never produce. It must persist.
    org, role, app = _seed(db)  # _seed leaves application_outcome="open"
    stale = _decision(
        db, org, role, app, decision_type="reject", age_days=DECISION_PENDING_SLA_DAYS + 5
    )
    db.commit()
    stale_id = int(stale.id)

    result = _run_sweep()
    assert result["status"] == "ok"
    assert stale_id not in result["expired_decision_ids"]

    db.expire_all()
    assert db.get(AgentDecision, stale_id).status == "pending"


def test_fresh_pending_decision_untouched(db):
    org, role, app = _seed(db)
    fresh = _decision(db, org, role, app, decision_type="reject", age_days=1)
    db.commit()
    fresh_id = int(fresh.id)

    result = _run_sweep()
    assert fresh_id not in result["expired_decision_ids"]

    db.expire_all()
    assert db.get(AgentDecision, fresh_id).status == "pending"


def test_snoozed_stale_decision_not_expired(db):
    org, role, app = _seed(db)
    snoozed = _decision(
        db,
        org,
        role,
        app,
        decision_type="reject",
        age_days=DECISION_PENDING_SLA_DAYS + 5,
        snoozed_until=datetime.now(timezone.utc) + timedelta(days=10),
    )
    db.commit()
    snoozed_id = int(snoozed.id)

    result = _run_sweep()
    assert snoozed_id not in result["expired_decision_ids"]

    db.expire_all()
    assert db.get(AgentDecision, snoozed_id).status == "pending"


def test_future_snoozed_stale_escalation_stays_snoozed(db):
    org, role, app = _seed(db)
    future_snooze = datetime.now(timezone.utc) + timedelta(days=10)
    escalation = _decision(
        db,
        org,
        role,
        app,
        decision_type="escalate_low_confidence",
        age_days=ESCALATION_REESCALATE_AFTER_DAYS + 2,
        snoozed_until=future_snooze,
    )
    db.commit()
    escalation_id = int(escalation.id)

    result = _run_sweep()

    assert escalation_id not in result["reescalated_decision_ids"]
    db.expire_all()
    row = db.get(AgentDecision, escalation_id)
    assert row.snoozed_until is not None
    stored_snooze = row.snoozed_until
    if stored_snooze.tzinfo is None:
        stored_snooze = stored_snooze.replace(tzinfo=timezone.utc)
    assert stored_snooze == future_snooze
    assert (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "agent_decision_reescalated",
        )
        .count()
        == 0
    )


def test_expired_snoozed_stale_escalation_is_reescalated(db):
    org, role, app = _seed(db)
    escalation = _decision(
        db,
        org,
        role,
        app,
        decision_type="escalate_low_confidence",
        age_days=ESCALATION_REESCALATE_AFTER_DAYS + 2,
        snoozed_until=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db.commit()
    escalation_id = int(escalation.id)

    result = _run_sweep()

    assert escalation_id in result["reescalated_decision_ids"]
    db.expire_all()
    row = db.get(AgentDecision, escalation_id)
    assert row.status == "pending"
    assert row.snoozed_until is None
    assert (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "agent_decision_reescalated",
        )
        .count()
        == 1
    )


def test_stale_escalation_is_reescalated_not_expired(db):
    org, role, app = _seed(db)
    esc = _decision(
        db,
        org,
        role,
        app,
        decision_type="escalate_low_confidence",
        age_days=ESCALATION_REESCALATE_AFTER_DAYS + 2,
    )
    db.commit()
    esc_id = int(esc.id)

    result = _run_sweep()
    assert esc_id not in result["expired_decision_ids"]
    assert esc_id in result["reescalated_decision_ids"]

    db.expire_all()
    row = db.get(AgentDecision, esc_id)
    assert row.status == "pending"  # escalation preserved, not expired

    events = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "agent_decision_reescalated",
        )
        .all()
    )
    assert len(events) == 1
    assert events[0].event_metadata.get("decision_id") == esc_id


def test_reescalation_throttled_within_window(db):
    org, role, app = _seed(db)
    _decision(
        db,
        org,
        role,
        app,
        decision_type="escalate_low_confidence",
        age_days=ESCALATION_REESCALATE_AFTER_DAYS + 1,
    )
    db.commit()

    first = _run_sweep()
    assert first["reescalated_count"] == 1
    # A second sweep in the same window must not re-fire (idempotent).
    second = _run_sweep()
    assert second["reescalated_count"] == 0

    db.expire_all()
    events = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.event_type == "agent_decision_reescalated",
        )
        .count()
    )
    assert events == 1
