"""LLM-driven orchestrator — replacement, not extension.

When active, this orchestrator plans the workflow, routes sub-agents
by uncertainty, admits OOD candidates, and proposes experiments.
When inactive, callers fall through to the v1 orchestrator
(``app.agent_runtime.orchestrator.run_cycle``).

The stub here returns ``None`` — caller is expected to interpret None
as "use v1". This is how the addendum's "replaces=['orchestrator']"
shape is implemented without ripping out the v1 path.

Requires: drift_monitor (so OOD admission has a signal source).
"""

from __future__ import annotations

from typing import Any

from .._stub_helpers import CapabilityContext


CAPABILITY = "reasoning_orchestrator"


def run_cycle_with_reasoning(ctx: CapabilityContext, *, role_id: int) -> Any | None:
    """Return the cycle report when active; None when the v1 path should run.

    Callers in the orchestrator dispatch shim look at the return value:
      if (out := run_cycle_with_reasoning(ctx, role_id=role_id)) is not None:
          return out
      return v1_run_cycle(role_id)
    """
    if not ctx.is_active(CAPABILITY):
        return None
    return None  # TODO: real LLM-driven plan/route loop


__all__ = ["CAPABILITY", "run_cycle_with_reasoning"]
