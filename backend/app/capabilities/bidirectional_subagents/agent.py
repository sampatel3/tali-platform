"""Bidirectional sub-agents — extends all four sub-agents.

Sub-agents request artifacts, propose counterfactuals, explain
themselves. The hook is per-score: every sub-agent calls ``enrich``
on its raw ``SubAgentResult.output`` before returning. When the
capability is off, ``enrich`` is a pass-through.

Requires: reasoning_orchestrator (since artifact requests need a
planner to route the request back to the recruiter or to fetch the
artifact directly).
"""

from __future__ import annotations

from typing import Any

from .._stub_helpers import CapabilityContext


CAPABILITY = "bidirectional_subagents"


def enrich(
    ctx: CapabilityContext, *, sub_agent: str, raw_output: dict[str, Any]
) -> dict[str, Any]:
    """Return an output dict possibly enriched with bidirectional fields.

    Convention: enrichment adds keys but never removes them. Existing
    consumers reading ``raw_output["score"]`` keep working unchanged.

    Possible enrichment keys (added when active):
      ``artifact_requests``: list[{kind, target, rationale}]
      ``counterfactuals``: list[{premise, outcome}]
      ``self_explanation``: str
      ``ood_signals``: list[str]
    """
    if not ctx.is_active(CAPABILITY):
        return raw_output
    return raw_output  # TODO: actual enrichment per sub_agent name


__all__ = ["CAPABILITY", "enrich"]
