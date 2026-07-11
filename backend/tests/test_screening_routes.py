"""Recruiter screening-question CRUD routes (authed, org-scoped)."""
import pytest
from fastapi.testclient import TestClient

from app.deps import get_current_user
from app.main import app as fastapi_app
from app.models import Organization, Role, User
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


def test_crud_lifecycle(rec_client):
    client, org, role, user = rec_client
    # Create
    r = client.post(
        _base(role),
        json={"prompt": "Years of Python?", "kind": "number", "required": True},
    )
    assert r.status_code == 201, r.text
    qid = r.json()["id"]
    assert r.json()["prompt"] == "Years of Python?"

    # List
    r = client.get(_base(role))
    assert r.status_code == 200 and len(r.json()) == 1

    # Update
    r = client.patch(
        f"{_base(role)}/{qid}",
        json={"knockout": True, "knockout_expected": [3, 4, 5]},
    )
    assert r.status_code == 200 and r.json()["knockout"] is True

    # Delete
    r = client.delete(f"{_base(role)}/{qid}")
    assert r.status_code == 204
    assert client.get(_base(role)).json() == []


def test_create_validation_422(rec_client):
    client, org, role, user = rec_client
    r = client.post(_base(role), json={"prompt": "x", "kind": "bogus"})
    assert r.status_code == 422


def test_unknown_role_404(rec_client):
    client, org, role, user = rec_client
    r = client.post(
        "/api/v1/roles/999999/screening-questions",
        json={"prompt": "Q", "kind": "text"},
    )
    assert r.status_code == 404


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
