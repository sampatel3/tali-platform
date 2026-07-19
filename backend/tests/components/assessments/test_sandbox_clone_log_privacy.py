from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.components.assessments import service as assessment_service


class _SandboxResult:
    def __init__(self, payload: object):
        self.stdout = json.dumps(payload)


class _RecordingSandbox:
    def __init__(self, payload: object):
        self.payload = payload
        self.code = ""

    def run_code(self, code: str):
        self.code = code
        return _SandboxResult(self.payload)


def test_permission_failure_keeps_sandbox_stderr_and_repo_name_out_of_logs(caplog):
    secret = "candidate-private-path bearer-secret"
    repo_root = f"/workspace/{secret}"
    sandbox = _RecordingSandbox(
        {
            "success": False,
            "returncode": 13,
            # Legacy sandboxes may still return this field during a rollout.
            "stderr": f"chmod failed for {secret}",
        }
    )

    assert assessment_service._ensure_workspace_repo_permissions(
        sandbox,
        repo_root,
        task_id=41,
        assessment_id=73,
    ) is False

    assert "stderr" not in sandbox.code
    assert secret not in caplog.text
    assert "task_id=41" in caplog.text
    assert "assessment_id=73" in caplog.text
    assert "returncode=13" in caplog.text


def test_clone_failure_returns_only_code_and_never_logs_credentials(
    monkeypatch,
    tmp_path,
    caplog,
):
    secret = "github-token-private-value"

    class _RepositoryService:
        mock_root = Path(tmp_path)

        def __init__(self, *_args, **_kwargs):
            pass

        def authenticated_repo_url(self, _repo_url: str) -> str:
            return f"https://x-access-token:{secret}@example.test/private.git"

    monkeypatch.setattr(
        assessment_service,
        "AssessmentRepositoryService",
        _RepositoryService,
    )
    sandbox = _RecordingSandbox(
        {
            "returncode": 128,
            # A rolling deploy can feed a result emitted by the previous code.
            "stderr": f"fatal: could not clone https://x-access-token:{secret}@example.test/private.git",
        }
    )
    assessment = SimpleNamespace(
        id=73,
        assessment_repo_url="https://example.test/private.git",
        assessment_branch="candidate/private-user-branch",
    )
    task = SimpleNamespace(id=41, repo_structure={"name": "private-user-workspace"})

    assert assessment_service._clone_assessment_branch_into_workspace(
        sandbox,
        assessment,
        task,
    ) is False

    assert "p.stderr" not in sandbox.code
    assert "'stderr'" not in sandbox.code
    assert secret not in caplog.text
    assert "private-user-workspace" not in caplog.text
    assert "candidate/private-user-branch" not in caplog.text
    assert "assessment_id=73" in caplog.text
    assert "task_id=41" in caplog.text
    assert "returncode=128" in caplog.text


def test_repo_snapshot_failure_never_logs_user_workspace_name(caplog):
    secret = "candidate-private-repository-name"
    sandbox = _RecordingSandbox(["invalid"])

    assert assessment_service._read_sandbox_repo_files(
        sandbox,
        f"/workspace/{secret}",
    ) is None

    assert secret not in caplog.text
    assert "stage=file_snapshot error_type=AttributeError" in caplog.text
