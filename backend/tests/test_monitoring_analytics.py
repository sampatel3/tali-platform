"""Backing data for the consolidated Monitoring section.

Covers the new role/window scoping on /analytics/decisions-breakdown and the
human_review KPI block on /analytics/reporting-summary.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.models.user import User
from tests.conftest import auth_headers


def _app(db, org_id, role_id, email, *, stage=None, outcome="open", score=None):
    cand = Candidate(organization_id=org_id, email=email, full_name=email.split("@")[0])
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org_id, candidate_id=cand.id, role_id=role_id,
        status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
        application_outcome=outcome, source="manual",
        external_stage_normalized=stage, workable_stage=stage, taali_score_cache_100=score,
    )
    db.add(app)
    db.flush()
    return app


def _decision(db, org_id, role_id, app_id, *, decision_type="advance_to_interview",
              status="approved", created_at=None, human_disposition=None):
    d = AgentDecision(
        organization_id=org_id, role_id=role_id, application_id=app_id,
        decision_type=decision_type, recommendation=decision_type, status=status,
        reasoning="seed", confidence=0.9, model_version="m", prompt_version="p",
        idempotency_key=f"mon:{app_id}:{decision_type}:{status}:{created_at}",
        created_at=created_at or datetime.now(timezone.utc),
        human_disposition=human_disposition,
    )
    db.add(d)
    db.flush()
    return d


def test_decisions_breakdown_role_and_window_scope(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    now = datetime.now(timezone.utc)

    role_a = Role(organization_id=org_id, name="Backend", source="manual", agentic_mode_enabled=True)
    role_b = Role(organization_id=org_id, name="Frontend", source="manual", agentic_mode_enabled=True)
    db.add_all([role_a, role_b])
    db.flush()

    # Role A: one advance today, one advance 40 days ago.
    a1 = _app(db, org_id, role_a.id, "a1@x.test", stage="offer")
    _decision(db, org_id, role_a.id, a1.id, created_at=now)
    a2 = _app(db, org_id, role_a.id, "a2@x.test", stage="technical_interview")
    _decision(db, org_id, role_a.id, a2.id, created_at=now - timedelta(days=40))
    # Role B: one advance today.
    b1 = _app(db, org_id, role_b.id, "b1@x.test", stage="final_interview")
    _decision(db, org_id, role_b.id, b1.id, created_at=now)
    db.commit()

    # Org-wide, all-time: 3 advance decisions across 2 roles.
    allt = client.get("/api/v1/analytics/decisions-breakdown", headers=headers).json()
    assert allt["totals"]["decisions"]["total"] == 3
    assert {r["role_name"] for r in allt["roles"]} == {"Backend", "Frontend"}

    # Role-scoped: only Backend's 2.
    scoped = client.get(f"/api/v1/analytics/decisions-breakdown?role_id={role_a.id}", headers=headers).json()
    assert scoped["totals"]["decisions"]["total"] == 2
    assert [r["role_name"] for r in scoped["roles"]] == ["Backend"]

    # Windowed (last 10 days): drops the 40-day-old Backend decision → 2 total (a1 + b1).
    since = (now - timedelta(days=10)).date().isoformat()
    windowed = client.get(f"/api/v1/analytics/decisions-breakdown?date_from={since}", headers=headers).json()
    assert windowed["totals"]["decisions"]["total"] == 2
    assert windowed["totals"]["advance_conversion"]["advanced_total"] == 2
    assert windowed["window"]["label"].startswith("Last ")


def test_reporting_summary_human_review(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(organization_id=org_id, name="Backend", source="manual", agentic_mode_enabled=True)
    db.add(role)
    db.flush()

    # 2 approved, 1 overridden, 1 taught (reverted_for_feedback + taught disposition), 1 pending.
    specs = [
        ("approved", None), ("approved", None), ("overridden", None),
        ("reverted_for_feedback", "taught"), ("pending", None),
    ]
    for i, (status, disp) in enumerate(specs):
        app = _app(db, org_id, role.id, f"hr{i}@x.test")
        _decision(db, org_id, role.id, app.id, status=status, human_disposition=disp)
    db.commit()

    payload = client.get("/api/v1/analytics/reporting-summary", headers=headers).json()
    hr = payload["kpis"]["human_review"]
    # Resolved = not pending / reverted_for_feedback → the 2 approved + 1 overridden = 3.
    assert hr["resolved"] == 3
    assert hr["approved"] == 2
    assert hr["overridden"] == 1
    assert hr["taught"] == 1
    assert hr["override_rate_pct"] == round((1 / 3) * 100, 1)
