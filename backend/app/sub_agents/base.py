"""Shared dataclasses + Protocol for the uniform sub-agent contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class SubAgentRequest:
    """Input to any sub-agent.

    ``metering_context`` flows through to the underlying Anthropic call
    sites (see ``cv_matching/runner.run_cv_match`` for the canonical
    shape) so token usage attributes back to the right org/role/agent
    run for billing reconciliation.
    """

    organization_id: int
    application_id: int
    role_id: int
    skip_cache: bool = False
    # Free-form extension slot. Sub-agents that need more (e.g.
    # intent_parser needs the slot dictionary it should parse) read
    # from here. Keeps the public signature stable across sub-agents.
    extra: dict[str, Any] = field(default_factory=dict)
    # Forwarded to Anthropic clients via their ``metering`` kwarg —
    # never mutated by the sub-agent.
    metering_context: dict[str, Any] | None = None


@dataclass
class SubAgentResult:
    """Output of any sub-agent.

    On error the sub-agent returns ``ok=False`` with ``error`` set; it
    does NOT raise. The orchestrator expects to inspect ``ok`` and skip
    the downstream policy step when False.
    """

    sub_agent: str
    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    error: str | None = None
    cache_hit: bool = False
    tokens_used: int = 0


class SubAgent(Protocol):
    """The uniform contract.

    Implementers expose a stable ``name`` (used by the registry +
    orchestrator MCP tool routing) and a ``run`` method. Implementations
    are typically callable classes with cache + budget + telemetry
    cross-cutting concerns embedded.
    """

    name: str

    def run(self, req: SubAgentRequest) -> SubAgentResult:
        ...


__all__ = ["SubAgent", "SubAgentRequest", "SubAgentResult"]
