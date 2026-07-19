"""Trust-boundary helpers for recruiter-authored task edits."""

from __future__ import annotations

import copy
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ...models.task import Task
from ...services.task_repo_service import (
    build_default_repo_structure,
    normalize_repo_files,
)
from .task_reference_guard import task_content_reference_kinds


_SYSTEM_TASK_EXTRA_KEYS = frozenset(
    {
        "generated",
        "needs_review",
        "approved_by_user_id",
        "provenance",
        "repository_ready",
        "battle_test",
        "battle_test_history",
        "battle_test_provisioning",
        "last_revision",
    }
)

_APPROVAL_SENSITIVE_FIELDS = frozenset(
    {
        "name",
        "description",
        "task_type",
        "difficulty",
        "duration_minutes",
        "starter_code",
        "test_code",
        "sample_data",
        "dependencies",
        "success_criteria",
        "test_weights",
        "calibration_prompt",
        "score_weights",
        "recruiter_weight_preset",
        "proctoring_enabled",
        "claude_budget_limit_usd",
        "task_key",
        "role",
        "scenario",
        "repo_structure",
        "evaluation_rubric",
        "extra_data",
    }
)

# These fields affect what the candidate executes or the structural checks
# performed by the sandbox battle-test. Scoring-only edits still require fresh
# human approval, but can reuse the already-valid execution report and avoid an
# unnecessary paid sandbox run.
_BATTLE_TEST_SENSITIVE_FIELDS = frozenset(
    {
        "name",
        "description",
        "task_type",
        "difficulty",
        "duration_minutes",
        "starter_code",
        "test_code",
        "sample_data",
        "dependencies",
        "success_criteria",
        "task_key",
        "role",
        "scenario",
        "repo_structure",
        "extra_data",
    }
)


def _is_system_task_extra_key(key: object) -> bool:
    name = str(key or "")
    return bool(
        name in _SYSTEM_TASK_EXTRA_KEYS
        or name.startswith(("approved_", "generated_", "generation_"))
        or name.startswith("provenance_")
        or name.startswith("repository_")
        or name.startswith("battle_test_")
        or name.endswith("_provisioning")
    )


def normalize_task_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Map alternate authoring keys into the persisted metadata shape."""
    normalized = dict(payload)
    alternates = {
        "expected_insights": normalized.pop("expected_insights", None),
        "valid_solutions": normalized.pop("valid_solutions", None),
        "expected_approaches": normalized.pop("expected_approaches", None),
    }
    if any(value is not None for value in alternates.values()):
        extra = normalized.get("extra_data") or {}
        extra.update(
            {key: value for key, value in alternates.items() if value is not None}
        )
        normalized["extra_data"] = extra
    return normalized


def ensure_repo_structure(
    payload: dict[str, Any],
    *,
    fallback_task: Task | None = None,
) -> dict[str, Any]:
    """Map legacy code fields into default paths without replacing a custom repo."""

    normalized = dict(payload)
    explicit_repo = "repo_structure" in normalized
    replacement = normalized.get("repo_structure")
    if explicit_repo:
        if isinstance(replacement, dict) and normalize_repo_files(replacement):
            return normalized
        # An empty/invalid authoring replacement is never permission to wipe
        # the candidate repository. Preserve the current structure, or build a
        # safe default below for a new/legacy task that has no structure yet.
        normalized.pop("repo_structure", None)

    starter_in_payload = "starter_code" in normalized
    test_in_payload = "test_code" in normalized
    existing = (
        copy.deepcopy(fallback_task.repo_structure)
        if fallback_task is not None
        and isinstance(fallback_task.repo_structure, dict)
        and fallback_task.repo_structure
        else None
    )
    if existing is not None:
        if not starter_in_payload and not test_in_payload:
            return normalized

        files = existing.get("files")

        def update_default_file(path: str, content: Any) -> None:
            nonlocal files
            if content is None:
                return
            if isinstance(files, dict):
                files[path] = content
                return
            if isinstance(files, list):
                for entry in files:
                    if not isinstance(entry, dict):
                        continue
                    if str(entry.get("path") or entry.get("name") or "") == path:
                        entry["content"] = content
                        return
                files.append({"path": path, "content": content})
                return
            files = {path: content}

        if starter_in_payload:
            update_default_file("src/task.py", normalized.get("starter_code"))
        if test_in_payload:
            update_default_file("tests/test_task.py", normalized.get("test_code"))
        existing["files"] = files
        normalized["repo_structure"] = existing
        return normalized

    starter_code = normalized.get("starter_code")
    test_code = normalized.get("test_code")
    if fallback_task is not None:
        if starter_code is None:
            starter_code = fallback_task.starter_code
        if test_code is None:
            test_code = fallback_task.test_code
    if not starter_code and not test_code:
        return normalized
    task_name = normalized.get("name") or getattr(fallback_task, "name", None)
    scenario = (
        normalized.get("scenario")
        or normalized.get("description")
        or getattr(fallback_task, "scenario", None)
    )
    generated = build_default_repo_structure(
        starter_code,
        test_code,
        task_name=task_name,
        scenario=scenario,
    )
    if explicit_repo and isinstance(replacement, dict):
        generated = {
            **generated,
            **copy.deepcopy(replacement),
            "files": generated["files"],
        }
    normalized["repo_structure"] = generated
    return normalized


def protect_system_task_metadata(
    payload: dict[str, Any],
    *,
    current_task: Task | None = None,
) -> dict[str, Any]:
    """Keep author edits separate from backend approval/provenance state."""
    if "extra_data" not in payload:
        return payload
    normalized = dict(payload)
    incoming = (
        copy.deepcopy(normalized.get("extra_data"))
        if isinstance(normalized.get("extra_data"), dict)
        else {}
    )
    current = (
        copy.deepcopy(current_task.extra_data)
        if current_task is not None and isinstance(current_task.extra_data, dict)
        else {}
    )
    for key in set(incoming) | set(current):
        if not _is_system_task_extra_key(key):
            continue
        if key in current:
            incoming[key] = current[key]
        else:
            incoming.pop(key, None)
    normalized["extra_data"] = incoming
    return normalized


def has_durable_generated_approval(extra: dict[str, Any]) -> bool:
    battle = extra.get("battle_test")
    repository = extra.get("repository_ready")
    return bool(
        extra.get("needs_review") is False
        and isinstance(battle, dict)
        and battle.get("verdict") == "pass"
        and isinstance(repository, dict)
        and repository.get("verified_at")
        and repository.get("repo_url")
    )


def changed_assessment_fields(
    task: Task,
    payload: dict[str, Any],
) -> set[str]:
    return {
        field
        for field in _APPROVAL_SENSITIVE_FIELDS
        if field in payload and payload[field] != getattr(task, field, None)
    }


def require_unreferenced_assessment_content(
    db: Session,
    *,
    task: Task,
    payload: dict[str, Any],
) -> None:
    """Keep live and historical assessments bound to immutable task content."""

    changed_fields = changed_assessment_fields(task, payload)
    if not changed_fields:
        return
    references = task_content_reference_kinds(db, task_id=int(task.id))
    if not references:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "TASK_VERSION_REQUIRED",
            "changed_fields": sorted(changed_fields),
            "references": list(references),
            "message": (
                "This task already has assessment history, so its candidate "
                "content and scoring contract are immutable. Duplicate it as "
                "a new task version, then assign that version going forward."
            ),
        },
    )


def task_repository_update_required(
    task: Task,
    payload: dict[str, Any],
) -> bool:
    """Avoid repository work for policy-only or activation-only edits."""

    return any(
        field in payload and payload[field] != getattr(task, field, None)
        for field in ("name", "task_key", "repo_structure")
    )


def prepare_task_update(
    task: Task,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], bool, bool]:
    """Sanitize an edit and invalidate approval of changed generated content."""
    protected = protect_system_task_metadata(payload, current_task=task)
    current = task.extra_data if isinstance(task.extra_data, dict) else {}
    changed_fields = changed_assessment_fields(task, protected)
    approval_required = bool(
        protected.get("is_active") is True
        and not bool(task.is_active)
        and current.get("generated")
        and not has_durable_generated_approval(current)
    )
    approval_invalidated = bool(current.get("generated") and changed_fields)
    if approval_invalidated:
        updated_extra = (
            copy.deepcopy(protected["extra_data"])
            if isinstance(protected.get("extra_data"), dict)
            else copy.deepcopy(current)
        )
        updated_extra["generated"] = True
        updated_extra["needs_review"] = True
        updated_extra.pop("approved_by_user_id", None)
        for key in list(updated_extra):
            if str(key).startswith("approved_"):
                updated_extra.pop(key, None)
        updated_extra["last_revision"] = {
            "source": "recruiter_task_edit",
            "invalidated_fields": sorted(changed_fields),
        }

        if changed_fields & _BATTLE_TEST_SENSITIVE_FIELDS:
            previous_report = current.get("battle_test")
            history = [
                copy.deepcopy(item)
                for item in (current.get("battle_test_history") or [])
                if isinstance(item, dict)
            ][-4:]
            if isinstance(previous_report, dict):
                history.append(copy.deepcopy(previous_report))
            if history:
                updated_extra["battle_test_history"] = history[-5:]
            updated_extra.pop("battle_test", None)
            updated_extra.pop("repository_ready", None)
            from ...services.task_battle_test import (
                initialize_battle_test_provisioning,
            )

            updated_extra = initialize_battle_test_provisioning(updated_extra)

        protected["extra_data"] = updated_extra
        # Approval belongs to the exact reviewed content. Never leave changed
        # generated content candidate-assignable, even when a caller combines
        # the edit with an activation flag in one PATCH.
        protected["is_active"] = False
    return protected, approval_required, approval_invalidated


__all__ = [
    "ensure_repo_structure",
    "normalize_task_payload",
    "prepare_task_update",
    "protect_system_task_metadata",
    "require_unreferenced_assessment_content",
    "task_repository_update_required",
]
