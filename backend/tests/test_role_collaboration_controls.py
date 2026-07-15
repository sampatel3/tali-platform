"""Multi-recruiter controls for shared Role state."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.organization import Organization
from app.models.role import Role
from app.models.role_change_event import RoleChangeEvent
from app.models.user import User
from tests.conftest import auth_headers


def _owner_org_id(db, email: str) -> int:
    return int(db.query(User).filter(User.email == email).one().organization_id)


def test_role_patch_requires_and_advances_expected_version(client, db):
    headers, email = auth_headers(client)
    created = client.post(
        "/api/v1/roles", json={"name": "Platform Engineer"}, headers=headers
    ).json()
    assert created["version"] == 1

    missing = client.patch(
        f"/api/v1/roles/{created['id']}",
        json={"score_threshold": 72},
        headers=headers,
    )
    assert missing.status_code == 422

    saved = client.patch(
        f"/api/v1/roles/{created['id']}",
        json={"expected_version": 1, "score_threshold": 72},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2
    assert saved.json()["score_threshold"] == 72
    event = db.query(RoleChangeEvent).filter(
        RoleChangeEvent.role_id == created["id"]
    ).one()
    assert event.actor_user_id == db.query(User).filter(User.email == email).one().id
    assert event.from_version == 1
    assert event.to_version == 2
    assert event.changes["score_threshold"] == {"before": None, "after": 72}

    stale = client.patch(
        f"/api/v1/roles/{created['id']}",
        json={"expected_version": 1, "score_threshold": 10},
        headers=headers,
    )
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["code"] == "ROLE_VERSION_CONFLICT"
    assert detail["current_version"] == 2
    assert detail["current_role"]["score_threshold"] == 72
    assert detail["changed_by"]["email"] == email

    current = client.get(
        f"/api/v1/roles/{created['id']}", headers=headers
    ).json()
    assert current["version"] == 2
    assert current["score_threshold"] == 72

    history = client.get(
        f"/api/v1/roles/{created['id']}/change-events", headers=headers
    )
    assert history.status_code == 200
    assert history.json()[0]["actor"]["email"] == email


def test_role_patch_noop_does_not_create_a_phantom_version(client, db):
    headers, _ = auth_headers(client)
    created = client.post(
        "/api/v1/roles", json={"name": "No-op Role"}, headers=headers
    ).json()

    response = client.patch(
        f"/api/v1/roles/{created['id']}",
        json={"expected_version": created["version"]},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["version"] == created["version"]
    assert (
        db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == created["id"])
        .count()
        == 0
    )


def test_job_spec_rejects_stale_editor_without_overwriting(client, db):
    headers, _ = auth_headers(client)
    created = client.post(
        "/api/v1/roles", json={"name": "Data Engineer"}, headers=headers
    ).json()
    first_text = (
        "Build reliable data products for the analytics team. "
        "Candidates need Python, SQL, orchestration, and production ownership."
    )
    second_text = (
        "This stale draft would silently replace another recruiter's work "
        "without an optimistic concurrency guard in the save contract."
    )
    saved = client.put(
        f"/api/v1/roles/{created['id']}/job-spec",
        json={"expected_version": 1, "job_spec_text": first_text},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["role"]["version"] == 2
    event = db.query(RoleChangeEvent).filter(
        RoleChangeEvent.role_id == created["id"]
    ).one()
    serialized_changes = str(event.changes)
    assert first_text not in serialized_changes
    assert event.changes["job_spec_text"]["after"]["length"] == len(first_text)

    stale = client.put(
        f"/api/v1/roles/{created['id']}/job-spec",
        json={"expected_version": 1, "job_spec_text": second_text},
        headers=headers,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "ROLE_VERSION_CONFLICT"

    current = client.get(
        f"/api/v1/roles/{created['id']}", headers=headers
    ).json()
    assert current["job_spec_text"] == first_text
    assert current["version"] == 2


def test_pause_and_resume_reject_stale_agent_controls(client, db):
    headers, email = auth_headers(client)
    role = Role(
        organization_id=_owner_org_id(db, email),
        name="Shared Agent",
        source="manual",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
    )
    db.add(role)
    db.commit()

    paused = client.post(
        f"/api/v1/roles/{role.id}/agent/pause",
        json={"expected_version": 1},
        headers=headers,
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["paused"] is True
    assert paused.json()["version"] == 2

    stale_resume = client.post(
        f"/api/v1/roles/{role.id}/agent/resume",
        json={"expected_version": 1},
        headers=headers,
    )
    assert stale_resume.status_code == 409
    assert stale_resume.json()["detail"]["current_version"] == 2


def test_run_now_does_not_bypass_agent_off(client, db):
    headers, email = auth_headers(client)
    role = Role(
        organization_id=_owner_org_id(db, email),
        name="Off Agent",
        source="manual",
        agentic_mode_enabled=False,
    )
    db.add(role)
    db.commit()

    response = client.post(
        f"/api/v1/roles/{role.id}/agent/run-now", json={}, headers=headers
    )
    assert response.status_code == 409
    assert "not enabled" in response.text.lower()


def test_run_now_reports_workspace_hold_without_queueing(client, db, monkeypatch):
    from app.tasks import agent_tasks

    headers, email = auth_headers(client)
    org_id = _owner_org_id(db, email)
    org = db.query(Organization).filter(Organization.id == org_id).one()
    org.agent_workspace_paused_at = datetime.now(timezone.utc)
    org.agent_workspace_paused_reason = "workspace paused by recruiter"
    role = Role(
        organization_id=org_id,
        name="Workspace-held Agent",
        source="manual",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
    )
    db.add(role)
    db.commit()

    queued: list[dict] = []
    monkeypatch.setattr(
        agent_tasks.agent_manual_run,
        "delay",
        lambda **kwargs: queued.append(kwargs),
    )

    response = client.post(
        f"/api/v1/roles/{role.id}/agent/run-now", json={}, headers=headers
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "role_id": role.id,
        "queued": False,
        "task_id": None,
        "detail": "agent run blocked while the workspace agent is paused",
        "blocked": True,
        "pause_scope": "workspace",
    }
    assert queued == []


def test_permanent_delete_requires_the_latest_job_revision(client, db):
    headers, _ = auth_headers(client)
    created = client.post(
        "/api/v1/roles", json={"name": "Delete with care"}, headers=headers
    ).json()
    updated = client.patch(
        f"/api/v1/roles/{created['id']}",
        json={"expected_version": created["version"], "description": "newer edit"},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text

    missing = client.delete(f"/api/v1/roles/{created['id']}", headers=headers)
    assert missing.status_code == 422

    stale = client.delete(
        f"/api/v1/roles/{created['id']}",
        params={"expected_version": created["version"]},
        headers=headers,
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "ROLE_VERSION_CONFLICT"
    assert db.query(Role).filter(Role.id == created["id"]).one_or_none() is not None
