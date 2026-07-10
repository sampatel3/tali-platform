"""POST /agent-decisions/{id}/re-evaluate — the engine-staleness branch.

Re-evaluating a decision whose score is from an OLD engine must trigger a
forced re-score on the current engine (so the score actually upgrades), NOT the
discard + agent-re-decide path used for input-change staleness (which would only
re-run the same stale score). Input-change staleness keeps the old behaviour.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.agent_decision import AgentDecision
from app.models.role import Role
from app.models.user import User
from tests.conftest import auth_headers


def _app(db, org_id, role_id, email, **extra):
    cand = Candidate(organization_id=org_id, email=email, full_name=email.split("@")[0])
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org_id,
        candidate_id=cand.id,
        role_id=role_id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        **extra,
    )
    db.add(app)
    db.flush()
    return app


def _pending(db, org_id, role_id, app_id):
    d = AgentDecision(
        organization_id=org_id,
        role_id=role_id,
        application_id=app_id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status="pending",
        reasoning="seed",
        confidence=0.9,
        model_version="m",
        prompt_version="p",
        idempotency_key=f"reeval-test:{app_id}",
    )
    db.add(d)
    db.flush()
    return d


def test_re_evaluate_old_engine_triggers_rescore(client, db, monkeypatch):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(
        organization_id=org_id, name="R", source="manual",
        agentic_mode_enabled=True, job_spec_text="hire an engineer",
    )
    db.add(role); db.flush()
    app = _app(
        db, org_id, role.id, "eng@x.test",
        cv_text="cv", cv_match_score=80.0,
        cv_match_details={"prompt_version": "cv_match_v16"},  # → engine v1.16.0
    )
    d = _pending(db, org_id, role.id, app.id)
    db.commit()

    enq = MagicMock(return_value=object())  # truthy job
    monkeypatch.setattr("app.services.cv_score_orchestrator.enqueue_score", enq)
    monkeypatch.setattr("app.services.cv_score_orchestrator.score_is_outdated", lambda a: True)

    resp = client.post(f"/api/v1/agent-decisions/{d.id}/re-evaluate", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["queued"] is True
    assert body["superseded"] == 0          # re-scored, not discarded
    assert "re-scor" in (body["detail"] or "").lower()

    # Forced full re-score (recruiter-directed), bypassing the cheap gate.
    assert enq.call_count == 1
    assert enq.call_args.kwargs["force"] is True
    assert enq.call_args.kwargs["bypass_pre_screen"] is True

    db.refresh(d)
    assert d.status == "pending"            # decision left intact for reconciliation


def test_feed_surfaces_rescore_in_flight(client, db):
    # A pending/running CvScoreJob for the candidate must surface as
    # rescore_in_flight on the decision payload — the queue greys that row
    # and freezes its actions until the fresh score lands.
    from app.models.cv_score_job import CvScoreJob

    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(
        organization_id=org_id, name="R3", source="manual",
        agentic_mode_enabled=True, job_spec_text="hire",
    )
    db.add(role); db.flush()
    app_hot = _app(db, org_id, role.id, "hot@x.test", cv_match_score=70.0)
    app_cold = _app(db, org_id, role.id, "cold@x.test", cv_match_score=71.0)
    _pending(db, org_id, role.id, app_hot.id)
    _pending(db, org_id, role.id, app_cold.id)
    db.add(CvScoreJob(application_id=app_hot.id, role_id=role.id, status="pending"))
    # A finished job must NOT flag the other candidate.
    db.add(CvScoreJob(application_id=app_cold.id, role_id=role.id, status="done"))
    db.commit()

    resp = client.get("/api/v1/agent-decisions?status=pending", headers=headers)
    assert resp.status_code == 200, resp.text
    by_app = {d["application_id"]: d for d in resp.json()}
    assert by_app[app_hot.id]["rescore_in_flight"] is True
    assert by_app[app_cold.id]["rescore_in_flight"] is False


def test_re_evaluate_input_change_still_discards(client, db, monkeypatch):
    # An app with no engine staleness (no cv_match_details) falls through to the
    # existing discard path; a paused role just discards without re-running.
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(
        organization_id=org_id, name="R2", source="manual",
        agentic_mode_enabled=True, job_spec_text="hire",
        agent_paused_at=datetime.now(timezone.utc),
    )
    db.add(role); db.flush()
    app = _app(db, org_id, role.id, "inp@x.test", cv_match_score=70.0)
    d = _pending(db, org_id, role.id, app.id)
    db.commit()

    enq = MagicMock()
    monkeypatch.setattr("app.services.cv_score_orchestrator.enqueue_score", enq)

    resp = client.post(f"/api/v1/agent-decisions/{d.id}/re-evaluate", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert enq.call_count == 0               # NOT a re-score
    assert body["superseded"] >= 1           # the stale decision was discarded
    assert body["queued"] is False           # role paused → not re-run

    db.refresh(d)
    assert d.status != "pending"
