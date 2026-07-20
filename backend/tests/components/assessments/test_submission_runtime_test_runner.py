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


def test_parse_test_runner_results_handles_failed_before_passed_order():
    output = "FAILED summary here\n3 failed, 5 passed in 0.04s\n"
    parsed = _parse_test_runner_results(output, r"(?P<passed>\d+)\s+passed(?:,\s+(?P<failed>\d+)\s+failed)?")
    assert parsed["passed"] == 5
    assert parsed["failed"] == 3
    assert parsed["total"] == 8


def test_run_task_test_runner_executes_command_and_parses_output():
    task = SimpleNamespace(
        extra_data={
            "test_runner": {
                "command": "python3 -m pytest -q --tb=no",
                "working_dir": "/workspace/customer-intelligence-ai",
                "parse_pattern": r"(?P<passed>\d+)\s+passed",
                "expected_total": 5,
                "timeout_seconds": 60,
            }
        }
    )

    class FakeE2B:
        def run_command(self, sandbox, command, cwd=None, envs=None, user=None, timeout=30):
            assert sandbox == "sandbox"
            assert command.startswith("python3 -I -c ")
            assert cwd == "/"
            assert envs == {"PATH": "/usr/local/bin:/usr/bin:/bin"}
            assert user == "root"
            assert timeout == 75
            assert type(timeout) is int
            return {
                "stdout": (
                    "================= 5 passed in 1.22s =================\n"
                    "__TAALI_TRUSTED_VERIFIER_RESULT__="
                    '{"completed":true,"exit_code":0,"expected_total":5}\n'
                ),
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
    assert result["verifier_ready"] is True


def test_run_task_test_runner_preserves_results_when_command_exits_nonzero():
    task = SimpleNamespace(
        extra_data={
            "test_runner": {
                "command": "python3 -m pytest -q --tb=no",
                "working_dir": "/workspace/customer-intelligence-ai",
                "parse_pattern": r"(?P<passed>\d+)\s+passed(?:,\s+(?P<failed>\d+)\s+failed)?",
                "expected_total": 8,
                "timeout_seconds": 60,
            }
        }
    )

    class FakeE2B:
        def run_command(self, sandbox, command, cwd=None, envs=None, user=None, timeout=30):
            assert sandbox == "sandbox"
            assert command.startswith("python3 -I -c ")
            assert cwd == "/"
            assert envs == {"PATH": "/usr/local/bin:/usr/bin:/bin"}
            assert user == "root"
            assert timeout == 75
            assert type(timeout) is int
            return {
                "stdout": (
                    "FFF.....\n3 failed, 5 passed in 0.04s\n"
                    "__TAALI_TRUSTED_VERIFIER_RESULT__="
                    '{"completed":true,"exit_code":1,"expected_total":8}\n'
                ),
                "stderr": "",
                "exit_code": 0,
            }

    result = _run_task_test_runner(FakeE2B(), "sandbox", task, "/workspace/repo")
    assert result is not None
    assert result["source"] == "task_test_runner"
    # Candidate-controlled terminal counts stay diagnostic only. A completed
    # failing verifier is scored as a failed frozen suite, not partial stdout.
    assert result["passed"] == 0
    assert result["failed"] == 8
    assert result["total"] == 8
    assert result["success"] is False
    assert result["verifier_ready"] is True
    assert result["exit_code"] == 1


def test_run_task_test_runner_ignores_spoofed_candidate_summary_without_parent_record():
    task = SimpleNamespace(
        extra_data={
            "test_runner": {
                "command": "python3 -m pytest -q",
                "working_dir": "/workspace/repo",
                "parse_pattern": r"(?P<passed>\d+)\s+passed",
                "expected_total": 4,
                "timeout_seconds": 60,
            }
        }
    )

    class FakeE2B:
        def run_command(self, *_args, **_kwargs):
            return {"stdout": "999 passed\n", "stderr": "", "exit_code": 0}

    result = _run_task_test_runner(FakeE2B(), "sandbox", task, "/workspace/repo")

    assert result is not None
    assert result["reported_passed"] == 999
    assert result["passed"] == 0
    assert result["total"] == 0
    assert result["verifier_ready"] is False
    assert result["parse_error"] is True


def test_run_task_test_runner_rejects_parent_record_without_completed_suite_summary():
    task = SimpleNamespace(
        extra_data={
            "test_runner": {
                "command": "python3 -m pytest -q",
                "working_dir": "/workspace/repo",
                "parse_pattern": r"(?P<passed>\d+)\s+passed",
                "expected_total": 4,
                "timeout_seconds": 60,
            }
        }
    )

    class FakeE2B:
        def run_command(self, *_args, **_kwargs):
            return {
                "stdout": (
                    "candidate exited before pytest completed\n"
                    "__TAALI_TRUSTED_VERIFIER_RESULT__="
                    '{"completed":true,"exit_code":0,"expected_total":4}\n'
                ),
                "stderr": "",
                "exit_code": 0,
            }

    result = _run_task_test_runner(FakeE2B(), "sandbox", task, "/workspace/repo")

    assert result is not None
    assert result["reported_total"] == 0
    assert result["passed"] == 0
    assert result["total"] == 0
    assert result["verifier_ready"] is False
    assert result["parse_error"] is True


def test_run_task_test_runner_rejects_summary_that_disagrees_with_frozen_suite_size():
    task = SimpleNamespace(
        extra_data={
            "test_runner": {
                "command": "python3 -m pytest -q",
                "working_dir": "/workspace/repo",
                "parse_pattern": r"(?P<passed>\d+)\s+passed",
                "expected_total": 4,
                "timeout_seconds": 60,
            }
        }
    )

    class FakeE2B:
        def run_command(self, *_args, **_kwargs):
            return {
                "stdout": (
                    "3 passed in 0.01s\n"
                    "__TAALI_TRUSTED_VERIFIER_RESULT__="
                    '{"completed":true,"exit_code":0,"expected_total":4}\n'
                ),
                "stderr": "",
                "exit_code": 0,
            }

    result = _run_task_test_runner(FakeE2B(), "sandbox", task, "/workspace/repo")

    assert result is not None
    assert result["reported_passed"] == 3
    assert result["passed"] == 0
    assert result["total"] == 0
    assert result["verifier_ready"] is False
    assert result["parse_error"] is True
