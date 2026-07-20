"""Fail-closed assessment-task snapshot approval and readiness.

Generated tasks are candidate-facing executable content. The recruiter's
Turn-on command is the authorization to use the exact automatically validated
draft; explicit task-management approval remains supported. Candidate
workspaces are materialized from the frozen task manifest, so approval validates
that exact manifest without depending on an external repository provider. This
module is the shared mutation and readiness seam for both paths.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models.task import Task
from ..platform.config import settings
from .assessment_repository_service import sanitize_candidate_workspace_files


class TaskApprovalError(RuntimeError):
    """The task cannot safely be made active for candidate assignment."""


def _validate_generated_battle_test(task: Task) -> None:
    """Generated candidate content must pass its automated execution review.

    Human approval is necessary, but it cannot override a missing/failed
    structural and sandbox test by accident. Manually-authored catalogue tasks
    predate this workflow and remain governed by their existing authoring path.
    """
    extra = task.extra_data if isinstance(task.extra_data, dict) else {}
    if not extra.get("generated"):
        return
    report = extra.get("battle_test")
    verdict = report.get("verdict") if isinstance(report, dict) else None
    if verdict != "pass":
        state = "failed" if verdict == "fail" else "pending"
        raise TaskApprovalError(
            f"Generated task battle test is {state}; a passing report is required"
        )


def _validate_repository_definition(task: Task) -> dict[str, str]:
    try:
        files = sanitize_candidate_workspace_files(
            getattr(task, "repo_structure", None)
        )
    except Exception as exc:
        raise TaskApprovalError(
            f"Task {getattr(task, 'id', '?')} has an unsafe workspace manifest: {exc}"
        ) from exc
    if not files:
        raise TaskApprovalError(
            f"Task {getattr(task, 'id', '?')} has no workspace files to publish"
        )
    return files


def _snapshot_metadata(files: dict[str, str]) -> dict[str, Any]:
    canonical = json.dumps(
        files,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "source": "frozen_task_snapshot",
        "file_count": len(files),
        "sha256": hashlib.sha256(canonical).hexdigest(),
    }


def provision_and_validate_task_repository(
    task: Task,
    *,
    settings_obj: Any = settings,
) -> dict[str, Any]:
    """Validate the exact candidate manifest used to materialize workspaces.

    The legacy function name is retained for compatibility. No GitHub client,
    credential, network call, or process-local filesystem snapshot participates
    in publication readiness. A failure leaves an inactive draft inactive.
    """
    del settings_obj  # compatibility-only; readiness has no provider settings
    _validate_generated_battle_test(task)
    files = _validate_repository_definition(task)
    return _snapshot_metadata(files)


def task_repository_readiness(
    task: Task,
    *,
    settings_obj: Any = settings,
) -> tuple[bool, str | None]:
    """Read-only readiness for one candidate-assignable frozen task snapshot."""
    del settings_obj  # compatibility-only; readiness has no provider settings
    try:
        _validate_generated_battle_test(task)
        _validate_repository_definition(task)
        return True, None
    except Exception as exc:
        return False, str(exc)[:500]


def approve_task_for_use(
    db: Session,
    task: Task,
    *,
    user_id: int | None,
    settings_obj: Any = settings,
) -> Task:
    """Validate the snapshot, then mark ``task`` active in the caller's transaction.

    The function intentionally does **not** commit.  This lets Turn on compose
    task approval with the role activation transaction.  Callers must commit on
    success and roll back on :class:`TaskApprovalError` or database failure.
    """
    # Validate the caller-visible draft before the lock query refreshes the
    # canonical row.  A battle-test worker may have changed this JSON in the
    # current transaction; silently replacing a failed/pending verdict with an
    # older persisted pass would turn the row refresh into an approval bypass.
    # The second validation below is still required for changes committed by a
    # concurrent transaction before we acquire the Task lock.
    _validate_generated_battle_test(task)

    # Lock every workspace/role currently linked to this draft before touching
    # either its repository or active flag. Automatic sends hold this same
    # Organization -> Role boundary through provisioning, so approval cannot
    # race a send or invert Role/Task lock order.
    from .task_mutation_guard import lock_task_mutation_boundary

    boundary = lock_task_mutation_boundary(db, task_ids=[int(task.id)])
    task = boundary.task(int(task.id))
    if task is None:
        raise TaskApprovalError("Task disappeared before approval")

    # Keep this post-lock check outside the provisioning helper too: tests and
    # alternate adapters may replace the snapshot seam, but can
    # never bypass the canonical candidate-content validation contract.
    _validate_generated_battle_test(task)
    snapshot = provision_and_validate_task_repository(task, settings_obj=settings_obj)
    extra = dict(task.extra_data) if isinstance(task.extra_data, dict) else {}
    now = datetime.now(timezone.utc)
    extra["needs_review"] = False
    if user_id is not None:
        extra["approved_by_user_id"] = int(user_id)
    extra["repository_ready"] = {
        "verified_at": now.isoformat(),
        **snapshot,
    }
    task.extra_data = extra
    task.is_active = True
    db.add(task)
    db.flush()

    # Generated drafts are linked to their role while still inactive. Once
    # approval makes the task executable, close the role's stale setup prompt
    # in this same transaction. Keeping this at the shared approval boundary
    # covers Tasks, Agent Chat, and durable Turn-on without route drift.
    from ..models.role import role_tasks
    from .agent_activation_checklist import resolve_satisfied_activation_questions

    for role in boundary.roles:
        has_other_active_task = (
            db.query(Task.id)
            .join(role_tasks, role_tasks.c.task_id == Task.id)
            .filter(
                role_tasks.c.role_id == int(role.id),
                Task.id != int(task.id),
                Task.is_active.is_(True),
            )
            .first()
            is not None
        )
        if not has_other_active_task:
            # This draft was the reason assessment skipping was fixed on.
            # Approval makes it the role's first usable assessment, so restore
            # the stage and let the recruiter opt out explicitly if desired.
            role.auto_skip_assessment = False
        resolve_satisfied_activation_questions(db, role=role)
    db.flush()
    return task


__all__ = [
    "TaskApprovalError",
    "approve_task_for_use",
    "provision_and_validate_task_repository",
    "task_repository_readiness",
]
