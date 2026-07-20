"""GET /agent-decisions/needs-reeval-count — accurate "Needs re-eval" total.

The home pill reads this so its number reflects the whole queue (and the
role/type scope), not the capped page the list endpoint returns.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.agent_decision import AgentDecision
from app.models.role import Role
from app.models.user import User
from tests.conftest import auth_headers


def _app(db, org_id, role_id, email, details):
    cand = Candidate(organization_id=org_id, email=email, full_name=email.split("@")[0])
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org_id, candidate_id=cand.id, role_id=role_id,
        status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
        application_outcome="open", source="manual",
        cv_match_score=80.0, cv_match_details=details,
    )
    db.add(app)
    db.flush()
    return app


def _pending(db, org_id, role_id, app_id, dtype, *, status="pending"):
    d = AgentDecision(
        organization_id=org_id, role_id=role_id, application_id=app_id,
        decision_type=dtype, recommendation=dtype, status=status,
        reasoning="x", confidence=0.9, model_version="m", prompt_version="p",
        idempotency_key=f"count-test:{app_id}",
        input_fingerprint={},  # pre-A1: engine staleness still flags
    )
    db.add(d)
    db.flush()
    return d


def test_needs_reeval_count_scopes_by_type(client, db, monkeypatch):
    monkeypatch.setattr(
        "app.services.cv_score_orchestrator._holistic_enabled_for", lambda a: True
    )
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(organization_id=org_id, name="R", source="manual",
                agentic_mode_enabled=True, job_spec_text="hire")
    db.add(role)
    db.flush()

    OLD = {"prompt_version": "cv_match_v16"}                              # → v1.16.0, stale
    NEW = {"prompt_version": "holistic_v2", "engine_version": "2.1.0"}    # current, fresh
    _pending(db, org_id, role.id, _app(db, org_id, role.id, "a@x.test", OLD).id, "advance_to_interview")
    _pending(db, org_id, role.id, _app(db, org_id, role.id, "b@x.test", NEW).id, "advance_to_interview")
    _pending(db, org_id, role.id, _app(db, org_id, role.id, "c@x.test", OLD).id, "reject")
    _pending(
        db,
        org_id,
        role.id,
        _app(db, org_id, role.id, "taught@x.test", OLD).id,
        "advance_to_interview",
        status="reverted_for_feedback",
    )
    _pending(
        db,
        org_id,
        role.id,
        _app(db, org_id, role.id, "taught-fresh@x.test", NEW).id,
        "advance_to_interview",
        status="reverted_for_feedback",
    )
    snoozed = _pending(
        db,
        org_id,
        role.id,
        _app(db, org_id, role.id, "taught-snoozed@x.test", OLD).id,
        "advance_to_interview",
        status="reverted_for_feedback",
    )
    snoozed.snoozed_until = datetime.now(timezone.utc) + timedelta(hours=1)
    db.commit()

    # All types: three stale (pending advance + reject + taught advance); the
    # fresh pending row is excluded.
    r = client.get("/api/v1/agent-decisions/needs-reeval-count", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 3

    # Scoped to advance: the pending and taught stale decisions both count.
    r2 = client.get("/api/v1/agent-decisions/needs-reeval-count?type=advance", headers=headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["count"] == 2

    # Scoped to the role.
    r3 = client.get(f"/api/v1/agent-decisions/needs-reeval-count?role_id={role.id}", headers=headers)
    assert r3.json()["count"] == 3


def test_needs_reeval_count_zero_when_all_current(client, db, monkeypatch):
    monkeypatch.setattr(
        "app.services.cv_score_orchestrator._holistic_enabled_for", lambda a: True
    )
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(organization_id=org_id, name="R2", source="manual",
                agentic_mode_enabled=True, job_spec_text="hire")
    db.add(role)
    db.flush()
    NEW = {"prompt_version": "holistic_v2", "engine_version": "2.1.0"}
    _pending(db, org_id, role.id, _app(db, org_id, role.id, "d@x.test", NEW).id, "advance_to_interview")
    db.commit()

    r = client.get("/api/v1/agent-decisions/needs-reeval-count", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 0
