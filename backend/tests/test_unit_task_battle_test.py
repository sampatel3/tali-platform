"""Battle-test report card for generated task drafts.

Sandbox interactions are faked at the adapter seam; the tests cover the
structural checks, verdict logic, degradation on infrastructure failure,
persistence, and the review-card summary.
"""

from types import SimpleNamespace

import app.services.task_battle_test as btmod
from app.services.task_battle_test import battle_test_summary, run_battle_test


def _make_task(**overrides):
    extra = {
        "generated": True,
        "needs_review": True,
        "deliverable": {"kind": "code", "primary_artifact": "src/main.py"},
        "decision_points": [{"id": "a"}, {"id": "b"}],
        "test_runner": {"command": "pytest -q", "working_dir": "/workspace/repo"},
        "workspace_bootstrap": {"commands": ["true"], "working_dir": "/workspace/repo"},
    }
    extra.update(overrides.pop("extra_data", {}))
    defaults = dict(
        id=101,
        task_key="draft_task",
        name="Draft task",
        repo_structure={"files": {"src/main.py": "x = 1", "tests/test_main.py": "def test(): assert False"}},
        extra_data=extra,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _patch_sandbox(monkeypatch, *, bootstrap_success=True, baseline=None, create_raises=False):
    closed = {"count": 0}

    class FakeAdapter:
        def create_sandbox(self):
            if create_raises:
                raise RuntimeError("no sandbox capacity")
            return object()

        def close_sandbox(self, sandbox):
            closed["count"] += 1

    monkeypatch.setattr(btmod, "run_battle_test", btmod.run_battle_test)
    import app.domains.integrations_notifications.adapters as adapters_mod
    import app.components.assessments.service as service_mod
    import app.components.assessments.submission_runtime as runtime_mod
    import app.components.assessments.terminal_runtime as terminal_mod

    monkeypatch.setattr(adapters_mod, "build_sandbox_adapter", lambda: FakeAdapter())
    monkeypatch.setattr(service_mod, "_materialize_task_repository", lambda sandbox, task: None)
    monkeypatch.setattr(
        service_mod,
        "_run_workspace_bootstrap",
        lambda e2b, sandbox, task, repo_root: {"ran": True, "success": bootstrap_success, "must_succeed": True},
    )
    monkeypatch.setattr(
        runtime_mod,
        "_run_task_test_runner",
        lambda e2b, sandbox, task, repo_root: baseline,
    )
    monkeypatch.setattr(terminal_mod, "workspace_repo_root", lambda task: "/workspace/repo")
    return closed


def test_pass_verdict_when_repo_boots_and_baseline_fails_meaningfully(monkeypatch):
    closed = _patch_sandbox(
        monkeypatch,
        baseline={"passed": 2, "failed": 7, "total": 9, "parse_error": False, "exit_code": 1},
    )
    report = run_battle_test(_make_task())
    assert report["verdict"] == "pass"
    assert report["bootstrap_ok"] is True
    assert report["baseline"]["total"] == 9
    check_ids = {c["id"]: c["ok"] for c in report["checks"]}
    assert check_ids["deliverable_in_repo"] is True
    assert check_ids["baseline_fails_meaningfully"] is True
    assert closed["count"] == 1  # sandbox always released


def test_fail_when_baseline_already_passes(monkeypatch):
    _patch_sandbox(
        monkeypatch,
        baseline={"passed": 9, "failed": 0, "total": 9, "parse_error": False, "exit_code": 0},
    )
    report = run_battle_test(_make_task())
    assert report["verdict"] == "fail"
    checks = {c["id"]: c["ok"] for c in report["checks"]}
    assert checks["baseline_fails_meaningfully"] is False


def test_fail_when_bootstrap_fails(monkeypatch):
    _patch_sandbox(monkeypatch, bootstrap_success=False, baseline=None)
    report = run_battle_test(_make_task())
    assert report["verdict"] == "fail"
    assert report["bootstrap_ok"] is False
    # No test-runner checks appended when the repo never booted.
    assert "tests_collect" not in {c["id"] for c in report["checks"]}


def test_no_deliverable_declared_is_ok(monkeypatch):
    # Legacy code-kind catalog specs don't declare a deliverable block —
    # the contract treats it as optional (test runner verifies), so the
    # structural check must not fail them.
    _patch_sandbox(
        monkeypatch,
        baseline={"passed": 2, "failed": 5, "total": 7, "parse_error": False, "exit_code": 1},
    )
    task = _make_task()
    extra = dict(task.extra_data)
    del extra["deliverable"]
    task.extra_data = extra
    report = run_battle_test(task)
    checks = {c["id"]: c["ok"] for c in report["checks"]}
    assert checks["deliverable_in_repo"] is True
    assert report["verdict"] == "pass"


def test_structural_failures_flagged(monkeypatch):
    _patch_sandbox(
        monkeypatch,
        baseline={"passed": 1, "failed": 3, "total": 4, "parse_error": False, "exit_code": 1},
    )
    task = _make_task(
        extra_data={"deliverable": {"kind": "code", "primary_artifact": "src/missing.py"}, "decision_points": []},
    )
    report = run_battle_test(task)
    checks = {c["id"]: c["ok"] for c in report["checks"]}
    assert checks["deliverable_in_repo"] is False
    assert checks["decision_points"] is False
    assert report["verdict"] == "fail"


def test_infrastructure_failure_degrades_to_failed_report(monkeypatch):
    _patch_sandbox(monkeypatch, create_raises=True)
    report = run_battle_test(_make_task())
    assert report["verdict"] == "fail"
    assert "no sandbox capacity" in (report["error"] or "")
    # Structural checks still present — reviewer gets something actionable.
    assert any(c["id"] == "deliverable_in_repo" for c in report["checks"])


def test_battle_test_summary_compacts_report():
    task = _make_task(
        extra_data={
            "battle_test": {
                "verdict": "fail",
                "ran_at": "2026-07-10T12:00:00+00:00",
                "baseline": {"passed": 9, "failed": 0, "total": 9},
                "checks": [
                    {"id": "deliverable_in_repo", "ok": True, "detail": "ok"},
                    {"id": "baseline_fails_meaningfully", "ok": False, "detail": "baseline 9/9 passing"},
                ],
                "error": None,
            }
        }
    )
    summary = battle_test_summary(task)
    assert summary["verdict"] == "fail"
    assert summary["failed_checks"] == [
        {"id": "baseline_fails_meaningfully", "detail": "baseline 9/9 passing"}
    ]

    assert battle_test_summary(_make_task()) is None
