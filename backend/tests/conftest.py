import os
import shutil
import tempfile
import warnings
from pathlib import Path

# Suppress starlette's PendingDeprecationWarning (multipart vs python_multipart) — from dependency
warnings.filterwarnings(
    "ignore",
    message="Please use `import python_multipart` instead",
    category=PendingDeprecationWarning,
    module="starlette.formparsers",
)

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
# Unit/API tests must never inherit developer or CI provider credentials. Tests
# that exercise provider-aware branches opt in with local fakes/monkeypatches.
# This is especially important now that semantic search can select Graphiti,
# whose query embedding would otherwise be a paid Voyage request.
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["VOYAGE_API_KEY"] = ""
os.environ["NEO4J_URI"] = ""
os.environ["NEO4J_PASSWORD"] = ""
os.environ["CLAUDE_MODEL"] = "claude-haiku-4-5-20251001"
# Preserve test fixtures that create/update/delete tasks through API helpers.
os.environ["TASK_AUTHORING_API_ENABLED"] = "true"
os.environ["GITHUB_MOCK_MODE"] = "true"

# Self-serve signup is OFF in production (sales-led onboarding), so the register
# router is unmounted by default. The auth tests still exercise that capability,
# so enable it for the test app. test_registration_gate.py asserts the prod
# default is off, which is the guard that actually matters.
os.environ["ALLOW_PUBLIC_REGISTRATION"] = "true"

# Task snapshots and assessment repository mocks are real local Git
# repositories. Test records commonly reuse small integer task IDs, so sharing
# either default root lets xdist workers mutate the same checkout concurrently
# and race on ``.git/index.lock`` or delete another worker's snapshot. Configure
# both roots before importing the application so every service instance sees a
# run- and worker-specific filesystem. Explicit test bases remain available for
# CI/debugging while still preserving worker isolation beneath them.
_pytest_run_id = os.environ.get("PYTEST_XDIST_TESTRUNUID") or f"pid-{os.getpid()}"
_pytest_worker_id = os.environ.get("PYTEST_XDIST_WORKER", "master")
_configured_mock_base = os.environ.get("TALI_TEST_GITHUB_MOCK_ROOT")
_configured_task_base = os.environ.get("TALI_TEST_TASK_REPOS_ROOT")
_pytest_mock_base = Path(_configured_mock_base or tempfile.gettempdir())
_pytest_task_base = Path(_configured_task_base or tempfile.gettempdir())
_pytest_github_mock_root = (
    _pytest_mock_base / f"taali_github_mock-{_pytest_run_id}" / _pytest_worker_id
)
_pytest_task_repos_root = (
    _pytest_task_base / f"taali_task_repos-{_pytest_run_id}" / _pytest_worker_id
)
os.environ["GITHUB_MOCK_ROOT"] = str(_pytest_github_mock_root)
os.environ["TASK_REPOS_ROOT"] = str(_pytest_task_repos_root)
# Run Celery tasks inline so unit/API tests don't need a live broker.
# Calling `.delay()` invokes the task body in-process and returns a
# completed AsyncResult. Tests that need to assert dispatch should patch
# the task at its call site (the production code path is unchanged).
from app.tasks.celery_app import celery_app as _celery_app  # noqa: E402

_celery_app.conf.task_always_eager = True
_celery_app.conf.task_eager_propagates = True

import asyncio
import re
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


@pytest.fixture(scope="session", autouse=True)
def _cleanup_worker_repository_roots():
    """Remove this pytest process's isolated repositories after teardown."""
    yield
    shutil.rmtree(_pytest_github_mock_root, ignore_errors=True)
    shutil.rmtree(_pytest_task_repos_root, ignore_errors=True)

# Enable foreign key support and WAL mode for SQLite (reduces locking with async engine)
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


# SQLite BigInteger-PK workaround for claude_call_log. SQLite only
# auto-increments INTEGER PRIMARY KEY; a BIGINT PK stays NULL on insert.
# claude_call_log rows are now written by MeteredAnthropicClient from many
# code paths (any test that triggers a Claude call through the wrapper),
# so the workaround lives here globally rather than per-test-file. Prod
# uses Postgres where BigInteger PKs auto-increment via sequence.
_CLAUDE_CALL_LOG_PK_COUNTER = {"n": 0}


def _assign_claude_call_log_pk(mapper, connection, target):  # pragma: no cover
    if getattr(target, "id", None) is None:
        _CLAUDE_CALL_LOG_PK_COUNTER["n"] += 1
        target.id = _CLAUDE_CALL_LOG_PK_COUNTER["n"]


try:
    from app.models.claude_call_log import ClaudeCallLog as _ClaudeCallLog

    event.listen(_ClaudeCallLog, "before_insert", _assign_claude_call_log_pk)
except Exception:  # pragma: no cover — model import shouldn't fail
    pass


# Same BigInteger-PK workaround for anthropic_wire_log (the transport-level
# ground-truth log). Written by the wire-tap from any process; register
# globally so wire-tap tests don't depend on import order.
_ANTHROPIC_WIRE_LOG_PK_COUNTER = {"n": 0}


def _assign_anthropic_wire_log_pk(mapper, connection, target):  # pragma: no cover
    if getattr(target, "id", None) is None:
        _ANTHROPIC_WIRE_LOG_PK_COUNTER["n"] += 1
        target.id = _ANTHROPIC_WIRE_LOG_PK_COUNTER["n"]


try:
    from app.models.anthropic_wire_log import AnthropicWireLog as _AnthropicWireLog

    event.listen(_AnthropicWireLog, "before_insert", _assign_anthropic_wire_log_pk)
except Exception:  # pragma: no cover — model import shouldn't fail
    pass


# Same BigInteger-PK workaround for agent_decisions. Decisions are created
# from many code paths (the pre-screen emitter, reconcile, the role PATCH
# reconcile, approve/override), so register the listener globally here
# rather than in a single test module — otherwise tests that create
# AgentDecisions only pass when that one module happens to be imported in
# the same pytest session (an import-order coupling).
_AGENT_DECISION_PK_COUNTER = {"n": 0}


def _assign_agent_decision_pk(mapper, connection, target):  # pragma: no cover
    if getattr(target, "id", None) is None:
        _AGENT_DECISION_PK_COUNTER["n"] += 1
        target.id = _AGENT_DECISION_PK_COUNTER["n"]


try:
    from app.models.agent_decision import AgentDecision as _AgentDecision

    event.listen(_AgentDecision, "before_insert", _assign_agent_decision_pk)
except Exception:  # pragma: no cover — model import shouldn't fail
    pass


# Same BigInteger-PK workaround for graph_episode_outbox — the durable
# Graphiti episode outbox. Rows are written from the outcome-learning and
# decision-queueing paths (many test modules), so register globally here.
_GRAPH_EPISODE_OUTBOX_PK_COUNTER = {"n": 0}


def _assign_graph_episode_outbox_pk(mapper, connection, target):  # pragma: no cover
    if getattr(target, "id", None) is None:
        _GRAPH_EPISODE_OUTBOX_PK_COUNTER["n"] += 1
        target.id = _GRAPH_EPISODE_OUTBOX_PK_COUNTER["n"]


try:
    from app.models.graph_episode_outbox import GraphEpisodeOutbox as _GraphEpisodeOutbox

    event.listen(_GraphEpisodeOutbox, "before_insert", _assign_graph_episode_outbox_pk)
except Exception:  # pragma: no cover — model import shouldn't fail
    pass


# Same BigInteger-PK workaround for brain_feed_outbox — the durable outbound
# mainspring brain feed. Rows are written by the brain-feed sweep; register
# globally here so any test exercising the feed gets an autoincrementing PK.
_BRAIN_FEED_OUTBOX_PK_COUNTER = {"n": 0}


def _assign_brain_feed_outbox_pk(mapper, connection, target):  # pragma: no cover
    if getattr(target, "id", None) is None:
        _BRAIN_FEED_OUTBOX_PK_COUNTER["n"] += 1
        target.id = _BRAIN_FEED_OUTBOX_PK_COUNTER["n"]


try:
    from app.models.brain_feed_outbox import BrainFeedOutbox as _BrainFeedOutbox

    event.listen(_BrainFeedOutbox, "before_insert", _assign_brain_feed_outbox_pk)
except Exception:  # pragma: no cover — model import shouldn't fail
    pass


# Same BigInteger-PK workaround for agent_needs_input. Rows are written by
# the ask_recruiter action from many code paths (data-readiness sync, the
# orchestrator survey, route-level reject flows), so register globally here —
# some test modules also register a local listener, but relying on that
# creates an import-order coupling (files fail when run in isolation).
_AGENT_NEEDS_INPUT_PK_COUNTER = {"n": 0}


def _assign_agent_needs_input_pk(mapper, connection, target):  # pragma: no cover
    if getattr(target, "id", None) is None:
        _AGENT_NEEDS_INPUT_PK_COUNTER["n"] += 1
        target.id = _AGENT_NEEDS_INPUT_PK_COUNTER["n"]


try:
    from app.models.agent_needs_input import AgentNeedsInput as _AgentNeedsInput

    event.listen(_AgentNeedsInput, "before_insert", _assign_agent_needs_input_pk)
except Exception:  # pragma: no cover — model import shouldn't fail
    pass


# Same BigInteger-PK workaround for decision_feedback (teach-loop rows).
_DECISION_FEEDBACK_PK_COUNTER = {"n": 0}


def _assign_decision_feedback_pk(mapper, connection, target):  # pragma: no cover
    if getattr(target, "id", None) is None:
        _DECISION_FEEDBACK_PK_COUNTER["n"] += 1
        target.id = _DECISION_FEEDBACK_PK_COUNTER["n"]


try:
    from app.models.decision_feedback import DecisionFeedback as _DecisionFeedback

    event.listen(_DecisionFeedback, "before_insert", _assign_decision_feedback_pk)
except Exception:  # pragma: no cover — model import shouldn't fail
    pass

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

def _dispose_async_engine_before_teardown():
    """Dispose async engine so drop_all can run without 'database is locked'."""
    from app.platform.database import async_engine
    asyncio.run(async_engine.dispose())
    time.sleep(0.05)  # Let SQLite release locks


def _safe_drop_all():
    """Drop all tables in reverse dependency order; use IF EXISTS for robustness."""
    from sqlalchemy import text
    _dispose_async_engine_before_teardown()
    with engine.connect() as conn:
        # Disable FK enforcement for the duration of the drop. We have at
        # least one cyclic FK pair (agent_decisions.feedback_id ↔
        # decision_feedback.decision_id) which SQLite refuses to drop in
        # any order while PRAGMA foreign_keys=ON.
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        # Drop in reverse dependency order (referencing tables first)
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(text(f"DROP TABLE IF EXISTS {table.name}"))
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.commit()


@pytest.fixture(scope="function")
def db():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    yield db
    db.close()
    _safe_drop_all()


@pytest.fixture(scope="function")
def client(db):
    app.dependency_overrides[get_db] = override_get_db
    # Clear in-memory rate limit state between tests to prevent 429 bleed-through
    _rate_limit_store.clear()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    _rate_limit_store.clear()


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
    unique = _unique_id()
    task_key = str(
        overrides.get("task_key")
        or overrides.get("task_id")
        or f"test-assessment-task-{unique}"
    )
    repo_name = re.sub(r"[^A-Za-z0-9._-]+", "-", task_key).strip("-").lower()
    repo_name = repo_name or f"test-assessment-task-{unique}"
    starter_code = overrides.get("starter_code", "# Start here\n")
    test_code = overrides.get("test_code", "def test_placeholder():\n    assert True\n")
    scenario = overrides.get(
        "scenario",
        "Implement and verify the requested repository change, then submit the working artifact.",
    )
    rubric = {
        "implementation": {
            "weight": 0.40,
            "lens": "deliverable",
            "criteria": "The submitted repository artifact correctly implements the requested change.",
        },
        "decision_ownership": {
            "weight": 0.15,
            "lens": "decision",
            "criteria": "The candidate owns and explains one material implementation decision.",
        },
        "ai_native_practice": {
            "weight": 0.15,
            "grader": "practice_outcome",
            "part": "applied",
            "fluency": "description",
        },
        "output_scrutiny": {
            "weight": 0.15,
            "lens": "discernment",
            "criteria": "The candidate reviews tool output and corrects material issues before submission.",
        },
        "verification_before_done": {
            "weight": 0.15,
            "lens": "diligence",
            "criteria": "The candidate runs and interprets the verifier before submitting.",
        },
    }
    selected_rubric = overrides.get("evaluation_rubric", rubric)
    rubric_dimensions = tuple(selected_rubric)
    extra_data = {
        "deliverable": {
            "kind": "code",
            "primary_artifact": "src/main.py",
            "required": True,
            "no_artifact_outcome": "incomplete",
            "submission_check": "test_runner",
        },
        "expected_candidate_journey": {
            "orient": ["Read the repository brief and inspect the starter implementation."],
            "implement": ["Make a substantive change in src/main.py."],
            "verify": ["Run the frozen pytest suite and review its output before submitting."],
        },
        "interviewer_signals": {
            "strong_positive": ["Ships a working artifact and verifies it."],
            "red_flags": ["Submits without changing or checking the primary artifact."],
        },
        "scoring_hints": {"min_reading_time_seconds": 1},
        "test_runner": {
            "command": "python3 -I -m pytest -q --tb=short",
            "working_dir": f"/workspace/{repo_name}",
            "parse_pattern": r"(?P<passed>\d+)\s+passed(?:,\s+(?P<failed>\d+)\s+failed)?",
            "timeout_seconds": 60,
            "expected_total": 1,
            "verifier_files": ["tests/test_main.py"],
        },
        "workspace_bootstrap": {
            "commands": ["python3 -I -c \"import pytest\""],
            "working_dir": f"/workspace/{repo_name}",
            "timeout_seconds": 30,
            "must_succeed": True,
        },
        "role_alignment": {
            "source_user_email": "test-author@example.com",
            "source_role_name": "Software Engineer",
            "source_role_identifier": "test:software-engineer",
            "captured_at": "2026-01-01T00:00:00Z",
            "must_cover": ["Implement and verify a repository change."],
            "must_not_cover": [],
            "jd_to_signal_map": [
                {
                    "job_requirement": "Implement and verify production work.",
                    "task_artifact": "Repository artifact and assessment process evidence.",
                    "rubric_dimension": dimension,
                }
                for dimension in rubric_dimensions
            ],
        },
        "human_testing_checklist": {
            "candidate_clarity": True,
            "repo_boot_ok": True,
            "tests_collect_ok": True,
            "baseline_failures_meaningful": True,
            "rubric_matches_role": True,
            "timebox_realistic": True,
        },
    }
    payload = {
        "name": overrides.get("name", f"Task-{unique}"),
        "description": overrides.get("description", "A test task for QA purposes"),
        "task_type": overrides.get("task_type", "python"),
        "difficulty": overrides.get("difficulty", "medium"),
        "duration_minutes": overrides.get("duration_minutes", 30),
        "starter_code": starter_code,
        "test_code": test_code,
        "task_key": task_key,
        "role": overrides.get("role", "software_engineer"),
        "scenario": scenario,
        "repo_structure": overrides.get(
            "repo_structure",
            {
                "name": repo_name,
                "files": {
                    "README.md": "# Test assessment repository\n",
                    "SCENARIO.md": f"# Scenario\n\n{scenario}\n",
                    "requirements.txt": "pytest>=8.0.0\n",
                    "src/main.py": starter_code,
                    "tests/test_main.py": test_code,
                },
            },
        ),
        "evaluation_rubric": selected_rubric,
        "extra_data": overrides.get("extra_data", extra_data),
    }
    payload.update(
        {
            k: v
            for k, v in overrides.items()
            if k not in payload and k != "task_id"
        }
    )
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
