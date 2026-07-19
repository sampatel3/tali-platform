"""Threshold resolution helpers shared by role update commands."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ...models.role import Role


def effective_role_fit_threshold(db: Session, role: Role) -> float | None:
    """Resolve the policy boundary without disguising failures as no threshold."""

    from ...services.auto_threshold_service import resolve_role_fit_threshold

    return resolve_role_fit_threshold(db, role=role)


def thresholds_equal(first: float | None, second: float | None) -> bool:
    if first is None or second is None:
        return first is None and second is None
    return abs(float(first) - float(second)) < 0.05


__all__ = ["effective_role_fit_threshold", "thresholds_equal"]
