"""Pydantic contracts for the writeback pipeline.

The recruiter UI submits ``GraphWriteHint`` dicts. The pipeline
validates, classifies sensitivity, and commits or queues.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GraphWriteHint(BaseModel):
    """Mirror of ``app.agent_runtime.contracts.GraphWriteHint``.

    Kept here too so the writeback package is self-contained — the
    contracts module is the recruiter-facing surface; this one is the
    apply-time pipeline's contract. Both validate the same dict shape.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "assert_edge",
        "invalidate_edge",
        "update_edge_property",
        "assert_node",
    ]
    from_node_id: str | None = None
    edge_type: str | None = None
    to_node_id: str | None = None
    properties: dict | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)


@dataclass
class ValidationResult:
    accepted: bool
    sensitivity: Literal["low", "medium", "high"] | None = None
    reason: str | None = None

    @classmethod
    def accept(cls, *, sensitivity: str) -> "ValidationResult":
        return cls(accepted=True, sensitivity=sensitivity)

    @classmethod
    def reject(cls, reason: str) -> "ValidationResult":
        return cls(accepted=False, reason=reason)


@dataclass
class WritebackReport:
    """Returned by the end-to-end handler — feeds back to the UI."""

    feedback_episode_uuid: str | None = None
    auto_committed: list[GraphWriteHint] = field(default_factory=list)
    queued_for_cosign: list[GraphWriteHint] = field(default_factory=list)
    blocked: list[tuple[GraphWriteHint, str]] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "auto_committed": len(self.auto_committed),
            "queued_for_cosign": len(self.queued_for_cosign),
            "blocked": len(self.blocked),
        }


__all__ = [
    "GraphWriteHint",
    "ValidationResult",
    "WritebackReport",
]
