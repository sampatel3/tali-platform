"""Import compatibility for capability APIs that are not product-ready.

The capability registry remains the source of truth.  These types preserve
historical import paths for downstream integrations without pretending that
an unavailable capability has an implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .flags import CapabilityFlags, get_shared
from .registry import get


class CapabilityUnavailableError(RuntimeError):
    """Raised when a compatibility API is called for an unavailable feature."""

    def __init__(self, capability: str) -> None:
        definition = get(capability)
        reason = (
            definition.unavailable_reason
            if definition is not None and definition.unavailable_reason
            else "The capability is not available in this release."
        )
        self.capability = capability
        self.reason = reason
        super().__init__(f"{capability} is unavailable: {reason}")


@dataclass
class CapabilityContext:
    """Historical decision context retained for import/type compatibility."""

    db: Session
    organization_id: int
    decision_id: str
    role_id: int | None = None
    role_family: str | None = None
    cohort_tags: tuple[str, ...] = field(default_factory=tuple)
    flags: CapabilityFlags | None = None

    def get_flags(self) -> CapabilityFlags:
        return self.flags or get_shared()

    def is_active(self, capability: str) -> bool:
        """Use the canonical registry-aware flag client (unavailable stays off)."""

        return self.get_flags().is_active(
            capability,
            db=self.db,
            organization_id=self.organization_id,
            decision_id=self.decision_id,
            role_id=self.role_id,
            role_family=self.role_family,
            cohort_tags=self.cohort_tags,
        )


def raise_unavailable(capability: str) -> None:
    """Fail closed instead of returning placeholder output."""

    raise CapabilityUnavailableError(capability)


__all__ = [
    "CapabilityContext",
    "CapabilityUnavailableError",
    "raise_unavailable",
]
