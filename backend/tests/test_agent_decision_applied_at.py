"""GET /agent-decisions — ``applied_at`` freshness field.

The Hub's decision cards show when the candidate applied. The payload
resolves it per application: the application's own Workable created_at
first (per-application, survives multi-role candidates), then the
candidate-level copy (legacy rows synced before the column existed),
then the local application created_at (manual sources).
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.models.user import User
from tests.conftest import auth_headers

APP_APPLIED = datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc)
CAND_APPLIED = datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)


def _seed(db, org_id, role_id, email, *, app_workable=None, cand_workable=None):
    cand = Candidate(
        organization_id=org_id,
        email=email,
        full_name=email.split("@")[0],
        workable_created_at=cand_workable,
    )
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
        workable_created_at=app_workable,
    )
    db.add(app)
    db.flush()
    d = AgentDecision(
        organization_id=org_id,
        role_id=role_id,
        application_id=app.id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status="pending",
        reasoning="seed",
        confidence=0.9,
        model_version="m",
        prompt_version="p",
        idempotency_key=f"appliedat-test:{app.id}",
    )
    db.add(d)
    db.flush()
    return app, d


def _fetch(client, headers, application_id):
    res = client.get(
        f"/api/v1/agent-decisions?application_id={application_id}", headers=headers
    )
    assert res.status_code == 200, res.text
    rows = res.json()
    assert len(rows) == 1
    return rows[0]


def test_applied_at_fallback_chain(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(organization_id=org_id, name="Backend", source="manual", agentic_mode_enabled=True)
    db.add(role)
    db.flush()

    # 1. Application-level Workable date wins over the candidate-level copy.
    app_a, _ = _seed(
        db, org_id, role.id, "a@x.test",
        app_workable=APP_APPLIED, cand_workable=CAND_APPLIED,
    )
    # 2. Legacy row: only the candidate-level copy exists.
    app_b, _ = _seed(db, org_id, role.id, "b@x.test", cand_workable=CAND_APPLIED)
    # 3. Manual source: falls back to the local application created_at.
    app_c, _ = _seed(db, org_id, role.id, "c@x.test")
    db.commit()

    row_a = _fetch(client, headers, app_a.id)
    assert row_a["applied_at"] is not None
    assert row_a["applied_at"].startswith("2026-06-12")

    row_b = _fetch(client, headers, app_b.id)
    assert row_b["applied_at"] is not None
    assert row_b["applied_at"].startswith("2026-05-01")

    row_c = _fetch(client, headers, app_c.id)
    # created_at is stamped by the DB at insert; just prove the fallback fires.
    assert row_c["applied_at"] is not None
