from types import SimpleNamespace

import pytest

from app.components.assessments import service as assessment_service
from app.domains.assessments_runtime import candidate_runtime_routes


def test_build_run_command_uses_pytest_for_test_files():
    command = candidate_runtime_routes._build_run_command("tests/test_main.py")
    assert 'PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"' in command
    assert '"$PYTHON_BIN" -m pytest -q tests/test_main.py' in command
    assert candidate_runtime_routes._build_run_command("src/main.py") == (
        'export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"; '
        'PYTHON_BIN="./.venv/bin/python"; '
        '[ -x "$PYTHON_BIN" ] || PYTHON_BIN=python; '
        '"$PYTHON_BIN" -m src.main'
    )
    assert candidate_runtime_routes._build_run_command("README.md") is None


def test_build_run_command_uses_task_test_runner_for_test_files():
    task = SimpleNamespace(
        extra_data={
            "test_runner": {
                "command": "./.venv/bin/python -m pytest -q --tb=short",
            }
        }
    )

    command = candidate_runtime_routes._build_run_command("tests/test_revenue_pipeline.py", task=task)
    assert 'PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"' in command
    assert command.endswith("./.venv/bin/python -m pytest -q --tb=short tests/test_revenue_pipeline.py")


def test_python_module_path_supports_package_files():
    assert candidate_runtime_routes._python_module_path("glue_jobs/revenue_pipeline.py") == "glue_jobs.revenue_pipeline"
    assert candidate_runtime_routes._python_module_path("glue_jobs/__init__.py") == "glue_jobs"


def test_normalize_runtime_repo_files_rejects_unsafe_paths():
    entries = [
        SimpleNamespace(path="src/main.py", content="print('ok')"),
        SimpleNamespace(path="src/lib/helper.py", content="HELPER = True"),
        SimpleNamespace(path="../secrets.txt", content="nope"),
        SimpleNamespace(path="..\\windows-escape.txt", content="nope"),
        SimpleNamespace(path="/absolute.txt", content="nope"),
        SimpleNamespace(path=".git/config", content="nope"),
        SimpleNamespace(path="nested/.GiT/hooks/pre-commit", content="nope"),
        SimpleNamespace(path=".git\\hooks\\post-commit", content="nope"),
    ]

    assert candidate_runtime_routes._normalize_runtime_repo_files(entries) == {
        "src/main.py": "print('ok')",
        "src/lib/helper.py": "HELPER = True",
    }


@pytest.mark.parametrize(
    "path",
    [
        ".git/config",
        "nested/.GIT/hooks/pre-commit",
        ".git\\config",
        "../escape.py",
        "..\\escape.py",
        "/absolute.py",
        "\\absolute.py",
    ],
)
def test_runtime_repo_path_rejects_git_control_and_escape_aliases(path):
    assert candidate_runtime_routes._sanitize_repo_path(path) == ""


def test_runtime_repo_path_preserves_safe_nested_file():
    assert (
        candidate_runtime_routes._sanitize_repo_path("src/lib/main.py")
        == "src/lib/main.py"
    )


def test_workspace_root_hashes_special_and_lossy_names_without_collisions():
    safe_task = SimpleNamespace(repo_structure={"name": "safe-task"})
    dot_task = SimpleNamespace(repo_structure={"name": ".."})
    git_task = SimpleNamespace(repo_structure={"name": ".GIT"})
    lossy_task = SimpleNamespace(repo_structure={"name": "safe/task"})

    assert candidate_runtime_routes._workspace_repo_root(safe_task) == (
        "/workspace/safe-task"
    )
    roots = {
        candidate_runtime_routes._workspace_repo_root(task)
        for task in (dot_task, git_task, lossy_task, safe_task)
    }
    assert len(roots) == 4
    assert all(root.startswith("/workspace/") for root in roots)
    assert "/workspace/.." not in roots
    assert all(not root.casefold().endswith("/.git") for root in roots)


def test_repo_exists_parse_failure_never_logs_user_workspace_name(caplog):
    secret = "candidate-private-workspace-name"
    sandbox = SimpleNamespace(run_code=lambda _code: {"stdout": "not-json"})

    assert candidate_runtime_routes._sandbox_repo_exists(
        sandbox, f"/workspace/{secret}"
    ) is False
    assert secret not in caplog.text
    assert "stage=exists_check error_type=JSONDecodeError" in caplog.text


def test_legacy_task_materialization_filters_escape_and_git_control_paths():
    files = assessment_service._repo_files_from_structure(
        {
            "files": {
                "src/main.py": "print('safe')",
                "../escaped.py": "unsafe",
                ".git/config": "unsafe",
                "nested/.GIT/hooks/pre-commit": "unsafe",
                "C:\\absolute.py": "unsafe",
            }
        }
    )

    assert files == [("src/main.py", "print('safe')")]


def test_run_selected_file_does_not_return_or_log_sandbox_exception(caplog):
    secret = "e2b-token=private-value"
    e2b = SimpleNamespace(
        run_command=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(secret)
        )
    )
    task = SimpleNamespace(repo_structure={"name": "safe-task"}, task_key="safe")

    result = candidate_runtime_routes._run_selected_repo_file(
        e2b, object(), task, "src/main.py"
    )

    assert result["error"] == "sandbox_command_failed"
    assert secret not in str(result)
    assert secret not in caplog.text
