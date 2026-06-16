"""Reconcile a Workable-side advance onto Taali.

A recruiter moving a candidate forward in Workable (Phone Screen / Technical /
Final Interview / Offer — post-handover) is a hand-off: Taali should show them
as 'advanced', not strand them as 'applied', and any stale pending decision must
be discarded. Plus: scoring skips Workable-disqualified candidates.
"""
from __future__ import annotations

from app.domains.assessments_runtime.pipeline_service import (
    reconcile_post_handover_advanced,
)
from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role


def _seed(db, *, workable_stage, pipeline_stage="applied", outcome="open"):
    org = Organization(name="O", slug=f"o-{id(db)}-{id(workable_stage)}-{workable_stage}")
    db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual",
                job_spec_text="hire an engineer", agentic_mode_enabled=True)
    db.add(role); db.flush()
    cand = Candidate(organization_id=org.id, email=f"c{id(db)}@x.test", full_name="C")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage=pipeline_stage, pipeline_stage_source="recruiter",
        application_outcome=outcome, source="workable",
        workable_stage=workable_stage, cv_match_score=40.0,
    )
    db.add(app); db.flush()
    return org, role, app


def test_advances_open_post_handover(db):
    _org, _role, app = _seed(db, workable_stage="Technical Interview")
    assert reconcile_post_handover_advanced(db, app=app) is True
    db.commit()
    assert app.pipeline_stage == "advanced"


def test_phone_screen_counts_as_handover(db):
    _org, _role, app = _seed(db, workable_stage="Phone Screen")
    assert reconcile_post_handover_advanced(db, app=app) is True
    assert app.pipeline_stage == "advanced"


def test_noop_when_not_post_handover(db):
    _org, _role, app = _seed(db, workable_stage="Applied")
    assert reconcile_post_handover_advanced(db, app=app) is False
    assert app.pipeline_stage == "applied"


def test_noop_when_already_advanced(db):
    _org, _role, app = _seed(db, workable_stage="Final Interview", pipeline_stage="advanced")
    assert reconcile_post_handover_advanced(db, app=app) is False


def test_noop_when_resolved(db):
    # A6: a rejected/hired candidate is frozen — don't re-advance.
    _org, _role, app = _seed(db, workable_stage="Technical Interview", outcome="rejected")
    assert reconcile_post_handover_advanced(db, app=app) is False


def test_discards_stale_pending_decision(db):
    org, role, app = _seed(db, workable_stage="Final Interview")
    d = AgentDecision(
        id=990000 + int(app.id),  # explicit PK — SQLite won't autoincrement BigInteger
        organization_id=org.id, role_id=role.id, application_id=app.id,
        decision_type="reject", recommendation="reject", status="pending",
        reasoning="below threshold", confidence=0.9,
        model_version="m", prompt_version="p", idempotency_key=f"t:{app.id}",
    )
    db.add(d); db.commit()

    assert reconcile_post_handover_advanced(db, app=app) is True
    db.commit()
    assert app.pipeline_stage == "advanced"
    still_pending = (
        db.query(AgentDecision)
        .filter(AgentDecision.application_id == app.id, AgentDecision.status == "pending")
        .first()
    )
    assert still_pending is None  # the stale reject was discarded


def test_enqueue_score_skips_disqualified(db, monkeypatch):
    from app.services import cv_score_orchestrator as O

    _org, _role, app = _seed(db, workable_stage="Applied")
    app.cv_text = "some real cv text"
    app.workable_disqualified = True
    db.add(app); db.commit()
    # Pass the API-key guard so we reach the disqualified guard specifically.
    monkeypatch.setattr(O.settings, "ANTHROPIC_API_KEY", "test-key", raising=False)

    assert O.enqueue_score(db, app) is None  # skipped: workable-disqualified
