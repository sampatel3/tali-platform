"""GET /agent-decisions/export and GET /bias-audit/results.

Covers: CSV + JSON happy paths (decisions + linked feedback), org scoping,
date filtering, format validation, and the bias-audit results endpoint
including the empty case. Follows the sibling agent-decision route tests.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.decision_feedback import DecisionFeedback
from app.models.policy_version import PolicyVersion
from app.models.promotion_gate import BiasAuditResult
from app.models.role import Role
from app.models.user import User
from tests.conftest import auth_headers


def _org_id(db, email):
    return db.query(User).filter(User.email == email).first().organization_id


def _user_id(db, email):
    return db.query(User).filter(User.email == email).first().id


def _app(db, org_id, role_id, email):
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
    )
    db.add(app)
    db.flush()
    return app


def _decision(db, org_id, role_id, app_id, *, status="pending", dtype="advance_to_interview",
              created_at=None, resolved_by=None, evidence=None):
    d = AgentDecision(
        organization_id=org_id,
        role_id=role_id,
        application_id=app_id,
        decision_type=dtype,
        recommendation=dtype,
        status=status,
        reasoning="because reasons",
        confidence=0.87,
        model_version="claude-x",
        prompt_version="p1",
        evidence=evidence,
        resolved_by_user_id=resolved_by,
        input_fingerprint={"cv": "abc"},
        criteria_fingerprint="crit123",
        cv_fingerprint="cv123",
        idempotency_key=f"export-test:{app_id}:{status}:{dtype}",
    )
    if created_at is not None:
        d.created_at = created_at
    db.add(d)
    db.flush()
    return d


def test_export_json_happy_path_with_feedback(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    uid = _user_id(db, email)
    role = Role(organization_id=org_id, name="Backend", source="manual")
    db.add(role)
    db.flush()

    app = _app(db, org_id, role.id, "a@x.test")
    d = _decision(
        db, org_id, role.id, app.id, status="overridden", resolved_by=uid,
        evidence={"top": ["signal-1"]},
    )
    # Linked teach feedback the export should join.
    fb = DecisionFeedback(
        decision_id=d.id,
        reviewer_id=uid,
        organization_id=org_id,
        role_id=role.id,
        failure_mode="wrong_threshold",
        correction_text="too strict",
        scope="decision",
        attributed_to="cv_scoring",
    )
    db.add(fb)
    db.flush()
    d.feedback_id = fb.id
    db.commit()

    resp = client.get("/api/v1/agent-decisions/export?format=json", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1
    assert body["truncated"] is False
    row = body["rows"][0]
    assert row["id"] == d.id
    assert row["decision_type"] == "advance_to_interview"
    assert row["status"] == "overridden"
    assert row["resolved_by_email"] == email
    assert row["confidence"] == 0.87
    assert row["criteria_fingerprint"] == "crit123"
    # Evidence + fingerprint serialised as JSON strings.
    assert '"signal-1"' in row["evidence"]
    assert '"cv"' in row["input_fingerprint"]
    # Joined feedback fields.
    assert row["feedback_failure_mode"] == "wrong_threshold"
    assert row["feedback_attributed_to"] == "cv_scoring"
    assert row["feedback_correction_text"] == "too strict"
    assert row["feedback_cosigned"] is False


def test_export_csv_happy_path(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = Role(organization_id=org_id, name="Backend", source="manual")
    db.add(role)
    db.flush()
    app = _app(db, org_id, role.id, "b@x.test")
    d = _decision(db, org_id, role.id, app.id)
    db.commit()

    resp = client.get("/api/v1/agent-decisions/export?format=csv", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["x-export-truncated"] == "false"
    assert resp.headers["x-export-count"] == "1"

    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["id"] == str(d.id)
    assert rows[0]["reasoning"] == "because reasons"
    assert rows[0]["model_version"] == "claude-x"


def test_export_is_org_scoped(client, db):
    headers_a, email_a = auth_headers(client)
    org_a = _org_id(db, email_a)
    role_a = Role(organization_id=org_a, name="A", source="manual")
    db.add(role_a)
    db.flush()
    app_a = _app(db, org_a, role_a.id, "own@x.test")
    _decision(db, org_a, role_a.id, app_a.id)
    # Commit before the next HTTP registration so the write lock is released
    # (the second auth_headers goes through a separate connection).
    db.commit()

    # A second org's decision must not leak.
    headers_b, email_b = auth_headers(client)
    org_b = _org_id(db, email_b)
    role_b = Role(organization_id=org_b, name="B", source="manual")
    db.add(role_b)
    db.flush()
    app_b = _app(db, org_b, role_b.id, "other@x.test")
    _decision(db, org_b, role_b.id, app_b.id)
    db.commit()

    resp = client.get("/api/v1/agent-decisions/export?format=json", headers=headers_a)
    assert resp.status_code == 200, resp.text
    app_ids = {r["application_id"] for r in resp.json()["rows"]}
    assert app_ids == {app_a.id}


def test_export_date_filter(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = Role(organization_id=org_id, name="Backend", source="manual")
    db.add(role)
    db.flush()

    old_app = _app(db, org_id, role.id, "old@x.test")
    new_app = _app(db, org_id, role.id, "new@x.test")
    old_dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    new_dt = datetime(2026, 6, 15, tzinfo=timezone.utc)
    _decision(db, org_id, role.id, old_app.id, created_at=old_dt)
    _decision(db, org_id, role.id, new_app.id, created_at=new_dt)
    db.commit()

    resp = client.get(
        "/api/v1/agent-decisions/export?format=json&from=2026-06-01&to=2026-06-30",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    app_ids = {r["application_id"] for r in resp.json()["rows"]}
    assert app_ids == {new_app.id}


def test_export_rejects_bad_format(client, db):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/agent-decisions/export?format=xml", headers=headers)
    assert resp.status_code == 422


def test_export_rejects_bad_date(client, db):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/agent-decisions/export?from=not-a-date", headers=headers)
    assert resp.status_code == 422


def test_export_requires_auth(client, db):
    resp = client.get("/api/v1/agent-decisions/export")
    assert resp.status_code in (401, 403)


def test_bias_audit_results_empty(client, db):
    headers, _ = auth_headers(client)
    resp = client.get("/api/v1/bias-audit/results", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_bias_audit_results_happy_path_and_scoping(client, db):
    headers_a, email_a = auth_headers(client)
    org_a = _org_id(db, email_a)
    role = Role(organization_id=org_a, name="Backend", source="manual")
    db.add(role)
    db.flush()

    # PolicyVersion / BiasAuditResult use BigInteger PKs that SQLite does not
    # autoincrement, so assign ids explicitly in tests.
    pv = PolicyVersion(
        id=1, organization_id=org_a, role_id=role.id, model_kind="logistic_pooled",
        model_json={"w": [1]}, status="live",
    )
    db.add(pv)
    db.flush()
    bar = BiasAuditResult(
        id=1,
        policy_version_id=pv.id,
        passed=False,
        metrics_json={"gender": {"F": {"selection_rate": 0.4}, "M": {"selection_rate": 0.6}}},
        violations_json=["disparate_impact:gender"],
    )
    db.add(bar)
    # Commit first-org rows before the next HTTP registration (release lock).
    db.commit()

    # Other org's audit must not leak.
    headers_b, email_b = auth_headers(client)
    org_b = _org_id(db, email_b)
    pv_b = PolicyVersion(
        id=2, organization_id=org_b, role_id=None, model_kind="logistic_pooled",
        model_json={"w": [2]}, status="live",
    )
    db.add(pv_b)
    db.flush()
    db.add(BiasAuditResult(id=2, policy_version_id=pv_b.id, passed=True, metrics_json={"x": 1}))
    db.commit()

    resp = client.get("/api/v1/bias-audit/results", headers=headers_a)
    assert resp.status_code == 200, resp.text
    results = resp.json()
    assert len(results) == 1
    r = results[0]
    assert r["policy_version_id"] == pv.id
    assert r["role_id"] == role.id
    assert r["passed"] is False
    assert r["metrics"]["gender"]["F"]["selection_rate"] == 0.4
    assert r["violations"] == ["disparate_impact:gender"]
