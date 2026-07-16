"""HTTP smoke tests for the Background jobs "Agents" aggregate endpoints.

  GET /api/v1/agent/panel       — pulse + KPIs + cards + 24h series + decision log
  GET /api/v1/agent/activity    — org-wide merged activity feed

Reuses the per-role activity seeding helper so both feeds run off the same
fixtures.
"""
from __future__ import annotations

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
