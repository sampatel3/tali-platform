"""P0.5: per-job hiring-team management API — CRUD, org-scoping, role gate."""
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import ROLE_RECRUITER, ROLE_VIEWER, User
from tests.conftest import auth_headers


def _org_id(db, email: str) -> int:
    return db.query(User).filter(User.email == email).first().organization_id


def _member(db, org_id, email="member@ht.test", role=ROLE_RECRUITER) -> User:
    u = User(
        email=email, hashed_password="x", is_active=True,
        is_superuser=False, is_verified=False, organization_id=org_id, role=role,
    )
    db.add(u)
    db.flush()
    return u


def test_hiring_team_add_list_upsert_remove(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = Role(organization_id=org_id, name="Backend Engineer")
    db.add(role)
    member = _member(db, org_id)
    db.commit()
    rid, uid = role.id, member.id

    # Starts empty.
    r = client.get(f"/api/v1/roles/{rid}/hiring-team", headers=headers)
    assert r.status_code == 200 and r.json() == []

    # Add a hiring manager.
    r = client.post(
        f"/api/v1/roles/{rid}/hiring-team",
        json={"user_id": uid, "team_role": "hiring_manager"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user_id"] == uid and body["team_role"] == "hiring_manager"
    assert body["email"] == "member@ht.test"

    r = client.get(f"/api/v1/roles/{rid}/hiring-team", headers=headers)
    assert len(r.json()) == 1

    # Re-posting the same user upserts the team role (no duplicate row).
    r = client.post(
        f"/api/v1/roles/{rid}/hiring-team",
        json={"user_id": uid, "team_role": "interviewer"},
        headers=headers,
    )
    assert r.status_code == 201 and r.json()["team_role"] == "interviewer"
    r = client.get(f"/api/v1/roles/{rid}/hiring-team", headers=headers)
    assert len(r.json()) == 1

    # Invalid team role rejected.
    r = client.post(
        f"/api/v1/roles/{rid}/hiring-team",
        json={"user_id": uid, "team_role": "boss"},
        headers=headers,
    )
    assert r.status_code == 422

    # Remove.
    r = client.delete(f"/api/v1/roles/{rid}/hiring-team/{uid}", headers=headers)
    assert r.status_code == 204
    r = client.get(f"/api/v1/roles/{rid}/hiring-team", headers=headers)
    assert r.json() == []


def test_hiring_team_cross_org_role_is_404(client, db):
    headers, _ = auth_headers(client)
    other = Organization(name="Other", slug="other-ht")
    db.add(other)
    db.flush()
    role = Role(organization_id=other.id, name="Not yours")
    db.add(role)
    db.commit()
    r = client.get(f"/api/v1/roles/{role.id}/hiring-team", headers=headers)
    assert r.status_code == 404


def test_hiring_team_add_member_from_other_org_is_404(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = Role(organization_id=org_id, name="Backend Engineer")
    db.add(role)
    other = Organization(name="Other2", slug="other2-ht")
    db.add(other)
    db.flush()
    outsider = _member(db, other.id, email="outsider@ht.test")
    db.commit()
    r = client.post(
        f"/api/v1/roles/{role.id}/hiring-team",
        json={"user_id": outsider.id, "team_role": "interviewer"},
        headers=headers,
    )
    assert r.status_code == 404


def test_hiring_team_write_requires_recruiter_or_admin(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    # Demote the caller to viewer — reads still work, writes 403.
    caller = db.query(User).filter(User.email == email).first()
    caller.role = ROLE_VIEWER
    role = Role(organization_id=org_id, name="Backend Engineer")
    db.add(role)
    member = _member(db, org_id)
    db.commit()

    r = client.get(f"/api/v1/roles/{role.id}/hiring-team", headers=headers)
    assert r.status_code == 200
    r = client.post(
        f"/api/v1/roles/{role.id}/hiring-team",
        json={"user_id": member.id, "team_role": "interviewer"},
        headers=headers,
    )
    assert r.status_code == 403
