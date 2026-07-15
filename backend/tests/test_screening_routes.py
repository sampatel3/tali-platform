"""Recruiter screening-question CRUD routes (authed, org-scoped)."""
import pytest
from fastapi.testclient import TestClient

from app.deps import get_current_user
from app.main import app as fastapi_app
from app.models import JobHiringTeam, Organization, Role, RoleChangeEvent, User
from app.platform.database import get_db


def _seed(db, *, slug):
    org = Organization(name=slug.title(), slug=slug)
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Eng", source="manual")
    db.add(role)
    db.flush()
    user = User(
        organization_id=org.id, email=f"u-{slug}@x.test", full_name="Rec",
        hashed_password="x", is_active=True, is_verified=True,
    )
    db.add(user)
    db.flush()
    db.add(
        JobHiringTeam(
            organization_id=org.id,
            role_id=role.id,
            user_id=user.id,
            team_role="recruiter",
        )
    )
    db.commit()
    return org, role, user


@pytest.fixture
def rec_client(db):
    org, role, user = _seed(db, slug="reco")

    def _yield():
        yield db

    fastapi_app.dependency_overrides[get_current_user] = lambda: user
    fastapi_app.dependency_overrides[get_db] = _yield
    with TestClient(fastapi_app) as c:
        yield c, org, role, user
    fastapi_app.dependency_overrides.clear()


def _base(role):
    return f"/api/v1/roles/{role.id}/screening-questions"


def test_crud_lifecycle(rec_client, db):
    client, org, role, user = rec_client
    # Create
    r = client.post(
        _base(role),
        json={
            "expected_version": 1,
            "prompt": "Years of Python?",
            "kind": "number",
            "required": True,
        },
    )
    assert r.status_code == 201, r.text
    qid = r.json()["id"]
    assert r.json()["prompt"] == "Years of Python?"
    assert r.json()["role_version"] == 2

    # List
    r = client.get(_base(role))
    assert r.status_code == 200 and len(r.json()) == 1

    # Update
    r = client.patch(
        f"{_base(role)}/{qid}",
        json={
            "expected_version": 2,
            "knockout": True,
            "knockout_expected": [3, 4, 5],
        },
    )
    assert r.status_code == 200 and r.json()["knockout"] is True
    assert r.json()["role_version"] == 3

    # Delete
    r = client.delete(
        f"{_base(role)}/{qid}", params={"expected_version": 3}
    )
    assert r.status_code == 200
    assert r.json() == {"deleted": True, "role_version": 4}
    assert client.get(_base(role)).json() == []
    assert [
        event.action
        for event in db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == role.id)
        .order_by(RoleChangeEvent.id)
        .all()
    ] == [
        "screening_question_created",
        "screening_question_updated",
        "screening_question_deleted",
    ]


def test_create_validation_422(rec_client):
    client, org, role, user = rec_client
    r = client.post(
        _base(role),
        json={"expected_version": 1, "prompt": "x", "kind": "bogus"},
    )
    assert r.status_code == 422


def test_unknown_role_404(rec_client):
    client, org, role, user = rec_client
    r = client.post(
        "/api/v1/roles/999999/screening-questions",
        json={"expected_version": 1, "prompt": "Q", "kind": "text"},
    )
    assert r.status_code == 403


def test_stale_and_unassigned_screening_writes_are_rejected(rec_client, db):
    client, org, role, user = rec_client
    created = client.post(
        _base(role),
        json={"expected_version": 1, "prompt": "Q", "kind": "text"},
    )
    assert created.status_code == 201
    question_id = created.json()["id"]

    stale = client.patch(
        f"{_base(role)}/{question_id}",
        json={"expected_version": 1, "prompt": "stale overwrite"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "ROLE_VERSION_CONFLICT"

    unassigned = User(
        organization_id=org.id,
        email="unassigned-screening@x.test",
        full_name="Unassigned",
        hashed_password="x",
        is_active=True,
        is_verified=True,
    )
    db.add(unassigned)
    db.commit()
    fastapi_app.dependency_overrides[get_current_user] = lambda: unassigned
    denied = client.patch(
        f"{_base(role)}/{question_id}",
        json={"expected_version": 2, "prompt": "unauthorized"},
    )
    assert denied.status_code == 403


def test_requires_auth(db):
    """Without the auth override, the CRUD route rejects the request."""
    _seed(db, slug="noauth")

    def _yield():
        yield db

    fastapi_app.dependency_overrides[get_db] = _yield
    with TestClient(fastapi_app) as c:
        r = c.get("/api/v1/roles/1/screening-questions")
    fastapi_app.dependency_overrides.clear()
    assert r.status_code in (401, 403)
