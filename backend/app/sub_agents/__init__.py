"""Uniform-contract LLM workers wrapping existing services.

Each sub-agent owns one domain (pre-screen, cv scoring, graph priors,
assessment scoring, intent parsing). They share a common
``run(req: SubAgentRequest) -> SubAgentResult`` interface so the
orchestrator can discover and call them identically through the MCP
tool registry.

Importing this package auto-registers the v1 sub-agents.
"""

from .base import SubAgent, SubAgentRequest, SubAgentResult
from .registry import all_sub_agents, get_sub_agent, register_sub_agent


# Auto-register the canonical sub-agents on import. ``graph_priors``
# went live in the multi-agent upgrade (Phase 2) — it falls back to a
# legacy heuristic when Graphiti is sparse, so registering it
# unconditionally is safe.
from . import assessment_scoring  # noqa: F401, E402
from . import cv_scoring  # noqa: F401, E402
from . import graph_priors  # noqa: F401, E402
from . import intent_parser  # noqa: F401, E402
from . import pre_screen  # noqa: F401, E402
from . import task_selection  # noqa: F401, E402


__all__ = [
    "SubAgent",
    "SubAgentRequest",
    "SubAgentResult",
    "all_sub_agents",
    "get_sub_agent",
    "register_sub_agent",
]
