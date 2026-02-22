from types import SimpleNamespace

from app.components.assessments.submission_runtime import (
    _parse_test_runner_results,
    _run_task_test_runner,
)


def test_parse_test_runner_results_extracts_passed_failed():
    output = "================= 7 passed, 2 failed in 3.10s ================="
    parsed = _parse_test_runner_results(output, r"(?P<passed>\d+)\s+passed")
    assert parsed["passed"] == 7
    assert parsed["failed"] == 2
    assert parsed["total"] == 9


def test_run_task_test_runner_executes_command_and_parses_output():
    task = SimpleNamespace(
        extra_data={
            "test_runner": {
                "command": "pytest -q --tb=no",
                "working_dir": "/workspace/customer-intelligence-ai",
                "parse_pattern": r"(?P<passed>\d+)\s+passed",
                "timeout_seconds": 60,
            }
        }
    )

    class FakeE2B:
        def run_command(self, sandbox, command, cwd=None, timeout=30):
            assert sandbox == "sandbox"
            assert command == "pytest -q --tb=no"
            assert cwd == "/workspace/customer-intelligence-ai"
            assert timeout == 60
            return {
                "stdout": "================= 5 passed in 1.22s =================",
                "stderr": "",
                "exit_code": 0,
            }

    result = _run_task_test_runner(FakeE2B(), "sandbox", task, "/workspace/repo")
    assert result is not None
    assert result["source"] == "task_test_runner"
    assert result["passed"] == 5
    assert result["failed"] == 0
    assert result["total"] == 5
    assert result["success"] is True
