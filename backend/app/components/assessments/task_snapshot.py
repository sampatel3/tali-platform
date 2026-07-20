"""Immutable task snapshots bound to individual assessments."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


TASK_SNAPSHOT_VERSION = 1
TASK_SNAPSHOT_FIELDS = (
    "id",
    "task_key",
    "name",
    "description",
    "role",
    "scenario",
    "duration_minutes",
    "starter_code",
    "test_code",
    "repo_structure",
    "evaluation_rubric",
    "extra_data",
    "calibration_prompt",
    "score_weights",
    "recruiter_weight_preset",
    "proctoring_enabled",
    "claude_budget_limit_usd",
)


def _snapshot_digest(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_task_snapshot(task: Any) -> dict[str, Any]:
    snapshot: dict[str, Any] = {"version": TASK_SNAPSHOT_VERSION}
    for field in TASK_SNAPSHOT_FIELDS:
        snapshot[field] = copy.deepcopy(getattr(task, field, None))
    return snapshot


def freeze_assessment_task(assessment: Any, task: Any) -> bool:
    """Bind the current task definition once; validate every later read.

    Returns ``True`` when a new snapshot was stored and ``False`` when an
    existing, valid snapshot was retained.
    """
    existing = getattr(assessment, "task_spec_snapshot", None)
    expected = str(
        getattr(assessment, "task_spec_snapshot_sha256", None) or ""
    ).strip()
    if (existing is None) != (not expected):
        raise RuntimeError("Assessment task snapshot metadata is incomplete")
    if existing is not None:
        if not isinstance(existing, dict) or existing.get("version") != TASK_SNAPSHOT_VERSION:
            raise RuntimeError("Assessment task snapshot has an unsupported format")
        if not expected or _snapshot_digest(existing) != expected:
            raise RuntimeError("Assessment task snapshot digest verification failed")
        return False

    snapshot = build_task_snapshot(task)
    assessment.task_spec_snapshot = snapshot
    assessment.task_spec_snapshot_sha256 = _snapshot_digest(snapshot)
    return True


class FrozenTaskView:
    """Read-only task-shaped view whose scoring/runtime fields are frozen."""

    def __init__(self, live_task: Any, snapshot: dict[str, Any]):
        object.__setattr__(self, "_live_task", live_task)
        object.__setattr__(self, "_snapshot", copy.deepcopy(snapshot))

    def __getattr__(self, name: str) -> Any:
        snapshot = object.__getattribute__(self, "_snapshot")
        if name in snapshot:
            return copy.deepcopy(snapshot[name])
        return getattr(object.__getattribute__(self, "_live_task"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Frozen task views are read-only")


def task_view_for_assessment(assessment: Any, live_task: Any) -> Any:
    """Return the assessment's digest-verified task definition."""
    freeze_assessment_task(assessment, live_task)
    snapshot = getattr(assessment, "task_spec_snapshot")
    return FrozenTaskView(live_task, snapshot)


__all__ = [
    "FrozenTaskView",
    "TASK_SNAPSHOT_FIELDS",
    "TASK_SNAPSHOT_VERSION",
    "build_task_snapshot",
    "freeze_assessment_task",
    "task_view_for_assessment",
]
