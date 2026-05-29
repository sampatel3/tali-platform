import os
import warnings

# Suppress starlette's PendingDeprecationWarning (multipart vs python_multipart) — from dependency
warnings.filterwarnings(
    "ignore",
    message="Please use `import python_multipart` instead",
    category=PendingDeprecationWarning,
    module="starlette.formparsers",
)

# --- Test datastore selection -------------------------------------------------
# Override DATABASE_URL before any app imports.
#
# Default (no TEST_DATABASE_URL): a shared-cache in-memory SQLite. Fast, no
# external services — used for local runs and the pre-pilot CI gate.
#
# TEST_DATABASE_URL set: run against a real datastore (Postgres) for prod
# parity. This MUST point at a *throwaway* database — CI's ephemeral service
# container, or a dedicated dev container on a NON-default port. The teardown
# below drops every table between tests, so pointing this at a real database
# (e.g. the host's 5432, which may be the prod pg / an ssh tunnel) would be
# destructive. We refuse the obvious footgun explicitly.
_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
if _TEST_DATABASE_URL:
    if ":5432/" in _TEST_DATABASE_URL or _TEST_DATABASE_URL.endswith(":5432"):
        raise RuntimeError(
            "TEST_DATABASE_URL points at port 5432 — refusing to run the "
            "destructive test teardown (drop_all per test) against what is "
            "likely the host/prod Postgres. Use a throwaway container on "
            "another port (e.g. 55432) or an ephemeral CI service DB."
        )
    os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
else:
    os.environ["DATABASE_URL"] = "sqlite:///file:taalitest?mode=memory&cache=shared"
# Keep external integrations disabled by default for unit/API tests. Individual
# tests can opt-in by monkeypatching settings.
os.environ["MVP_DISABLE_WORKABLE"] = "true"
os.environ["MVP_DISABLE_STRIPE"] = "true"
os.environ["CLAUDE_MODEL"] = "claude-3-5-haiku-latest"
# Preserve test fixtures that create/update/delete tasks through API helpers.
os.environ["TASK_AUTHORING_API_ENABLED"] = "true"
os.environ["GITHUB_MOCK_MODE"] = "true"
# Run Celery tasks inline so unit/API tests don't need a live broker.
# Calling `.delay()` invokes the task body in-process and returns a
# completed AsyncResult. Tests that need to assert dispatch should patch
# the task at its call site (the production code path is unchanged).
from app.tasks.celery_app import celery_app as _celery_app  # noqa: E402

_celery_app.conf.task_always_eager = True
_celery_app.conf.task_eager_propagates = True

import asyncio
import time
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from app.platform.database import Base, get_db
from app.main import app
from app.platform.middleware import _rate_limit_store
from app.models.user import User
from app.models.organization import Organization
from app.models.task import Task
from app.models.candidate import Candidate
from app.models.assessment import Assessment

SQLALCHEMY_DATABASE_URL = os.environ["DATABASE_URL"]
IS_SQLITE = SQLALCHEMY_DATABASE_URL.startswith("sqlite")

# Use same URL as app so sync + async share the DB.
if IS_SQLITE:
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
else:
    # Real Postgres (CI service container / throwaway dev container). NullPool
    # keeps connection state from bleeding across the per-test drop/create.
    engine = create_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Keep one sync connection open so SQLite shared-memory state survives across
# short-lived request/test sessions during the suite. Not needed for Postgres
# (the server owns the DB regardless of client connections).
_keepalive_connection = engine.connect() if IS_SQLITE else None

# Enable foreign key support and WAL mode for SQLite (reduces locking with async engine)
if IS_SQLITE:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


# SQLite BigInteger-PK workaround. SQLite only auto-increments INTEGER PRIMARY
# KEY; a BIGINT PK stays NULL on insert. Several high-traffic tables use
# BigInteger PKs and are written from many code paths, so we assign their PKs
# from a counter via a before_insert listener.
#
# CRITICAL: these counters are reset to zero before *every* test by the
# autouse `_isolate_test` fixture below. They used to be process-global and
# monotonic for the whole session, which made any test asserting a specific
# id (or a clean first row) pass alone but fail when another row-creating
# test ran first — the "passes in isolation, fails in suite" coupling.
#
# The listeners are registered ONLY for SQLite. On Postgres (CI / prod
# parity) the real BIGSERIAL sequence assigns ids; forcing them from a Python
# counter there would fight the sequence and collide.
_CLAUDE_CALL_LOG_PK_COUNTER = {"n": 0}
_AGENT_DECISION_PK_COUNTER = {"n": 0}
_GRAPH_EPISODE_OUTBOX_PK_COUNTER = {"n": 0}


def _reset_pk_counters() -> None:
    _CLAUDE_CALL_LOG_PK_COUNTER["n"] = 0
    _AGENT_DECISION_PK_COUNTER["n"] = 0
    _GRAPH_EPISODE_OUTBOX_PK_COUNTER["n"] = 0


def _make_pk_assigner(counter):
    def _assign(mapper, connection, target):  # pragma: no cover
        if getattr(target, "id", None) is None:
            counter["n"] += 1
            target.id = counter["n"]
    return _assign


if IS_SQLITE:
    for _model_path, _attr, _counter in (
        ("app.models.claude_call_log", "ClaudeCallLog", _CLAUDE_CALL_LOG_PK_COUNTER),
        ("app.models.agent_decision", "AgentDecision", _AGENT_DECISION_PK_COUNTER),
        ("app.models.graph_episode_outbox", "GraphEpisodeOutbox", _GRAPH_EPISODE_OUTBOX_PK_COUNTER),
    ):
        try:
            _module = __import__(_model_path, fromlist=[_attr])
            event.listen(getattr(_module, _attr), "before_insert", _make_pk_assigner(_counter))
        except Exception:  # pragma: no cover — model import shouldn't fail
            pass


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

def _dispose_async_engine_before_teardown():
    """Dispose async engine so the drop can run without 'database is locked'."""
    from app.platform.database import async_engine
    asyncio.run(async_engine.dispose())
    if IS_SQLITE:
        time.sleep(0.05)  # Let SQLite release WAL locks


def _safe_drop_all():
    """Drop every table so the next test starts from a pristine schema.

    Dialect-aware: SQLite drops tables individually with FK enforcement off
    (we have a cyclic FK pair, agent_decisions.feedback_id ↔
    decision_feedback.decision_id, that can't be dropped in any order while
    foreign_keys=ON); Postgres resets the public schema in one shot.
    """
    from sqlalchemy import text
    _dispose_async_engine_before_teardown()
    with engine.connect() as conn:
        if IS_SQLITE:
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(text(f"DROP TABLE IF EXISTS {table.name}"))
            conn.execute(text("PRAGMA foreign_keys=ON"))
        else:
            # Throwaway Postgres only (guarded above): wipe and recreate the
            # schema — fast, and immune to the cyclic-FK drop-order problem.
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        conn.commit()


@pytest.fixture(scope="function", autouse=True)
def _isolate_test():
    """Per-test isolation for EVERY test — not just those that request `db`.

    Setup: reset the SQLite PK counters and create a pristine schema.
    Teardown: clear in-process state and drop the schema again.

    This is the fix for the cross-file state leak: previously only tests that
    requested the `db` fixture got create_all/drop_all, and the PK counters
    were never reset, so a test's result depended on which other tests ran
    first in the same process. Now isolation is unconditional and the run
    order can't change a single pass/fail.
    """
    _reset_pk_counters()
    _rate_limit_store.clear()
    Base.metadata.create_all(bind=engine)
    yield
    _rate_limit_store.clear()
    _safe_drop_all()
    _reset_pk_counters()


@pytest.fixture(scope="function")
def db():
    # Schema lifecycle is owned by the autouse `_isolate_test` fixture, which
    # runs first; here we only hand out a session bound to that fresh schema.
    db = TestingSessionLocal()
    yield db
    db.close()


@pytest.fixture(scope="function")
def client(db):
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


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
