"""Enabling agentic mode on a role auto-stars it for the periodic sync.

Rationale: agent-on roles need the periodic Workable fetch (comments,
activities, questionnaire answers) running so the agent's pre-screen
and scoring see fresh signal. Forcing the recruiter to remember to
click both the agent toggle AND the star is bad UX and easy to miss,
so we tie the two together.

One-way: disabling the agent does NOT unstar (star is sticky, can be
turned off independently).

We patch ``surface_activation_questions`` to a no-op because the
activation checklist inserts ``agent_needs_input`` rows whose
BigInteger PK doesn't autoincrement in SQLite test mode. The auto-star
logic runs BEFORE the checklist surface, so this doesn't affect what
we're testing.
"""

from __future__ import annotations

from unittest.mock import patch

from tests.conftest import auth_headers


def _create_role_via_api(client, headers, name="Test Role") -> dict:
    resp = client.post("/api/v1/roles", json={"name": name}, headers=headers)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def test_enabling_agentic_mode_auto_stars_role(client):
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Agent Auto-Star Target")
    assert role.get("starred_for_auto_sync") is False
    assert role.get("agentic_mode_enabled") is False

    # Activating the agent requires a budget; PATCH both together.
    with patch(
        "app.services.agent_activation_checklist.surface_activation_questions",
        return_value=None,
    ):
        patch_resp = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={"agentic_mode_enabled": True, "monthly_usd_budget_cents": 5000},
            headers=headers,
        )
    assert patch_resp.status_code == 200, patch_resp.text
    body = patch_resp.json()
    assert body["agentic_mode_enabled"] is True
    assert body["starred_for_auto_sync"] is True


def test_disabling_agentic_mode_leaves_star_in_place(client):
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Agent Toggle-Off Target")

    # Turn on (auto-stars).
    with patch(
        "app.services.agent_activation_checklist.surface_activation_questions",
        return_value=None,
    ):
        on = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={"agentic_mode_enabled": True, "monthly_usd_budget_cents": 5000},
            headers=headers,
        )
    assert on.status_code == 200
    assert on.json()["starred_for_auto_sync"] is True

    # Turn off — star must remain (sticky).
    off = client.patch(
        f"/api/v1/roles/{role['id']}",
        json={"agentic_mode_enabled": False},
        headers=headers,
    )
    assert off.status_code == 200, off.text
    body = off.json()
    assert body["agentic_mode_enabled"] is False
    assert body["starred_for_auto_sync"] is True


def test_enabling_agent_on_already_starred_role_is_idempotent(client):
    headers, _ = auth_headers(client)
    role = _create_role_via_api(client, headers, name="Pre-starred Target")

    star = client.post(f"/api/v1/roles/{role['id']}/star", headers=headers)
    assert star.status_code == 200

    with patch(
        "app.services.agent_activation_checklist.surface_activation_questions",
        return_value=None,
    ):
        patch_resp = client.patch(
            f"/api/v1/roles/{role['id']}",
            json={"agentic_mode_enabled": True, "monthly_usd_budget_cents": 5000},
            headers=headers,
        )
    assert patch_resp.status_code == 200, patch_resp.text
    body = patch_resp.json()
    assert body["agentic_mode_enabled"] is True
    assert body["starred_for_auto_sync"] is True
