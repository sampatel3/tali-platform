"""Coverage for GET /analytics/activity-timeseries.

Locks in the daily decision buckets, the pending-backlog curve (which must
reconcile with the Home tab badge), and the Workable-error requeue callout.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.agent_decision import AgentDecision
from app.models.agent_needs_input import AgentNeedsInput
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.models.user import User
from tests.conftest import auth_headers


def _now():
    return datetime.now(timezone.utc)


def _candidate(db, org_id, email):
    c = Candidate(organization_id=org_id, email=email, full_name=email.split("@")[0])
    db.add(c)
    db.flush()
    return c


def _app(db, org_id, role_id, candidate_id):
    a = CandidateApplication(
        organization_id=org_id,
        candidate_id=candidate_id,
        role_id=role_id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(a)
    db.flush()
    return a


def _decision(db, org_id, role_id, app_id, *, decision_type, status, created_at, resolved_at=None, resolution_note=None):
    d = AgentDecision(
        organization_id=org_id,
        role_id=role_id,
        application_id=app_id,
        decision_type=decision_type,
        recommendation=decision_type,
        status=status,
        reasoning="seed",
        confidence=0.9,
        model_version="m",
        prompt_version="p",
        idempotency_key=f"ts:{app_id}:{decision_type}:{created_at.isoformat()}",
        created_at=created_at,
        resolved_at=resolved_at,
        resolution_note=resolution_note,
    )
    db.add(d)
    db.flush()
    return d


def test_activity_timeseries(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(organization_id=org_id, name="Backend", source="manual", agentic_mode_enabled=True)
    db.add(role)
    db.flush()
    now = _now()

    # D1: today, advance, pending (open)
    a1 = _app(db, org_id, role.id, _candidate(db, org_id, "a@x.test").id)
    _decision(db, org_id, role.id, a1.id, decision_type="advance_to_interview", status="pending", created_at=now)
    # D2: 5 days ago, reject, approved (resolved 5 days ago)
    a2 = _app(db, org_id, role.id, _candidate(db, org_id, "b@x.test").id)
    _decision(db, org_id, role.id, a2.id, decision_type="reject", status="approved",
              created_at=now - timedelta(days=5), resolved_at=now - timedelta(days=5))
    # D3: 10 days ago, advance, pending (still open) — contributes to backlog all the way to today
    a3 = _app(db, org_id, role.id, _candidate(db, org_id, "c@x.test").id)
    _decision(db, org_id, role.id, a3.id, decision_type="advance_to_interview", status="pending",
              created_at=now - timedelta(days=10))
    # D4: 2 days ago, advance, pending, bounced back by a Workable error
    a4 = _app(db, org_id, role.id, _candidate(db, org_id, "d@x.test").id)
    _decision(db, org_id, role.id, a4.id, decision_type="advance_to_interview", status="pending",
              created_at=now - timedelta(days=2),
              resolution_note="Returned to queue: Workable writeback failed (api_error). 429 rate limit")
    # Open agent question (the other half of the badge)
    db.add(AgentNeedsInput(id=1, organization_id=org_id, role_id=role.id, kind="monthly_budget_missing",
                           prompt="?", created_at=now - timedelta(days=3)))

    # A removed candidate's structured question is unavailable everywhere:
    # it must affect neither historical backlog nor the current pending count.
    removed_app = _app(
        db,
        org_id,
        role.id,
        _candidate(db, org_id, "removed@x.test").id,
    )
    db.add(
        AgentNeedsInput(
            id=2,
            organization_id=org_id,
            role_id=role.id,
            kind="candidate_tie_break",
            subject_id=removed_app.id,
            prompt="Private removed-candidate question",
            created_at=now - timedelta(days=20),
        )
    )
    _decision(
        db,
        org_id,
        role.id,
        removed_app.id,
        decision_type="resend_assessment_invite",
        status="pending",
        created_at=now - timedelta(days=20),
        resolution_note=(
            "Returned to queue: Workable writeback failed "
            "(private removed candidate)."
        ),
    )
    removed_app.deleted_at = now

    # Second role + a pending decision today — exercises role filtering.
    role2 = Role(organization_id=org_id, name="Other", source="manual", agentic_mode_enabled=True)
    db.add(role2)
    db.flush()
    a5 = _app(db, org_id, role2.id, _candidate(db, org_id, "e@x.test").id)
    _decision(db, org_id, role2.id, a5.id, decision_type="reject", status="pending", created_at=now)
    db.commit()

    resp = client.get("/api/v1/analytics/activity-timeseries?days=30", headers=headers)
    assert resp.status_code == 200, resp.text
    p = resp.json()

    assert len(p["series"]) == 30
    today = p["series"][-1]
    # Backlog today = D1, D3, D4 (role1 pending) + D5 (role2 pending) + 1 open question = 5
    assert today["backlog"] == 5
    assert today["created"] == 2  # D1 (advance) + D5 (reject)
    assert today["by_type"].get("advance_to_interview") == 1
    assert today["by_type"].get("reject") == 1
    assert p["series"][14]["backlog"] == 0

    five_days_ago = p["series"][24]  # index 29-5
    assert five_days_ago["resolved"] == 1  # D2
    assert five_days_ago["by_type"].get("reject") == 1

    assert p["pending_now"] == {
        "decisions": 4, "questions": 1, "total": 5,
        "by_type": {"advance_to_interview": 3, "reject": 1},
    }
    assert "resend_assessment_invite" not in p["pending_now"]["by_type"]
    assert set(p["decision_types"]) >= {"advance_to_interview", "reject"}
    assert "resend_assessment_invite" not in p["decision_types"]

    assert p["workable_errors"]["total"] == 1
    assert len(p["workable_errors"]["by_role"]) == 1
    err = p["workable_errors"]["by_role"][0]
    assert err["count"] == 1
    assert err["role_name"] == "Backend"
    assert err["example"].startswith("Returned to queue")

    # Role-scoped: only role1's rows.
    r2 = client.get(f"/api/v1/analytics/activity-timeseries?days=30&role_id={role.id}", headers=headers)
    p2 = r2.json()
    assert p2["pending_now"] == {
        "decisions": 3, "questions": 1, "total": 4,  # D1, D3, D4 + question
        "by_type": {"advance_to_interview": 3},
    }
    today2 = p2["series"][-1]
    assert today2["backlog"] == 4  # D1, D3, D4 + question
    assert today2["created"] == 1  # D1 only
    assert p2["workable_errors"]["total"] == 1


def test_activity_timeseries_empty_org(client, db):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/analytics/activity-timeseries", headers=headers)
    assert resp.status_code == 200, resp.text
    p = resp.json()
    assert len(p["series"]) == 30
    assert all(row["created"] == 0 and row["backlog"] == 0 for row in p["series"])
    assert p["pending_now"]["total"] == 0
    assert p["pending_now"]["by_type"] == {}
    assert p["workable_errors"]["total"] == 0
