"""Role-agent chat: conversational steering of a role's autonomous agent.

The recruiter talks to a role's agent in natural language to adjust
constraints and thresholds, see the impact, and re-run screening. Sits
beside ``taali_chat`` (read-only candidate search) but is action-taking and
role-scoped, and it unifies the role's HITL surface (open questions + pending
decisions) into one timeline.
"""

from .engine import run_agent_turn

__all__ = ["run_agent_turn"]
