"""Coverage for GET /analytics/decisions-breakdown (by-role analytics).

Locks in the three questions the Hub by-role table answers:
  a) decisions made + approved, grouped by role/type
  b) where advanced candidates now sit in Workable (live snapshot)
  c) how many advance decisions reached final interview / offer / hired
Plus the headline-score stats (avg / median).
"""
from __future__ import annotations

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.models.user import User
from tests.conftest import auth_headers


def _candidate(db, org_id, email):
    cand = Candidate(organization_id=org_id, email=email, full_name=email.split("@")[0])
    db.add(cand)
    db.flush()
    return cand


def _application(db, org_id, role_id, candidate_id, *, stage, outcome="open", score=None, disqualified=None):
    app = CandidateApplication(
        organization_id=org_id,
        candidate_id=candidate_id,
        role_id=role_id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome=outcome,
        source="manual",
        external_stage_normalized=stage,
        workable_stage=stage,
        taali_score_cache_100=score,
        workable_disqualified=disqualified,
    )
    db.add(app)
    db.flush()
    return app


def _decision(db, org_id, role_id, application_id, *, decision_type, status):
    decision = AgentDecision(
        organization_id=org_id,
        role_id=role_id,
        application_id=application_id,
        decision_type=decision_type,
        recommendation=decision_type,
        status=status,
        reasoning="seed",
        confidence=0.9,
        model_version="m",
        prompt_version="p",
        idempotency_key=f"test:{application_id}:{decision_type}:{status}",
    )
    db.add(decision)
    db.flush()
    return decision


def test_decisions_breakdown_by_role(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id

    role = Role(organization_id=org_id, name="Backend", source="manual", agentic_mode_enabled=True)
    db.add(role)
    db.flush()

    # A: approved advance, now at final interview
    a = _application(db, org_id, role.id, _candidate(db, org_id, "a@x.test").id,
                     stage="final_interview", score=82.0)
    _decision(db, org_id, role.id, a.id, decision_type="advance_to_interview", status="approved")
    # B: approved advance, now at offer
    b = _application(db, org_id, role.id, _candidate(db, org_id, "b@x.test").id,
                     stage="offer", score=75.0)
    _decision(db, org_id, role.id, b.id, decision_type="advance_to_interview", status="approved")
    # C: approved advance, hired
    c = _application(db, org_id, role.id, _candidate(db, org_id, "c@x.test").id,
                     stage="hired", outcome="hired", score=90.0)
    _decision(db, org_id, role.id, c.id, decision_type="advance_to_interview", status="approved")
    # D: approved reject, applied + rejected outcome
    d = _application(db, org_id, role.id, _candidate(db, org_id, "d@x.test").id,
                     stage="applied", outcome="rejected", score=30.0)
    _decision(db, org_id, role.id, d.id, decision_type="reject", status="approved")
    # E: PENDING advance — must not count toward approved/advanced
    e = _application(db, org_id, role.id, _candidate(db, org_id, "e@x.test").id,
                     stage="applied", score=60.0)
    _decision(db, org_id, role.id, e.id, decision_type="advance_to_interview", status="pending")
    db.commit()

    resp = client.get("/api/v1/analytics/decisions-breakdown", headers=headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()

    totals = payload["totals"]
    dec = totals["decisions"]
    assert dec["total"] == 5
    assert dec["approved"] == 4
    assert dec["by_type"]["advance_to_interview"] == {"total": 4, "approved": 3}
    assert dec["by_type"]["reject"] == {"total": 1, "approved": 1}

    conv = totals["advance_conversion"]
    assert conv["advanced_total"] == 3  # A, B, C (approved advance only)
    assert conv["reached_final_interview"] == 3  # final + offer + hired all count
    assert conv["reached_offer"] == 2  # offer + hired
    assert conv["hired"] == 1
    assert conv["rejected"] == 0
    assert conv["by_stage"] == {"final_interview": 1, "offer": 1, "hired": 1}

    stages = totals["workable_stages"]
    assert stages["final_interview"] == 1
    assert stages["offer"] == 1
    assert stages["hired"] == 1
    assert stages["applied"] == 2  # D + E

    score = totals["score_stats"]
    assert score["count"] == 5
    assert score["avg"] == 67.4  # (82+75+90+30+60)/5
    assert score["median"] == 75.0

    assert len(payload["roles"]) == 1
    role_row = payload["roles"][0]
    assert role_row["role_name"] == "Backend"
    assert role_row["decisions"]["approved"] == 4
    assert role_row["advance_conversion"]["hired"] == 1
    assert role_row["score_stats"]["median"] == 75.0


def test_decisions_breakdown_empty_org(client, db):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/analytics/decisions-breakdown", headers=headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["roles"] == []
    assert payload["totals"]["decisions"]["total"] == 0
    assert payload["totals"]["advance_conversion"]["advanced_total"] == 0
    assert payload["totals"]["score_stats"]["count"] == 0
