"""Deterministic bulk decisioning gives EVERY scored candidate a verdict,
on the single role threshold, with no LLM calls and full dedup.
"""
from __future__ import annotations

from sqlalchemy import event

from app.decision_policy.bootstrap import bootstrap_org
from app.models.agent_decision import AgentDecision
from app.models.agent_needs_input import AgentNeedsInput
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.decision_policy import DecisionPolicy
from app.models.organization import Organization
from app.models.role import Role
from app.models.rubric_revision import RubricRevision
from app.models.task import Task
from app.models.usage_event import UsageEvent
from app.services import bulk_decision_service
from app.services.bulk_decision_service import decide_role_cohort

# SQLite BigInteger-PK workaround for the models this pass writes.
_PK: dict[str, int] = {}


def _assign_pk(mapper, connection, target):  # pragma: no cover — SQLA hook
    t = target.__table__.name
    if getattr(target, "id", None) is None:
        _PK[t] = _PK.get(t, 0) + 1
        target.id = _PK[t]


for _m in (AgentRun, AgentDecision, DecisionPolicy, RubricRevision, AgentNeedsInput):
    event.listen(_m, "before_insert", _assign_pk)


def _seed_role(db, *, score_threshold=50, with_task=False):
    org = Organization(name="O", slug=f"o-{id(db)}-{score_threshold}-{with_task}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id, name="R", source="manual",
        score_threshold=score_threshold,
    )
    db.add(role)
    db.flush()
    if with_task:
        task = Task(name="Take-home assessment")
        db.add(task)
        db.flush()
        role.tasks.append(task)
        db.flush()
    bootstrap_org(db, organization_id=int(org.id))
    db.commit()
    return org, role


def _add_app(db, org, role, *, role_fit, pre_screen=70.0, cv_match_details=None):
    cand = Candidate(
        organization_id=org.id,
        email=f"c{role_fit}-{id(db)}@x.test",
        full_name=f"C{role_fit}",
    )
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="applied", pipeline_stage_source="recruiter",
        application_outcome="open", source="manual", cv_text="cv text",
        cv_match_score=role_fit, pre_screen_score_100=pre_screen,
        cv_match_details=cv_match_details,
    )
    db.add(app)
    db.commit()
    return app


def _pending(db, role):
    return (
        db.query(AgentDecision)
        .filter(AgentDecision.role_id == role.id, AgentDecision.status == "pending")
        .all()
    )


def test_every_candidate_decided_no_task_advances_strong(db):
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    _add_app(db, org, role, role_fit=80.0)  # >= 50 -> advance (no task)
    _add_app(db, org, role, role_fit=55.0)  # >= 50 -> advance
    _add_app(db, org, role, role_fit=40.0)  # < 50  -> reject
    _add_app(db, org, role, role_fit=20.0)  # < 50  -> reject

    summary = decide_role_cohort(db, role=role)

    decs = _pending(db, role)
    assert len(decs) == 4, "every scored candidate must get a decision"
    types = sorted(d.decision_type for d in decs)
    assert types == ["advance_to_interview", "advance_to_interview", "reject", "reject"]
    assert summary["created"] == 4
    # No LLM: the deterministic pass writes zero usage_events.
    assert db.query(UsageEvent).count() == 0


def test_reasoning_sourced_from_cv_match_summary(db):
    """Recruiter-facing reasoning is the CV-match narrative (same source as
    the report hero), with the threshold mechanics demoted to evidence."""
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    summary = "Strong backend depth; ships production Python and owns CI."
    _add_app(
        db, org, role, role_fit=80.0,
        cv_match_details={"summary": summary, "score_rationale_bullets": ["x"]},
    )

    decide_role_cohort(db, role=role)

    dec = _pending(db, role)[0]
    assert dec.reasoning == summary
    # Audit basis lives in evidence, not the headline.
    assert "role-fit 80 vs threshold" in dec.evidence["policy_basis"]
    assert "Deterministic policy" not in dec.reasoning


def test_reasoning_falls_back_to_rationale_then_policy(db):
    """No summary -> first rationale bullet; nothing at all -> policy basis,
    so reasoning is never blank (queue_decision rejects empty reasoning)."""
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    _add_app(
        db, org, role, role_fit=80.0,
        cv_match_details={"summary": "", "score_rationale_bullets": ["Clears the bar on core skills."]},
    )
    _add_app(db, org, role, role_fit=75.0, cv_match_details={"summary": ""})

    decide_role_cohort(db, role=role)

    reasons = {d.reasoning for d in _pending(db, role)}
    assert "Clears the bar on core skills." in reasons
    assert any(r.startswith("Deterministic policy:") for r in reasons)


def test_strong_candidate_sends_assessment_when_task_assigned(db):
    org, role = _seed_role(db, score_threshold=50, with_task=True)
    _add_app(db, org, role, role_fit=80.0)  # >= 50 + task -> send_assessment
    _add_app(db, org, role, role_fit=30.0)  # < 50 -> reject

    decide_role_cohort(db, role=role)

    decs = _pending(db, role)
    types = sorted(d.decision_type for d in decs)
    assert types == ["reject", "send_assessment"]


def test_idempotent_no_double_queue(db):
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    _add_app(db, org, role, role_fit=80.0)
    _add_app(db, org, role, role_fit=20.0)

    first = decide_role_cohort(db, role=role)
    assert first["created"] == 2
    second = decide_role_cohort(db, role=role)
    # Second pass selects nothing (all have a pending decision now).
    assert second.get("created", 0) == 0
    assert len(_pending(db, role)) == 2


def test_skips_candidate_with_existing_pending(db):
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    app = _add_app(db, org, role, role_fit=80.0)
    # Pre-existing pending decision for this app (e.g. from the LLM agent).
    db.add(AgentDecision(
        organization_id=org.id, role_id=role.id, application_id=app.id,
        decision_type="advance_to_interview", recommendation="advance_to_interview",
        status="pending", reasoning="manual", model_version="x", prompt_version="x",
        idempotency_key=f"pre:{app.id}",
    ))
    db.commit()

    summary = decide_role_cohort(db, role=role)
    assert summary["candidates"] == 0  # excluded by the pending filter
    assert len(_pending(db, role)) == 1


def test_volume_guard_raises_threshold_question(db, monkeypatch):
    monkeypatch.setattr(bulk_decision_service, "VOLUME_GUARD_PENDING_LIMIT", 2)
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    _add_app(db, org, role, role_fit=80.0)
    _add_app(db, org, role, role_fit=75.0)
    _add_app(db, org, role, role_fit=70.0)

    decide_role_cohort(db, role=role)

    qs = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.role_id == role.id,
            AgentNeedsInput.kind == "threshold_ambiguous",
            AgentNeedsInput.resolved_at.is_(None),
        )
        .all()
    )
    assert len(qs) == 1, "high review load should open one threshold question"


def test_reconcile_re_decides_pending_on_threshold_shift(db):
    """When the threshold recalibrates, an existing pending bulk decision
    whose band flipped is discarded and re-decided with the new bar."""
    org, role = _seed_role(db, score_threshold=40, with_task=False)  # manual mode
    _add_app(db, org, role, role_fit=55.0)  # 55 >= 40 -> advance (no task)

    decide_role_cohort(db, role=role)
    decs = _pending(db, role)
    assert len(decs) == 1 and decs[0].decision_type == "advance_to_interview"

    # Raise the threshold above the candidate's score -> should flip to reject.
    role.score_threshold = 70
    db.commit()
    summary = decide_role_cohort(db, role=role)
    assert summary.get("reconciled_discarded", 0) >= 1

    decs = _pending(db, role)
    assert len(decs) == 1, "still exactly one pending decision (re-decided, not duplicated)"
    assert decs[0].decision_type == "reject"


def test_reconcile_noop_when_threshold_unchanged(db):
    """No churn: re-running with the same threshold discards nothing."""
    org, role = _seed_role(db, score_threshold=40, with_task=False)
    _add_app(db, org, role, role_fit=55.0)
    decide_role_cohort(db, role=role)
    summary = decide_role_cohort(db, role=role)
    assert summary.get("reconciled_discarded", 0) == 0
    assert len(_pending(db, role)) == 1
