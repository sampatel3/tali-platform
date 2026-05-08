"""Unified action layer.

Every action a recruiter performs manually lives here as a pure function
``run(db, actor, **kwargs) -> ...``. HTTP route handlers and the
autonomous agent's MCP tools both call these — so the audit trail,
idempotency, and side effects stay identical regardless of who triggered
the action.
"""

from . import (  # noqa: F401  re-export for ``from app.actions import advance_stage``
    advance_stage,
    approve_decision,
    ask_recruiter,
    override_decision,
    queue_decision,
    reject_application,
    score_cv,
    send_assessment,
    teach_decision,
)
from .types import Actor

__all__ = [
    "Actor",
    "advance_stage",
    "approve_decision",
    "ask_recruiter",
    "override_decision",
    "queue_decision",
    "reject_application",
    "score_cv",
    "send_assessment",
    "teach_decision",
]
