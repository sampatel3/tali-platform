"""Uniform-contract LLM workers wrapping existing services.

The production policy evaluator registers and runs exactly **four**:

  pre_screen · cv_scoring · graph_priors · assessment_scoring

The Amendment A2 ``task_selection`` implementation remains importable for
offline evaluation, but is intentionally unregistered: its three outcomes are
not connected to the current role-linked task, experiment, HITL, and artifact
request workflows.  Registration is not an availability signal.

Recruiter intent is captured as ``RoleIntent`` (Amendment A1), fetched once by
the policy evaluator, and consumed by the pre-screen and CV scorers. The
superseded ``intent_parser`` execution path is retired; its provider-free
compatibility facade remains unregistered.
"""

from .base import SubAgent, SubAgentRequest, SubAgentResult
from .registry import all_sub_agents, get_sub_agent, register_sub_agent


# Auto-register the four production pre-evaluation sub-agents on import.
from . import assessment_scoring  # noqa: F401, E402
from . import cv_scoring  # noqa: F401, E402
from . import graph_priors  # noqa: F401, E402
from . import pre_screen  # noqa: F401, E402
# Preserve the experimental module import surface without registering it.
from . import task_selection  # noqa: F401, E402


__all__ = [
    "SubAgent",
    "SubAgentRequest",
    "SubAgentResult",
    "all_sub_agents",
    "get_sub_agent",
    "register_sub_agent",
]
