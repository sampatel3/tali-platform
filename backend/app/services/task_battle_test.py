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
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..models.task import Task

logger = logging.getLogger("taali.task_battle_test")


def _extra(task: Task) -> Dict[str, Any]:
    return task.extra_data if isinstance(task.extra_data, dict) else {}


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
    "run_battle_test",
    "persist_battle_test",
    "battle_test_summary",
]
