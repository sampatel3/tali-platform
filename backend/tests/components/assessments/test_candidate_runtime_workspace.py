from types import SimpleNamespace

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
        SimpleNamespace(path="../secrets.txt", content="nope"),
    ]

    assert candidate_runtime_routes._normalize_runtime_repo_files(entries) == {
        "src/main.py": "print('ok')",
    }


