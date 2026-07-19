"""Fail-closed assessment-task repository approval and readiness.

Generated tasks are candidate-facing executable content. The recruiter's
Turn-on command is the authorization to use the exact automatically validated
draft; explicit task-management approval remains supported. Neither path is
truthful unless the repository future assessment branches use is provisioned
and verifiably has a ``main`` branch. This module is the shared mutation and
readiness seam for both paths.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session

from ..models.role import Role, role_tasks
from ..models.task import Task
from ..platform.config import settings
from .assessment_repository_service import (
    AssessmentRepositoryError,
    AssessmentRepositoryService,
)
from .task_repo_service import (
    is_safe_repo_file_path,
    normalize_repo_files,
    recreate_task_main_repo,
)

logger = logging.getLogger(__name__)

_TASK_APPROVAL_FINGERPRINT_FIELDS = tuple(
    column.key
    for column in Task.__table__.columns
    if column.key not in {"created_at", "updated_at"}
)


@dataclass(frozen=True)
class CapturedTaskApproval:
    fingerprint: str
    task_snapshot: Any


@dataclass(frozen=True)
class PreparedTaskApproval:
    fingerprint: str
    repo_url: str


class TaskApprovalError(RuntimeError):
    """The task cannot safely be made active for candidate assignment."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "task_repository_unavailable",
        public_message: str = "The task repository is temporarily unavailable; the draft remains inactive. Retry after repository access recovers.",
    ):
        super().__init__(message)
        self.code = code
        self.public_message = public_message

    @property
    def public_detail(self) -> str:
        return f"{self.code}: {self.public_message}"


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
            f"Generated task battle test is {state}; a passing report is required",
            code=f"task_battle_test_{state}",
            public_message=(
                f"The generated task battle test is {state}; a passing report is required."
            ),
        )


def _canonical_fingerprint_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _canonical_fingerprint_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_fingerprint_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    return value


def task_approval_fingerprint(task: Any) -> str:
    payload = {
        field: _canonical_fingerprint_value(
            copy.deepcopy(getattr(task, field, None))
        )
        for field in _TASK_APPROVAL_FINGERPRINT_FIELDS
    }
    encoded = json.dumps(
        payload,
        default=repr,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def capture_task_approval(task: Task) -> CapturedTaskApproval:
    snapshot = SimpleNamespace(
        **{
            field: copy.deepcopy(getattr(task, field, None))
            for field in _TASK_APPROVAL_FINGERPRINT_FIELDS
        }
    )
    return CapturedTaskApproval(
        fingerprint=task_approval_fingerprint(snapshot),
        task_snapshot=snapshot,
    )


def prepare_task_approval(
    captured: CapturedTaskApproval,
    *,
    settings_obj: Any = settings,
) -> PreparedTaskApproval:
    """Perform repository I/O against a detached exact-content snapshot."""

    repo_url = provision_and_validate_task_repository(
        captured.task_snapshot,
        settings_obj=settings_obj,
    )
    return PreparedTaskApproval(
        fingerprint=captured.fingerprint,
        repo_url=repo_url,
    )


def _repository_service(*, settings_obj: Any) -> AssessmentRepositoryService:
    return AssessmentRepositoryService(
        getattr(settings_obj, "GITHUB_ORG", None),
        getattr(settings_obj, "GITHUB_TOKEN", None),
    )


def _validate_repository_definition(task: Task) -> dict[str, str]:
    files = normalize_repo_files(getattr(task, "repo_structure", None))
    if not files:
        raise TaskApprovalError(
            f"Task {getattr(task, 'id', '?')} has no repository files to provision",
            code="task_repository_definition_missing",
            public_message="The task has no repository files to provision.",
        )
    unsafe = [
        rel
        for rel in files
        if not is_safe_repo_file_path(rel)
    ]
    if unsafe:
        raise TaskApprovalError(
            "Task repository contains unsafe paths: " + ", ".join(unsafe[:3]),
            code="task_repository_definition_unsafe",
            public_message="The task repository contains unsafe file paths.",
        )
    return files


def provision_and_validate_task_repository(
    task: Task,
    *,
    settings_obj: Any = settings,
) -> str:
    """Create the local snapshot + remote template and verify the exact repo.

    No database state is changed here.  A failure therefore leaves an inactive
    draft inactive, while a later retry can safely reuse the idempotent repo
    provisioning operations.
    """
    _validate_generated_battle_test(task)
    files = _validate_repository_definition(task)
    try:
        local_path = Path(recreate_task_main_repo(task))
        if not local_path.is_dir():
            raise TaskApprovalError(
                f"Task {getattr(task, 'id', '?')} local repository was not created"
            )
        missing = [rel for rel in files if not (local_path / rel).is_file()]
        if missing:
            raise TaskApprovalError(
                "Local task repository is missing files: " + ", ".join(missing[:3])
            )

        repo_service = _repository_service(settings_obj=settings_obj)
        repo_service.create_template_repo(task)
        return repo_service.verify_template_repo(task)
    except TaskApprovalError:
        raise
    except (AssessmentRepositoryError, OSError, RuntimeError) as exc:
        logger.exception(
            "Task repository provisioning failed task_id=%s",
            getattr(task, "id", None),
        )
        raise TaskApprovalError(
            f"Task repository provisioning/verification failed: {exc}"
        ) from exc
    except Exception as exc:  # defensive: third-party SDK/subprocess boundaries
        logger.exception(
            "Unexpected task repository provisioning failure task_id=%s",
            getattr(task, "id", None),
        )
        raise TaskApprovalError(
            f"Task repository provisioning/verification failed: {exc}"
        ) from exc


def task_repository_readiness(
    task: Task,
    *,
    settings_obj: Any = settings,
) -> tuple[bool, str | None]:
    """Read-only production readiness for one candidate-assignable task."""
    try:
        _validate_generated_battle_test(task)
        _validate_repository_definition(task)
        _repository_service(settings_obj=settings_obj).verify_template_repo(task)
        return True, None
    except TaskApprovalError as exc:
        logger.warning(
            "Task repository readiness blocked task_id=%s code=%s",
            getattr(task, "id", None),
            exc.code,
        )
        return False, exc.public_detail
    except Exception:
        logger.exception(
            "Unexpected task repository readiness failure task_id=%s",
            getattr(task, "id", None),
        )
        return (
            False,
            TaskApprovalError("unexpected readiness failure").public_detail,
        )


def approve_task_for_use(
    db: Session,
    task: Task,
    *,
    user_id: int | None,
    approval_role_id: int | None = None,
    settings_obj: Any = settings,
) -> Task:
    """Provision first, then mark ``task`` active in the caller's transaction.

    The function intentionally does **not** commit.  This lets Turn on compose
    task approval with the role activation transaction.  Callers must commit on
    success and roll back on :class:`TaskApprovalError` or database failure.
    """
    _validate_approval_role_scope(
        db,
        task=task,
        approval_role_id=approval_role_id,
    )

    # Keep this wrapper for non-HTTP callers. HTTP and activation callers use
    # capture/prepare/apply so provider I/O never runs while database locks are
    # held. The apply seam still revalidates the exact content fingerprint.
    _validate_generated_battle_test(task)
    prepared = PreparedTaskApproval(
        fingerprint=task_approval_fingerprint(task),
        repo_url=provision_and_validate_task_repository(
            task,
            settings_obj=settings_obj,
        ),
    )
    return apply_prepared_task_approval(
        db,
        task,
        prepared=prepared,
        user_id=user_id,
        approval_role_id=approval_role_id,
    )


def _validate_approval_role_scope(
    db: Session,
    *,
    task: Task,
    approval_role_id: int | None,
) -> None:
    """Reject role-local approval when another live role shares the task."""

    # Generic task management is allowed to approve a shared task only after
    # its route has locked and authorized every linked role, so it deliberately
    # omits ``approval_role_id``.
    if approval_role_id is not None:
        linked_role_ids = {
            int(row[0])
            for row in db.query(role_tasks.c.role_id)
            .join(Role, Role.id == role_tasks.c.role_id)
            .filter(
                role_tasks.c.task_id == int(task.id),
                Role.deleted_at.is_(None),
            )
            .all()
        }
        if linked_role_ids - {int(approval_role_id)}:
            raise TaskApprovalError(
                "A role-scoped approval cannot activate a task shared by another role",
                code="task_shared_approval_scope",
                public_message=(
                    "This draft is assigned to more than one role. Review its "
                    "assignments and approve it from the task manager with access "
                    "to every affected role."
                ),
            )


def apply_prepared_task_approval(
    db: Session,
    task: Task,
    prepared: PreparedTaskApproval,
    *,
    user_id: int | None,
    approval_role_id: int | None = None,
) -> Task:
    """Apply a remotely prepared approval only to the exact captured task.

    Callers must reacquire their canonical Role→Task locks before entering
    this function. No provider or filesystem operation occurs here.
    """

    _validate_approval_role_scope(
        db,
        task=task,
        approval_role_id=approval_role_id,
    )
    if task_approval_fingerprint(task) != prepared.fingerprint:
        raise TaskApprovalError(
            f"Task {getattr(task, 'id', '?')} changed while its repository was prepared",
            code="task_approval_superseded",
            public_message=(
                "The task changed while its repository was being prepared. "
                "Review the latest draft and approve it again."
            ),
        )

    # Alternate provisioning adapters may be replaced in tests, but can never
    # bypass candidate-content validation at the mutation boundary.
    _validate_generated_battle_test(task)
    extra = dict(task.extra_data) if isinstance(task.extra_data, dict) else {}
    now = datetime.now(timezone.utc)
    extra["needs_review"] = False
    if user_id is not None:
        extra["approved_by_user_id"] = int(user_id)
    extra["repository_ready"] = {
        "verified_at": now.isoformat(),
        "repo_url": prepared.repo_url,
    }
    task.extra_data = extra
    task.is_active = True
    db.add(task)
    db.flush()
    return task


__all__ = [
    "CapturedTaskApproval",
    "PreparedTaskApproval",
    "TaskApprovalError",
    "apply_prepared_task_approval",
    "approve_task_for_use",
    "capture_task_approval",
    "prepare_task_approval",
    "provision_and_validate_task_repository",
    "task_approval_fingerprint",
    "task_repository_readiness",
]
