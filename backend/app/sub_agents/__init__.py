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


# Auto-register the v1 sub-agents on import. Phase 4 adds graph_priors.
from . import assessment_scoring  # noqa: F401, E402
from . import cv_scoring  # noqa: F401, E402
from . import intent_parser  # noqa: F401, E402
from . import pre_screen  # noqa: F401, E402


__all__ = [
    "SubAgent",
    "SubAgentRequest",
    "SubAgentResult",
    "all_sub_agents",
    "get_sub_agent",
    "register_sub_agent",
]
