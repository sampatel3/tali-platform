from types import SimpleNamespace

from app.domains.assessments_runtime import candidate_claude_routes, candidate_runtime_routes
from app.schemas.assessment import ClaudeRequest, RepoFileSnapshotEntry


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
        SimpleNamespace(path="../secrets.txt", content="nope"),
    ]

    assert candidate_runtime_routes._normalize_runtime_repo_files(entries) == {
        "src/main.py": "print('ok')",
    }


def test_claude_prompt_includes_repo_excerpts_and_selected_file():
    task = SimpleNamespace(
        scenario="Debug the billing worker.",
        description="",
        repo_structure={
            "files": {
                "src/main.py": "print('repo file')\n",
                "README.md": "# Demo repo\n",
            }
        },
    )
    data = ClaudeRequest(
        message="What should I inspect?",
        code_context="print('editor snapshot')",
        selected_file_path="src/main.py",
        repo_files=[
            RepoFileSnapshotEntry(path="src/main.py", content="print('edited file')\n"),
            RepoFileSnapshotEntry(path="tests/test_main.py", content="def test_ok():\n    assert True\n"),
        ],
    )

    prompt = candidate_claude_routes._build_system_prompt(task, data)

    assert "Selected file:\nsrc/main.py" in prompt
    assert "=== src/main.py ===\nprint('edited file')" in prompt
    assert "=== tests/test_main.py ===" in prompt
    assert "Current editor snapshot:\nprint('editor snapshot')" in prompt
    assert "# Demo repo" not in prompt
