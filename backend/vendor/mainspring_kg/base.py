"""Pluggable backend interface for the platform's knowledge graph."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass
class EpisodePayload:
    """Brand-agnostic episode payload passed to a backend.

    Mirrors what's stored in the ``Episode`` table; backends may also
    enrich with brand-specific graph structure when they're a real
    graph DB.
    """
    brand_id: int
    case_id: Optional[int]
    kind: str                                  # "entity" | "decision" | "outcome"
    payload: dict[str, Any]
    valid_at: datetime
    recorded_at: datetime
    source: str = "engine"


@dataclass
class Priors:
    """The outcome distribution among similar past cases."""
    case_id: int
    neighbour_count: int
    p_positive: float                          # P(positive outcome | similar cases)
    p_advance: float                           # P(advanced through policy | similar cases)
    confidence: float                          # 0..1, scales with neighbour count
    examples: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def empty(cls, case_id: int) -> "Priors":
        return cls(case_id=case_id, neighbour_count=0, p_positive=0.0,
                   p_advance=0.0, confidence=0.0)


@dataclass
class ReplayResult:
    """The full episode timeline for a case, as-of a moment."""
    case_id: int
    as_of: datetime
    episodes: list[EpisodePayload]


@runtime_checkable
class KnowledgeGraphBackend(Protocol):
    """A backend implements these four methods. Everything else (storage,
    sharding, indexes) is its own business."""

    name: str

    def write(self, episode: EpisodePayload) -> None:
        """Persist one episode. Idempotent — re-writes of the same
        (brand, case, kind, recorded_at) payload are no-ops."""
        ...

    def get_priors(self, *, brand_id: int, case_id: int) -> Priors:
        """Return the outcome distribution for cases similar to this one."""
        ...

    def replay_as_of(
        self, *, brand_id: int, case_id: int, as_of: datetime,
    ) -> ReplayResult:
        """Return every episode for the case where ``recorded_at <= as_of``."""
        ...

    def healthcheck(self) -> bool:
        """Return True iff the backend is reachable and accepting writes."""
        ...
