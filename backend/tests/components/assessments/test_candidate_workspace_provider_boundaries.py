from __future__ import annotations

from datetime import timedelta
from threading import Event, Lock, Thread
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.components.assessments import service as assessment_service
from app.components.assessments.repository import utcnow
from app.domains.assessments_runtime import candidate_runtime_routes
from app.models.assessment import Assessment, AssessmentStatus
from app.models.organization import Organization
from app.models.task import Task
from app.schemas.assessment import CodeExecutionRequest, RepoFileSaveRequest
from tests.conftest import TestingSessionLocal


def _seed_assessment(
    db,
    *,
    status: AssessmentStatus,
    session_id: str | None = None,
    branch: bool = True,
) -> Assessment:
    organization = Organization(
        name="Detached workspace org",
        slug=f"detached-workspace-{status.value}-{session_id or 'new'}-{int(branch)}",
        credits_balance=10,
    )
    db.add(organization)
    db.flush()
    task = Task(
        organization_id=int(organization.id),
        name="Detached workspace task",
        description="Prove providers are detached from ORM locks",
        duration_minutes=30,
        starter_code="print('starter')",
        task_key=f"detached-workspace-{organization.id}",
        role="Backend Engineer",
        scenario="Safely update the runtime workspace.",
        repo_structure={
            "name": "detached-workspace",
            "files": {"main.py": "print('starter')"},
        },
        evaluation_rubric={"correctness": 1.0},
        extra_data={},
        is_active=True,
    )
    db.add(task)
    db.flush()
    assessment = Assessment(
        organization_id=int(organization.id),
        task_id=int(task.id),
        token=f"detached-token-{organization.id}",
        status=status,
        started_at=utcnow() if status == AssessmentStatus.IN_PROGRESS else None,
        duration_minutes=30,
        e2b_session_id=session_id,
        assessment_repo_url=("https://example.test/task.git" if branch else None),
        assessment_branch=("assessment/existing" if branch else None),
        clone_command=("git clone example.test/task.git" if branch else None),
    )
    db.add(assessment)
    db.commit()
    db.refresh(assessment)
    return assessment


class _ProviderCheckedSandbox:
    def __init__(self, db, sandbox_id: str):
        self._db = db
        self.sandbox_id = sandbox_id
        self.files = SimpleNamespace(write=self._write)

    def _write(self, _path, _content):
        assert self._db.in_transaction() is False

    def run_code(self, code):
        assert self._db.in_transaction() is False
        if "'exists': repo_root.exists()" in code:
            return {"stdout": '{"exists": true, "is_dir": true}\n'}
        if "'success': proc.returncode == 0" in code:
            return {"stdout": '{"success": true, "stderr": ""}\n'}
        if "payload={'returncode': p.returncode" in code:
            return {"stdout": '{"returncode": 0, "stderr": ""}\n'}
        if "files = {}" in code:
            return {"stdout": '{"files": {"main.py": "candidate edit"}}\n'}
        return {"stdout": ""}


def test_start_detaches_e2b_github_and_sandbox_io_from_request_transaction(
    db,
    monkeypatch,
):
    assessment = _seed_assessment(
        db,
        status=AssessmentStatus.PENDING,
        session_id=None,
        branch=False,
    )
    assessment_id = int(assessment.id)
    task_id = int(assessment.task_id)
    expected_repository_name = str(
        db.get(Task, task_id).template_repository_name
    )
    provider_calls: list[str] = []

    class CheckedE2B:
        def __init__(self, _api_key):
            assert db.in_transaction() is False

        def create_sandbox(self):
            assert db.in_transaction() is False
            provider_calls.append("e2b_create")
            return _ProviderCheckedSandbox(db, "detached-start-sandbox")

        def get_sandbox_id(self, sandbox):
            assert db.in_transaction() is False
            return sandbox.sandbox_id

        def close_sandbox(self, _sandbox):
            assert db.in_transaction() is False
            provider_calls.append("e2b_close")

    class CheckedRepository:
        def __init__(self, *_args):
            assert db.in_transaction() is False

        def create_assessment_branch(self, task, assessment_id):
            assert db.in_transaction() is False
            assert task.id == task_id
            assert task.template_repository_name == expected_repository_name
            provider_calls.append("github_branch")
            return SimpleNamespace(
                repo_url="https://example.test/task.git",
                branch_name=f"assessment/{assessment_id}",
                clone_command=f"git clone --branch assessment/{assessment_id} example.test/task.git",
            )

        def authenticated_repo_url(self, repo_url):
            assert db.in_transaction() is False
            return repo_url

    monkeypatch.setattr(assessment_service.settings, "E2B_API_KEY", "test-e2b")
    monkeypatch.setattr(assessment_service, "E2BService", CheckedE2B)
    monkeypatch.setattr(
        assessment_service,
        "AssessmentRepositoryService",
        CheckedRepository,
    )
    monkeypatch.setattr(
        assessment_service,
        "resolve_ai_mode",
        lambda: "claude_cli_terminal",
    )
    monkeypatch.setattr(
        assessment_service,
        "terminal_capabilities",
        lambda: {"enabled": True},
    )

    result = assessment_service.start_or_resume_assessment(assessment, db)

    assert result["sandbox_id"] == "detached-start-sandbox"
    assert provider_calls == ["e2b_create", "github_branch"]
    db.expire_all()
    stored = db.get(Assessment, assessment_id)
    assert stored.status == AssessmentStatus.IN_PROGRESS
    assert stored.e2b_session_id == "detached-start-sandbox"
    assert stored.assessment_branch == f"assessment/{assessment_id}"
    assert [event["event_type"] for event in stored.timeline] == ["assessment_started"]


def test_start_closes_provisional_sandbox_when_authority_drifts(db, monkeypatch):
    assessment = _seed_assessment(
        db,
        status=AssessmentStatus.PENDING,
        session_id=None,
        branch=True,
    )
    assessment_id = int(assessment.id)
    closed: list[str] = []

    class DriftingE2B:
        def __init__(self, _api_key):
            assert db.in_transaction() is False

        def create_sandbox(self):
            assert db.in_transaction() is False
            with TestingSessionLocal() as writer:
                current = writer.get(Assessment, assessment_id)
                current.is_voided = True
                writer.commit()
            return _ProviderCheckedSandbox(db, "rejected-start-sandbox")

        def get_sandbox_id(self, sandbox):
            assert db.in_transaction() is False
            return sandbox.sandbox_id

        def close_sandbox(self, sandbox):
            assert db.in_transaction() is False
            closed.append(sandbox.sandbox_id)

    monkeypatch.setattr(assessment_service.settings, "E2B_API_KEY", "test-e2b")
    monkeypatch.setattr(assessment_service, "E2BService", DriftingE2B)
    monkeypatch.setattr(
        assessment_service,
        "resolve_ai_mode",
        lambda: "claude_cli_terminal",
    )

    with pytest.raises(HTTPException) as exc_info:
        assessment_service.start_or_resume_assessment(assessment, db)

    assert exc_info.value.status_code == 409
    assert closed == ["rejected-start-sandbox"]
    db.expire_all()
    stored = db.get(Assessment, assessment_id)
    assert stored.is_voided is True
    assert stored.status == AssessmentStatus.PENDING
    assert stored.e2b_session_id is None


def test_duplicate_start_calls_share_one_created_workspace(db, monkeypatch):
    assessment = _seed_assessment(
        db,
        status=AssessmentStatus.PENDING,
        session_id=None,
        branch=True,
    )
    assessment_id = int(assessment.id)
    create_entered = Event()
    release_create = Event()
    second_started = Event()
    calls_guard = Lock()
    create_calls: list[str] = []
    connect_calls: list[str] = []
    results: list[dict] = []
    errors: list[Exception] = []

    class SerializedSandbox:
        sandbox_id = "serialized-start-sandbox"

        def run_code(self, code):
            if "'success': proc.returncode == 0" in code:
                return {"stdout": '{"success": true, "stderr": ""}\n'}
            if "payload={'returncode': p.returncode" in code:
                return {"stdout": '{"returncode": 0, "stderr": ""}\n'}
            if "files = {}" in code:
                return {"stdout": '{"files": {"main.py": "candidate edit"}}\n'}
            return {"stdout": ""}

    class SerializedE2B:
        def __init__(self, _api_key):
            pass

        def create_sandbox(self):
            with calls_guard:
                create_calls.append("create")
            create_entered.set()
            assert release_create.wait(timeout=5)
            return SerializedSandbox()

        def connect_sandbox(self, session_id):
            with calls_guard:
                connect_calls.append(session_id)
            return SerializedSandbox()

        def get_sandbox_id(self, sandbox):
            return sandbox.sandbox_id

        def close_sandbox(self, _sandbox):
            raise AssertionError("No serialized start sandbox should be discarded")

    monkeypatch.setattr(assessment_service.settings, "E2B_API_KEY", "test-e2b")
    monkeypatch.setattr(assessment_service, "E2BService", SerializedE2B)
    monkeypatch.setattr(
        assessment_service,
        "resolve_ai_mode",
        lambda: "claude_cli_terminal",
    )

    def run_start(*, second: bool) -> None:
        with TestingSessionLocal() as session:
            try:
                current = session.get(Assessment, assessment_id)
                if second:
                    second_started.set()
                results.append(
                    assessment_service.start_or_resume_assessment(current, session)
                )
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

    first = Thread(target=run_start, kwargs={"second": False}, daemon=True)
    second = Thread(target=run_start, kwargs={"second": True}, daemon=True)
    first.start()
    assert create_entered.wait(timeout=3)
    second.start()
    assert second_started.wait(timeout=3)
    second.join(timeout=0.15)
    assert second.is_alive() is True
    with calls_guard:
        assert create_calls == ["create"]
        assert connect_calls == []

    release_create.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert first.is_alive() is False
    assert second.is_alive() is False
    assert errors == []
    assert len(results) == 2
    assert {result["sandbox_id"] for result in results} == {"serialized-start-sandbox"}
    assert create_calls == ["create"]
    assert connect_calls == ["serialized-start-sandbox"]
    db.expire_all()
    stored = db.get(Assessment, assessment_id)
    assert stored.status == AssessmentStatus.IN_PROGRESS
    assert [event["event_type"] for event in stored.timeline].count(
        "assessment_started"
    ) == 1


def test_execute_and_save_detach_provider_io_then_persist_exact_events(db, monkeypatch):
    assessment = _seed_assessment(
        db,
        status=AssessmentStatus.IN_PROGRESS,
        session_id="existing-runtime-sandbox",
        branch=True,
    )
    assessment_id = int(assessment.id)
    token = str(assessment.token)

    class CheckedRuntime:
        def connect_sandbox(self, session_id):
            assert db.in_transaction() is False
            return _ProviderCheckedSandbox(db, session_id)

        def get_sandbox_id(self, sandbox):
            assert db.in_transaction() is False
            return sandbox.sandbox_id

        def execute_code(self, _sandbox, code):
            assert db.in_transaction() is False
            return {"stdout": code, "stderr": "", "tests_passed": 1, "tests_total": 1}

        def close_sandbox(self, _sandbox):
            raise AssertionError("A connected durable sandbox must not be closed")

    runtime = CheckedRuntime()
    monkeypatch.setattr(
        candidate_runtime_routes,
        "build_sandbox_adapter",
        lambda: runtime,
    )

    execute_result = candidate_runtime_routes.execute_code(
        assessment_id,
        CodeExecutionRequest(code="print('detached')"),
        token,
        db,
    )
    save_result = candidate_runtime_routes.save_repo_file(
        assessment_id,
        RepoFileSaveRequest(path="main.py", content="print('saved')"),
        token,
        db,
    )

    assert execute_result["stdout"] == "print('detached')"
    assert save_result == {
        "success": True,
        "path": "main.py",
        "paths": ["main.py"],
        "file_count": 1,
        "message": "Saved main.py",
    }
    db.expire_all()
    stored = db.get(Assessment, assessment_id)
    assert [event["event_type"] for event in stored.timeline] == [
        "code_execute",
        "repo_file_save",
    ]
    assert all(
        event["session_id"] == "existing-runtime-sandbox" for event in stored.timeline
    )


@pytest.mark.parametrize("operation", ["execute", "save"])
def test_expired_execute_or_save_uses_canonical_timeout_before_provider_io(
    db, monkeypatch, operation,
):
    assessment = _seed_assessment(
        db,
        status=AssessmentStatus.IN_PROGRESS,
        session_id="expired-runtime-sandbox",
        branch=True,
    )
    assessment.started_at = utcnow() - timedelta(minutes=40)
    db.commit()
    finalize_calls: list[bool] = []

    def canonical_timeout(current, request_db, *, workspace_lock_held=False):
        assert current.id == assessment.id
        assert request_db is db
        finalize_calls.append(workspace_lock_held)
        return {"status": "finalized"}

    monkeypatch.setattr(
        assessment_service,
        "finalize_timed_out_assessment",
        canonical_timeout,
    )
    monkeypatch.setattr(
        candidate_runtime_routes,
        "build_sandbox_adapter",
        lambda: pytest.fail("expired workspace provider must not be opened"),
    )

    with pytest.raises(HTTPException) as exc_info:
        if operation == "execute":
            candidate_runtime_routes.execute_code(
                assessment.id,
                CodeExecutionRequest(code="print('too late')"),
                assessment.token,
                db,
            )
        else:
            candidate_runtime_routes.save_repo_file(
                assessment.id,
                RepoFileSaveRequest(path="main.py", content="too late"),
                assessment.token,
                db,
            )

    assert exc_info.value.status_code == 409
    assert finalize_calls == [False]


@pytest.mark.parametrize("drift", ["authority", "session", "task"])
def test_execute_rejects_post_provider_exact_drift_without_timeline_write(
    db,
    monkeypatch,
    drift,
):
    assessment = _seed_assessment(
        db,
        status=AssessmentStatus.IN_PROGRESS,
        session_id="drift-runtime-sandbox",
        branch=True,
    )
    assessment_id = int(assessment.id)
    task_id = int(assessment.task_id)
    token = str(assessment.token)

    class DriftingRuntime:
        def connect_sandbox(self, session_id):
            assert db.in_transaction() is False
            return _ProviderCheckedSandbox(db, session_id)

        def get_sandbox_id(self, sandbox):
            return sandbox.sandbox_id

        def execute_code(self, _sandbox, _code):
            assert db.in_transaction() is False
            with TestingSessionLocal() as writer:
                if drift == "task":
                    task = writer.get(Task, task_id)
                    task.repo_structure = {
                        "name": "detached-workspace",
                        "files": {"main.py": "changed during provider call"},
                    }
                else:
                    current = writer.get(Assessment, assessment_id)
                    if drift == "authority":
                        current.is_voided = True
                    else:
                        current.e2b_session_id = "replacement-runtime-sandbox"
                writer.commit()
            return {"stdout": "ran", "stderr": ""}

        def close_sandbox(self, _sandbox):
            raise AssertionError("The existing workspace must remain available")

    monkeypatch.setattr(
        candidate_runtime_routes,
        "build_sandbox_adapter",
        DriftingRuntime,
    )

    with pytest.raises(HTTPException) as exc_info:
        candidate_runtime_routes.execute_code(
            assessment_id,
            CodeExecutionRequest(code="print('race')"),
            token,
            db,
        )

    assert exc_info.value.status_code == 409
    db.expire_all()
    stored = db.get(Assessment, assessment_id)
    if drift == "authority":
        assert stored.is_voided is True
    elif drift == "session":
        assert stored.e2b_session_id == "replacement-runtime-sandbox"
    else:
        assert db.get(Task, task_id).repo_structure["files"]["main.py"] == (
            "changed during provider call"
        )
    assert stored.timeline in (None, [])
