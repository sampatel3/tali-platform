import os
# Override DATABASE_URL before any app imports to avoid PostgreSQL driver requirement
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
# Keep external integrations disabled by default for unit/API tests. Individual
# tests can opt-in by monkeypatching settings.
os.environ["MVP_DISABLE_LEMON"] = "true"
os.environ["MVP_DISABLE_WORKABLE"] = "true"
os.environ["MVP_DISABLE_STRIPE"] = "true"
os.environ["MVP_DISABLE_CELERY"] = "true"
os.environ["CLAUDE_MODEL"] = "claude-3-5-haiku-latest"
# Preserve test fixtures that create/update/delete tasks through API helpers.
os.environ["TASK_AUTHORING_API_ENABLED"] = "true"
os.environ["GITHUB_MOCK_MODE"] = "true"

import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from app.platform.database import Base, get_db
from app.main import app
from app.platform.middleware import _rate_limit_store
from app.models.user import User
from app.models.organization import Organization
from app.models.task import Task
from app.models.candidate import Candidate
from app.models.assessment import Assessment

SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Enable foreign key support for SQLite
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture(scope="function")
def db():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def client(db):
    app.dependency_overrides[get_db] = override_get_db
    Base.metadata.create_all(bind=engine)
    # Clear in-memory rate limit state between tests to prevent 429 bleed-through
    _rate_limit_store.clear()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    _rate_limit_store.clear()
    Base.metadata.drop_all(bind=engine)


# ---------------------------------------------------------------------------
# Helper: verify a user's email directly in DB
# ---------------------------------------------------------------------------

def verify_user(email: str) -> None:
    """Mark a user as email-verified in the test DB (call after register)."""
    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user:
            # FastAPI-Users uses is_verified; fallback for pre-migration schema
            if hasattr(user, "is_verified"):
                user.is_verified = True
            else:
                user.is_email_verified = True
            db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Factory helpers — create test entities quickly and consistently
# ---------------------------------------------------------------------------

_counter = 0

def _unique_id() -> str:
    global _counter
    _counter += 1
    return f"{_counter}-{uuid.uuid4().hex[:8]}"


def register_user(client, email=None, password="TestPass123!", full_name="Test User", organization_name=None):
    """Register a user via the API. Returns the response."""
    email = email or f"user-{_unique_id()}@test.com"
    payload = {
        "email": email,
        "password": password,
        "full_name": full_name,
    }
    if organization_name is not None:
        payload["organization_name"] = organization_name
    resp = client.post("/api/v1/auth/register", json=payload)
    return resp


def login_user(client, email, password="TestPass123!"):
    """Log in a user via the API (FastAPI-Users JWT). Returns the response."""
    return client.post(
        "/api/v1/auth/jwt/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


def auth_headers(client, email=None, password="TestPass123!", full_name="Test User", organization_name="TestOrg"):
    """Register, verify, login a user and return Authorization headers + email.

    Returns (headers_dict, email) tuple.
    """
    email = email or f"user-{_unique_id()}@test.com"
    reg = register_user(client, email=email, password=password, full_name=full_name, organization_name=organization_name)
    assert reg.status_code == 201, f"Registration failed: {reg.text}"
    verify_user(email)
    login_resp = login_user(client, email, password)
    assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
    token = login_resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, email


def create_task_via_api(client, headers, **overrides):
    """Create a task via the API. Returns the response."""
    payload = {
        "name": overrides.get("name", f"Task-{_unique_id()}"),
        "description": overrides.get("description", "A test task for QA purposes"),
        "task_type": overrides.get("task_type", "python"),
        "difficulty": overrides.get("difficulty", "medium"),
        "duration_minutes": overrides.get("duration_minutes", 30),
        "starter_code": overrides.get("starter_code", "# Start here\n"),
        "test_code": overrides.get("test_code", "def test_placeholder(): pass\n"),
    }
    payload.update({k: v for k, v in overrides.items() if k not in payload})
    return client.post("/api/v1/tasks/", json=payload, headers=headers)


def create_candidate_via_api(client, headers, **overrides):
    """Create a candidate via the API. Returns the response."""
    payload = {
        "email": overrides.get("email", f"candidate-{_unique_id()}@test.com"),
        "full_name": overrides.get("full_name", "Jane Doe"),
        "position": overrides.get("position", "Software Engineer"),
    }
    payload.update({k: v for k, v in overrides.items() if k not in payload})
    return client.post("/api/v1/candidates/", json=payload, headers=headers)


def create_assessment_via_api(client, headers, task_id, candidate_email=None, candidate_name="Test Candidate"):
    """Create an assessment via the API. Returns the response."""
    candidate_email = candidate_email or f"candidate-{_unique_id()}@test.com"
    payload = {
        "candidate_email": candidate_email,
        "candidate_name": candidate_name,
        "task_id": task_id,
    }
    return client.post("/api/v1/assessments/", json=payload, headers=headers)


def setup_full_environment(client):
    """Create a complete test environment: user, org, task, candidate, assessment.

    Returns a dict with all IDs and auth headers.
    """
    headers, email = auth_headers(client)
    task_resp = create_task_via_api(client, headers)
    assert task_resp.status_code == 201, f"Task creation failed: {task_resp.text}"
    task = task_resp.json()

    cand_resp = create_candidate_via_api(client, headers)
    assert cand_resp.status_code == 201, f"Candidate creation failed: {cand_resp.text}"
    candidate = cand_resp.json()

    assess_resp = create_assessment_via_api(client, headers, task["id"],
                                             candidate_email=candidate["email"],
                                             candidate_name=candidate.get("full_name", "Test Candidate"))
    assert assess_resp.status_code == 201, f"Assessment creation failed: {assess_resp.text}"
    assessment = assess_resp.json()

    return {
        "headers": headers,
        "email": email,
        "task": task,
        "candidate": candidate,
        "assessment": assessment,
    }
