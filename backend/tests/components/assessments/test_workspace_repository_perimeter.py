from __future__ import annotations

import io
import shutil
import subprocess
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.components.assessments import service as assessment_service


class _LocalFiles:
    def __init__(self):
        self.writes = []

    def write(self, path, content):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.writes.append((str(target), content))


class _LocalSandbox:
    def __init__(self):
        self.files = _LocalFiles()
        self.run_code_calls = []

    def run_code(self, code):
        self.run_code_calls.append(code)
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exec(compile(code, "<sandbox-security-test>", "exec"), {})
        return {"stdout": stdout.getvalue(), "stderr": "", "error": None}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_candidate_workspace_is_fresh_local_only_git(monkeypatch, tmp_path):
    repo_root = tmp_path / "candidate-repo"
    repo_root.mkdir(parents=True)
    (repo_root / "old-candidate-answer.py").write_text("secret = 42\n", encoding="utf-8")
    (repo_root / ".git").mkdir()
    leaked_token = "github_pat_must_not_reach_candidate"
    (repo_root / ".git" / "config").write_text(
        "[remote \"origin\"]\n"
        f"  url = https://x-access-token:{leaked_token}@github.com/acme/private.git\n",
        encoding="utf-8",
    )

    task = SimpleNamespace(
        id=9,
        task_key="security-task",
        repo_structure={
            "files": {
                "README.md": "# Candidate task\n",
                "src/main.py": "def solve():\n    return None\n",
                ".gitignore": ".venv/\n",
            }
        },
    )
    assessment = SimpleNamespace(
        id=42,
        assessment_repo_url=(
            f"https://x-access-token:{leaked_token}@github.com/acme/private.git"
        ),
        assessment_branch="assessment/other-candidate",
    )
    sandbox = _LocalSandbox()
    monkeypatch.setattr(
        assessment_service,
        "_workspace_repo_root",
        lambda _task: str(repo_root),
    )

    assert assessment_service._clone_assessment_branch_into_workspace(
        sandbox, assessment, task
    ) is True

    assert not (repo_root / "old-candidate-answer.py").exists()
    assert (repo_root / "src" / "main.py").read_text(encoding="utf-8").startswith(
        "def solve"
    )
    assert _git(repo_root, "remote") == ""
    assert _git(repo_root, "for-each-ref", "--format=%(refname)", "refs/remotes") == ""
    assert _git(repo_root, "for-each-ref", "--format=%(refname)", "refs/heads") == (
        "refs/heads/candidate"
    )

    git_config = (repo_root / ".git" / "config").read_text(encoding="utf-8")
    assert "[remote " not in git_config
    assert leaked_token not in git_config
    assert leaked_token not in "\n".join(sandbox.run_code_calls)
    assert "assessment/other-candidate" not in "\n".join(sandbox.run_code_calls)
    assert assessment_service._sandbox_workspace_is_ready(sandbox, task) is True

    # A half-created root is not resumable merely because the directory and
    # candidate files exist. Missing Git initialization must fail closed.
    shutil.rmtree(repo_root / ".git")
    assert assessment_service._sandbox_workspace_is_ready(sandbox, task) is False


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "/absolute.py",
        "../outside.py",
        r"C:\Windows\outside.py",
        ".git/config",
        "x" * 256,
        "é" * 128,
        "/".join(["n" * 250] * 17),
    ],
)
def test_candidate_workspace_rejects_unsafe_task_paths_before_e2b_writes(
    unsafe_path,
):
    task = SimpleNamespace(
        id=9,
        task_key="unsafe-task",
        repo_structure={"files": {unsafe_path: "must not be written"}},
    )
    assessment = SimpleNamespace(id=42)
    sandbox = _LocalSandbox()

    assert assessment_service._clone_assessment_branch_into_workspace(
        sandbox,
        assessment,
        task,
    ) is False
    assert sandbox.files.writes == []
    assert sandbox.run_code_calls == []


@pytest.mark.parametrize("root_name", [".", "..", ".git", ".GIT", "r" * 256])
def test_candidate_workspace_rejects_reserved_repository_roots(root_name):
    task = SimpleNamespace(
        id=9,
        task_key="fallback",
        repo_structure={"name": root_name, "files": {"README.md": "task"}},
    )

    with pytest.raises(RuntimeError, match="Unsafe candidate workspace root"):
        assessment_service._workspace_repo_root(task)


def test_candidate_workspace_accepts_filesystem_safe_245_byte_root():
    task = SimpleNamespace(
        id=9,
        task_key="fallback",
        repo_structure={"name": "r" * 245, "files": {"README.md": "task"}},
    )

    assert assessment_service._workspace_repo_root(task) == f"/workspace/{'r' * 245}"
