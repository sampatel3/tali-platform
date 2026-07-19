"""Immutable provider inputs for generated-assessment re-authoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models.role import Role
from ..models.task import Task
from .task_battle_test import (
    battle_test_repair_feedback,
    reconstruct_generated_task_spec,
)
from .task_provisioning_service import _role_jd_text, _slugify


@dataclass(frozen=True, slots=True)
class AssessmentRepairProviderPlan:
    """Primitive snapshot safe to retain after the database phase ends."""

    prior_spec: dict[str, Any] = field(repr=False)
    failed_report: dict[str, Any] = field(repr=False)
    feedback: str = field(repr=False)
    role_name: str
    role_slug: str
    jd_text: str = field(repr=False)
    role_id: int


def build_assessment_repair_provider_plan(
    *,
    task: Task,
    role: Role,
) -> AssessmentRepairProviderPlan:
    failed_report = (
        dict(task.extra_data.get("battle_test"))
        if isinstance(task.extra_data, dict)
        and isinstance(task.extra_data.get("battle_test"), dict)
        else {}
    )
    role_name = str(role.name or "Role")
    return AssessmentRepairProviderPlan(
        prior_spec=reconstruct_generated_task_spec(task),
        failed_report=failed_report,
        feedback=battle_test_repair_feedback(failed_report),
        role_name=role_name,
        role_slug=_slugify(role_name),
        jd_text=_role_jd_text(role),
        role_id=int(role.id),
    )


__all__ = [
    "AssessmentRepairProviderPlan",
    "build_assessment_repair_provider_plan",
]
