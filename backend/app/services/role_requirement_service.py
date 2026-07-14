"""Canonical conversion of recruiter criteria into pre-screen requirements."""

from __future__ import annotations

from ..cv_matching.schemas import Priority, RequirementInput
from ..models.role import Role


def build_pre_screen_requirements(role: Role) -> list[RequirementInput]:
    """Return ordered, active criteria in the runner's structured contract."""
    requirements: list[RequirementInput] = []
    for criterion in sorted(
        (role.criteria or []), key=lambda item: getattr(item, "ordering", 0)
    ):
        if getattr(criterion, "deleted_at", None) is not None:
            continue
        text = str(getattr(criterion, "text", None) or "").strip()
        if not text:
            continue
        bucket = str(
            getattr(criterion, "bucket", None)
            or ("must" if bool(getattr(criterion, "must_have", False)) else "preferred")
        )
        priority = (
            Priority.MUST_HAVE
            if bucket in {"must", "constraint"}
            else Priority.STRONG_PREFERENCE
        )
        requirements.append(
            RequirementInput(
                id=f"crit_{int(criterion.id)}",
                requirement=text,
                priority=priority,
            )
        )
    return requirements


__all__ = ["build_pre_screen_requirements"]
