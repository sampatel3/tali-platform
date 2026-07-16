"""API + service tests for role-level recruiter feedback notes.

Covers the create/list endpoints, the service-layer validation, scoping
to the caller's organization, and the agent-prompt rendering that
inlines recent notes into ``build_system_prompt`` output.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent_runtime import role_feedback_notes as svc
from app.agent_runtime.system_prompt import build_system_prompt
from app.models.organization import Organization
from app.models.role import Role
from app.models.role_feedback_note import RoleFeedbackNote
from tests.conftest import auth_headers


def _seed_role(db, *, org_name="FB Org"):
    org = Organization(name=org_name, slug=f"fb-{id(db)}-{org_name}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id, name="Backend Engineer", source="manual",
        agentic_mode_enabled=False, monthly_usd_budget_cents=0,
    )
    db.add(role); db.flush()
    return SimpleNamespace(org=org, role=role)


def test_create_note_rejects_empty(db):
    fixture = _seed_role(db)
    try:
        svc.create_note(
            db, organization_id=fixture.org.id, role_id=fixture.role.id, note="   ",
        )
    except ValueError as exc:
        assert "required" in str(exc)
    else:
        raise AssertionError("expected ValueError for empty note")


def test_list_notes_returns_newest_first(db):
    fixture = _seed_role(db)
    a = svc.create_note(
        db, organization_id=fixture.org.id, role_id=fixture.role.id,
        note="weighting too high on saas",
    )
    b = svc.create_note(
        db, organization_id=fixture.org.id, role_id=fixture.role.id,
        note="want more ngo backgrounds",
    )
    rows = svc.list_notes(db, role_id=fixture.role.id, limit=10)
    assert [r.id for r in rows] == [b.id, a.id]


def test_build_system_prompt_includes_recent_feedback(db):
    fixture = _seed_role(db)
    svc.create_note(
        db, organization_id=fixture.org.id, role_id=fixture.role.id,
        note="agent over-weighting recent SaaS experience",
    )
    db.commit()
    blocks = build_system_prompt(role=fixture.role, trigger_context="test")
    role_block = next(b for b in blocks if b["text"].startswith("ROLE:"))
    assert "RECRUITER FEEDBACK" in role_block["text"]
    assert "SaaS experience" in role_block["text"]


def test_endpoints_create_list_and_isolate_by_org(client):
    headers_a, _ = auth_headers(client, email="recruiter-a@example.com", organization_name="OrgA")
    headers_b, _ = auth_headers(client, email="recruiter-b@example.com", organization_name="OrgB")

    role_resp = client.post(
        "/api/v1/roles",
        json={"name": "Backend Engineer", "description": "desc"},
        headers=headers_a,
    )
    assert role_resp.status_code == 201, role_resp.text
    role_id = role_resp.json()["id"]

    # Empty list to start.
    empty = client.get(f"/api/v1/roles/{role_id}/feedback-notes", headers=headers_a)
    assert empty.status_code == 200
    assert empty.json() == []

    # Recruiter A authors a note.
    create = client.post(
        f"/api/v1/roles/{role_id}/feedback-notes",
        json={"note": "Cohort skews too senior — bias the agent toward mid-level applicants."},
        headers=headers_a,
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["note"].startswith("Cohort skews too senior")
    assert body["author_user_id"] is not None
    assert body["role_id"] == role_id

    # Listed back to the same recruiter.
    listed = client.get(f"/api/v1/roles/{role_id}/feedback-notes", headers=headers_a)
    assert listed.status_code == 200
    payload = listed.json()
    assert len(payload) == 1
    assert payload[0]["id"] == body["id"]

    # Recruiter B (different org) cannot see or write to the role.
    forbidden_list = client.get(f"/api/v1/roles/{role_id}/feedback-notes", headers=headers_b)
    assert forbidden_list.status_code == 404
    forbidden_post = client.post(
        f"/api/v1/roles/{role_id}/feedback-notes",
        json={"note": "shouldn't work"},
        headers=headers_b,
    )
    assert forbidden_post.status_code == 404


def test_endpoint_rejects_blank_note(client):
    headers, _ = auth_headers(client, email="blank-note@example.com", organization_name="BlankOrg")
    role_resp = client.post(
        "/api/v1/roles",
        json={"name": "Test", "description": "d"},
        headers=headers,
    )
    assert role_resp.status_code == 201
    role_id = role_resp.json()["id"]
    resp = client.post(
        f"/api/v1/roles/{role_id}/feedback-notes",
        json={"note": ""},
        headers=headers,
    )
    assert resp.status_code == 422


def test_model_persists_with_created_at_default(db):
    fixture = _seed_role(db)
    row = RoleFeedbackNote(
        organization_id=fixture.org.id, role_id=fixture.role.id, note="hello",
    )
    db.add(row); db.commit()
    db.refresh(row)
    assert row.id is not None
    assert row.created_at is not None
