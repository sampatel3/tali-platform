"""Focused security contracts for the unauthenticated candidate runtime."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.domains.assessments_runtime import candidate_runtime_routes as runtime
from app.domains.assessments_runtime import candidate_workspace_routes as workspace_runtime
from app.domains.assessments_runtime import candidate_workspace as workspace
from app.domains.assessments_runtime.candidate_auth import validate_runtime_candidate_session
from app.components.assessments.submission_runtime import _build_submission_artifact
from app.models.assessment import Assessment, AssessmentStatus
from app.models.task import Task
from app.platform.middleware import (
    is_candidate_assessment_path,
    redact_sensitive_request_path,
)
from tests.candidate_proof_helpers import (
    CandidateProofSigner,
    compact_json_body,
    signed_candidate_headers,
)

CANDIDATE_SESSION_KEY = "S" * 43
PROOF_SIGNER = CandidateProofSigner()


def _post_candidate(client, assessment, path: str, payload: object, *, session_key: str = CANDIDATE_SESSION_KEY, token: str | None = None):
    raw_body = compact_json_body(payload)
    headers = signed_candidate_headers(
        PROOF_SIGNER,
        token=token or assessment.token,
        session_key=session_key,
        method="POST",
        path_and_query=path,
        raw_body=raw_body,
    )
    return client.post(
        path,
        content=raw_body,
        headers={"Content-Type": "application/json", **headers},
    )


def _get_candidate(client, assessment, path_and_query: str):
    headers = signed_candidate_headers(
        PROOF_SIGNER,
        token=assessment.token,
        session_key=CANDIDATE_SESSION_KEY,
        method="GET",
        path_and_query=path_and_query,
    )
    return client.get(path_and_query, headers=headers)


@pytest.mark.parametrize(
    "path",
    [
        ".git/config",
        ".GIT/HEAD",
        ".venv/bin/python",
        "src/node_modules/pkg/index.js",
        ".github/workflows/push.yml",
        ".gitmodules",
        ".env",
        "src/../.git/config",
    ],
)
def test_candidate_repo_path_rejects_control_state(path: str) -> None:
    assert workspace.sanitize_repo_path(path) == ""


def test_candidate_start_response_preserves_only_explicit_clipboard_accommodation() -> None:
    response = runtime._candidate_start_response(
        {
            "assessment_id": 7,
            "task": {"repo_structure": {"files": {"src/main.py": "secret"}}},
            "time_remaining": 1800,
            "allow_external_clipboard": True,
        }
    )
    assert response["allow_external_clipboard"] is True
    assert response["task"]["repo_structure"] == {"files": {"src/main.py": ""}}


def test_candidate_repo_snapshot_rejects_duplicates_and_excess_new_files() -> None:
    task = SimpleNamespace(repo_structure={"files": {"src/main.py": ""}})
    with pytest.raises(HTTPException) as duplicate:
        workspace.normalize_runtime_repo_files(
            [
                SimpleNamespace(path="src/main.py", content="one"),
                SimpleNamespace(path="src/main.py", content="two"),
            ],
            task=task,
        )
    assert duplicate.value.status_code == 400

    with pytest.raises(HTTPException) as too_many_new:
        workspace.normalize_runtime_repo_files(
            [SimpleNamespace(path=f"generated/{index}.py", content="") for index in range(21)],
            task=task,
        )
    assert too_many_new.value.status_code == 413


def test_candidate_repo_target_inspection_fails_closed_on_symlink() -> None:
    sandbox = SimpleNamespace(
        run_code=lambda _code: {
            "stdout": json.dumps(
                {"safe": False, "exists": True, "kind": "file", "reason": "symlink"}
            )
        }
    )
    with pytest.raises(HTTPException) as exc:
        workspace._require_writable_regular_target(
            sandbox,
            "/workspace/task",
            "src/main.py",
        )
    assert exc.value.status_code == 400


def test_candidate_execution_result_caps_output_and_strips_internal_metadata() -> None:
    result = workspace.bounded_execution_result(
        {
            "stdout": "x" * 20_000,
            "stderr": "y" * 20_000,
            "sandbox_id": "secret-sandbox",
            "repo_url": "https://credential.example/repo",
        },
        repo_root="/workspace/task",
    )
    assert len(result["stdout"]) < 20_000
    assert len(result["stderr"]) < 20_000
    assert "truncated" in result["stdout"]
    assert "sandbox_id" not in result
    assert "repo_url" not in result


def test_candidate_start_history_strips_tool_inputs_results_and_code_context() -> None:
    response = runtime._candidate_start_response(
        {
            "assessment_id": 7,
            "time_remaining": 1200,
            "task": {"repo_structure": {"files": {"src/main.py": "secret"}}},
            "ai_prompts": [
                {
                    "message": "Please inspect it",
                    "response": "I found the issue.",
                    "opener": False,
                    "code_after": "workspace contents",
                    "code_context": "private context",
                    "tool_calls_made": [
                        {
                            "name": "write_file",
                            "input": {"content": "full private file"},
                            "result": "full tool output",
                        }
                    ],
                    "interrogation_state": {"secret": "internal"},
                }
            ],
        }
    )

    assert response["ai_prompts"] == [
        {
            "message": "Please inspect it",
            "response": "I found the issue.",
            "opener": False,
        }
    ]
    assert response["task"]["repo_structure"] == {"files": {"src/main.py": ""}}


@pytest.mark.parametrize("is_demo", [False, True])
def test_every_live_runtime_requires_bound_candidate_session(is_demo: bool) -> None:
    with pytest.raises(HTTPException) as missing:
        validate_runtime_candidate_session(SimpleNamespace(is_demo=is_demo), None)
    assert missing.value.status_code == 403


def test_live_start_binds_one_candidate_session_and_rejects_a_second_browser(
    client, db, monkeypatch,
) -> None:
    task = Task(
        name="Session binding",
        task_key="session-binding",
        repo_structure={"files": {"src/main.py": "print('ok')"}},
        duration_minutes=30,
    )
    db.add(task)
    db.flush()
    assessment = Assessment(
        task_id=task.id,
        token="session-binding-token",
        status=AssessmentStatus.PENDING,
        duration_minutes=30,
    )
    db.add(assessment)
    db.commit()

    def fake_start(row, session):
        session.commit()
        return {
            "assessment_id": row.id,
            "task": {"repo_structure": {"files": {"src/main.py": "secret"}}},
            "time_remaining": 1800,
        }

    monkeypatch.setattr(runtime, "start_or_resume_assessment", fake_start)
    url = f"/api/v1/assessments/token/{assessment.token}/start"

    missing = client.post(url)
    assert missing.status_code == 422

    first_body = PROOF_SIGNER.start_body(session_key=CANDIDATE_SESSION_KEY)
    first = client.post(
        url,
        content=first_body,
        headers={
            "Content-Type": "application/json",
            **PROOF_SIGNER.headers(method="POST", path_and_query=url, raw_body=first_body),
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["task"]["repo_structure"] == {"files": {"src/main.py": ""}}
    db.refresh(assessment)
    assert assessment.candidate_session_hash != CANDIDATE_SESSION_KEY
    bound_events = [
        event
        for event in assessment.timeline or []
        if event.get("event_type") == "candidate_session_bound"
    ]
    assert len(bound_events) == 1

    resumed_body = PROOF_SIGNER.start_body(session_key=CANDIDATE_SESSION_KEY)
    resumed = client.post(
        url,
        content=resumed_body,
        headers={
            "Content-Type": "application/json",
            **PROOF_SIGNER.headers(method="POST", path_and_query=url, raw_body=resumed_body),
        },
    )
    assert resumed.status_code == 200
    rejected_body = PROOF_SIGNER.start_body(session_key="B" * 43)
    rejected = client.post(
        url,
        content=rejected_body,
        headers={
            "Content-Type": "application/json",
            **PROOF_SIGNER.headers(method="POST", path_and_query=url, raw_body=rejected_body),
        },
    )
    assert rejected.status_code == 409


def test_execute_requires_selected_repo_file_before_sandbox_access(client, db, monkeypatch) -> None:
    task = Task(
        name="Selected file only",
        task_key="selected-file-only",
        repo_structure={"files": {"src/main.py": "print('ok')"}},
        duration_minutes=30,
    )
    db.add(task)
    db.flush()
    assessment = Assessment(
        task_id=task.id,
        token="selected-file-token",
        status=AssessmentStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        duration_minutes=30,
    )
    PROOF_SIGNER.bind_assessment(assessment, session_key=CANDIDATE_SESSION_KEY)
    db.add(assessment)
    db.commit()

    monkeypatch.setattr(
        workspace_runtime,
        "build_sandbox_adapter",
        lambda: pytest.fail("raw execution must be rejected before sandbox access"),
    )
    path = f"/api/v1/assessments/{assessment.id}/execute"
    response = _post_candidate(
        client,
        assessment,
        path,
        {"code": "print('raw')"},
    )
    assert response.status_code == 400
    assert "Raw code execution is disabled" in response.json()["detail"]


def test_live_routes_reject_wrong_session_and_bulk_workspace_snapshots(
    client, db, monkeypatch,
) -> None:
    task = Task(
        name="Single-file workspace",
        task_key="single-file-workspace",
        repo_structure={"files": {"src/main.py": "print('ok')"}},
        duration_minutes=30,
    )
    db.add(task)
    db.flush()
    assessment = Assessment(
        task_id=task.id,
        token="single-file-token",
        status=AssessmentStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc),
        duration_minutes=30,
    )
    PROOF_SIGNER.bind_assessment(assessment, session_key=CANDIDATE_SESSION_KEY)
    db.add(assessment)
    db.commit()
    monkeypatch.setattr(
        workspace_runtime,
        "build_sandbox_adapter",
        lambda: pytest.fail("rejected requests must not access the sandbox"),
    )

    execute_path = f"/api/v1/assessments/{assessment.id}/execute"
    wrong_session = _post_candidate(
        client,
        assessment,
        execute_path,
        {"code": "print('x')", "selected_file_path": "src/main.py"},
        session_key="W" * 43,
    )
    assert wrong_session.status_code == 403

    bulk_execute = _post_candidate(
        client,
        assessment,
        execute_path,
        {
            "code": "print('x')",
            "selected_file_path": "src/main.py",
            "repo_files": [{"path": "src/main.py", "content": "changed"}],
        },
    )
    assert bulk_execute.status_code == 400
    assert "Bulk repository replacement" in bulk_execute.json()["detail"]

    save_path = f"/api/v1/assessments/{assessment.id}/repo-file"
    bulk_save = _post_candidate(
        client,
        assessment,
        save_path,
        {"files": [{"path": "src/main.py", "content": "changed"}]},
    )
    assert bulk_save.status_code == 400
    assert "Bulk repository replacement" in bulk_save.json()["detail"]

    bulk_submit = _post_candidate(
        client,
        assessment,
        f"/api/v1/assessments/{assessment.id}/submit",
        {
            "final_code": "changed",
            "repo_files": [{"path": "src/main.py", "content": "changed"}],
        },
    )
    assert bulk_submit.status_code == 400
    assert "Bulk repository replacement" in bulk_submit.json()["detail"]


def test_submit_retry_returns_the_existing_durable_receipt(client, db) -> None:
    task = Task(
        name="Idempotent submission receipt",
        task_key="idempotent-submission-receipt",
        repo_structure={"files": {"src/main.py": "starter\n"}},
        duration_minutes=30,
    )
    db.add(task)
    db.flush()
    artifact = _build_submission_artifact({"src/main.py": "candidate work\n"})
    captured_at = datetime.now(timezone.utc)
    assessment = Assessment(
        task_id=task.id,
        token="idempotent-submission-token",
        status=AssessmentStatus.COMPLETED,
        started_at=captured_at - timedelta(minutes=5),
        completed_at=captured_at,
        duration_minutes=30,
        submission_artifact=artifact,
        submission_artifact_sha256=artifact["sha256"],
        submission_artifact_captured_at=captured_at,
        code_snapshots=[{"final": "candidate work accepted by the first request"}],
        tab_switch_count=3,
        scoring_partial=True,
        scoring_failed=False,
    )
    PROOF_SIGNER.bind_assessment(assessment, session_key=CANDIDATE_SESSION_KEY)
    db.add(assessment)
    db.commit()
    path = f"/api/v1/assessments/{assessment.id}/submit"
    response = _post_candidate(
        client,
        assessment,
        path,
        {
            "final_code": "a stale browser retry is ignored",
            "tab_switch_count": 99,
        },
    )

    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["success"] is True
    assert receipt["grading_status"] == "pending"
    assert receipt["artifact_gate"]["artifact_sha256"] == artifact["sha256"]
    db.refresh(assessment)
    assert assessment.code_snapshots == [
        {"final": "candidate work accepted by the first request"}
    ]
    assert assessment.tab_switch_count == 3


def test_submit_at_deadline_returns_the_timeout_capture_receipt(
    client, db, monkeypatch
) -> None:
    task = Task(
        name="Deadline submission receipt",
        task_key="deadline-submission-receipt",
        repo_structure={"files": {"src/main.py": "starter\n"}},
        duration_minutes=30,
    )
    db.add(task)
    db.flush()
    assessment = Assessment(
        task_id=task.id,
        token="deadline-submission-token",
        status=AssessmentStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        duration_minutes=30,
    )
    PROOF_SIGNER.bind_assessment(assessment, session_key=CANDIDATE_SESSION_KEY)
    db.add(assessment)
    db.commit()

    artifact = _build_submission_artifact({"src/main.py": "candidate work\n"})
    captured_at = datetime.now(timezone.utc)

    def freeze_on_timeout(expired_assessment, session) -> None:
        expired_assessment.status = AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT
        expired_assessment.completed_due_to_timeout = True
        expired_assessment.completed_at = captured_at
        expired_assessment.submission_artifact = artifact
        expired_assessment.submission_artifact_sha256 = artifact["sha256"]
        expired_assessment.submission_artifact_captured_at = captured_at
        expired_assessment.scoring_partial = True
        session.commit()
        raise HTTPException(
            status_code=409,
            detail="Assessment time expired and was auto-submitted",
        )

    monkeypatch.setattr(runtime, "enforce_active_or_timeout", freeze_on_timeout)
    monkeypatch.setattr(
        runtime,
        "_submit_assessment",
        lambda *_args, **_kwargs: pytest.fail(
            "a successful timeout capture must not run explicit submission"
        ),
    )

    path = f"/api/v1/assessments/{assessment.id}/submit"
    response = _post_candidate(
        client,
        assessment,
        path,
        {"final_code": "the browser's final editor buffer"},
    )

    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["success"] is True
    assert receipt["grading_status"] == "pending"
    assert receipt["artifact_gate"]["artifact_sha256"] == artifact["sha256"]


def test_single_file_save_records_hash_only_telemetry(client, db, monkeypatch) -> None:
    task = Task(
        name="Hash-only save telemetry",
        task_key="hash-only-save-telemetry",
        repo_structure={"files": {"src/main.py": "print('ok')"}},
        duration_minutes=30,
    )
    db.add(task)
    db.flush()
    assessment = Assessment(
        task_id=task.id,
        token="hash-only-save-token",
        status=AssessmentStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc),
        duration_minutes=30,
    )
    PROOF_SIGNER.bind_assessment(assessment, session_key=CANDIDATE_SESSION_KEY)
    db.add(assessment)
    db.commit()

    original = "print('ok')"
    writes: list[tuple[str, str]] = []
    sandbox = SimpleNamespace(
        files=SimpleNamespace(
            read=lambda _path: original,
            write=lambda path, content: writes.append((path, content)),
        ),
        run_code=lambda _code: {
            "stdout": json.dumps(
                {"safe": True, "exists": True, "kind": "file", "size": 11}
            )
        },
    )
    monkeypatch.setattr(workspace_runtime, "build_sandbox_adapter", lambda: object())
    monkeypatch.setattr(
        runtime,
        "_connect_assessment_sandbox",
        lambda _e2b, _assessment, _task, _db: (sandbox, "/workspace/hash-only"),
    )
    content = "print('changed')\n"
    response = _post_candidate(
        client,
        assessment,
        f"/api/v1/assessments/{assessment.id}/repo-file",
        {
            "path": "src/main.py",
            "content": content,
            "base_revision": hashlib.sha256(original.encode("utf-8")).hexdigest(),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert writes == [("/workspace/hash-only/src/main.py", content)]
    db.refresh(assessment)
    event = next(
        event
        for event in reversed(assessment.timeline or [])
        if event.get("event_type") == "repo_file_save"
    )
    assert event["content_sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert event["source"] == "repo_file_api"
    assert content not in json.dumps(event)


def test_single_file_save_rejects_stale_revision_without_overwrite(
    client, db, monkeypatch,
) -> None:
    task = Task(
        name="Revision-safe save",
        task_key="revision-safe-save",
        repo_structure={"files": {"src/main.py": "old"}},
        duration_minutes=30,
    )
    db.add(task)
    db.flush()
    assessment = Assessment(
        task_id=task.id,
        token="revision-safe-save-token",
        status=AssessmentStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc),
        duration_minutes=30,
    )
    PROOF_SIGNER.bind_assessment(assessment, session_key=CANDIDATE_SESSION_KEY)
    db.add(assessment)
    db.commit()

    writes: list[tuple[str, str]] = []
    sandbox = SimpleNamespace(
        files=SimpleNamespace(
            read=lambda _path: "changed-by-claude",
            write=lambda path, content: writes.append((path, content)),
        ),
        run_code=lambda _code: {
            "stdout": json.dumps(
                {"safe": True, "exists": True, "kind": "file", "size": 17}
            )
        },
    )
    monkeypatch.setattr(workspace_runtime, "build_sandbox_adapter", lambda: object())
    monkeypatch.setattr(
        runtime,
        "_connect_assessment_sandbox",
        lambda _e2b, _assessment, _task, _db: (sandbox, "/workspace/revision-safe"),
    )

    response = _post_candidate(
        client,
        assessment,
        f"/api/v1/assessments/{assessment.id}/repo-file",
        {
            "path": "src/main.py",
            "content": "candidate-buffer",
            "base_revision": hashlib.sha256(b"old").hexdigest(),
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {
        "code": "FILE_REVISION_CONFLICT",
        "message": "This file changed in the workspace. Review the latest version before overwriting it.",
        "path": "src/main.py",
        "current_revision": hashlib.sha256(b"changed-by-claude").hexdigest(),
    }
    assert writes == []


def test_new_file_save_accepts_explicit_null_base_revision(
    client, db, monkeypatch,
) -> None:
    task = Task(
        name="Revision-safe new file",
        task_key="revision-safe-new-file",
        repo_structure={"files": {"src/main.py": "old"}},
        duration_minutes=30,
    )
    db.add(task)
    db.flush()
    assessment = Assessment(
        task_id=task.id,
        token="revision-safe-new-file-token",
        status=AssessmentStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc),
        duration_minutes=30,
    )
    PROOF_SIGNER.bind_assessment(assessment, session_key=CANDIDATE_SESSION_KEY)
    db.add(assessment)
    db.commit()

    writes: list[tuple[str, str]] = []
    sandbox = SimpleNamespace(
        files=SimpleNamespace(write=lambda path, content: writes.append((path, content))),
        run_code=lambda _code: {
            "stdout": json.dumps(
                {"safe": True, "exists": False, "kind": "missing", "reason": None}
            )
        },
    )
    monkeypatch.setattr(workspace_runtime, "build_sandbox_adapter", lambda: object())
    monkeypatch.setattr(
        runtime,
        "_connect_assessment_sandbox",
        lambda _e2b, _assessment, _task, _db: (sandbox, "/workspace/revision-new"),
    )

    content = "candidate-created\n"
    response = _post_candidate(
        client,
        assessment,
        f"/api/v1/assessments/{assessment.id}/repo-file",
        {"path": "notes.md", "content": content, "base_revision": None},
    )
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert writes == [("/workspace/revision-new/notes.md", content)]


def test_signed_keepalive_touches_existing_sandbox_without_workspace_lease(
    client, db, monkeypatch,
) -> None:
    task = Task(
        name="Keepalive",
        task_key="signed-keepalive",
        repo_structure={"files": {"src/main.py": "old"}},
        duration_minutes=30,
    )
    db.add(task)
    db.flush()
    assessment = Assessment(
        task_id=task.id,
        token="signed-keepalive-token",
        status=AssessmentStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc),
        duration_minutes=30,
        e2b_session_id="sandbox-keepalive",
    )
    PROOF_SIGNER.bind_assessment(assessment, session_key=CANDIDATE_SESSION_KEY)
    db.add(assessment)
    db.commit()

    calls: list[tuple[str, object]] = []
    sandbox = object()
    adapter = SimpleNamespace(
        connect_sandbox=lambda sandbox_id: (
            calls.append(("connect", sandbox_id)) or sandbox
        ),
        touch_sandbox=lambda target: calls.append(("touch", target)),
    )
    monkeypatch.setattr(workspace_runtime, "build_sandbox_adapter", lambda: adapter)

    response = _post_candidate(
        client,
        assessment,
        f"/api/v1/assessments/{assessment.id}/keepalive",
        {},
    )
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    assert response.json()["time_remaining"] > 0
    assert calls == [("connect", "sandbox-keepalive"), ("touch", sandbox)]


def test_signed_keepalive_reports_timeout_renewal_failure(client, db, monkeypatch) -> None:
    task = Task(
        name="Expired keepalive",
        task_key="expired-signed-keepalive",
        repo_structure={"files": {"src/main.py": "old"}},
        duration_minutes=30,
    )
    db.add(task)
    db.flush()
    assessment = Assessment(
        task_id=task.id,
        token="expired-signed-keepalive-token",
        status=AssessmentStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc),
        duration_minutes=30,
        e2b_session_id="expired-sandbox-keepalive",
    )
    PROOF_SIGNER.bind_assessment(assessment, session_key=CANDIDATE_SESSION_KEY)
    db.add(assessment)
    db.commit()

    adapter = SimpleNamespace(
        connect_sandbox=lambda _sandbox_id: object(),
        touch_sandbox=lambda _sandbox: (_ for _ in ()).throw(RuntimeError("expired")),
    )
    monkeypatch.setattr(workspace_runtime, "build_sandbox_adapter", lambda: adapter)

    response = _post_candidate(
        client,
        assessment,
        f"/api/v1/assessments/{assessment.id}/keepalive",
        {},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "The workspace could not be kept active. Your saved work has not been replaced."
    )


def test_repo_file_get_loads_one_utf8_file_and_records_server_event(
    client, db, monkeypatch,
) -> None:
    task = Task(
        name="Lazy file",
        task_key="lazy-file",
        repo_structure={"files": {"src/main.py": "print('ok')"}},
        duration_minutes=30,
    )
    db.add(task)
    db.flush()
    assessment = Assessment(
        task_id=task.id,
        token="lazy-file-token",
        status=AssessmentStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        duration_minutes=30,
    )
    PROOF_SIGNER.bind_assessment(assessment, session_key=CANDIDATE_SESSION_KEY)
    db.add(assessment)
    db.commit()

    files = SimpleNamespace(read=lambda target: "print('loaded')\n")
    sandbox = SimpleNamespace(
        files=files,
        run_code=lambda _code: {
            "stdout": json.dumps(
                {"safe": True, "exists": True, "kind": "file", "size": 16, "reason": None}
            )
        },
    )
    monkeypatch.setattr(workspace_runtime, "build_sandbox_adapter", lambda: object())
    monkeypatch.setattr(
        runtime,
        "_connect_assessment_sandbox",
        lambda _e2b, _assessment, _task, _db: (sandbox, "/workspace/lazy-file"),
    )

    response = _get_candidate(
        client,
        assessment,
        f"/api/v1/assessments/{assessment.id}/repo-file?path=src%2Fmain.py",
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "path": "src/main.py",
        "content": "print('loaded')\n",
        "revision": hashlib.sha256("print('loaded')\n".encode("utf-8")).hexdigest(),
    }
    db.refresh(assessment)
    events = [event for event in assessment.timeline or [] if event.get("event_type") == "file_opened"]
    assert len(events) == 1
    assert events[0]["path"] == "src/main.py"
    assert events[0]["byte_length"] == len("print('loaded')\n".encode("utf-8"))
    assert events[0]["timestamp"]


def test_repo_file_get_rejects_protected_path_before_sandbox_access(
    client, db, monkeypatch,
) -> None:
    task = Task(
        name="Protected lazy file",
        task_key="protected-lazy-file",
        repo_structure={"files": {"src/main.py": ""}},
        duration_minutes=30,
    )
    db.add(task)
    db.flush()
    assessment = Assessment(
        task_id=task.id,
        token="protected-lazy-file-token",
        status=AssessmentStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc),
        duration_minutes=30,
    )
    PROOF_SIGNER.bind_assessment(assessment, session_key=CANDIDATE_SESSION_KEY)
    db.add(assessment)
    db.commit()
    monkeypatch.setattr(
        workspace_runtime,
        "build_sandbox_adapter",
        lambda: pytest.fail("protected paths must fail before sandbox access"),
    )

    response = _get_candidate(
        client,
        assessment,
        f"/api/v1/assessments/{assessment.id}/repo-file?path=.git%2Fconfig",
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    ("raw_content", "expected_status"),
    [
        (b"\xff\xfe", 415),
        (b"x" * 500_001, 413),
        ("text\x00binary", 415),
    ],
)
def test_repo_file_get_rejects_binary_or_oversized_content(
    client, db, monkeypatch, raw_content, expected_status,
) -> None:
    task = Task(
        name="Bounded lazy file",
        task_key=f"bounded-lazy-file-{expected_status}-{type(raw_content).__name__}",
        repo_structure={"files": {"artifact.bin": ""}},
        duration_minutes=30,
    )
    db.add(task)
    db.flush()
    assessment = Assessment(
        task_id=task.id,
        token=f"bounded-lazy-token-{expected_status}-{type(raw_content).__name__}",
        status=AssessmentStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc),
        duration_minutes=30,
    )
    PROOF_SIGNER.bind_assessment(assessment, session_key=CANDIDATE_SESSION_KEY)
    db.add(assessment)
    db.commit()
    sandbox = SimpleNamespace(
        files=SimpleNamespace(read=lambda _target: raw_content),
        run_code=lambda _code: {
            "stdout": json.dumps(
                {
                    "safe": True,
                    "exists": True,
                    "kind": "file",
                    "size": len(raw_content if isinstance(raw_content, bytes) else raw_content.encode("utf-8")),
                    "reason": None,
                }
            )
        },
    )
    monkeypatch.setattr(workspace_runtime, "build_sandbox_adapter", lambda: object())
    monkeypatch.setattr(
        runtime,
        "_connect_assessment_sandbox",
        lambda _e2b, _assessment, _task, _db: (sandbox, "/workspace/bounded"),
    )

    response = _get_candidate(
        client,
        assessment,
        f"/api/v1/assessments/{assessment.id}/repo-file?path=artifact.bin",
    )
    assert response.status_code == expected_status


def test_candidate_responses_are_no_store_and_restrict_browser_capabilities(client) -> None:
    response = client.get("/api/v1/assessments/token/not-a-real-token/preview")
    assert response.status_code == 404
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "camera=()" in response.headers["permissions-policy"]


def test_candidate_token_paths_are_recognized_and_redacted() -> None:
    raw = "/api/v1/assessments/token/super-secret-bearer/preview"
    assert is_candidate_assessment_path(raw) is True
    redacted = redact_sensitive_request_path(raw)
    assert "super-secret-bearer" not in redacted
    assert redacted == "/api/v1/assessments/token/[REDACTED]/preview"


def test_candidate_request_is_scrubbed_before_observability_export() -> None:
    from app.main import _scrub_sentry_candidate_request

    event = {
        "request": {
            "url": (
                "https://api.example/api/v1/assessments/token/top-secret/start"
                "?path=private/source.py&candidate=secret"
            ),
            "data": {"calibration_warmup_prompt": "candidate text"},
            "cookies": {"session": "secret"},
            "headers": {
                "X-Assessment-Token": "top-secret",
                "X-Assessment-Session": "session-secret",
                "X-Assessment-Key-Id": "proof-key-id",
                "X-Assessment-Proof-Timestamp": "proof-timestamp",
                "X-Assessment-Proof-Nonce": "proof-nonce",
                "X-Assessment-Proof": "proof-signature",
                "User-Agent": "test",
            },
        },
        "transaction": "/api/v1/assessments/token/top-secret/start",
        "breadcrumbs": {
            "values": [
                {
                    "category": "fetch",
                    "data": {
                        "url": (
                            "https://api.example/api/v1/assessments/7/repo-file"
                            "?path=private/source.py"
                        ),
                        "query": "path=private/source.py",
                        "headers": {"X-Assessment-Proof": "proof-signature"},
                    },
                }
            ]
        },
    }
    scrubbed = _scrub_sentry_candidate_request(event)
    assert "top-secret" not in scrubbed["request"]["url"]
    assert "?" not in scrubbed["request"]["url"]
    assert "data" not in scrubbed["request"]
    assert "cookies" not in scrubbed["request"]
    assert scrubbed["request"]["headers"] == {"User-Agent": "test"}
    assert "top-secret" not in scrubbed["transaction"]
    crumb = scrubbed["breadcrumbs"]["values"][0]["data"]
    assert "?" not in crumb["url"]
    assert "private/source.py" not in crumb["url"]
    assert "query" not in crumb
    assert "headers" not in crumb
