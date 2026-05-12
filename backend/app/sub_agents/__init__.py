"""Uniform-contract LLM workers wrapping existing services.

Per §2 of ``recruitment_system_architecture.md`` the canonical sub-agent
set is exactly **five**:

  pre_screen · cv_scoring · graph_priors · task_selection · assessment_scoring

All five share the same ``run(req: SubAgentRequest) -> SubAgentResult``
protocol so the orchestrator can dispatch them identically.

The ``intent_parser`` module is preserved as an internal helper for the
Workable-note slot extractor that feeds the engine's
``decision_policy.intent`` overlay, but it is **not** a canonical
sub-agent and is **not** auto-registered here. Recruiter intent is
captured manually as ``RoleIntent`` (Amendment A1) and read by every
sub-agent at score time.
"""

from .base import SubAgent, SubAgentRequest, SubAgentResult
from .registry import all_sub_agents, get_sub_agent, register_sub_agent


# Auto-register the canonical five sub-agents on import.
from . import assessment_scoring  # noqa: F401, E402
from . import cv_scoring  # noqa: F401, E402
from . import graph_priors  # noqa: F401, E402
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
