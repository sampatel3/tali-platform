#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models.assessment import Assessment, AssessmentStatus  # noqa: E402
from app.models.task import Task  # noqa: E402
from app.platform.database import SessionLocal  # noqa: E402


CANONICAL_TASK_KEYS = (
    "ai_eng_genai_production_readiness",
    "data_eng_aws_glue_pipeline_recovery",
)


@dataclass
class AssessmentSnapshot:
    id: int
    task_key: str
    is_demo: bool
    status: str
    started_at: str | None
    completed_at: str | None
    tests_passed: int
    tests_total: int
    bootstrap_success: bool | None
    bootstrap_steps: int
    submit_recorded: bool
    submit_tests_passed: int | None
    submit_tests_total: int | None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_since(value: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("empty timestamp")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _timeline_events(assessment: Assessment, event_type: str) -> list[dict[str, Any]]:
    timeline = getattr(assessment, "timeline", None)
    if not isinstance(timeline, list):
        return []
    return [
        event
        for event in timeline
        if isinstance(event, dict) and str(event.get("event_type") or "") == event_type
    ]


def _last_event(assessment: Assessment, event_type: str) -> dict[str, Any] | None:
    events = _timeline_events(assessment, event_type)
    return events[-1] if events else None


def _assessment_snapshot(assessment: Assessment, task_key: str) -> AssessmentSnapshot:
    bootstrap = _last_event(assessment, "workspace_bootstrap") or {}
    submit = _last_event(assessment, "assessment_submit") or {}
    status_value = getattr(getattr(assessment, "status", None), "value", None) or str(getattr(assessment, "status", ""))
    return AssessmentSnapshot(
        id=int(assessment.id),
        task_key=task_key,
        is_demo=bool(getattr(assessment, "is_demo", False)),
        status=status_value,
        started_at=_iso(getattr(assessment, "started_at", None)),
        completed_at=_iso(getattr(assessment, "completed_at", None)),
        tests_passed=int(getattr(assessment, "tests_passed", 0) or 0),
        tests_total=int(getattr(assessment, "tests_total", 0) or 0),
        bootstrap_success=bootstrap.get("success") if isinstance(bootstrap, dict) else None,
        bootstrap_steps=len((bootstrap.get("steps") or [])) if isinstance(bootstrap, dict) else 0,
        submit_recorded=bool(submit),
        submit_tests_passed=(int(submit.get("tests_passed") or 0) if submit else None),
        submit_tests_total=(int(submit.get("tests_total") or 0) if submit else None),
    )


def _summarize_task(task_key: str, assessments: list[AssessmentSnapshot]) -> dict[str, Any]:
    bootstrap_failures = [item for item in assessments if item.bootstrap_success is False]
    zero_test_submits = [
        item for item in assessments if item.submit_recorded and int(item.tests_total or 0) == 0
    ]
    completed = [
        item
        for item in assessments
        if item.status in {AssessmentStatus.COMPLETED.value, AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT.value}
    ]
    completed_with_tests = [item for item in completed if item.tests_total > 0]
    status_counts = Counter(item.status for item in assessments)
    latest = sorted(
        assessments,
        key=lambda item: (item.completed_at or item.started_at or "", item.id),
        reverse=True,
    )[:5]
    return {
        "task_key": task_key,
        "assessment_count": len(assessments),
        "demo_count": sum(1 for item in assessments if item.is_demo),
        "non_demo_count": sum(1 for item in assessments if not item.is_demo),
        "status_counts": dict(status_counts),
        "bootstrap_failures": len(bootstrap_failures),
        "zero_test_submits": len(zero_test_submits),
        "completed_count": len(completed),
        "completed_with_recorded_tests": len(completed_with_tests),
        "latest": [item.__dict__ for item in latest],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check production readiness health for the two canonical task tracks.")
    parser.add_argument("--hours", type=int, default=72, help="Lookback window in hours for assessment activity.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum assessment rows per task to inspect.")
    parser.add_argument("--since", type=str, default="", help="Optional UTC ISO timestamp override, e.g. 2026-03-03T07:09:00Z.")
    args = parser.parse_args()

    cutoff = _utcnow() - timedelta(hours=max(1, args.hours))
    if str(args.since or "").strip():
        cutoff = _parse_since(args.since)

    db = SessionLocal()
    try:
        active_templates = (
            db.query(Task)
            .filter(Task.is_template == True, Task.is_active == True, Task.organization_id == None)  # noqa: E712,E711
            .order_by(Task.id.asc())
            .all()
        )
        active_task_keys = [str(task.task_key or "") for task in active_templates]
        task_rows = {
            str(task.task_key or ""): task
            for task in db.query(Task).filter(Task.task_key.in_(CANONICAL_TASK_KEYS)).all()
        }

        assessments_by_task: dict[str, list[AssessmentSnapshot]] = defaultdict(list)
        for task_key in CANONICAL_TASK_KEYS:
            task = task_rows.get(task_key)
            if task is None:
                continue
            rows = (
                db.query(Assessment)
                .filter(Assessment.task_id == task.id, Assessment.created_at >= cutoff)
                .order_by(Assessment.id.desc())
                .limit(max(1, args.limit))
                .all()
            )
            for row in rows:
                assessments_by_task[task_key].append(_assessment_snapshot(row, task_key))

        task_summaries = [
            _summarize_task(task_key, assessments_by_task.get(task_key, []))
            for task_key in CANONICAL_TASK_KEYS
        ]

        alerts: list[str] = []
        if len(active_task_keys) != 2 or sorted(active_task_keys) != sorted(CANONICAL_TASK_KEYS):
            alerts.append("active_template_catalog_mismatch")
        for summary in task_summaries:
            if summary["bootstrap_failures"] > 0:
                alerts.append(f"{summary['task_key']}:bootstrap_failures_present")
            if summary["zero_test_submits"] > 0:
                alerts.append(f"{summary['task_key']}:zero_test_submits_present")

        report = {
            "generated_at": _utcnow().isoformat(),
            "lookback_hours": max(1, args.hours),
            "since": cutoff.isoformat(),
            "active_template_count": len(active_task_keys),
            "active_task_keys": active_task_keys,
            "canonical_task_keys": list(CANONICAL_TASK_KEYS),
            "alerts": alerts,
            "tasks": task_summaries,
        }
        print(json.dumps(report, indent=2))
        return 1 if alerts else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
