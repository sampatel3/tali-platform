"""Coverage for the two new Analytics-page aggregation endpoints:

  GET /analytics/decision-trend     — monthly override / agreement rate over
                                       RESOLVED agent decisions (pending/sent-
                                       back excluded), org/role scoped.
  GET /analytics/threshold-history  — a role's score-threshold change history
                                       from the persisted ThresholdCalibration
                                       rows; has_history=false + a single
                                       current-threshold entry when none exist
                                       (never fabricates past changes).
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.models.threshold_calibration import (
    STATUS_ACTIVE,
    STATUS_SUPERSEDED,
    ThresholdCalibration,
)
from app.models.user import User
from tests.conftest import auth_headers


_seed_counter = {"n": 0}


def _seed_decision(db, org_id, role_id, app_id, *, status, when):
    _seed_counter["n"] += 1
    d = AgentDecision(
        organization_id=org_id,
        role_id=role_id,
        application_id=app_id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status=status,
        reasoning="seed",
        confidence=0.9,
        model_version="m",
        prompt_version="p",
        idempotency_key=f"t:{app_id}:{status}:{when.isoformat()}:{_seed_counter['n']}",
    )
    db.add(d)
    db.flush()
    # created_at has a server default; override to land in a specific month.
    d.created_at = when
    db.flush()
    return d


def _app(db, org_id, role_id):
    cand = Candidate(organization_id=org_id, email=f"c{id(object())}@x.test", full_name="C")
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org_id,
        candidate_id=cand.id,
        role_id=role_id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        source="manual",
    )
    db.add(app)
    db.flush()
    return app


def test_decision_trend_override_and_agreement(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(organization_id=org_id, name="Backend", source="manual", agentic_mode_enabled=True)
    db.add(role)
    db.flush()
    app = _app(db, org_id, role.id)

    now = datetime.now(timezone.utc)
    this_month = now.replace(day=1, hour=12, minute=0, second=0, microsecond=0)

    # This month: 4 resolved (1 overridden, 2 approved, 1 expired) + 1 pending
    # (must be ignored). Override rate = 1/4 = 25%, agreement = 75%.
    _seed_decision(db, org_id, role.id, app.id, status="overridden", when=this_month)
    _seed_decision(db, org_id, role.id, app.id, status="approved", when=this_month)
    _seed_decision(db, org_id, role.id, app.id, status="approved", when=this_month)
    _seed_decision(db, org_id, role.id, app.id, status="expired", when=this_month)
    _seed_decision(db, org_id, role.id, app.id, status="pending", when=this_month)
    db.commit()

    resp = client.get("/api/v1/analytics/decision-trend", headers=headers)
    assert resp.status_code == 200, resp.text
    months = resp.json()["months"]
    assert len(months) == 6  # trailing 6 calendar months, oldest first
    current = months[-1]
    assert current["decisions"] == 4  # pending excluded
    assert current["override_rate_pct"] == 25.0
    assert current["agreement_rate_pct"] == 75.0
    # A month with no decisions reports 0 across the board (no fabrication).
    assert months[0]["decisions"] == 0
    assert months[0]["override_rate_pct"] == 0.0


def test_decision_trend_empty_org(client, db):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/analytics/decision-trend", headers=headers)
    assert resp.status_code == 200, resp.text
    months = resp.json()["months"]
    assert len(months) == 6
    assert all(m["decisions"] == 0 for m in months)


def test_threshold_history_real_history(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(organization_id=org_id, name="Senior AWS", source="manual",
                score_threshold=55, auto_reject_threshold_mode="manual")
    db.add(role)
    db.flush()

    # A superseded then an active calibration row = genuine change history.
    older = ThresholdCalibration(
        organization_id=org_id, role_id=role.id, scope="role",
        learned_threshold=60.0, metric_name="youden_j", status=STATUS_SUPERSEDED,
        n_positive=12, n_negative=20,
        activated_at=datetime(2026, 3, 2, tzinfo=timezone.utc),
    )
    newer = ThresholdCalibration(
        organization_id=org_id, role_id=role.id, scope="role",
        learned_threshold=55.0, metric_name="youden_j", status=STATUS_ACTIVE,
        n_positive=30, n_negative=18,
        activated_at=datetime(2026, 4, 18, tzinfo=timezone.utc),
    )
    db.add_all([older, newer])
    db.commit()

    resp = client.get(f"/api/v1/analytics/threshold-history?role_id={role.id}", headers=headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["has_history"] is True
    assert payload["role_name"] == "Senior AWS"
    entries = payload["entries"]
    assert len(entries) == 2
    # Newest first.
    assert entries[0]["threshold"] == 55.0
    assert entries[1]["threshold"] == 60.0
    assert "youden j" in entries[0]["note"]


def test_threshold_history_no_history_fallback(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(organization_id=org_id, name="No Calib", source="manual",
                score_threshold=42, auto_reject_threshold_mode="manual")
    db.add(role)
    db.commit()

    resp = client.get(f"/api/v1/analytics/threshold-history?role_id={role.id}", headers=headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["has_history"] is False
    assert len(payload["entries"]) == 1
    only = payload["entries"][0]
    assert only["at"] is None  # no fabricated timestamp
    assert only["threshold"] == 42.0  # manual pin resolves through
    assert payload["current_threshold"] == 42.0


def test_threshold_history_role_not_found(client, db):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/analytics/threshold-history?role_id=999999", headers=headers)
    assert resp.status_code == 404, resp.text
