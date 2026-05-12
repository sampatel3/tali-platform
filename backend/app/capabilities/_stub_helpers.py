"""Shared helpers for capability scaffolds.

Each capability stub looks the same: take a ``CapabilityContext``,
return a typed result, and noop when the flag is off. Centralising
the context type + the ``is_off`` short-circuit keeps the scaffolds
identical and trivial to extend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy.orm import Session

from .flags import CapabilityFlags, get_shared


@dataclass
class CapabilityContext:
    """The minimum context every capability needs to evaluate its flag
    and produce a contribution.

    ``decision_id`` is the idempotency-key style identifier the v1
    decision pipeline already uses (``run_id:application_id:decision_type``).
    """

    db: Session
    organization_id: int
    decision_id: str
    role_id: int | None = None
    role_family: str | None = None
    cohort_tags: tuple[str, ...] = field(default_factory=tuple)
    flags: CapabilityFlags | None = None  # injectable; tests can pass a fresh one

    def get_flags(self) -> CapabilityFlags:
        return self.flags or get_shared()

    def is_active(self, capability: str) -> bool:
        return self.get_flags().is_active(
            capability,
            db=self.db,
            organization_id=self.organization_id,
            decision_id=self.decision_id,
            role_id=self.role_id,
            role_family=self.role_family,
            cohort_tags=self.cohort_tags,
        )


__all__ = ["CapabilityContext"]
