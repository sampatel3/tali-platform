from types import SimpleNamespace

from app.components.assessments.submission_runtime import (
    _parse_test_runner_results,
    _public_git_evidence,
    _public_rubric_dimension_error,
    _public_test_results,
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
            assert type(timeout) is int
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


def test_run_task_test_runner_preserves_results_when_command_exits_nonzero():
    task = SimpleNamespace(
        extra_data={
            "test_runner": {
                "command": "pytest -q --tb=no",
                "working_dir": "/workspace/customer-intelligence-ai",
                "parse_pattern": r"(?P<passed>\d+)\s+passed(?:,\s+(?P<failed>\d+)\s+failed)?",
                "timeout_seconds": 60,
            }
        }
    )

    class FakeCommandExit(Exception):
        def __init__(self):
            super().__init__()
            self.stdout = "FFF.....\n3 failed, 5 passed in 0.04s\n"
            self.stderr = ""
            self.exit_code = 1

    class FakeE2B:
        def run_command(self, sandbox, command, cwd=None, timeout=30):
            assert sandbox == "sandbox"
            assert command == "pytest -q --tb=no"
            assert cwd == "/workspace/customer-intelligence-ai"
            assert timeout == 60
            assert type(timeout) is int
            raise FakeCommandExit()

    result = _run_task_test_runner(FakeE2B(), "sandbox", task, "/workspace/repo")
    assert result is not None
    assert result["source"] == "task_test_runner"
    assert result["passed"] == 5
    assert result["failed"] == 3
    assert result["total"] == 8
    assert result["success"] is False
    assert result["exit_code"] == 1


def test_run_task_test_runner_does_not_serialize_infrastructure_exception():
    task = SimpleNamespace(
        id=73,
        extra_data={
            "test_runner": {
                "command": "pytest -q",
                "working_dir": "/workspace/repo",
            }
        },
    )

    class ProviderFailure(Exception):
        stderr = "Authorization: Bearer tenant-secret"
        exit_code = None

    class FakeE2B:
        def run_command(self, *_args, **_kwargs):
            raise ProviderFailure("private-e2b.internal token=tenant-secret")

    result = _run_task_test_runner(FakeE2B(), "sandbox", task, "/workspace/repo")

    assert result is not None
    assert result["error"] == "test_runner_unavailable"
    assert result["stderr"] == ""
    assert "tenant-secret" not in str(result)
    assert "private-e2b" not in str(result)


def test_public_assessment_error_helpers_keep_codes_and_drop_raw_details():
    sanitized_tests = _public_test_results(
        {
            "passed": 0,
            "failed": 0,
            "error": "HTTPSConnectionPool(private.internal): api_key=secret",
        }
    )
    sanitized_git = _public_git_evidence(
        {
            "head_sha": "abc123",
            "push_stderr": "fatal: https://token@github.example/private",
            "git_probe_stderr": "Authorization: Bearer secret",
            "diff_main_error": "private filesystem path /srv/tenant",
            "error": "provider exploded with api_key=secret",
        }
    )

    assert sanitized_tests["error"] == "test_runner_unavailable"
    assert sanitized_git == {
        "head_sha": "abc123",
        "diff_main_error": "git_diff_failed",
        "error": "git_evidence_capture_failed",
    }
    assert _public_rubric_dimension_error("missing_decision_points") == (
        "missing_decision_points"
    )
    assert _public_rubric_dimension_error("api_key=secret") == (
        "rubric_dimension_failed"
    )
