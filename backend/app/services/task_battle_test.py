"""Automated battle-test for generated task drafts.

The JD→spec generator produces contract-valid drafts, but contract validity
says nothing about whether the task actually *works*: does the repo boot,
do the baseline tests collect and meaningfully fail, is the deliverable
where the spec says it is? Historically that proof was a manual E2B session
per task — which is why every generated draft sat un-reviewed.

``run_battle_test(task)`` executes the draft in a real E2B sandbox through
the SAME helpers the live assessment uses (materialize → bootstrap →
test-runner), then runs cheap structural checks, and returns a one-page
report card. ``persist_battle_test`` stamps it at
``task.extra_data.battle_test`` where the agent-chat review card surfaces
it — turning draft approval into a 2-minute read instead of an
800-line-JSON audit.

This deliberately does NOT run a model-driven "lazy operator" baseline —
that needs an Anthropic budget decision and lands with the calibration
loop (docs/ASSESSMENT_E2E_DEEP_DIVE.md §4, P2-3).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..models.task import Task

logger = logging.getLogger("taali.task_battle_test")

BATTLE_TEST_PENDING = "pending"
BATTLE_TEST_RUNNING = "running"
BATTLE_TEST_RETRY_WAIT = "retry_wait"
BATTLE_TEST_FAILED = "failed"
BATTLE_TEST_SUCCEEDED = "succeeded"
BATTLE_TEST_REPAIR_PENDING = "repair_pending"
BATTLE_TEST_REPAIRING = "repairing"
BATTLE_TEST_REPAIR_RETRY_WAIT = "repair_retry_wait"
BATTLE_TEST_REPAIR_FAILED = "repair_failed"
BATTLE_TEST_REPAIR_EXHAUSTED = "repair_exhausted"
BATTLE_TEST_MAX_REPAIR_ATTEMPTS = 2
BATTLE_TEST_STALE_AFTER = timedelta(minutes=15)


def _extra(task: Task) -> Dict[str, Any]:
    return task.extra_data if isinstance(task.extra_data, dict) else {}


def initialize_battle_test_provisioning(
    extra: Dict[str, Any], *, now: datetime | None = None
) -> Dict[str, Any]:
    """Add a durable battle-test intent to generated-task ``extra_data``."""
    current_time = now or datetime.now(timezone.utc)
    updated = dict(extra)
    updated["battle_test_provisioning"] = {
        "status": BATTLE_TEST_PENDING,
        "attempts": 0,
        "repair_attempts": 0,
        "requested_at": current_time.isoformat(),
        "updated_at": current_time.isoformat(),
        "next_attempt_at": None,
        "last_error": None,
    }
    return updated


def battle_test_provisioning_action(
    task: Task, *, now: datetime | None = None
) -> str | None:
    """Return ``battle_test``/``repair`` when the durable sweep should act."""
    current_time = now or datetime.now(timezone.utc)
    extra = _extra(task)
    state = (
        extra.get("battle_test_provisioning")
        if isinstance(extra.get("battle_test_provisioning"), dict)
        else {}
    )
    status = str(state.get("status") or BATTLE_TEST_PENDING)

    def _parsed(key: str) -> datetime | None:
        raw = state.get(key)
        if not raw:
            return None
        try:
            value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if status in {BATTLE_TEST_REPAIR_PENDING}:
        return "repair"
    if status == BATTLE_TEST_REPAIRING:
        updated_at = _parsed("updated_at")
        return (
            "repair"
            if updated_at is None
            or updated_at <= current_time - BATTLE_TEST_STALE_AFTER
            else None
        )
    if status in {BATTLE_TEST_REPAIR_RETRY_WAIT, BATTLE_TEST_REPAIR_FAILED}:
        next_attempt_at = _parsed("next_attempt_at")
        return (
            "repair"
            if next_attempt_at is None or next_attempt_at <= current_time
            else None
        )
    if status == BATTLE_TEST_REPAIR_EXHAUSTED:
        return None
    if isinstance(extra.get("battle_test"), dict):
        return None
    if status == BATTLE_TEST_PENDING:
        return "battle_test"
    if status == BATTLE_TEST_RUNNING:
        updated_at = _parsed("updated_at")
        return (
            "battle_test"
            if updated_at is None
            or updated_at <= current_time - BATTLE_TEST_STALE_AFTER
            else None
        )
    if status in {BATTLE_TEST_RETRY_WAIT, BATTLE_TEST_FAILED}:
        next_attempt_at = _parsed("next_attempt_at")
        return (
            "battle_test"
            if next_attempt_at is None or next_attempt_at <= current_time
            else None
        )
    return None


def battle_test_provisioning_is_due(
    task: Task, *, now: datetime | None = None
) -> bool:
    """Compatibility boolean for callers that do not need action routing."""
    return battle_test_provisioning_action(task, now=now) is not None


def battle_test_repair_feedback(report: Dict[str, Any]) -> str:
    """Turn deterministic sandbox failures into bounded re-author feedback."""
    failed = [
        f"- {check.get('id')}: {check.get('detail') or 'failed'}"
        for check in (report.get("checks") or [])
        if isinstance(check, dict) and not check.get("ok")
    ]
    baseline = report.get("baseline") if isinstance(report.get("baseline"), dict) else {}
    details = "\n".join(failed[:12]) or "- battle-test verdict was fail"
    return (
        "The automated sandbox review failed. Re-author the task in place so "
        "the repository boots, tests collect, and the untouched starter "
        "baseline fails meaningfully. Preserve role alignment and the intended "
        "candidate difficulty. Fix every issue below:\n"
        f"{details}\n"
        f"Baseline summary: passed={baseline.get('passed')}, "
        f"failed={baseline.get('failed')}, total={baseline.get('total')}, "
        f"parse_error={baseline.get('parse_error')}."
    )


def _structural_checks(task: Task) -> List[Dict[str, Any]]:
    """Sandbox-free sanity checks a reviewer would otherwise eyeball."""
    extra = _extra(task)
    files = (task.repo_structure or {}).get("files") if isinstance(task.repo_structure, dict) else {}
    files = files if isinstance(files, dict) else {}

    checks: List[Dict[str, Any]] = []

    # ``deliverable`` is optional in the central contract (absent = code-kind
    # default; the test runner verifies the submission) — mirror that here.
    # Fail only when a deliverable IS declared but its artifact is missing.
    deliverable = extra.get("deliverable") if isinstance(extra.get("deliverable"), dict) else {}
    primary = str(deliverable.get("primary_artifact") or "").strip()
    checks.append(
        {
            "id": "deliverable_in_repo",
            "ok": (primary in files) if primary else True,
            "detail": (
                f"primary_artifact={primary}"
                if primary
                else "no deliverable declared (code-kind default; test runner verifies)"
            ),
        }
    )

    decision_points = [d for d in (extra.get("decision_points") or []) if isinstance(d, dict)]
    checks.append(
        {
            "id": "decision_points",
            "ok": len(decision_points) >= 2,
            "detail": f"{len(decision_points)} decision point(s)",
        }
    )

    test_files = [p for p in files if p.startswith("tests/") and p.endswith(".py")]
    checks.append(
        {
            "id": "test_files_present",
            "ok": len(test_files) >= 1,
            "detail": f"{len(test_files)} test file(s)",
        }
    )
    return checks


def run_battle_test(task: Task) -> Dict[str, Any]:
    """Execute the draft in a fresh E2B sandbox and return the report card.

    Never raises: infrastructure failures come back as a failed report with
    the error captured, so the review card can still render something
    actionable.
    """
    from datetime import datetime, timezone

    from ..components.assessments.service import (
        _materialize_task_repository,
        _run_workspace_bootstrap,
    )
    from ..components.assessments.submission_runtime import _run_task_test_runner
    from ..components.assessments.terminal_runtime import workspace_repo_root
    from ..domains.integrations_notifications.adapters import build_sandbox_adapter

    report: Dict[str, Any] = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "checks": _structural_checks(task),
        "bootstrap_ok": None,
        "baseline": None,
        "error": None,
    }

    sandbox = None
    e2b = None
    try:
        e2b = build_sandbox_adapter()
        sandbox = e2b.create_sandbox()
        _materialize_task_repository(sandbox, task)
        repo_root = workspace_repo_root(task)

        bootstrap = _run_workspace_bootstrap(e2b, sandbox, task, repo_root)
        bootstrap_ok = (not bootstrap.get("ran")) or bool(bootstrap.get("success"))
        report["bootstrap_ok"] = bootstrap_ok
        report["checks"].append(
            {
                "id": "repo_boots",
                "ok": bootstrap_ok,
                "detail": "bootstrap succeeded" if bootstrap_ok else "bootstrap FAILED",
            }
        )

        baseline = _run_task_test_runner(e2b, sandbox, task, repo_root) if bootstrap_ok else None
        if baseline is not None:
            report["baseline"] = {
                "passed": baseline.get("passed"),
                "failed": baseline.get("failed"),
                "total": baseline.get("total"),
                "parse_error": bool(baseline.get("parse_error")),
                "exit_code": baseline.get("exit_code"),
            }
            collected = (not baseline.get("parse_error")) and int(baseline.get("total") or 0) > 0
            report["checks"].append(
                {
                    "id": "tests_collect",
                    "ok": collected,
                    "detail": f"{baseline.get('total')} test(s) collected",
                }
            )
            # The starter repo must FAIL meaningfully: a baseline that already
            # passes cannot discriminate, and 0-collected means broken tests.
            meaningful = collected and int(baseline.get("failed") or 0) > 0
            report["checks"].append(
                {
                    "id": "baseline_fails_meaningfully",
                    "ok": meaningful,
                    "detail": f"baseline {baseline.get('passed')}/{baseline.get('total')} passing",
                }
            )
        elif bootstrap_ok:
            report["checks"].append(
                {"id": "tests_collect", "ok": False, "detail": "no test_runner configured"}
            )
    except Exception as exc:  # noqa: BLE001 — report, never raise
        logger.exception("battle test failed for task %s", task.id)
        report["error"] = str(exc)[:500]
    finally:
        if sandbox is not None and e2b is not None:
            try:
                e2b.close_sandbox(sandbox)
            except Exception:
                logger.warning("failed to close battle-test sandbox for task %s", task.id)

    report["verdict"] = (
        "pass"
        if report["error"] is None and all(c.get("ok") for c in report["checks"])
        else "fail"
    )
    return report


def reconstruct_generated_task_spec(task: Task) -> Dict[str, Any]:
    """Rebuild generator seed context without provisioning bookkeeping."""
    extra = dict(_extra(task))
    for key in (
        "generated",
        "needs_review",
        "approved_by_user_id",
        "last_revision",
        "battle_test",
        "battle_test_history",
        "battle_test_provisioning",
    ):
        extra.pop(key, None)
    extra.update(
        {
            "task_id": task.task_key,
            "name": task.name,
            "role": task.role,
            "duration_minutes": task.duration_minutes or 30,
            "calibration_prompt": task.calibration_prompt,
            "scenario": task.scenario,
            "repo_structure": task.repo_structure,
            "evaluation_rubric": task.evaluation_rubric,
        }
    )
    return extra


def apply_battle_test_repair(
    task: Task,
    spec: Dict[str, Any],
    *,
    feedback: str,
    failed_report: Dict[str, Any],
    repair_attempts: int,
) -> None:
    """Apply a validated repair in place and durably request a re-test."""
    from .task_catalog import PERSISTED_TASK_SPEC_KEYS

    previous_extra = dict(_extra(task))
    history = [
        item
        for item in (previous_extra.get("battle_test_history") or [])
        if isinstance(item, dict)
    ][-4:]
    history.append(failed_report)

    scenario = spec.get("scenario")
    task.name = spec.get("name", task.name)
    if isinstance(scenario, str):
        task.description = scenario[:500]
        task.scenario = scenario
    task.calibration_prompt = spec.get("calibration_prompt")
    task.role = spec.get("role") or task.role
    task.duration_minutes = spec.get("duration_minutes", 30)
    task.repo_structure = spec.get("repo_structure")
    task.evaluation_rubric = spec.get("evaluation_rubric")
    task.claude_budget_limit_usd = spec.get("claude_budget_limit_usd")

    extra = {k: v for k, v in spec.items() if k not in PERSISTED_TASK_SPEC_KEYS}
    extra["generated"] = True
    extra["needs_review"] = True
    extra["last_revision"] = {
        "source": "automated_battle_test_repair",
        "feedback": feedback[:4000],
    }
    extra["battle_test_history"] = history
    now = datetime.now(timezone.utc).isoformat()
    extra["battle_test_provisioning"] = {
        "status": BATTLE_TEST_PENDING,
        "attempts": 0,
        "repair_attempts": int(repair_attempts),
        "requested_at": now,
        "updated_at": now,
        "next_attempt_at": None,
        "last_error": None,
    }
    # Deliberately omit the old failed ``battle_test``: approval stays pending
    # while the new exact spec awaits its own report.
    task.extra_data = extra


def persist_battle_test(db: Session, task: Task, report: Dict[str, Any]) -> None:
    """Stamp the report onto the task row (JSON column → reassign, not mutate)."""
    extra = dict(_extra(task))
    extra["battle_test"] = report
    task.extra_data = extra
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("failed to persist battle test for task %s", task.id)
        raise


def battle_test_summary(task: Task) -> Optional[Dict[str, Any]]:
    """Compact view for the draft review card. None when never run."""
    report = _extra(task).get("battle_test")
    if not isinstance(report, dict):
        return None
    return {
        "verdict": report.get("verdict"),
        "ran_at": report.get("ran_at"),
        "baseline": report.get("baseline"),
        "failed_checks": [
            {"id": c.get("id"), "detail": c.get("detail")}
            for c in (report.get("checks") or [])
            if isinstance(c, dict) and not c.get("ok")
        ],
        "error": report.get("error"),
    }


__all__ = [
    "BATTLE_TEST_FAILED",
    "BATTLE_TEST_MAX_REPAIR_ATTEMPTS",
    "BATTLE_TEST_PENDING",
    "BATTLE_TEST_REPAIR_EXHAUSTED",
    "BATTLE_TEST_REPAIR_FAILED",
    "BATTLE_TEST_REPAIR_PENDING",
    "BATTLE_TEST_REPAIR_RETRY_WAIT",
    "BATTLE_TEST_REPAIRING",
    "BATTLE_TEST_RETRY_WAIT",
    "BATTLE_TEST_RUNNING",
    "BATTLE_TEST_STALE_AFTER",
    "BATTLE_TEST_SUCCEEDED",
    "apply_battle_test_repair",
    "battle_test_provisioning_action",
    "battle_test_provisioning_is_due",
    "battle_test_repair_feedback",
    "run_battle_test",
    "persist_battle_test",
    "battle_test_summary",
    "initialize_battle_test_provisioning",
    "reconstruct_generated_task_spec",
]
