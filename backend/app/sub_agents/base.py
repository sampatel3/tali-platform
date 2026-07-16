"""Shared dataclasses + Protocol for the uniform sub-agent contract."""

from __future__ import annotations

import re
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

    v2 fields (architecture spec §6.2):
      ``uncertainty``: 0..1 calibrated standard-error-like number.
        Higher means the agent is less sure of its score. The policy
        engine reads this to gate ``escalate_low_confidence``.
      ``citations``: list of ``{node_ids, edge_ids, summary}`` dicts —
        the graph paths the agent leaned on (matches GraphCitation
        in app.agent_runtime.contracts).
      ``exemplars_used``: list of ``{exemplar_id, similarity}`` dicts
        — exemplar-store hits retrieved at score time and injected as
        few-shot. Empty list means no exemplar lookup (Phase 1 era).

    All three default to safe-empty values so legacy sub-agent
    implementations stay valid without changes.
    """

    sub_agent: str
    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    error: str | None = None
    cache_hit: bool = False
    tokens_used: int = 0
    uncertainty: float = 0.0
    citations: list[dict[str, Any]] = field(default_factory=list)
    exemplars_used: list[dict[str, Any]] = field(default_factory=list)


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


def public_sub_agent_error(error: object) -> str | None:
    """Return only stable machine codes at model/API serialization boundaries."""
    if error is None:
        return None
    code = str(error).strip()
    if re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code):
        return code
    return "sub_agent_failed"


__all__ = [
    "SubAgent",
    "SubAgentRequest",
    "SubAgentResult",
    "public_sub_agent_error",
]
