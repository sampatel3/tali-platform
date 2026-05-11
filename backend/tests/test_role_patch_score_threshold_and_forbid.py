"""PATCH /roles/{id} actually applies score_threshold; RoleUpdate rejects unknown fields.

Codex #84 + #107.
"""

from __future__ import annotations

from app.models.role import Role
from app.models.user import User

from .conftest import auth_headers


def _seed_role(db, *, org_id: int, score_threshold: int | None = None) -> Role:
    role = Role(
        organization_id=org_id,
        name=f"Backend {id(db)}",
        source="manual",
        score_threshold=score_threshold,
    )
    db.add(role)
    db.flush()
    db.commit()
    return role


def _current_user(db) -> User:
    return db.query(User).order_by(User.id.desc()).first()


def test_patch_role_applies_score_threshold(db, client):
    headers, _ = auth_headers(client, organization_name="ScoreOrg")
    me = _current_user(db)
    role = _seed_role(db, org_id=me.organization_id, score_threshold=60)

    resp = client.patch(
        f"/api/v1/roles/{role.id}",
        json={"score_threshold": 75},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["score_threshold"] == 75

    db.expire(role)
    assert role.score_threshold == 75


def test_patch_role_can_clear_score_threshold(db, client):
    headers, _ = auth_headers(client, organization_name="ScoreOrg2")
    me = _current_user(db)
    role = _seed_role(db, org_id=me.organization_id, score_threshold=70)

    resp = client.patch(
        f"/api/v1/roles/{role.id}",
        json={"score_threshold": None},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    db.expire(role)
    assert role.score_threshold is None


def test_patch_role_rejects_unknown_field(db, client):
    headers, _ = auth_headers(client, organization_name="ForbidOrg")
    me = _current_user(db)
    role = _seed_role(db, org_id=me.organization_id)

    resp = client.patch(
        f"/api/v1/roles/{role.id}",
        json={"additional_requirements": "this key was retired in alembic 068"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


def test_create_role_rejects_unknown_field(db, client):
    headers, _ = auth_headers(client, organization_name="ForbidOrg2")

    resp = client.post(
        "/api/v1/roles",
        json={
            "name": "Test Role",
            "additional_requirements": "retired key",
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
