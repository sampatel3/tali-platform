"""HTTP smoke tests for the Background jobs "Agents" aggregate endpoints.

  GET /api/v1/agent/panel       — pulse + KPIs + cards + 24h series + decision log
  GET /api/v1/agent/activity    — org-wide merged activity feed

Reuses the per-role activity seeding helper (which also installs the
BigInteger-PK autoincrement shim SQLite needs) so both feeds run off the
same fixtures.
"""
from __future__ import annotations

from datetime import datetime, timezone

from tests.test_agent_activity_route import _attach_user_to_org, _seed_activity


def test_agent_panel_returns_aggregate(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_activity(org_name="Panel Org")
    _attach_user_to_org(email, seeded["org_id"])

    resp = client.get("/api/v1/agent/panel", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # The seeded role is agent-enabled and not paused → shows as a running card.
    assert body["kpis"]["agents_running"] >= 1
    names = [a["name"] for a in body["agents"]]
    assert "Activity Role" in names
    card = next(a for a in body["agents"] if a["name"] == "Activity Role")
    assert card["running"] is True
    assert card["activity"]["label"] in ("WORKING", "IDLE", "PAUSED")

    # 24 hourly buckets; the seeded run (30m ago) + decision (20m ago) land in window.
    assert len(body["timeseries"]["labels"]) == 24
    assert sum(body["timeseries"]["cycles"]) >= 1
    assert sum(body["timeseries"]["decisions"]) >= 1

    # Decision log carries the seeded decision with its role name.
    rec = body["recent_decisions"]
    assert any(d["decision_type"] == "advance_to_interview" for d in rec)
    assert any(d["role_name"] == "Activity Role" for d in rec)

    # Non-sensitive: no raw cost / model fields anywhere in the payload.
    assert "anthropic" not in resp.text.lower()
    assert "cost_usd" not in resp.text


def test_agent_panel_cards_show_effective_workspace_pause(client):
    from tests.conftest import TestingSessionLocal, auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_activity(org_name="Panel Workspace Hold")
    _attach_user_to_org(email, seeded["org_id"])

    sess = TestingSessionLocal()
    try:
        from app.models.organization import Organization

        org = sess.query(Organization).filter(Organization.id == seeded["org_id"]).one()
        org.agent_workspace_paused_at = datetime.now(timezone.utc)
        org.agent_workspace_paused_reason = "workspace paused by recruiter"
        sess.commit()
    finally:
        sess.close()

    resp = client.get("/api/v1/agent/panel", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    card = next(a for a in body["agents"] if a["role_id"] == seeded["role_id"])

    assert body["kpis"]["agents_running"] == 0
    assert body["kpis"]["agents_paused"] >= 1
    assert card["running"] is False
    assert card["workspace_paused"] is True
    assert card["role_paused"] is False
    assert card["pause_scope"] == "workspace"
    assert card["paused_reason"] == "workspace paused by recruiter"
    assert card["activity"] == {
        "label": "PAUSED",
        "text": "workspace paused by recruiter",
    }


def test_related_agent_card_reports_role_local_scoring_work(client):
    from app.models.candidate_application import CandidateApplication
    from app.models.role import ROLE_KIND_SISTER, Role
    from app.models.sister_role_evaluation import SisterRoleEvaluation
    from tests.conftest import TestingSessionLocal, auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_activity(org_name="Related Agent Work Org")
    _attach_user_to_org(email, seeded["org_id"])

    sess = TestingSessionLocal()
    try:
        transport = (
            sess.query(CandidateApplication)
            .filter(CandidateApplication.id == seeded["application_id"])
            .one()
        )
        related = Role(
            organization_id=seeded["org_id"],
            name="Related Scoring Role",
            source="manual",
            role_kind=ROLE_KIND_SISTER,
            ats_owner_role_id=seeded["role_id"],
            agentic_mode_enabled=True,
            monthly_usd_budget_cents=0,
        )
        sess.add(related)
        sess.flush()
        sess.add(
            SisterRoleEvaluation(
                organization_id=seeded["org_id"],
                role_id=related.id,
                candidate_id=transport.candidate_id,
                source_application_id=transport.id,
                ats_application_id=transport.id,
                status="pending",
                pipeline_stage="applied",
                pipeline_stage_source="initial_snapshot",
                application_outcome="open",
                application_outcome_source="initial_snapshot",
                membership_source="initial_snapshot",
                spec_fingerprint="related-panel-spec",
            )
        )
        sess.commit()
        related_id = int(related.id)
    finally:
        sess.close()

    response = client.get("/api/v1/agent/panel", headers=headers)
    assert response.status_code == 200, response.text
    card = next(item for item in response.json()["agents"] if item["role_id"] == related_id)
    assert card["activity"] == {"label": "WORKING", "text": "scoring 1 candidate"}


def test_org_activity_feed_labels_roles(client):
    from tests.conftest import auth_headers

    headers, email = auth_headers(client)
    seeded = _seed_activity(org_name="Panel Org Activity")
    _attach_user_to_org(email, seeded["org_id"])

    resp = client.get("/api/v1/agent/activity?limit=50", headers=headers)
    assert resp.status_code == 200, resp.text
    entries = resp.json()["entries"]
    # run + decision + event + needs_input
    assert len(entries) == 4
    decision = next(e for e in entries if e["kind"] == "decision")
    assert decision["role_name"] == "Activity Role"
    assert decision["candidate_name"] == "Ada Lovelace"


def test_agent_panel_isolated_by_org(client):
    """A user only sees their own org's agents — no cross-org leakage."""
    from tests.conftest import auth_headers

    headers, _ = auth_headers(client)
    _seed_activity(org_name="Other Panel Org")  # NOT attached to the test user

    panel = client.get("/api/v1/agent/panel", headers=headers)
    assert panel.status_code == 200
    assert all(a["name"] != "Activity Role" for a in panel.json()["agents"])

    activity = client.get("/api/v1/agent/activity", headers=headers)
    assert activity.status_code == 200
    assert activity.json()["entries"] == []
