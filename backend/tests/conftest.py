import os

# Override DATABASE_URL before any app imports. Shared in-memory avoids disk I/O
# and locking when sync + async engines both access the DB. Parallel verification
# processes can opt into a distinct, still-test-only database name without ever
# inheriting the application's normal DATABASE_URL.
os.environ["DATABASE_URL"] = os.environ.get(
    "TALI_TEST_DATABASE_URL",
    # ``uri=true`` is required for SQLAlchemy to pass ``mode=memory`` and
    # ``cache=shared`` to SQLite.  Without it this URL silently creates a
    # persistent file literally named ``file:taalitest``; concurrent pytest
    # processes then drop each other's tables despite the in-memory comment.
    "sqlite:///file:taalitest?mode=memory&cache=shared&uri=true",
)
# Keep external integrations disabled by default for unit/API tests. Individual
# tests can opt-in by monkeypatching settings.
os.environ["MVP_DISABLE_WORKABLE"] = "true"
os.environ["MVP_DISABLE_STRIPE"] = "true"
os.environ["CLAUDE_MODEL"] = "claude-haiku-4-5-20251001"
# Preserve test fixtures that create/update/delete tasks through API helpers.
os.environ["TASK_AUTHORING_API_ENABLED"] = "true"
os.environ["GITHUB_MOCK_MODE"] = "true"
os.environ["ADMIN_SECRET"] = "test-admin-secret"
# Keep the real bcrypt algorithm and salt semantics while avoiding the
# production work factor for thousands of short-lived fixture accounts.
os.environ["BCRYPT_ROUNDS"] = "4"
# Never let unit/API tests share limiter state through a developer or CI Redis.
# The limiter's dedicated tests opt in with monkeypatching; the rest of the
# suite resets the deterministic in-memory fallback between TestClient cases.
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/15"
# Run Celery tasks inline so unit/API tests don't need a live broker.
# Calling `.delay()` invokes the task body in-process and returns a
# completed AsyncResult. Tests that need to assert dispatch should patch
# the task at its call site (the production code path is unchanged).
from app.tasks.celery_app import celery_app as _celery_app  # noqa: E402

_celery_app.conf.task_always_eager = True
_celery_app.conf.task_eager_propagates = True

import asyncio
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from app.platform.database import Base, get_db
from app.main import app
from app.services.rate_limit import reset_memory_buckets
from app.models.user import User


@pytest.fixture(scope="session")
def event_loop_policy():
    """Keep pytest-asyncio from restoring an implicitly created legacy loop.

    On Python 3.11, ``get_event_loop()`` creates a selector loop when the policy
    has no explicit current-loop value.  pytest-asyncio snapshots that value
    before opening its managed runner, then restores it without closing it.
    Marking the current loop as explicitly absent makes the snapshot raise
    instead, so the plugin restores ``None`` and owns every loop it creates.
    """

    policy = asyncio.get_event_loop_policy()
    asyncio.set_event_loop(None)
    return policy

SQLALCHEMY_DATABASE_URL = os.environ["DATABASE_URL"]
# Use same URL as app so sync + async share the in-memory DB
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
    poolclass=NullPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
# Keep one sync connection open so SQLite shared-memory state survives across
# short-lived request/test sessions during the suite.
_keepalive_connection = engine.connect()

# Enable foreign key support and WAL mode for SQLite (reduces locking with async engine)
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


# SQLite only auto-increments a column declared exactly ``INTEGER PRIMARY KEY``;
# mapped ``BigInteger`` primary keys therefore remain NULL even though Postgres
# supplies their production sequences.  Keep the emulation in one test-harness
# listener so every test and newly added BigInteger model behaves consistently,
# including tests that use a private SQLite engine instead of the shared fixture.
_SQLITE_BIGINT_PK_COUNTERS: dict[str, int] = {}


def _assign_sqlite_bigint_pk(mapper, connection, target):  # pragma: no cover
    if connection.dialect.name != "sqlite" or len(mapper.primary_key) != 1:
        return

    primary_key = mapper.primary_key[0]
    if not isinstance(primary_key.type, BigInteger):
        return

    attribute = mapper.get_property_by_column(primary_key).key
    table_key = primary_key.table.fullname
    current = getattr(target, attribute, None)
    if current is not None:
        # Explicit fixture IDs must advance the shared counter so a later
        # implicit row in the same test cannot collide with them.
        _SQLITE_BIGINT_PK_COUNTERS[table_key] = max(
            _SQLITE_BIGINT_PK_COUNTERS.get(table_key, 0), int(current)
        )
        return

    next_id = _SQLITE_BIGINT_PK_COUNTERS.get(table_key, 0) + 1
    _SQLITE_BIGINT_PK_COUNTERS[table_key] = next_id
    setattr(target, attribute, next_id)


event.listen(Base, "before_insert", _assign_sqlite_bigint_pk, propagate=True)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

def _schema_exists() -> bool:
    """Return whether every mapped table still exists in shared SQLite.

    A few specialised tests intentionally rebuild the shared schema with their
    own fixture.  One sqlite_master query lets the normal fixture recover from
    complete or partial rebuilds without paying ``create_all``'s check-first
    query for every metadata table before every ordinary test.
    """
    with engine.connect() as conn:
        present_tables = {
            row[0]
            for row in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    mapped_tables = {table.name for table in Base.metadata.tables.values()}
    return mapped_tables.issubset(present_tables)


def _ensure_schema() -> None:
    if not _schema_exists():
        Base.metadata.create_all(bind=engine)


def _clear_database_rows() -> None:
    """Restore an empty SQLite database without rebuilding its schema.

    Tests exercise both sync and async sessions against the same shared-memory
    database, so wrapping each test in a single connection transaction cannot
    isolate all writes.  Clearing committed rows gives the same externally
    visible isolation as the former drop/create cycle while retaining tables,
    indexes and constraints.  Foreign keys are disabled only on this cleanup
    connection so mutual FK cycles can be cleared safely; normal test
    connections continue to enforce them.
    """
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            # Be tolerant of specialised schema tests which deliberately drop
            # some or all metadata tables.  The next db fixture recreates any
            # missing schema through _ensure_schema().
            present_tables = {
                row[0]
                for row in conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            for table in reversed(Base.metadata.sorted_tables):
                if table.name in present_tables:
                    conn.execute(table.delete())

            # One table uses SQLite's explicit AUTOINCREMENT.  Dropping and
            # recreating the schema reset sqlite_sequence, so preserve that
            # observable behaviour as well as row isolation.
            if "sqlite_sequence" in present_tables:
                conn.exec_driver_sql("DELETE FROM sqlite_sequence")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            # PRAGMA foreign_keys cannot change inside a transaction.  The
            # commit/rollback above deliberately happens before re-enabling it.
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.commit()


@pytest.fixture(scope="session", autouse=True)
def _isolated_local_repo_roots(tmp_path_factory):
    """Keep generated Git repositories isolated to this pytest session.

    Both services read their environment value when called or instantiated, so
    setting these before function-scoped tests run avoids cross-run buildup and
    collisions between parallel pytest processes. pytest owns and cleans the
    directories; no user-provided path is removed.
    """
    variables = ("GITHUB_MOCK_ROOT", "TASK_REPOS_ROOT")
    previous = {name: os.environ.get(name) for name in variables}
    os.environ["GITHUB_MOCK_ROOT"] = str(tmp_path_factory.mktemp("github-mock"))
    os.environ["TASK_REPOS_ROOT"] = str(tmp_path_factory.mktemp("task-repos"))
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@pytest.fixture(scope="session", autouse=True)
def _test_database_schema():
    """Create the shared in-memory schema once, then release engines once."""
    Base.metadata.create_all(bind=engine)
    yield

    # NullPool means no request connection is retained between tests, so the
    # async engine only needs disposal at session shutdown.  The keepalive
    # connection remains open until then to preserve the shared-memory DB.
    from app.platform.database import async_engine

    asyncio.run(async_engine.dispose())
    _keepalive_connection.close()
    engine.dispose()


@pytest.fixture(scope="function")
def db():
    _ensure_schema()
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        _clear_database_rows()


@pytest.fixture(scope="function")
def client(db):
    app.dependency_overrides[get_db] = override_get_db
    # Clear in-memory rate limit state between tests to prevent 429 bleed-through
    reset_memory_buckets()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    reset_memory_buckets()


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
